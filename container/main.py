#!/usr/bin/env python3
import argparse
import base64
import glob
import os
import shutil
import tempfile
import time
import urllib.parse

import requests
from asgiref.wsgi import WsgiToAsgi
from flask import Flask, Response, jsonify, request

# DotsOCR parser from the repo
from dots_ocr.parser import DotsOCRParser

app = Flask(__name__)

# ---------------- CLI flags (minimal; works local + SageMaker) ----------------
parser = argparse.ArgumentParser(description="DotsOCR vLLM API Server")
parser.add_argument(
    "--vllm-api",
    type=str,
    default="http://127.0.0.1:8081",
    help="Address of vLLM server (health & parser host/port)",
)
parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
parser.add_argument("--port", type=int, default=8080, help="Bind port")
args, _ = parser.parse_known_args()

# ---------------- Health ----------------
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


# ---------------- Helpers ----------------
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
        # Optional future flags:
        # "fitz_preprocess": not _env_default("DOTSOCR_NO_FITZ_PREPROCESS", False),
        # "min_pixels": _env_default("DOTSOCR_MIN_PIXELS", None),
        # "max_pixels": _env_default("DOTSOCR_MAX_PIXELS", None),
    }


def _apply_overrides_from_json(options: dict, body: dict) -> None:
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
    # Optional:
    # options["fitz_preprocess"] = not bool(body.get("no_fitz_preprocess", False))


def _apply_overrides_from_headers(options: dict) -> None:
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


def _build_parser_from_options(options: dict) -> DotsOCRParser:
    return DotsOCRParser(
        ip=options["ip"],
        port=options["port"],
        model_name=options["model_name"],
        temperature=options["temperature"],
        top_p=options["top_p"],
        max_completion_tokens=options["max_completion_tokens"],
        num_thread=options["num_thread"],
        dpi=options["dpi"],
        output_dir="/app/output",  # inside container; volume-mount this on host
        min_pixels=options.get("min_pixels"),
        max_pixels=options.get("max_pixels"),
        use_hf=False,
    )


def _cleanup_jsonl_and_intermediates(output_root: str):
    """
    Remove any *.jsonl files and known intermediate folders under /app/output.
    Safe no-op if paths don't exist.
    """
    try:
        # Remove jsonl anywhere under output root (including output_consolidated)
        for p in glob.glob(os.path.join(output_root, "**", "*.jsonl"), recursive=True):
            try:
                os.remove(p)
            except Exception:
                pass
        # Also remove any top-level temporary parse folders that start with 'tmp'
        for p in glob.glob(os.path.join(output_root, "tmp*")):
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
        # Remove empty output_consolidated if it exists
        oc_dir = os.path.join(output_root, "output_consolidated")
        try:
            # If it's empty after moves, remove it
            if os.path.isdir(oc_dir) and not os.listdir(oc_dir):
                os.rmdir(oc_dir)
        except Exception:
            pass
    except Exception:
        pass


# ---------------- Inference ----------------
@app.post("/invocations")
def invocations():
    """
    Accepts either:
      1) JSON: {"file_data": "<base64>", "prompt": "...", ...}
         Content-Type: application/json
      2) Raw bytes (PDF/JPG/PNG/TIFF):
         Content-Type: application/octet-stream (or application/pdf, image/*)

    Returns JSON with:
      - result: per-page outputs (from parser)
      - final_dir: /app/output/final/<doc_id>
      - final_md:  /app/output/final/<doc_id>/document.md
    """
    ct = (request.headers.get("Content-Type") or "").lower().split(";")[0].strip()
    options = _default_options()

    # ---- Read body (JSON base64 or raw bytes)
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
    else:
        raw = request.get_data(cache=False, as_text=False)
        if not raw:
            return jsonify({"error": "Empty request body."}), 400
        _apply_overrides_from_headers(options)

    # ---- Persist input to a temp file
    src_path = _save_temp_file(raw)
    doc_id = os.path.splitext(os.path.basename(src_path))[0]  # e.g., tmpabc123

    final_dir = None
    final_md = None

    try:
        # ---- Run DotsOCR
        parser_obj = _build_parser_from_options(options)
        result = parser_obj.parse_file(
            src_path,
            prompt_mode=options.get("prompt", "prompt_layout_all_en"),
            bbox=options.get("bbox"),
            fitz_preprocess=options.get("fitz_preprocess", False),
        )

        # ---- Always run postprocess to consolidate Markdown
        first = (result or [])[0] if isinstance(result, list) and result else None
        md_path = first.get("md_content_path") if isinstance(first, dict) else None

        output_root = "/app/output"
        os.makedirs(output_root, exist_ok=True)

        if md_path:
            # 1) Consolidate pages into ./output_consolidated/<doc_id>/*
            from postprocess_dotsocr import process_dotsocr_output

            input_dir = os.path.dirname(md_path)  # e.g., /app/output/tmpXYZ
            old_cwd = os.getcwd()
            try:
                os.chdir(output_root)
                rel_input_dir = os.path.relpath(input_dir, output_root)
                process_dotsocr_output(rel_input_dir)
                consolidated_dir = os.path.join(
                    output_root, "output_consolidated", os.path.basename(rel_input_dir)
                )
                consolidated_md = os.path.join(
                    consolidated_dir,
                    f"{os.path.basename(rel_input_dir)}_consolidated.md",
                )
            finally:
                os.chdir(old_cwd)

            # 2) Move final artifacts to stable place and delete intermediates
            final_dir = os.path.join(output_root, "final", doc_id)
            final_assets = os.path.join(final_dir, "assets")
            assets_src = os.path.join(consolidated_dir, "assets")

            shutil.rmtree(final_dir, ignore_errors=True)
            os.makedirs(final_assets, exist_ok=True)

            if os.path.isfile(consolidated_md):
                final_md = os.path.join(final_dir, "document.md")
                shutil.copy2(consolidated_md, final_md)

            if os.path.isdir(assets_src):
                for name in os.listdir(assets_src):
                    src_fp = os.path.join(assets_src, name)
                    if os.path.isfile(src_fp):
                        shutil.copy2(src_fp, os.path.join(final_assets, name))

            # Remove the consolidated working folder (intermediate)
            shutil.rmtree(consolidated_dir, ignore_errors=True)

        # 3) Remove any residual *.jsonl or tmp dirs anywhere under /app/output
        _cleanup_jsonl_and_intermediates(output_root)

        return jsonify(
            {
                "object": "ocr.completion",
                "model": "DotsOCR",
                "created": int(time.time()),
                "result": result,
                "final_dir": final_dir,
                "final_md": final_md,
            }
        )

    except Exception as e:
        # Best-effort cleanup of residual jsonl/intermediates even on error
        try:
            _cleanup_jsonl_and_intermediates("/app/output")
        except Exception:
            pass
        return jsonify({"error": f"OCR failed: {e}"}), 500

    finally:
        try:
            os.unlink(src_path)
        except Exception:
            pass


# ---------------- ASGI wrapper ----------------
asgi_app = WsgiToAsgi(app)

if __name__ == "__main__":
    app.run(args.host, port=args.port, debug=False)
