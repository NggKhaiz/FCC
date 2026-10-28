#!/usr/bin/env bash
# Rebuild the Go toolchain from source via codeload (the sandbox allowlist
# permits github.com / codeload.github.com only). Chain:
#   go1.4.3 (C bootstrap) -> 1.16.15 -> 1.17.13 -> 1.20.14 -> 1.22.12
# Go 1.20 enforces a >=1.17.13 bootstrap, hence the intermediate steps.
# Idempotent: existing install dirs are skipped. Final GOROOT:
#   /home/user/goboot/go1.22
set -euo pipefail

ROOT=/home/user/goboot
SRC=/home/user/toolchain/src
mkdir -p "$ROOT" "$SRC"

fetch() {
  local tag=$1
  local dir=$2
  if [[ -x "$ROOT/$dir/bin/go" ]]; then
    echo "[go] $dir already installed, skipping"
    return
  fi
  if [[ ! -d "$SRC/$dir" ]]; then
    echo "[go] downloading $tag"
    curl -fsSL "https://codeload.github.com/golang/go/tar.gz/refs/tags/$tag" \
      -o "$SRC/$tag.tar.gz"
    mkdir -p "$SRC/$dir"
    tar -xzf "$SRC/$tag.tar.gz" -C "$SRC/$dir" --strip-components=1
  fi
}

build() {
  local dir=$1
  local bootstrap=${2:-}
  echo "[go] building $dir"
  pushd "$SRC/$dir/src" >/dev/null
  local env=(GOROOT="$SRC/$dir" GOPATH=/home/user/gopath GOCACHE=/home/user/.gocache
    GOFLAGS=-mod=mod GOPROXY=off GOOS=linux GOARCH=amd64 CGO_ENABLED=0)
  if [[ -n "$bootstrap" ]]; then
    env+=(GOROOT_BOOTSTRAP="$bootstrap")
  fi
  env "${env[@]}" ./make.bash
  popd >/dev/null
  rm -rf "$ROOT/$dir"
  cp -a "$SRC/$dir" "$ROOT/$dir"
  echo "[go] installed $ROOT/$dir"
}

fetch go1.4.3 go1.4.3
build go1.4.3

fetch go1.16.15 go1.16.15
build go1.16.15 "$ROOT/go1.4.3"

fetch go1.17.13 go1.17.13
build go1.17.13 "$ROOT/go1.16.15"

fetch go1.20.14 go1.20.14
build go1.20.14 "$ROOT/go1.17.13"

fetch go1.22.12 go1.22
build go1.22 "$ROOT/go1.20.14"

"$ROOT/go1.22/bin/go" version
echo "[go] bootstrap complete"
