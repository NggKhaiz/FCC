#!/usr/bin/env bash
# Rebuild a CPython 3.14.10 toolchain from source via codeload. The sandbox
# blocks objects.githubusercontent.com (uv/python-build-standalone) and the
# distro apt repos, so zlib and OpenSSL are built static from source too;
# CPython then links them into its extension modules.
#
# ctypes is enabled from the base image's libffi.so.8 runtime: the distro
# ships no dev package (ffi.h / libffi.so symlink), so the libffi 3.4.8
# headers are generated from the GitHub source tree without autotools and
# the missing linker symlink points at the installed shared library.
#
# Optional extensions still without system libs (sqlite, lzma, bz2,
# readline) are skipped; add their libs and rerun if a later phase needs
# them. Prefix: /home/user/py314
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

# --- libffi headers (runtime libffi.so.8 comes from the base image) ---------
# The GitHub tarball lacks a generated configure/ffi.h, and the sandbox has
# no autotools; ffi.h.in only needs four substitutions on x86-64 glibc.
if ! /home/user/py314/bin/python3.14 -c "import ctypes" 2>/dev/null; then
  fetch libffi/libffi v3.4.8 libffi
  mkdir -p "$OPT/libffi/include" "$OPT/libffi/lib"
  sed \
    -e 's/@VERSION@/3.4.8/g' \
    -e 's/@TARGET@/X86_64/g' \
    -e 's/@HAVE_LONG_DOUBLE@/1/g' \
    -e 's/@FFI_EXEC_TRAMPOLINE_TABLE@/0/g' \
    "$SRC/libffi/include/ffi.h.in" > "$OPT/libffi/include/ffi.h"
  cp "$SRC/libffi/src/x86/ffitarget.h" "$OPT/libffi/include/ffitarget.h"
  runtime_ffi="$(find /usr/lib /lib -name 'libffi.so.8*' -type f 2>/dev/null | head -1)"
  if [[ -z "$runtime_ffi" ]]; then
    echo "[py] libffi.so.8 runtime missing from base image" >&2
    exit 1
  fi
  ln -sf "$runtime_ffi" "$OPT/libffi/lib/libffi.so"
  echo "[py] libffi headers installed against $runtime_ffi"
fi

# --- CPython 3.14.10 ---------------------------------------------------------
# 3.14 dropped --with-zlib; custom prefixes are exposed through CPPFLAGS so
# the configure header checks find zlib.h / ffi.h, and LDFLAGS finds the libs.
if ! /home/user/py314/bin/python3.14 -c "import ssl, zlib, ctypes" 2>/dev/null; then
  fetch python/cpython v3.14.10 cpython
  pushd "$SRC/cpython" >/dev/null
  make distclean >/dev/null 2>&1 || true
  CPPFLAGS="-I$OPT/zlib/include -I$OPT/openssl/include -I$OPT/libffi/include" \
  LDFLAGS="-L$OPT/zlib/lib -L$OPT/openssl/lib -L$OPT/libffi/lib" \
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
import ctypes, ssl, sys, zlib
print(
    "python", sys.version.split()[0],
    "ssl", ssl.OPENSSL_VERSION,
    "zlib ok, ctypes", ctypes.sizeof(ctypes.c_void_p) * 8, "bit",
)
PY
echo "[py] bootstrap complete"
