#!/usr/bin/env python3
import argparse
import base64
import os
import tempfile
import time
import urllib.parse

import requests
from asgiref.wsgi import WsgiToAsgi
from flask import Flask, Response, jsonify, request

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
        if b.startswith(b"%PDF-"):
            return ".pdf"
        if b[:2] == b"\xFF\xD8":
            return ".jpg"
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if b[:4] in (b"II*\x00", b"MM\x00*"):
            return ".tif"
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


def _env_default(name: str, default):
    v = os.getenv(name)
    if v is None:
        return default
    # Coerce to the right type if default is numeric
    try:
        if isinstance(default, int):
            return int(v)
        if isinstance(default, float):
            return float(v)
    except Exception:
        return default
    return v


def _default_options():
    parsed = urllib.parse.urlparse(args.vllm_api)
    ip = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8081
    return {
        "ip": ip,
        "port": port,
        "model_name": "model",
        "prompt": _env_default("DOTSOCR_PROMPT", "prompt_layout_all_en"),
        "dpi": _env_default("DOTSOCR_DPI", 120),
        "num_thread": _env_default("DOTSOCR_THREADS", 1),
        "temperature": _env_default("DOTSOCR_TEMPERATURE", 0.1),
        "top_p": _env_default("DOTSOCR_TOP_P", 0.9),
        "max_completion_tokens": _env_default("DOTSOCR_MAX_TOKENS", 4096),
    }


def _apply_overrides_from_json(options: dict, body: dict) -> None:
    # Optional overrides from JSON body
    if "prompt" in body:
        options["prompt"] = str(body.get("prompt"))
    if "dpi" in body:
        options["dpi"] = int(body.get("dpi"))
    if "num_threads" in body:
        options["num_thread"] = int(body.get("num_threads"))
    if "temperature" in body:
        options["temperature"] = float(body.get("temperature"))
    if "top_p" in body:
        options["top_p"] = float(body.get("top_p"))
    if "max_tokens" in body:
        options["max_completion_tokens"] = int(body.get("max_tokens"))
    # Leave room for optional parser flags if you expose them:
    # e.g., options["no_fitz_preprocess"] = bool(body.get("no_fitz_preprocess", False))


def _apply_overrides_from_headers(options: dict) -> None:
    """
    Allow safe header-based overrides (mostly useful for manual testing;
    SageMaker Batch Transform generally won't set these per object).
    """
    h = request.headers
    if "X-DotsOCR-Prompt" in h:
        options["prompt"] = h.get("X-DotsOCR-Prompt")
    if "X-DotsOCR-DPI" in h:
        try:
            options["dpi"] = int(h.get("X-DotsOCR-DPI"))
        except Exception:
            pass
    if "X-DotsOCR-Threads" in h:
        try:
            options["num_thread"] = int(h.get("X-DotsOCR-Threads"))
        except Exception:
            pass
    if "X-DotsOCR-Temperature" in h:
        try:
            options["temperature"] = float(h.get("X-DotsOCR-Temperature"))
        except Exception:
            pass
    if "X-DotsOCR-TopP" in h:
        try:
            options["top_p"] = float(h.get("X-DotsOCR-TopP"))
        except Exception:
            pass
    if "X-DotsOCR-MaxTokens" in h:
        try:
            options["max_completion_tokens"] = int(h.get("X-DotsOCR-MaxTokens"))
        except Exception:
            pass


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

    # Prepare options (allow JSON or headers to override defaults)
    options = _default_options()

    raw: bytes

    # Mode 1: JSON with base64
    if ct == "application/json":
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"error": "Invalid JSON"}), 400
        if "file_data" not in body:
            return jsonify({"error": "Missing 'file_data' (base64 PDF or image)"}), 400
        try:
            raw = (
                base64.b64decode(body["file_data"])
                if isinstance(body["file_data"], str)
                else body["file_data"]
            )
        except Exception:
            return jsonify({"error": "file_data must be base64 string"}), 400

        _apply_overrides_from_json(options, body)

    # Mode 2: raw bytes (Batch Transform with splitType=None, or manual curl)
    else:
        raw = request.get_data(cache=False, as_text=False)
        if not raw:
            return jsonify({"error": "Empty request body"}), 400
        # Optional: header overrides for manual testing
        _apply_overrides_from_headers(options)

    # Write to temp file and run the parser
    src_path = _save_temp_file(raw)
    try:
        result = process_document(src_path, options)
        return jsonify(
            {
                "object": "ocr.completion",
                "model": "DotsOCR",
                "created": int(time.time()),
                "result": result,
            }
        )
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
