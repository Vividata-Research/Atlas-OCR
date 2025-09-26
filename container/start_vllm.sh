#!/usr/bin/env bash
set -euo pipefail
echo "[start_vLLM] starting…"

MODEL_PATH="${MODEL_PATH:-/opt/ml/model/DotsOCR}"
VLLM_PORT="${VLLM_PORT:-8081}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.95}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

echo "[start_vLLM] MODEL_PATH=$MODEL_PATH"
echo "[start_vLLM] VLLM_PORT=$VLLM_PORT"

# GPU check
if nvidia-smi &>/dev/null; then
  echo "[start_vLLM] GPU detected"
else
  echo "[start_vLLM] No GPU detected; CPU mode (slow)"
  GPU_MEMORY_UTILIZATION=0
fi

# Optional: port check if 'ss' exists
if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :$VLLM_PORT )" | grep -q LISTEN; then
    echo "[start_vLLM] ERROR: Port $VLLM_PORT already in use" >&2
    exit 1
  fi
else
  echo "[start_vLLM] 'ss' not found; skipping pre-flight port check"
fi

# Make both the model dir and its parent importable
# - Parent on sys.path allows `import DotsOCR.*`
# - Model dir on sys.path allows direct module access if needed
export PYTHONPATH="$(dirname "$MODEL_PATH"):${MODEL_PATH}:${PYTHONPATH:-}"

# Patch the vLLM console script once so all processes preload DotsOCR integration
VLLM_BIN="$(command -v vllm || true)"
if [ -z "${VLLM_BIN}" ] || [ ! -f "${VLLM_BIN}" ]; then
  echo "[start_vLLM] ERROR: 'vllm' executable not found in PATH." >&2
  exit 1
fi

if ! grep -q "DOTSOCR_PRELOAD_PKG" "${VLLM_BIN}"; then
  echo "[start_vLLM] Patching ${VLLM_BIN} for DotsOCR preload…"
  cp "${VLLM_BIN}" "${VLLM_BIN}.orig"
  cat > "${VLLM_BIN}" <<'PY'
#!/usr/bin/env python3
# DOTSOCR_PRELOAD_PKG: wrapper to preload DotsOCR vLLM integration in parent & children
import os, sys, types, importlib

mp = os.environ.get("MODEL_PATH", "/models/DotsOCR")
# Ensure parent of model path is on sys.path, so "import DotsOCR.*" works
parent = os.path.dirname(mp)
if parent and parent not in sys.path:
    sys.path.insert(0, parent)
# Also add model path itself (harmless)
if mp not in sys.path:
    sys.path.insert(0, mp)

# Synthesize a package "DotsOCR" pointing at the weights dir if missing
if "DotsOCR" not in sys.modules:
    pkg = types.ModuleType("DotsOCR")
    pkg.__path__ = [mp]
    sys.modules["DotsOCR"] = pkg

try:
    # Import via package name so relative imports inside the file resolve
    importlib.import_module("DotsOCR.modeling_dots_ocr_vllm")
except Exception as e:
    print(f"[dotsocr] preload warn: {e}", file=sys.stderr)

from vllm.entrypoints.cli.main import main
if __name__ == "__main__":
    sys.exit(main())
PY
  chmod +x "${VLLM_BIN}"
else
  echo "[start_vLLM] vLLM console script already patched; skipping."
fi

# Launch vLLM
vllm serve "$MODEL_PATH" \
  --host 0.0.0.0 \
  --port "$VLLM_PORT" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --trust-remote-code \
  --served-model-name model \
  --chat-template-content-format string \
  --max-model-len 131072 \
  --disable-log-requests &

# Wait for vLLM health
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${VLLM_PORT}/health" >/dev/null 2>&1; then
    echo "[start_vLLM] vLLM ready"
    exit 0
  fi
  sleep 2
done

echo "[start_vLLM] ERROR: vLLM did not become healthy on port ${VLLM_PORT}" >&2
exit 1
