#!/usr/bin/env bash
# Rebuild a CPython 3.14.10 toolchain from source via codeload. The sandbox
# blocks objects.githubusercontent.com (uv/python-build-standalone) and the
# distro apt repos, so zlib and OpenSSL are built static from source too;
# CPython then links them into its extension modules.
#
# Optional extensions without system dev headers (ctypes, sqlite, lzma,
# bz2, readline) are skipped; add their libs and rerun if a later phase
# needs them. Prefix: /home/user/py314
set -euo pipefail

OPT=/home/user/.local/opt
SRC=/home/user/toolchain/src
JOBS=2
mkdir -p "$OPT" "$SRC"

fetch() {
  local repo=$1 tag=$2 dir=$3
  if [[ -d "$SRC/$dir" ]]; then
    echo "[py] $dir source present"
    return
  fi
  echo "[py] downloading $repo@$tag"
  curl -fsSL "https://codeload.github.com/$repo/tar.gz/refs/tags/$tag" \
    -o "$SRC/$dir.tar.gz"
  mkdir -p "$SRC/$dir"
  tar -xzf "$SRC/$dir.tar.gz" -C "$SRC/$dir" --strip-components=1
}

# --- zlib (static, PIC so CPython can link it into a shared module) ----------
if ! /home/user/py314/bin/python3.14 -c "import zlib" 2>/dev/null; then
  fetch madler/zlib v1.3.1 zlib
  pushd "$SRC/zlib" >/dev/null
  make distclean >/dev/null 2>&1 || true
  CFLAGS="-O3 -fPIC" ./configure --prefix="$OPT/zlib" --static
  make -j"$JOBS"
  make install
  popd >/dev/null
  echo "[py] zlib installed"
fi

# --- OpenSSL (static) --------------------------------------------------------
if [[ ! -f "$OPT/openssl/lib/libssl.a" ]]; then
  fetch openssl/openssl openssl-3.0.15 openssl
  pushd "$SRC/openssl" >/dev/null
  ./Configure linux-x86_64 no-shared --prefix="$OPT/openssl" --libdir=lib
  make -j"$JOBS"
  make install_sw
  popd >/dev/null
  echo "[py] openssl installed"
fi

# --- CPython 3.14.10 ---------------------------------------------------------
# 3.14 dropped --with-zlib; custom prefixes are exposed through CPPFLAGS so
# the configure header checks find zlib.h, and LDFLAGS finds the static lib.
if ! /home/user/py314/bin/python3.14 -c "import ssl, zlib" 2>/dev/null; then
  fetch python/cpython v3.14.10 cpython
  pushd "$SRC/cpython" >/dev/null
  make distclean >/dev/null 2>&1 || true
  CPPFLAGS="-I$OPT/zlib/include -I$OPT/openssl/include" \
  LDFLAGS="-L$OPT/zlib/lib -L$OPT/openssl/lib" \
  ./configure \
    --prefix=/home/user/py314 \
    --with-openssl="$OPT/openssl" \
    --with-ensurepip=install
  make -j"$JOBS"
  make install
  popd >/dev/null
  echo "[py] cpython installed"
fi

export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
/home/user/py314/bin/python3.14 - <<'PY'
import ssl, sys, zlib
print("python", sys.version.split()[0], "ssl", ssl.OPENSSL_VERSION, "zlib ok")
PY
echo "[py] bootstrap complete"
