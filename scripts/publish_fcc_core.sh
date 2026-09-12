#!/usr/bin/env bash
# Publish fcc-core wheels to a Python index (TestPyPI / PyPI).
# Never runs unless FCC_CORE_PUBLISH=1 and TWINE credentials are set.
# Does NOT default to production PyPI — set FCC_CORE_REPOSITORY explicitly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "${FCC_CORE_PUBLISH:-0}" != "1" ]]; then
  cat <<'MSG'
Refusing to publish (safety gate).

  FCC_CORE_PUBLISH=1 FCC_CORE_REPOSITORY=testpypi ./scripts/publish_fcc_core.sh

Steps:
  1. ./scripts/package_fcc_core.sh          # build wheels into dist/fcc_core
  2. pip install twine
  3. export TWINE_USERNAME=__token__
  4. export TWINE_PASSWORD=<pypi-token>
  5. FCC_CORE_PUBLISH=1 FCC_CORE_REPOSITORY=testpypi ./scripts/publish_fcc_core.sh

Production PyPI requires FCC_CORE_REPOSITORY=pypi (explicit).
MSG
  exit 0
fi

OUT="${FCC_CORE_WHEEL_DIR:-$ROOT/dist/fcc_core}"
if ! ls "$OUT"/*.whl >/dev/null 2>&1; then
  echo "No wheels in $OUT — run ./scripts/package_fcc_core.sh first"
  exit 1
fi

if ! command -v twine >/dev/null 2>&1; then
  echo "twine not found — pip install twine"
  exit 1
fi

REPO="${FCC_CORE_REPOSITORY:-testpypi}"
case "$REPO" in
  testpypi|pypi) ;;
  *)
    echo "FCC_CORE_REPOSITORY must be testpypi or pypi (got: $REPO)"
    exit 1
    ;;
esac

echo "==> twine check"
twine check "$OUT"/*.whl

echo "==> twine upload → $REPO"
if [[ "$REPO" == "testpypi" ]]; then
  twine upload --repository testpypi "$OUT"/*.whl
else
  twine upload "$OUT"/*.whl
fi
echo "==> done"
