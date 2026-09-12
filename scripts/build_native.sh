#!/usr/bin/env bash
# Build optional Rust fcc_core wheel when toolchain is present.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v rustc >/dev/null 2>&1; then
  echo "rustc not found — skipping native build (Python ultra-core remains active)"
  exit 0
fi
if ! command -v maturin >/dev/null 2>&1; then
  echo "maturin not found — try: pip install maturin"
  exit 0
fi

echo "==> building fcc_core (release)"
maturin build --release -m crates/fcc_core/Cargo.toml
if [[ "${FCC_CORE_PACKAGE:-0}" == "1" ]]; then
  ./scripts/package_fcc_core.sh
fi
echo "==> done. Install wheel from target/wheels/ or: maturin develop --release -m crates/fcc_core/Cargo.toml"
echo "    Package for dist/: FCC_CORE_PACKAGE=1 ./scripts/build_native.sh"
