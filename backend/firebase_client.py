"""Firebase RTDB client — Admin SDK, backend-only writes.

Credentials come from FIREBASE_CREDENTIALS_JSON (base64 of the service
account JSON). If unset/invalid, the app still runs — status sync is
best-effort and never blocks a download.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

_app = None
_app_lock = threading.Lock()


def init_firebase() -> bool:
    """Initialize the Admin SDK once. Returns True when ready."""
    global _app
    if _app is not None:
        return True

    with _app_lock:
        if _app is not None:
            return True
        try:
            import firebase_admin
            from firebase_admin import credentials

            raw = os.environ.get("FIREBASE_CREDENTIALS_JSON", "").strip()
            if not raw:
                log.warning("FIREBASE_CREDENTIALS_JSON not set — RTDB status sync disabled")
                return False

            # Accept BOTH formats: raw service-account JSON (what the Firebase
            # console gives you) or its base64 encoding (env-var friendly).
            if raw.startswith("{"):
                service_account = json.loads(raw)
            else:
                service_account = json.loads(base64.b64decode(raw).decode("utf-8"))
            cred = credentials.Certificate(service_account)
            _app = firebase_admin.initialize_app(cred, {
                "databaseURL": os.environ.get(
                    "FIREBASE_DATABASE_URL",
                    service_account.get("databaseURL", ""),
                )
            })
            log.info("Firebase Admin SDK initialized")
            return True
        except Exception as exc:  # never crash the app over status sync
            log.warning("Firebase init failed (status sync disabled): %s", exc)
            return False


def set_status(file_id: str, status: str, **extra) -> None:
    """Write /pdfs/{fileId} status (+ extra metadata). Best-effort."""
    if _app is None:
        return
    try:
        from firebase_admin import db

        ref = db.reference(f"/pdfs/{file_id}")
        payload = {"status": status}
        if status in ("queued", "processing"):
            # reset fields from any previous attempt
            payload.update({"error": None, "fileName": None, "completedAt": None})
        payload.update(extra)
        ref.update(payload)
    except Exception as exc:  # pragma: no cover - network best-effort
        log.warning("RTDB write failed for %s: %s", file_id, exc)
