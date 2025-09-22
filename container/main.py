#!/usr/bin/env python3
import argparse
from asgiref.wsgi import WsgiToAsgi
from flask import Flask, jsonify, request, Response
import urllib.parse
import requests
import time
import os
import base64
import tempfile
from dots_ocr.parser import process_document

app = Flask(__name__)

# ---- CLI flags (optional) ----
parser = argparse.ArgumentParser(description="DotsOCR vLLM API Server (SageMaker-managed)")
parser.add_argument(
    "--vllm-api",
    type=str,
    default="http://127.0.0.1:8081",
    help="Address of vLLM server (health & parser host/port)",
)
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


# ---- Helpers ----
def _suffix_for_bytes(b: bytes) -> str:
    """Guess a file suffix from magic bytes (fallback .pdf)."""
    try:
        if b.startswith(b"%PDF-"): return ".pdf"
        if b[:2] == b"\xFF\xD8":   return ".jpg"
        if b[:8] == b"\x89PNG\r\n\x1a\n": return ".png"
        if b[:4] in (b"II*\x00", b"MM\x00*"): return ".tif"
    except Exception:
        pass
    return ".pdf"

def _save_temp_file(raw: bytes) -> str:
    suffix = _suffix_for_bytes(raw)
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.write(raw)
    f.flush()
    f.close()
    return f.name

def _default_options():
    parsed = urllib.parse.urlparse(args.vllm_api)
    ip = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8081
    return {
        "ip": ip,
        "port": port,
        "model_name": "model",
        "prompt": "prompt_layout_all_en",
        "dpi": 120,
        "num_thread": 1,
        "temperature": 0.1,
        "top_p": 0.9,
        "max_completion_tokens": 4096,
    }


# ---- Inference ----
@app.post("/invocations")
def invocations():
    """
    Accepts either:
      1) JSON: {"file_data": "<base64>", "prompt": "...", ...}
         Content-Type: application/json
      2) Raw bytes (PDF/JPG/PNG/TIFF):
         Content-Type: application/octet-stream (or application/pdf, image/*)
    """
    ct = (request.headers.get("Content-Type") or "").lower().split(";")[0].strip()

    # Prepare options (allow JSON to override defaults)
    options = _default_options()

    # Mode 1: JSON with base64
    if ct == "application/json":
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"error": "Invalid JSON"}), 400
        if "file_data" not in body:
            return jsonify({"error": "Missing 'file_data' (base64 PDF or image)"}), 400
        try:
            raw = base64.b64decode(body["file_data"]) if isinstance(body["file_data"], str) else body["file_data"]
        except Exception:
            return jsonify({"error": "file_data must be base64 string"}), 400

        # Optional overrides from JSON
        if "prompt" in body: options["prompt"] = str(body.get("prompt"))
        if "dpi" in body: options["dpi"] = int(body.get("dpi"))
        if "num_threads" in body: options["num_thread"] = int(body.get("num_threads"))
        if "temperature" in body: options["temperature"] = float(body.get("temperature"))
        if "top_p" in body: options["top_p"] = float(body.get("top_p"))
        if "max_tokens" in body: options["max_completion_tokens"] = int(body.get("max_tokens"))

    # Mode 2: raw bytes (Batch Transform with splitType=None)
    else:
        raw = request.get_data(cache=False, as_text=False)
        if not raw:
            return jsonify({"error": "Empty request body"}), 400
        # You could also allow header-based overrides for BT, e.g.:
        #   X-DotsOCR-Prompt, X-DotsOCR-DPI, etc. (optional)
        # For now we stick to defaults.

    # Write to temp file and run the parser
    src_path = _save_temp_file(raw)
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
    finally:
        try:
            os.unlink(src_path)
        except Exception:
            pass


# ---- ASGI wrapper for uvicorn (serve script) ----
asgi_app = WsgiToAsgi(app)

if __name__ == "__main__":
    app.run(args.host, port=args.port, debug=False)
