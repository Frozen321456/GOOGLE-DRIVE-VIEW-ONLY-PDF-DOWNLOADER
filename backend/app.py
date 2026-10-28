"""GDrive PDF Fetcher — Flask backend (Render free tier, Docker).

Endpoints:
  GET  /api/health       -> keep-alive / warm-up probe
  POST /api/download     -> { url } | { fileId } -> application/pdf blob
                            (sync path; fine for small docs)
  POST /api/jobs         -> { url } | { fileId } -> 202 { jobId }
                            async path for large docs; render runs in a
                            background thread, status goes to RTDB
  GET  /api/jobs/<id>    -> { status, fileName?, size?, error? }
  GET  /api/jobs/<id>/result -> application/pdf when ready (409 before)

Status model (RTDB /pdfs/{fileId} + in-memory jobs):
  queued -> processing -> completed | failed
Job results are held in memory only (Render free has no disk); a finished
job stays retrievable for RESULT_TTL seconds, then the payload is dropped.
"""
import os
import threading
import time
import uuid

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

# Optional rate limiting: 10 req/min per IP on /api/download + /api/jobs.
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


# ---------------- async job registry (in-memory) ----------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
RESULT_TTL = 15 * 60  # keep finished job payloads for 15 minutes
MAX_CONCURRENT_RENDERS = 1  # free tier: one Chromium at a time
_RENDER_SEM = threading.Semaphore(MAX_CONCURRENT_RENDERS)


def _extract_file_id(payload: dict) -> str | None:
    """Accept {url} (any Drive URL form) or {fileId} (raw ID)."""
    from downloader import extract_file_id

    if not payload:
        return None
    raw = payload.get("url") or payload.get("fileId") or ""
    return extract_file_id(str(raw))


def _run_job(job_id: str, file_id: str) -> None:
    """Background worker: build the PDF, store result, update RTDB.

    build_pdf's progress callback streams real capture state
    (scrollPct / pagesDone / pagesTotal) into the job record, so the
    frontend can show actual progress instead of a fake creeping bar.
    """
    with _RENDER_SEM:
        job = JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "processing"
        job["startedAt"] = time.time()
        set_status(file_id, "processing", jobId=job_id)

        def on_progress(info: dict):
            job.update(
                phase=info.get("phase"),
                scrollPct=info.get("scrollPct"),
                pagesDone=info.get("pagesDone"),
                pagesTotal=info.get("pagesTotal"),
                progressAt=time.time(),
            )
            # best-effort RTDB mirror for cross-tab visibility
            set_status(
                file_id,
                "processing",
                jobId=job_id,
                phase=info.get("phase"),
                pagesDone=info.get("pagesDone"),
                pagesTotal=info.get("pagesTotal"),
            )

        try:
            pdf_bytes, file_name = build_pdf(file_id, progress=on_progress)
        except DownloadError as exc:
            job.update(status="failed", error=str(exc), code=exc.code,
                       finishedAt=time.time())
            set_status(file_id, "failed", error=str(exc), jobId=job_id)
            return
        except Exception as exc:  # unexpected
            job.update(status="failed", error=f"Unexpected error: {exc}",
                       code="RENDER_FAILED", finishedAt=time.time())
            set_status(file_id, "failed", error=f"Unexpected error: {exc}",
                       jobId=job_id)
            return

        job.update(
            status="completed",
            fileName=file_name,
            size=len(pdf_bytes),
            result=pdf_bytes,
            finishedAt=time.time(),
        )
        set_status(
            file_id,
            "completed",
            fileName=file_name,
            size=len(pdf_bytes),
            jobId=job_id,
            completedAt=int(time.time() * 1000),
        )

        # TTL cleanup: drop payload after RESULT_TTL
        def _gc():
            time.sleep(RESULT_TTL)
            with JOBS_LOCK:
                j = JOBS.get(job_id)
                if j and j.get("status") in ("completed", "failed"):
                    j.pop("result", None)
        threading.Thread(target=_gc, daemon=True).start()

        # payload consumed → free it immediately once downloaded (jobs/<id>/result
        # sets job["_consumed"]; sweep clears it on next request)
        with JOBS_LOCK:
            _dead = [
                jid for jid, j in JOBS.items()
                if j.get("status") in ("completed", "failed")
                and (j.get("_consumed") or time.time() - j.get("finishedAt", 0) > RESULT_TTL)
                and time.time() - j.get("finishedAt", 0) > 60
            ]
            for jid in _dead:
                JOBS.pop(jid, None)


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


@app.post("/api/jobs")
@rate_limited
def create_job():
    payload = request.get_json(silent=True) or {}
    file_id = _extract_file_id(payload)

    if not file_id:
        return (
            jsonify({"error": "Could not parse a Drive file ID from the input", "code": "INVALID_URL"}),
            400,
        )

    # Reuse an identical in-flight/completed job instead of re-rendering
    with JOBS_LOCK:
        now = time.time()
        for jid, j in JOBS.items():
            if j.get("fileId") != file_id:
                continue
            if j.get("status") in ("queued", "processing"):
                return jsonify({"jobId": jid, "status": j["status"], "reused": True}), 202
            if j.get("status") == "completed" and now - j.get("finishedAt", 0) <= RESULT_TTL:
                return jsonify({"jobId": jid, "status": "completed", "reused": True}), 202

        job_id = uuid.uuid4().hex[:16]
        JOBS[job_id] = {
            "jobId": job_id,
            "fileId": file_id,
            "status": "queued",
            "createdAt": now,
        }

    set_status(file_id, "queued", requestedAt=int(time.time() * 1000), jobId=job_id)
    threading.Thread(target=_run_job, args=(job_id, file_id), daemon=True).start()

    return jsonify({"jobId": job_id, "status": "queued"}), 202


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job", "code": "NOT_FOUND"}), 404

    body = {
        "jobId": job_id,
        "fileId": job.get("fileId"),
        "status": job.get("status"),
    }
    if job.get("status") == "processing":
        body.update(
            phase=job.get("phase"),
            scrollPct=job.get("scrollPct"),
            pagesDone=job.get("pagesDone"),
            pagesTotal=job.get("pagesTotal"),
        )
    elif job.get("status") == "completed":
        body.update(fileName=job.get("fileName"), size=job.get("size"))
    elif job.get("status") == "failed":
        body.update(error=job.get("error"), code=job.get("code"))
    return jsonify(body), 200


@app.get("/api/jobs/<job_id>/result")
def job_result(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job", "code": "NOT_FOUND"}), 404
    if job.get("status") == "failed":
        return jsonify({"error": job.get("error", "render failed"),
                        "code": job.get("code", "RENDER_FAILED")}), 500
    if job.get("status") != "completed" or not job.get("result"):
        return jsonify({"error": "Result not ready", "code": "NOT_READY"}), 409

    pdf_bytes = job["result"]
    job["_consumed"] = True
    resp = Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{job.get("fileName", job_id + ".pdf")}"',
            "Content-Length": str(len(pdf_bytes)),
            "Cache-Control": "no-store",
        },
    )

    @resp.call_on_close
    def _free_payload():
        # bytes handed to the socket → drop the in-RAM copy right away
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j is not None:
                j.pop("result", None)

    return resp


# ---- init Firebase once at import (Admin SDK bypasses RTDB rules) ----
init_firebase()

if __name__ == "__main__":  # local dev only; gunicorn in production
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
