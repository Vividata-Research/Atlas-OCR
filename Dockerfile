FROM vllm/vllm-openai:v0.9.1

ENV PYTHONUNBUFFERED=1
ENV MODEL_PATH=/opt/ml/model/DotsOCR \
    VLLM_PORT=8081 \
    HEALTH_CHECK_TIMEOUT=30 \
    GPU_MEMORY_UTILIZATION=0.95 \
    TENSOR_PARALLEL_SIZE=1

WORKDIR /app

# OS deps: curl for healthcheck; poppler-utils supports pdf rasterization in the parser
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl poppler-utils \
 && rm -rf /var/lib/apt/lists/*

# Python deps used by the parser + tiny API (vLLM/torch come from base image)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# --- Bring in dots_ocr code (no weights) ---
# You downloaded it to vendor/dots_ocr locally (see steps above)
COPY vendor/dots_ocr /app/dots_ocr

# App entry
COPY main.py /app/
COPY serve /app/
COPY start_vLLM.sh /app/
RUN chmod +x /app/serve /app/start_vLLM.sh

EXPOSE 8080 8081

ENTRYPOINT ["/bin/bash","-lc"]
CMD ["/app/serve"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -fsS http://localhost:8080/ping || exit 1
