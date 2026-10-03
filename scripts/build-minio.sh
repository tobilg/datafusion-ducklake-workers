#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
mode="${1:-docker}"
[[ "$mode" == docker || "$mode" == --native ]] || { echo 'Expected optional --native' >&2; exit 2; }
test "$(go env GOVERSION)" = go1.25.3
if [[ ! -d vendor/minio ]]; then
  git clone --depth 1 --branch RELEASE.2025-09-07T16-13-09Z https://github.com/minio/minio.git vendor/minio
fi
test "$(git -C vendor/minio rev-parse HEAD)" = 07c3a429bfed433e49018cb0f78a52145d4bedeb
cd vendor/minio
export GOTOOLCHAIN=local CGO_ENABLED=0
export GOCACHE="$PROJECT_ROOT/.cache/go-build" GOMODCACHE="$PROJECT_ROOT/.cache/go-mod"
if [[ "$mode" == --native ]]; then
  export GOOS="$(go env GOHOSTOS)" GOARCH="$(go env GOHOSTARCH)"
  mkdir -p "$PROJECT_ROOT/.tools/bin"
  python3 "$PROJECT_ROOT/scripts/tool_cache.py" minio -- go build -mod=readonly -trimpath -ldflags='-s -w' -o "$PROJECT_ROOT/.tools/bin/minio" .
  exit 0
fi
export GOOS=linux GOARCH="${MINIO_FIXTURE_ARCH:-arm64}"
mkdir -p "$PROJECT_ROOT/.cache/minio-image"
go build -mod=readonly -trimpath -ldflags='-s -w' -o "$PROJECT_ROOT/.cache/minio-image/minio" .
cp "$PROJECT_ROOT/fixtures/minio.Dockerfile" "$PROJECT_ROOT/.cache/minio-image/Dockerfile"
docker build --network=none -t datafusion-ducklake-minio:07c3a429 "$PROJECT_ROOT/.cache/minio-image"
