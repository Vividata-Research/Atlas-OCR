#!/usr/bin/env bash
set -euo pipefail
echo "[start_vLLM] starting…"

MODEL_PATH="${MODEL_PATH:-/opt/ml/model/DotsOCR}"
VLLM_PORT="${VLLM_PORT:-8081}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.95}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

echo "[start_vLLM] MODEL_PATH=$MODEL_PATH"
echo "[start_vLLM] VLLM_PORT=$VLLM_PORT"

# If GPU not present, let vLLM start anyway (debug; slow)
if nvidia-smi &>/dev/null; then
  echo "[start_vLLM] GPU detected"
else
  echo "[start_vLLM] No GPU detected; CPU mode (slow)"
  GPU_MEMORY_UTILIZATION=0
fi

# Start vLLM; it loads the remote code inside weights (DotsOCR/) with --trust-remote-code
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
    break
  fi
  sleep 2
done
