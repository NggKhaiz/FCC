#!/usr/bin/env bash
# Package fcc_core wheels for distribution (manylinux when possible).
# Safe no-op when rustc/maturin missing. Does not push to PyPI by default.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OUT="${FCC_CORE_WHEEL_DIR:-$ROOT/dist/fcc_core}"
mkdir -p "$OUT"

if ! command -v rustc >/dev/null 2>&1; then
  echo "rustc not found — write packaging stub only"
  cat > "$OUT/README.txt" <<'STUB'
fcc_core wheels were not built (rustc missing).
On CI / a Rust host:
  ./scripts/package_fcc_core.sh
  # or: maturin build --release -m crates/fcc_core/Cargo.toml --out dist/fcc_core
Install:
  pip install dist/fcc_core/fcc_core-*.whl
  python -c "from free_claude_code.native import backend; print(backend())"
STUB
  exit 0
fi

if ! command -v maturin >/dev/null 2>&1; then
  echo "maturin not found — try: pip install 'maturin>=1.7,<2'"
  exit 0
fi

echo "==> maturin build --release → $OUT"
maturin build --release -m crates/fcc_core/Cargo.toml --out "$OUT"
echo "==> wheels:"
ls -la "$OUT"/*.whl 2>/dev/null || ls -la "$OUT"
echo "==> done"

echo "To publish (gated): FCC_CORE_PUBLISH=1 FCC_CORE_REPOSITORY=testpypi ./scripts/publish_fcc_core.sh"
