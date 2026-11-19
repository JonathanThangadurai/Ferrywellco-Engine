#!/usr/bin/env bash
# Run the whole chain on this computer, without Docker or a server:
# fetch the newest data for Amsterdam and Chennai, ingest, quality check, precompute, then the API on
# http://localhost:8000 (Swagger UI at /docs).
#
#   scripts/run_local.sh              everything, then serve (Ctrl-C stops the API)
#   scripts/run_local.sh --serve-only skip fetching: precompute if needed, then serve
#
# Data, keys and the test key live in ~/gotime-data-local (override with GOTIME_LOCAL_DIR).
# Source keys come from the environment or ~/.config/gotime/keys.env, as everywhere else.
# ERA5 is left out: its CDS queue can take hours, and the API does not use it.
set -euo pipefail

cd "$(dirname "$0")/.."
LOCAL="${GOTIME_LOCAL_DIR:-$HOME/gotime-data-local}"
mkdir -p "$LOCAL/data"
chmod 700 "$LOCAL"
export GOTIME_STORAGE_ROOT="$LOCAL/data"
export GOTIME_API_KEYS_FILE="$LOCAL/api_keys.json"
export GOTIME_ENV=local
KEY_FILE="$LOCAL/sandbox_key.txt"

step() { printf '\n== %s\n' "$*"; }

if [ "${1:-}" != "--serve-only" ]; then
  # Forecasts first (the API needs them), then observations (verification only; a failure is not fatal).
  for src in ecmwf cams_eu cams_global eea knmi openaq; do
    step "fetch + ingest $src"
    if uv run gotime-data fetch "$src" && uv run gotime-data ingest "$src"; then
      :
    else
      echo "WARNING: $src failed; carrying on (see the messages above)"
    fi
  done

  for src in ecmwf_ifs cams_eu cams_global; do
    step "quality check $src"
    uv run gotime-ops quality "$src" || echo "WARNING: $src quality check did not pass; the API shows it as degraded"
  done
fi

step "precompute"
uv run gotime-api precompute

if [ ! -s "$KEY_FILE" ]; then
  step "create a local sandbox key"
  uv run gotime-api keys create --partner local-test --mode sandbox >/dev/null
  new=$(ls -t "$LOCAL"/new_key_*.txt | head -1)
  mv "$new" "$KEY_FILE"
  chmod 600 "$KEY_FILE"
fi
echo "Sandbox key (readable by you only): $KEY_FILE"

step "API on http://localhost:8000  (docs: http://localhost:8000/docs; Ctrl-C to stop)"
exec uv run gotime-api serve --host 127.0.0.1 --port 8000
