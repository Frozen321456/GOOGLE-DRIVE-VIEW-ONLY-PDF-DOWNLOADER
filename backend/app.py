"""GDrive PDF Fetcher — Flask backend (Render free tier, Docker).

Endpoints:
  GET  /api/health    -> keep-alive / warm-up probe
  POST /api/download  -> { url } | { fileId } -> application/pdf blob

Never writes PDFs to disk (Render free has no persistent disk).
Status writes go to Firebase RTDB (metadata only).
"""
import os
import time

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from downloader import build_pdf, DownloadError
from firebase_client import init_firebase, set_status

load_dotenv()

app = Flask(__name__)

_origins = os.environ.get("ALLOWED_ORIGINS", "*")
CORS(
    app,
    resources={r"/api/*": {"origins": _origins}},
    supports_private_network=True,
)

# Optional rate limiting: 10 req/min per IP on /api/download.
# Falls back silently when flask-limiter is not installed.
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    _limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    )

    def rate_limited(fn):
        return _limiter.limit("10 per minute")(fn)

except ImportError:  # pragma: no cover - slim dep path

    def rate_limited(fn):
        return fn


def _extract_file_id(payload: dict) -> str | None:
    """Accept {url} (any Drive URL form) or {fileId} (raw ID)."""
    from downloader import extract_file_id

    if not payload:
        return None
    raw = payload.get("url") or payload.get("fileId") or ""
    return extract_file_id(str(raw))


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "timestamp": int(time.time() * 1000)})


@app.post("/api/download")
@rate_limited
def download():
    payload = request.get_json(silent=True) or {}
    file_id = _extract_file_id(payload)

    if not file_id:
        return (
            jsonify({"error": "Could not parse a Drive file ID from the input", "code": "INVALID_URL"}),
            400,
        )

    set_status(file_id, "queued", requestedAt=int(time.time() * 1000))
    set_status(file_id, "processing")

    try:
        pdf_bytes, file_name = build_pdf(file_id)
    except DownloadError as exc:
        set_status(file_id, "failed", error=str(exc))
        return jsonify({"error": str(exc), "code": exc.code}), exc.http_status
    except Exception as exc:  # unexpected — still report cleanly
        set_status(file_id, "failed", error=f"Unexpected error: {exc}")
        return (
            jsonify({"error": "Failed to render PDF, try again", "code": "RENDER_FAILED"}),
            500,
        )

    set_status(
        file_id,
        "completed",
        fileName=file_name,
        size=len(pdf_bytes),
        completedAt=int(time.time() * 1000),
    )

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Content-Length": str(len(pdf_bytes)),
            "Cache-Control": "no-store",
        },
    )


# ---- init Firebase once at import (Admin SDK bypasses RTDB rules) ----
init_firebase()

if __name__ == "__main__":  # local dev only; gunicorn in production
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
