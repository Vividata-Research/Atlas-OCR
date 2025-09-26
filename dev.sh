#!/usr/bin/env bash
set -euo pipefail

IMAGE="dotsocr:dev"
BASE_IMAGE="${BASE_IMAGE:-docker.io/vllm/vllm-openai:v0.9.1}"
WEIGHTS_DIR="${WEIGHTS_DIR:-./weights/DotsOCR}"

cmd="${1:-run}"

build() {
  docker build -t "$IMAGE" \
    --build-arg BASE_IMAGE="$BASE_IMAGE" \
    -f container/Dockerfile container
}

run() {
  if [ ! -d "$WEIGHTS_DIR" ]; then
    echo "ERROR: WEIGHTS_DIR not found: $WEIGHTS_DIR" >&2; exit 1
  fi
  docker run --rm --name dotsocr \
    $(command -v nvidia-smi >/dev/null && echo --gpus all) \
    -p 8080:8080 -p 8081:8081 \
    -v "$(realpath "$WEIGHTS_DIR")":/models/DotsOCR:ro \
    -e MODEL_PATH=/models/DotsOCR \
    -e PYTHONPATH=/models/DotsOCR:${PYTHONPATH:-} \
    -e DOTSOCR_PROMPT="${DOTSOCR_PROMPT:-prompt_layout_all_en}" \
    -e DOTSOCR_DPI="${DOTSOCR_DPI:-120}" \
    -e DOTSOCR_THREADS="${DOTSOCR_THREADS:-1}" \
    "$IMAGE"
}

stop() {
  docker rm -f dotsocr >/dev/null 2>&1 || true
}

health() {
  curl -fsS http://localhost:8080/health | jq .
}

test_file() {
  local f="${1:?Usage: $0 test_file path/to/doc}"
  curl -sS -X POST http://localhost:8080/invocations \
    -H "Content-Type: application/octet-stream" \
    --data-binary @"$f" | jq .
}

case "$cmd" in
  build) build ;;
  run)   run ;;
  stop)  stop ;;
  health) health ;;
  test_file) shift; test_file "$@" ;;
  *) echo "Usage: $0 {build|run|stop|health|test_file <doc>}" >&2; exit 1 ;;
esac
