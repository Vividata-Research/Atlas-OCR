# Let CI pass the full base reference; keep a sensible local default.
ARG BASE_IMAGE=docker.io/vllm/vllm-openai:v0.9.1
FROM ${BASE_IMAGE}

# Minimal extras (no venv)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# --- Runtime env ---
# SageMaker mounts your model bundle at /opt/ml/model; keep subdir "DotsOCR" (no dots).
ENV MODEL_PATH=/opt/ml/model/DotsOCR \
    VLLM_PORT=8081 \
    PYTHONUNBUFFERED=1 \
    HEALTH_CHECK_TIMEOUT=30 \
    TENSOR_PARALLEL_SIZE=1 \
    GPU_MEMORY_UTILIZATION=0.95

# Ensure vLLM can import any custom code shipped inside MODEL_PATH when --trust-remote-code is used
ENV PYTHONPATH=/opt/ml/model:${PYTHONPATH:-}

WORKDIR /app

# Disable pip build isolation globally (so flash-attn sees torch)
ENV PIP_NO_BUILD_ISOLATION=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# --- Python deps for the server façade + pins you wanted ---
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN python3 -m pip install --no-cache-dir \
    --ignore-installed --no-deps --force-reinstall \
    "blinker==1.9.0"
RUN pip install --no-cache-dir -r /app/requirements.txt

# --- App files (your façade/launcher scripts) ---
COPY main.py /app/
COPY start_vllm.sh /app/
COPY serve /app/
RUN chmod +x /app/start_vllm.sh /app/serve

# --- Bring in the local dots.ocr repo (repo root with setup.py), then editable-install ---
# Place this repo locally at: container/third_party/dots.ocr (repo root, not just dots_ocr/)
COPY third_party/dots.ocr /app/third_party/dots.ocr
RUN pip install --no-cache-dir -e /app/third_party/dots.ocr

# Healthcheck hits your Flask /ping (proxying vLLM /health) — useful locally
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -sf http://localhost:8080/ping || exit 1

EXPOSE 8080 8081

# Make the container self-starting (SageMaker expects a server on :8080)
ENTRYPOINT ["/app/serve"]