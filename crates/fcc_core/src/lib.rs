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
        m.add_class::<PyBloom>()?;
        m.add_class::<PyWindow>()?;
        m.add("__version__", "0.1.0")?;
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
}
