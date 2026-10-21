"""Playwright-based Drive preview → PDF builder.

Strategy (in order, first that works wins):
  1. drive.google.com/uc?export=download&id={id}   — direct download endpoint
  2. drive.google.com/uc?export=view&id={id}       — inline view endpoint
  3. Preview page → headless-Chromium print-to-PDF (falls back to printing
     whatever the preview page renders, including the embedded viewer)

Returns (pdf_bytes, suggested_filename). Raises DownloadError with a
machine-readable code + HTTP status for the API layer.
"""
from __future__ import annotations

import re

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25  # seconds for direct HTTP attempts
NAV_TIMEOUT = 30_000  # ms for Playwright navigation
MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB hard stop (free-tier RAM guard)


class DownloadError(Exception):
    """Backend error carrying a UI-facing code and HTTP status."""

    def __init__(self, message: str, code: str = "RENDER_FAILED", http_status: int = 500):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def extract_file_id(raw: str) -> str | None:
    """Pull a Drive file ID out of any common URL form, or a raw ID."""
    if not raw:
        return None
    s = str(raw).strip()
    if not (20 <= len(s) <= 200):
        return None
    patterns = [
        r"/file/d/([A-Za-z0-9_-]{20,})",
        r"/d/([A-Za-z0-9_-]{20,})",
        r"[?&]id=([A-Za-z0-9_-]{20,})",
        r"^([A-Za-z0-9_-]{25,})$",
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            return m.group(1)
    return None


def _pdf_response(resp: requests.Response) -> bytes | None:
    """Return bytes when `resp` actually carries a PDF."""
    ctype = (resp.headers.get("content-type") or "").lower()
    body = resp.content
    if resp.status_code == 200 and ("application/pdf" in ctype or body[:5] == b"%PDF-"):
        if len(body) > MAX_PDF_BYTES:
            raise DownloadError("PDF exceeds the 100 MB limit", "RENDER_FAILED", 500)
        return body
    return None


def _filename_from_headers(resp: requests.Response, fallback: str) -> str:
    disp = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disp)
    if m:
        name = m.group(1).strip()
        return name if name.lower().endswith(".pdf") else f"{name}.pdf"
    return fallback


def _looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def _try_direct_download(session: requests.Session, file_id: str):
    """Attempt uc?export=download / uc?export=view over plain HTTP."""
    urls = [
        f"https://drive.google.com/uc?export=download&id={file_id}",
        f"https://drive.google.com/uc?export=view&id={file_id}",
    ]
    for url in urls:
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            continue
        pdf = _pdf_response(resp)
        if pdf:
            return pdf, _filename_from_headers(resp, f"{file_id}.pdf")
        # confirm-scan page (large files) carries a form; follow it once
        if _looks_like_html(resp.content) and b"confirm=" in resp.content:
            html = resp.content.decode("utf-8", "ignore")
            action = re.search(r'action="([^"]+)"', html)
            confirm = re.search(r'name="confirm" value="([^"]+)"', html)
            if action and confirm:
                next_url = action.group(1).replace("&", "&")
                try:
                    resp2 = session.get(next_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                    pdf = _pdf_response(resp2)
                    if pdf:
                        return pdf, _filename_from_headers(resp2, f"{file_id}.pdf")
                except requests.RequestException:
                    continue
    return None


def _safe_filename_from_title(title: str, fallback: str) -> str:
    title = (title or "").strip()
    if not title:
        return fallback
    title = re.sub(r'[\\/:*?"<>|]', "_", title)
    if not title.lower().endswith(".pdf"):
        title += ".pdf"
    return title[:120]


def build_pdf(file_id: str) -> tuple[bytes, str]:
    """Build PDF bytes for a Drive file ID. See module docstring."""
    if not file_id or not re.fullmatch(r"[A-Za-z0-9_-]{20,}", file_id):
        raise DownloadError("Invalid Drive file ID", "INVALID_URL", 400)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        # Tier 1: direct endpoints
        direct = _try_direct_download(session, file_id)
        if direct:
            return direct

        # Tier 2: headless Chromium — preview page print-to-PDF
        preview_url = f"https://drive.google.com/file/d/{file_id}/preview"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DownloadError(
                "Playwright is not installed on the server", "RENDER_FAILED", 500
            ) from exc

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                page.goto(preview_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

                # Permission-wall / error-page detection (fake or private IDs
                # land on Google's "sorry" page instead of a real viewer)
                content = page.content().lower()
                error_markers = (
                    "you need access",
                    "request access",
                    "you don't have access",
                    "sorry, this file is private",
                    "this file is private",
                    "can't be scanned for viruses",
                    "drive.google.com/drive/error",
                )
                if any(marker in content for marker in error_markers):
                    raise DownloadError(
                        "File is not accessible (permission denied or not found)",
                        "NOT_ACCESSIBLE",
                        403,
                    )

                # Locate the in-page viewer embed/iframe when present
                viewer_src = None
                for sel in ("embed#docsrc", "iframe#docsrc", "#docsrc"):
                    el = page.query_selector(sel)
                    if el:
                        viewer_src = el.get_attribute("src")
                        if viewer_src:
                            break

                if viewer_src:
                    page.goto(viewer_src, wait_until="load", timeout=NAV_TIMEOUT)
                    page.wait_for_timeout(2500)  # viewer render settle
                else:
                    page.wait_for_timeout(3500)  # preview render settle

                pdf_bytes = page.pdf(format="A4", print_background=True)
                if not pdf_bytes or len(pdf_bytes) < 200:
                    raise DownloadError("Rendered PDF was empty", "RENDER_FAILED", 500)

                title = page.title() or ""
                file_name = _safe_filename_from_title(title, f"{file_id}.pdf")
                return pdf_bytes, file_name
            finally:
                browser.close()
    except DownloadError:
        raise
    except Exception as exc:
        # Playwright errors (and timeouts) arrive here once imported lazily
        raise DownloadError(f"Playwright render failed: {exc}", "RENDER_FAILED", 500) from exc
    finally:
        session.close()
