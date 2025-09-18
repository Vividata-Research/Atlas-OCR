# Lean on the official vLLM image so CUDA/Torch/vLLM are pre-aligned
FROM vllm/vllm-openai:v0.7.3

# Minimal extras (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Runtime env
ENV VLLM_PORT=8081 \
    PYTHONUNBUFFERED=1 \
    MODEL_PATH=/opt/ml/model/DotsOCR \
    HEALTH_CHECK_TIMEOUT=30 \
    TENSOR_PARALLEL_SIZE=1 \
    GPU_MEMORY_UTILIZATION=0.95

WORKDIR /app

# Python deps (note: vLLM is already in the base image; don't pin it here)
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# App files
COPY main.py /app/
# 🔧 use lowercase name; keep your file as start_vllm.sh in the repo
COPY start_vllm.sh /app/
COPY serve /app/
RUN chmod +x /app/start_vllm.sh /app/serve

# ✅ Bring YOUR local dots_ocr code into the image for the parser
#    (this is the repo you downloaded, WITHOUT weights)
COPY third_party/dots_ocr /app/third_party/dots_ocr

# Ensure Python can import "dots_ocr" from /app/third_party
ENV PYTHONPATH=/app/third_party:/app:$PYTHONPATH

# Healthcheck hits Flask /ping that proxies vLLM’s /health
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -sf http://localhost:8080/ping || exit 1

EXPOSE 8080 8081

# We'll use your `serve` script as the container entrypoint
ENTRYPOINT ["/bin/bash"]
