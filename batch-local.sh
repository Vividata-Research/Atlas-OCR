#!/usr/bin/env bash
set -euo pipefail

INPUT_DIR="${1:-input_data}"
OUTPUT_DIR="${2:-output_data}"
API_URL="${3:-http://localhost:8080}"
CONCURRENCY="${CONCURRENCY:-4}"   # simple parallelism knob

if ! curl -fsS "${API_URL}/health" >/dev/null 2>&1; then
  echo "ERROR: API not healthy at ${API_URL}. Start container first (./dev.sh run)." >&2
  exit 1
fi

shopt -s globstar nullglob
files=()
# Add more extensions if you like
for f in "${INPUT_DIR}"/**/*.{pdf,PDF,png,jpg,jpeg,tif,tiff}; do
  files+=("$f")
done

if [ ${#files[@]} -eq 0 ]; then
  echo "No input files found under ${INPUT_DIR}" >&2
  exit 0
fi

# worker function
process_file() {
  local f="$1"
  local rel="${f#${INPUT_DIR}/}"         # e.g., company=mass/sample.pdf
  local stem="${rel%.*}"                  # e.g., company=mass/sample
  local out_dir="${OUTPUT_DIR}/$(dirname "$rel")"
  local out_file="${OUTPUT_DIR}/${stem}.json"

  mkdir -p "$out_dir"
  echo "[batch] -> $out_file"

  # Send raw bytes (your server auto-detects content type)
  if ! curl -sS -X POST "${API_URL}/invocations" \
        -H "Content-Type: application/octet-stream" \
        --data-binary @"$f" > "${out_file}.tmp"; then
    echo "{\"error\":\"request failed\"}" > "${out_file}.tmp"
  fi

  mv "${out_file}.tmp" "$out_file"
}

export -f process_file
export INPUT_DIR OUTPUT_DIR API_URL

# Simple parallel loop (requires xargs). Set CONCURRENCY=1 to disable.
printf '%s\0' "${files[@]}" | xargs -0 -n1 -P "$CONCURRENCY" bash -c 'process_file "$@"' _

echo "[batch] done. Outputs under ${OUTPUT_DIR}"
