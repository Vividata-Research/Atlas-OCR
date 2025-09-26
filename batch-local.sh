#!/usr/bin/env bash
set -euo pipefail

INPUT_DIR="${1:-input_data}"
OUTPUT_DIR="${2:-output_data}"
API_URL="${3:-http://localhost:8080}"
CONCURRENCY="${CONCURRENCY:-4}"          # simple parallelism knob
KEEP_INTERMEDIATE="${DOTSOCR_KEEP_INTERMEDIATE:-0}"

if ! curl -fsS "${API_URL}/health" >/dev/null 2>&1; then
  echo "ERROR: API not healthy at ${API_URL}. Start container first." >&2
  exit 1
fi

shopt -s globstar nullglob
files=()
for f in "${INPUT_DIR}"/**/*.{pdf,PDF,png,jpg,jpeg,tif,tiff}; do
  files+=("$f")
done

if [ ${#files[@]} -eq 0 ]; then
  echo "No input files found under ${INPUT_DIR}" >&2
  exit 0
fi

process_file() {
  local f="$1"
  local rel="${f#${INPUT_DIR}/}"            # e.g., company=mass/sample.pdf
  local stem="${rel%.*}"                    # e.g., company=mass/sample
  local out_dir="${OUTPUT_DIR}/$(dirname "$rel")"
  local out_json="${OUTPUT_DIR}/${stem}.json"

  mkdir -p "$out_dir"
  echo "[batch] -> $out_json"

  # Send raw bytes to the API
  if ! curl -sS -X POST "${API_URL}/invocations" \
        -H "Content-Type: application/octet-stream" \
        --data-binary @"$f" > "${out_json}.tmp"; then
    echo "{\"error\":\"request failed\"}" > "${out_json}.tmp"
  fi
  mv "${out_json}.tmp" "$out_json"

  # Read consolidated paths returned by the server
  local cons_md cons_dir
  cons_md="$(jq -r '.consolidated_md // empty' "$out_json")"
  cons_dir="$(jq -r '.consolidated_dir // empty' "$out_json")"

  if [[ -z "$cons_md" || -z "$cons_dir" ]]; then
    echo "WARN: No consolidated output visible for: $f"
    return 0
  fi

  # Translate container paths (/app/output/...) to host paths (OUTPUT_DIR/...)
  local host_cons_md host_cons_dir
  host_cons_md="$(echo "$cons_md" | sed "s|^/app/output/|${OUTPUT_DIR}/|")"
  host_cons_dir="$(echo "$cons_dir" | sed "s|^/app/output/|${OUTPUT_DIR}/|")"

  # Where we want the final outputs to live
  local final_md="${OUTPUT_DIR}/${stem}.md"
  local final_assets_dir="${OUTPUT_DIR}/$(dirname "$rel")/$(basename "$stem")_assets"

  mkdir -p "$(dirname "$final_md")" "$final_assets_dir"

  # Copy consolidated markdown
  if [[ -f "$host_cons_md" ]]; then
    cp -f "$host_cons_md" "$final_md"
  else
    echo "WARN: consolidated markdown not found on host: $host_cons_md"
  fi

  # Copy consolidated assets (if present)
  if [[ -d "${host_cons_dir}/assets" ]]; then
    # copy assets content into final_assets_dir
    rsync -a --delete "${host_cons_dir}/assets/" "${final_assets_dir}/"
  fi

  # Cleanup intermediates unless asked to keep them
  if [[ "$KEEP_INTERMEDIATE" != "1" ]]; then
    # consolidated dir looks like: ${OUTPUT_DIR}/output_consolidated/tmpXXXXXX
    local stem_dir
    stem_dir="$(basename "$host_cons_dir")"                        # tmpXXXXXX
    local host_tmp="${OUTPUT_DIR}/${stem_dir}"                     # ${OUTPUT_DIR}/tmpXXXXXX
    local host_cons_parent="${OUTPUT_DIR}/output_consolidated/${stem_dir}"

    # Remove only the specific request’s temp dirs
    if [[ -d "$host_tmp" ]]; then rm -rf -- "$host_tmp"; fi
    if [[ -d "$host_cons_parent" ]]; then rm -rf -- "$host_cons_parent"; fi
  fi
}

export -f process_file
export INPUT_DIR OUTPUT_DIR API_URL DOTSOCR_KEEP_INTERMEDIATE

# Parallel loop (set CONCURRENCY=1 to serialize)
printf '%s\0' "${files[@]}" | xargs -0 -n1 -P "$CONCURRENCY" bash -c 'process_file "$@"' _

echo "[batch] done. Final markdowns under ${OUTPUT_DIR}"
