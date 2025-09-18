#!/usr/bin/env python3
import argparse
from asgiref.wsgi import WsgiToAsgi
from flask import Flask, jsonify, request, Response
import urllib.parse
import requests
import time
import json
import os
import base64
import tempfile
from dots_ocr.parser import process_document

app = Flask(__name__)

# ---- CLI flags (optional) ----
parser = argparse.ArgumentParser(description="DotsOCR vLLM API Server (SageMaker-managed)")
parser.add_argument("--vllm-api", type=str, default="http://127.0.0.1:8081",
                    help="Address of vLLM server (health & parser host/port)")
parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
parser.add_argument("--port", type=int, default=8080, help="Bind port")
args, _ = parser.parse_known_args()

# ---- Health ----
VLLM_HEALTH_URL = f"{args.vllm_api.rstrip('/')}/health"
HEALTH_TIMEOUT_S = float(os.getenv("HEALTH_CHECK_TIMEOUT", "30"))

@app.get("/ping")
def ping() -> Response:
    try:
        r = requests.get(VLLM_HEALTH_URL, timeout=HEALTH_TIMEOUT_S)
        if r.status_code == 200:
            return Response(status=200)
    except Exception:
        pass
    return Response(status=503)

@app.get("/health")
def health() -> Response:
    try:
        r = requests.get(VLLM_HEALTH_URL, timeout=HEALTH_TIMEOUT_S)
        if r.status_code == 200:
            return jsonify({"status": "healthy", "vllm": "ready"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503
    return jsonify({"status": "unhealthy", "vllm": "not ready"}), 503

# ---- Inference ----
def _suffix_for_bytes(b: bytes) -> str:
    try:
        if b.startswith(b"%PDF-"): return ".pdf"
        if b[:2] == b"\xFF\xD8":   return ".jpg"
        if b[:8] == b"\x89PNG\r\n\x1a\n": return ".png"
        if b[:4] in (b"II*\x00", b"MM\x00*"): return ".tif"
    except Exception:
        pass
    return ".pdf"

@app.post("/invocations")
def invocations():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid JSON"}), 400
    if "file_data" not in body:
        return jsonify({"error": "Missing 'file_data' (base64 PDF or image)"}), 400

    # Decode and save to a temp file
    try:
        raw = base64.b64decode(body["file_data"]) if isinstance(body["file_data"], str) else body["file_data"]
    except Exception:
        return jsonify({"error": "file_data must be base64 string"}), 400

    suffix = _suffix_for_bytes(raw)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(raw)
        src_path = f.name

    # Parser config mirrors the CLI
    parsed = urllib.parse.urlparse(args.vllm_api)
    ip = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8081

    options = {
        "ip": ip,
        "port": port,
        "model_name": "model",
        "prompt": body.get("prompt", "prompt_layout_all_en"),
        "dpi": int(body.get("dpi", 120)),
        "num_thread": int(body.get("num_threads", 1)),
        "temperature": float(body.get("temperature", 0.1)),
        "top_p": float(body.get("top_p", 0.9)),
        "max_completion_tokens": int(body.get("max_tokens", 4096)),
        # add flags as needed: "no_fitz_preprocess", "min_pixels", "max_pixels", etc.
    }

    try:
        result = process_document(src_path, options)
        return jsonify({
            "object": "ocr.completion",
            "model": "DotsOCR",
            "created": int(time.time()),
            "result": result
        })
    except Exception as e:
        return jsonify({"error": f"OCR failed: {e}"}), 500

# ---- ASGI wrapper for uvicorn (serve script) ----
asgi_app = WsgiToAsgi(app)

if __name__ == "__main__":
    app.run(args.host, port=args.port, debug=False)
