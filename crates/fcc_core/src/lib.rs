//! FCC native hot-path core.
//! Pure Rust logic + optional PyO3 bindings (`feature = "python"`).

use std::collections::{HashMap, VecDeque};
use std::time::Instant;

/// FNV-1a 64-bit
pub fn fnv1a64_bytes(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &b in data {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

pub fn fnv1a64_str(s: &str) -> u64 {
    fnv1a64_bytes(s.as_bytes())
}

pub fn validate_provider_id_fast(s: &str) -> bool {
    let b = s.as_bytes();
    if b.is_empty() || b.len() > 64 {
        return false;
    }
    if !(b[0].is_ascii_lowercase()) {
        return false;
    }
    b.iter()
        .all(|&c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
}

pub fn validate_model_ref_fast(s: &str) -> bool {
    if s.is_empty() || s.len() > 512 {
        return false;
    }
    let Some(slash) = s.find('/') else {
        return false;
    };
    if slash == 0 || slash + 1 >= s.len() {
        return false;
    }
    let (provider, model) = s.split_at(slash);
    let model = &model[1..];
    if !validate_provider_id_fast(provider) {
        return false;
    }
    model.chars().all(|ch| {
        ch.is_ascii_alphanumeric()
            || matches!(ch, '/' | '_' | '-' | '.' | ':')
    })
}

pub fn validate_session_id_fast(s: &str) -> bool {
    if s.is_empty() || s.len() > 128 {
        return false;
    }
    s.chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

pub fn is_safe_asset_name(s: &str) -> bool {
    if s.is_empty() || s.len() > 128 {
        return false;
    }
    if s == "." || s == ".." || s.starts_with('.') {
        return false;
    }
    if s.contains('/') || s.contains('\\') || s.contains('\0') {
        return false;
    }
    s.chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

fn looks_like_id(value: &str) -> bool {
    if value.len() < 8 {
        return false;
    }
    let mut has_digit = false;
    for c in value.chars() {
        match c {
            '0'..='9' => has_digit = true,
            'a'..='f' | 'A'..='F' | '-' | '_' => {}
            _ => return false,
        }
    }
    has_digit
}

pub fn normalize_path_key(path: &str) -> String {
    if path.is_empty() {
        return "/".to_string();
    }
    let path = path.split('?').next().unwrap_or(path);
    let mut out: Vec<&str> = Vec::new();
    for part in path.split('/') {
        if part.is_empty() {
            continue;
        }
        if part.len() > 40 || part.chars().all(|c| c.is_ascii_digit()) || looks_like_id(part)
        {
            out.push(":id");
        } else {
            let end = part.len().min(64);
            out.push(&part[..end]);
        }
    }
    if out.is_empty() {
        "/".to_string()
    } else {
        format!("/{}", out.join("/"))
    }
}

pub fn estimate_tokens_fast(text: &str) -> usize {
    if text.is_empty() {
        return 0;
    }
    let mut ascii = 0usize;
    let mut wide = 0usize;
    for ch in text.chars() {
        let o = ch as u32;
        if (0x4E00..=0x9FFF).contains(&o)
            || (0x3040..=0x30FF).contains(&o)
            || (0xAC00..=0xD7AF).contains(&o)
            || (0xFF00..=0xFFEF).contains(&o)
        {
            wide += 1;
        } else {
            ascii += 1;
        }
    }
    let tokens = wide + (ascii + 3) / 4;
    if tokens == 0 {
        1
    } else {
        tokens
    }
}

pub fn sanitize_log_fast(value: &str, max_length: usize) -> String {
    let mut out = String::with_capacity(value.len().min(max_length + 8));
    for ch in value.chars() {
        match ch {
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if (c as u32) >= 32 || c == '\t' => out.push(c),
            _ => {}
        }
        if out.len() >= max_length {
            break;
        }
    }
    if value.len() > max_length || out.len() >= max_length {
        let mut s: String = out.chars().take(max_length).collect();
        s.push_str("...");
        s
    } else {
        out
    }
}

/// Double-hash Bloom filter
pub struct BloomFilter {
    bits: Vec<u8>,
    m: usize,
    k: usize,
    count: usize,
}

impl BloomFilter {
    pub fn new(capacity: usize, error_rate: f64) -> Self {
        let capacity = capacity.max(1);
        let error_rate = if error_rate <= 0.0 || error_rate >= 1.0 {
            0.01
        } else {
            error_rate
        };
        let mut m = ((- (capacity as f64) * error_rate.ln()) / (2f64.ln().powi(2))) as usize;
        m = m.max(64);
        m = (m + 63) / 64 * 64;
        let mut k = ((m as f64 / capacity as f64) * 2f64.ln()) as usize;
        k = k.clamp(1, 16);
        Self {
            bits: vec![0u8; m / 8],
            m,
            k,
            count: 0,
        }
    }

    fn indexes(&self, key: &[u8]) -> impl Iterator<Item = usize> + '_ {
        let h1 = fnv1a64_bytes(key);
        let h2 = fnv1a64_bytes(&h1.to_le_bytes());
        let m = self.m as u64;
        let k = self.k;
        (0..k).map(move |i| ((h1.wrapping_add((i as u64).wrapping_mul(h2))) % m) as usize)
    }

    pub fn add(&mut self, key: &[u8]) {
        for idx in self.indexes(key) {
            self.bits[idx >> 3] |= 1 << (idx & 7);
        }
        self.count += 1;
    }

    pub fn might_contain(&self, key: &[u8]) -> bool {
        for idx in self.indexes(key) {
            if self.bits[idx >> 3] & (1 << (idx & 7)) == 0 {
                return false;
            }
        }
        true
    }

    pub fn clear(&mut self) {
        for b in &mut self.bits {
            *b = 0;
        }
        self.count = 0;
    }

    pub fn count(&self) -> usize {
        self.count
    }
}

/// Sliding window rate limiter
pub struct SlidingWindow {
    windows: HashMap<String, VecDeque<Instant>>,
    blocked: HashMap<String, Instant>,
    start: Instant,
}

impl SlidingWindow {
    pub fn new() -> Self {
        Self {
            windows: HashMap::new(),
            blocked: HashMap::new(),
            start: Instant::now(),
        }
    }

    pub fn allow(
        &mut self,
        key: &str,
        max_requests: usize,
        window_seconds: f64,
        block_seconds: f64,
    ) -> (bool, u64) {
        let now = Instant::now();
        if let Some(until) = self.blocked.get(key) {
            if now < *until {
                let retry = until.duration_since(now).as_secs().max(1);
                return (false, retry);
            }
        }
        let q = self.windows.entry(key.to_string()).or_default();
        let window = std::time::Duration::from_secs_f64(window_seconds.max(0.001));
        while let Some(front) = q.front() {
            if now.duration_since(*front) > window {
                q.pop_front();
            } else {
                break;
            }
        }
        if q.len() >= max_requests {
            let block = std::time::Duration::from_secs_f64(block_seconds.max(0.0));
            self.blocked.insert(key.to_string(), now + block);
            return (false, block_seconds.max(1.0) as u64);
        }
        q.push_back(now);
        let _ = self.start; // silence unused in non-python builds
        (true, 0)
    }
}

impl Default for SlidingWindow {
    fn default() -> Self {
        Self::new()
    }
}


// ---------------------------------------------------------------------------
// Phase 12 hot paths: SHA-256, HMAC, hex, peer URL gate, FCCOM1, content digest
// Pure Rust, no external crates (max portability / minimal attack surface)
// ---------------------------------------------------------------------------

/// SHA-256 (FIPS 180-4) — compact implementation for hot-path digests.
pub mod sha256 {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];

    #[inline]
    fn rotr(x: u32, n: u32) -> u32 {
        x.rotate_right(n)
    }

    pub fn hash(data: &[u8]) -> [u8; 32] {
        let mut h: [u32; 8] = [
            0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
            0x5be0cd19,
        ];
        let bit_len = (data.len() as u64).wrapping_mul(8);
        let mut buf = data.to_vec();
        buf.push(0x80);
        while (buf.len() % 64) != 56 {
            buf.push(0);
        }
        buf.extend_from_slice(&bit_len.to_be_bytes());
        for chunk in buf.chunks_exact(64) {
            let mut w = [0u32; 64];
            for i in 0..16 {
                w[i] = u32::from_be_bytes([
                    chunk[i * 4],
                    chunk[i * 4 + 1],
                    chunk[i * 4 + 2],
                    chunk[i * 4 + 3],
                ]);
            }
            for i in 16..64 {
                let s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
                let s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
                w[i] = w[i - 16]
                    .wrapping_add(s0)
                    .wrapping_add(w[i - 7])
                    .wrapping_add(s1);
            }
            let mut a = h[0];
            let mut b = h[1];
            let mut c = h[2];
            let mut d = h[3];
            let mut e = h[4];
            let mut f = h[5];
            let mut g = h[6];
            let mut hh = h[7];
            for i in 0..64 {
                let s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
                let ch = (e & f) ^ ((!e) & g);
                let t1 = hh
                    .wrapping_add(s1)
                    .wrapping_add(ch)
                    .wrapping_add(K[i])
                    .wrapping_add(w[i]);
                let s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
                let maj = (a & b) ^ (a & c) ^ (b & c);
                let t2 = s0.wrapping_add(maj);
                hh = g;
                g = f;
                f = e;
                e = d.wrapping_add(t1);
                d = c;
                c = b;
                b = a;
                a = t1.wrapping_add(t2);
            }
            h[0] = h[0].wrapping_add(a);
            h[1] = h[1].wrapping_add(b);
            h[2] = h[2].wrapping_add(c);
            h[3] = h[3].wrapping_add(d);
            h[4] = h[4].wrapping_add(e);
            h[5] = h[5].wrapping_add(f);
            h[6] = h[6].wrapping_add(g);
            h[7] = h[7].wrapping_add(hh);
        }
        let mut out = [0u8; 32];
        for (i, v) in h.iter().enumerate() {
            out[i * 4..(i + 1) * 4].copy_from_slice(&v.to_be_bytes());
        }
        out
    }

    pub fn hash_hex(data: &[u8]) -> String {
        hex::encode(&hash(data))
    }
}

pub mod hex {
    const LUT: &[u8; 16] = b"0123456789abcdef";
    pub fn encode(data: &[u8]) -> String {
        let mut s = String::with_capacity(data.len() * 2);
        for &b in data {
            s.push(LUT[(b >> 4) as usize] as char);
            s.push(LUT[(b & 0xf) as usize] as char);
        }
        s
    }
}

/// HMAC-SHA256
pub fn hmac_sha256(key: &[u8], message: &[u8]) -> [u8; 32] {
    const BLOCK: usize = 64;
    let mut k = if key.len() > BLOCK {
        sha256::hash(key).to_vec()
    } else {
        key.to_vec()
    };
    k.resize(BLOCK, 0);
    let mut ipad = [0u8; BLOCK];
    let mut opad = [0u8; BLOCK];
    for i in 0..BLOCK {
        ipad[i] = k[i] ^ 0x36;
        opad[i] = k[i] ^ 0x5c;
    }
    let mut inner = Vec::with_capacity(BLOCK + message.len());
    inner.extend_from_slice(&ipad);
    inner.extend_from_slice(message);
    let ih = sha256::hash(&inner);
    let mut outer = Vec::with_capacity(BLOCK + 32);
    outer.extend_from_slice(&opad);
    outer.extend_from_slice(&ih);
    sha256::hash(&outer)
}

pub fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> String {
    hex::encode(&hmac_sha256(key, message))
}

pub fn sha256_hex(data: &[u8]) -> String {
    hex::encode(&sha256::hash(data))
}

pub fn key_id16(key: &[u8]) -> String {
    let h = sha256::hash(key);
    hex::encode(&h[..8])
}

/// Canonical content digest: sorted `name\\0hex\\n` lines → sha256 hex
pub fn content_digest_hex(pairs: &[(String, Vec<u8>)]) -> String {
    let mut items: Vec<(String, String)> = pairs
        .iter()
        .filter(|(n, _)| n != "signature.hmac.json" && n != "signature.ed25519.json" && !n.ends_with('/'))
        .map(|(n, b)| (n.clone(), hex::encode(&sha256::hash(b))))
        .collect();
    items.sort_by(|a, b| a.0.cmp(&b.0));
    let mut blob = String::new();
    for (i, (n, d)) in items.iter().enumerate() {
        if i > 0 {
            blob.push('\n');
        }
        blob.push_str(n);
        blob.push('\0');
        blob.push_str(d);
    }
    sha256_hex(blob.as_bytes())
}

/// SSRF-oriented peer URL gate (mirrors Python peer_scrape.normalize checks partially)
pub fn peer_url_ok(url: &str) -> bool {
    let url = url.trim();
    if url.is_empty() || url.len() > 512 {
        return false;
    }
    let lower = url.to_ascii_lowercase();
    if !(lower.starts_with("http://") || lower.starts_with("https://")) {
        return false;
    }
    if url.contains('@') {
        return false; // credentials
    }
    if url.contains('#') {
        return false;
    }
    // block metadata IP literal
    if lower.contains("169.254.169.254") || lower.contains("[fd00:ec2::254]") {
        return false;
    }
    // must eventually be under /admin/api/
    if let Some(idx) = url.find("://") {
        let rest = &url[idx + 3..];
        let path = if let Some(s) = rest.find('/') {
            &rest[s..]
        } else {
            "/"
        };
        let path = path.split('?').next().unwrap_or(path);
        if path == "/" {
            return true; // will default to export path
        }
        path.starts_with("/admin/api/") && !path.contains("..")
    } else {
        false
    }
}

/// FCCOM1 binary metrics encoder (mirrors openmetrics_protobuf.py)
pub fn fccom1_encode_basic(
    node_id: &str,
    version: &str,
    total_requests: f64,
    total_errors: f64,
    uptime: f64,
) -> Vec<u8> {
    let mut out: Vec<u8> = Vec::with_capacity(256);
    out.extend_from_slice(b"FCCOM1\x00\x00");
    out.extend_from_slice(&1u32.to_le_bytes());
    // placeholder count
    let count_pos = out.len();
    out.extend_from_slice(&0u32.to_le_bytes());
    let mut count = 0u32;
    let mut push = |name: &str, mtype: u8, value: f64, labels: &[(&str, &str)]| {
        let nb = name.as_bytes();
        out.extend_from_slice(&(nb.len() as u16).to_le_bytes());
        out.extend_from_slice(nb);
        out.push(mtype);
        out.extend_from_slice(&value.to_le_bytes());
        out.extend_from_slice(&(labels.len() as u16).to_le_bytes());
        for (k, v) in labels {
            let kb = k.as_bytes();
            let vb = v.as_bytes();
            out.extend_from_slice(&(kb.len() as u16).to_le_bytes());
            out.extend_from_slice(kb);
            out.extend_from_slice(&(vb.len() as u16).to_le_bytes());
            out.extend_from_slice(vb);
        }
        count += 1;
    };
    let node = if node_id.is_empty() { "local" } else { node_id };
    push(
        "fcc_info",
        2,
        1.0,
        &[("node_id", node), ("version", version)],
    );
    push("fcc_uptime_seconds", 0, uptime, &[("node_id", node)]);
    push("fcc_requests_total", 1, total_requests, &[("node_id", node)]);
    push("fcc_errors_total", 1, total_errors, &[("node_id", node)]);
    let cbytes = count.to_le_bytes();
    out[count_pos..count_pos + 4].copy_from_slice(&cbytes);
    out
}


// -------------------- PyO3 bindings --------------------
#[cfg(feature = "python")]
mod python_api {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::PyBytes;

    #[pyfunction]
    fn fnv1a64(data: &Bound<'_, PyAny>) -> PyResult<u64> {
        if let Ok(s) = data.extract::<&str>() {
            return Ok(fnv1a64_str(s));
        }
        if let Ok(b) = data.downcast::<PyBytes>() {
            return Ok(fnv1a64_bytes(b.as_bytes()));
        }
        let s: String = data.extract()?;
        Ok(fnv1a64_str(&s))
    }

    #[pyfunction]
    fn validate_provider_id_fast(s: &str) -> bool {
        super::validate_provider_id_fast(s)
    }

    #[pyfunction]
    fn validate_model_ref_fast(s: &str) -> bool {
        super::validate_model_ref_fast(s)
    }

    #[pyfunction]
    fn validate_session_id_fast(s: &str) -> bool {
        super::validate_session_id_fast(s)
    }

    #[pyfunction]
    fn is_safe_asset_name(s: &str) -> bool {
        super::is_safe_asset_name(s)
    }

    #[pyfunction]
    fn normalize_path_key(s: &str) -> String {
        super::normalize_path_key(s)
    }

    #[pyfunction]
    fn estimate_tokens_fast(s: &str) -> usize {
        super::estimate_tokens_fast(s)
    }

    #[pyfunction]
    #[pyo3(signature = (s, max_length=200))]
    fn sanitize_log_fast(s: &str, max_length: usize) -> String {
        super::sanitize_log_fast(s, max_length)
    }

    #[pyclass(name = "BloomFilter")]
    struct PyBloom {
        inner: BloomFilter,
    }

    #[pymethods]
    impl PyBloom {
        #[new]
        #[pyo3(signature = (capacity=10000, error_rate=0.01))]
        fn new(capacity: usize, error_rate: f64) -> Self {
            Self {
                inner: BloomFilter::new(capacity, error_rate),
            }
        }

        fn add(&mut self, key: &str) {
            self.inner.add(key.as_bytes());
        }

        fn might_contain(&self, key: &str) -> bool {
            self.inner.might_contain(key.as_bytes())
        }

        fn __contains__(&self, key: &str) -> bool {
            self.inner.might_contain(key.as_bytes())
        }

        fn clear(&mut self) {
            self.inner.clear();
        }

        #[getter]
        fn count(&self) -> usize {
            self.inner.count()
        }
    }

    #[pyclass(name = "SlidingWindow")]
    struct PyWindow {
        inner: SlidingWindow,
    }

    #[pymethods]
    impl PyWindow {
        #[new]
        fn new() -> Self {
            Self {
                inner: SlidingWindow::new(),
            }
        }

        #[pyo3(signature = (key, max_requests, window_seconds, block_seconds=60.0))]
        fn allow(
            &mut self,
            key: &str,
            max_requests: usize,
            window_seconds: f64,
            block_seconds: f64,
        ) -> (bool, u64) {
            self.inner
                .allow(key, max_requests, window_seconds, block_seconds)
        }
    }


    #[pyfunction]
    fn sha256_hex(data: &Bound<'_, PyAny>) -> PyResult<String> {
        if let Ok(b) = data.downcast::<PyBytes>() {
            return Ok(super::sha256_hex(b.as_bytes()));
        }
        if let Ok(s) = data.extract::<&str>() {
            return Ok(super::sha256_hex(s.as_bytes()));
        }
        let s: String = data.extract()?;
        Ok(super::sha256_hex(s.as_bytes()))
    }

    #[pyfunction]
    fn hmac_sha256_hex(key: &Bound<'_, PyAny>, message: &Bound<'_, PyAny>) -> PyResult<String> {
        let k: Vec<u8> = if let Ok(b) = key.downcast::<PyBytes>() {
            b.as_bytes().to_vec()
        } else if let Ok(s) = key.extract::<&str>() {
            s.as_bytes().to_vec()
        } else {
            key.extract::<Vec<u8>>()?
        };
        let m: Vec<u8> = if let Ok(b) = message.downcast::<PyBytes>() {
            b.as_bytes().to_vec()
        } else if let Ok(s) = message.extract::<&str>() {
            s.as_bytes().to_vec()
        } else {
            message.extract::<Vec<u8>>()?
        };
        Ok(super::hmac_sha256_hex(&k, &m))
    }

    #[pyfunction]
    fn key_id16(key: &Bound<'_, PyAny>) -> PyResult<String> {
        let k: Vec<u8> = if let Ok(b) = key.downcast::<PyBytes>() {
            b.as_bytes().to_vec()
        } else if let Ok(s) = key.extract::<&str>() {
            s.as_bytes().to_vec()
        } else {
            key.extract::<Vec<u8>>()?
        };
        Ok(super::key_id16(&k))
    }

    #[pyfunction]
    fn peer_url_ok(url: &str) -> bool {
        super::peer_url_ok(url)
    }

    #[pyfunction]
    fn content_digest_hex(pairs: Vec<(String, Vec<u8>)>) -> String {
        super::content_digest_hex(&pairs)
    }

    #[pyfunction]
    #[pyo3(signature = (node_id, version, total_requests, total_errors, uptime))]
    fn fccom1_encode_basic(
        node_id: &str,
        version: &str,
        total_requests: f64,
        total_errors: f64,
        uptime: f64,
    ) -> Vec<u8> {
        super::fccom1_encode_basic(node_id, version, total_requests, total_errors, uptime)
    }

    #[pymodule]
    fn fcc_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(fnv1a64, m)?)?;
        m.add_function(wrap_pyfunction!(validate_provider_id_fast, m)?)?;
        m.add_function(wrap_pyfunction!(validate_model_ref_fast, m)?)?;
        m.add_function(wrap_pyfunction!(validate_session_id_fast, m)?)?;
        m.add_function(wrap_pyfunction!(is_safe_asset_name, m)?)?;
        m.add_function(wrap_pyfunction!(normalize_path_key, m)?)?;
        m.add_function(wrap_pyfunction!(estimate_tokens_fast, m)?)?;
        m.add_function(wrap_pyfunction!(sanitize_log_fast, m)?)?;
        m.add_function(wrap_pyfunction!(sha256_hex, m)?)?;
        m.add_function(wrap_pyfunction!(hmac_sha256_hex, m)?)?;
        m.add_function(wrap_pyfunction!(key_id16, m)?)?;
        m.add_function(wrap_pyfunction!(peer_url_ok, m)?)?;
        m.add_function(wrap_pyfunction!(content_digest_hex, m)?)?;
        m.add_function(wrap_pyfunction!(fccom1_encode_basic, m)?)?;
        m.add_class::<PyBloom>()?;
        m.add_class::<PyWindow>()?;
        m.add("__version__", "0.2.0")?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_ok() {
        assert!(validate_provider_id_fast("groq"));
        assert!(!validate_provider_id_fast("Groq"));
        assert!(!validate_provider_id_fast(""));
        assert!(!validate_provider_id_fast(&"a".repeat(65)));
    }

    #[test]
    fn model_ok() {
        assert!(validate_model_ref_fast("groq/llama-3.1"));
        assert!(!validate_model_ref_fast("nopath"));
        assert!(!validate_model_ref_fast("/x"));
    }

    #[test]
    fn bloom_no_false_negative() {
        let mut b = BloomFilter::new(1000, 0.01);
        b.add(b"alpha");
        assert!(b.might_contain(b"alpha"));
    }

    #[test]
    fn tokens_cjk() {
        assert_eq!(estimate_tokens_fast("你好"), 2);
        assert!(estimate_tokens_fast("hello world") >= 2);
    }

    #[test]
    fn path_collapse() {
        let p = normalize_path_key("/admin/api/code/sessions/abcdef12-3456-7890-abcd-ef1234567890");
        assert!(p.contains(":id"));
    }

    #[test]
    fn sha256_empty() {
        // e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        let h = sha256_hex(b"");
        assert_eq!(h, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    }

    #[test]
    fn hmac_rfc4231_case1() {
        // RFC 4231 test case 1
        let key = vec![0x0bu8; 20];
        let msg = b"Hi There";
        let h = hmac_sha256_hex(&key, msg);
        assert_eq!(
            h,
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
        );
    }

    #[test]
    fn peer_url_gate() {
        assert!(peer_url_ok("http://replica:8082/admin/api/security/events/export"));
        assert!(peer_url_ok("https://hub.example"));
        assert!(!peer_url_ok("http://169.254.169.254/latest"));
        assert!(!peer_url_ok("file:///etc/passwd"));
        assert!(!peer_url_ok("http://user:pass@h/admin/api/x"));
    }

    #[test]
    fn fccom1_magic() {
        let b = fccom1_encode_basic("n", "1", 10.0, 1.0, 3.0);
        assert_eq!(&b[..8], b"FCCOM1\0\0");
    }
}
