# Match the project’s guidance (CUDA/Torch/vLLM already aligned)
FROM vllm/vllm-openai:v0.9.1

# Minimal extras + venv for clean Python deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates python3-venv \
 && rm -rf /var/lib/apt/lists/*

# --- Runtime env ---
# SageMaker will place your model bundle at /opt/ml/model; keep a subdir "DotsOCR" (no dots).
ENV MODEL_PATH=/opt/ml/model/DotsOCR \
    VLLM_PORT=8081 \
    PYTHONUNBUFFERED=1 \
    HEALTH_CHECK_TIMEOUT=30 \
    TENSOR_PARALLEL_SIZE=1 \
    GPU_MEMORY_UTILIZATION=0.95

# Ensure vLLM can import any custom code shipped inside MODEL_PATH when --trust-remote-code is used
ENV PYTHONPATH=/opt/ml/model:${PYTHONPATH}

WORKDIR /app

# --- Virtualenv ---
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# --- Python deps for the server facade + pins you requested ---
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# --- App files (your façade/launcher scripts) ---
COPY main.py /app/
COPY start_vllm.sh /app/
COPY serve /app/
RUN chmod +x /app/start_vllm.sh /app/serve

# Healthcheck hits your Flask /ping (proxying vLLM /health) — useful locally
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -sf http://localhost:8080/ping || exit 1

EXPOSE 8080 8081

# Make the container self-starting (SageMaker expects a server on :8080)
ENTRYPOINT ["/app/serve"]
