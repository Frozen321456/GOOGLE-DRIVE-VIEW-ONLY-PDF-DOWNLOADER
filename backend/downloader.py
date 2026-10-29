"""Drive preview → PDF builder (full-document capture).

Strategy (in order, first that works wins):
  1. drive.google.com/uc?export=download&id={id}   — direct download endpoint
  2. Headless Chromium on the Drive viewer (/view): intercept the rendered
     page images Drive serves, scroll through the whole document to trigger
     lazy-loading, group by `page=` URL param, combine with img2pdf.

  Why not page.pdf(): the Drive viewer is a JS app that renders ONE page at
  a time — printing it captures a single page (or a blank/loading shell).
  Intercepting the page images + img2pdf produces the full document.

Returns (pdf_bytes, suggested_filename). Raises DownloadError with a
machine-readable code + HTTP status for the API layer.
"""
from __future__ import annotations

import hashlib
import hashlib
import io
import os
import re
import shutil
import tempfile
import urllib.parse

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25    # seconds for direct HTTP attempts
NAV_TIMEOUT = 60_000    # ms for Playwright navigation
MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB hard stop (free-tier RAM guard)
MAX_PAGE_IMAGE_BYTES = 2_500_000      # per-page image budget before downscale
MAX_CAPTURE_DISK_BYTES = 350 * 1024 * 1024  # total temp-image budget on disk
# Steps needed ≈ pages * pageHeight / (0.5 * viewportHeight). A 61-page doc at
# 1000px viewport, ~1050px pages needs ~130 steps; cap generously at 300.
# Job API renders in a background thread, so long walks are fine.
SCROLL_STEPS_MAX = 300
STABLE_ROUNDS_END = 5   # consecutive no-scroll rounds = bottom reached


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


def _clean_title_to_filename(title: str, fallback: str) -> str:
    """'Unit 01.pdf - Google Drive' -> 'Unit 01.pdf'"""
    t = (title or "").strip()
    if " - Google Drive" in t:
        t = t.split(" - Google Drive")[0]
    t = re.sub(r'[\\/:*?"<>|]', "_", t).strip()
    if not t:
        return fallback
    if not t.lower().endswith(".pdf"):
        t += ".pdf"
    return t[:120]


# JS injected into the viewer page: report scrollTop of Drive's scroll container.
_SCROLL_POS_JS = """
() => {
    const findScrollContainer = () => {
        const doc = document.querySelector('[role="document"]');
        if (doc) {
            let parent = doc;
            while (parent) {
                if (parent.scrollHeight > parent.clientHeight + 10) {
                    const style = window.getComputedStyle(parent);
                    if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                        return parent;
                    }
                }
                parent = parent.parentElement;
            }
        }
        const divs = Array.from(document.querySelectorAll('div'));
        let best = null;
        let maxScroll = 0;
        for (const div of divs) {
            if (div.scrollHeight > div.clientHeight + 50) {
                const style = window.getComputedStyle(div);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    if (div.scrollHeight > maxScroll) {
                        maxScroll = div.scrollHeight;
                        best = div;
                    }
                }
            }
        }
        return best || document.documentElement;
    };
    const c = findScrollContainer();
    return c ? c.scrollTop : 0;
}
"""

# JS: scroll the container down by ~80% of its viewport.
_SCROLL_STEP_JS = """
() => {
    const findScrollContainer = () => {
        const doc = document.querySelector('[role="document"]');
        if (doc) {
            let parent = doc;
            while (parent) {
                if (parent.scrollHeight > parent.clientHeight + 10) {
                    const style = window.getComputedStyle(parent);
                    if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                        return parent;
                    }
                }
                parent = parent.parentElement;
            }
        }
        const divs = Array.from(document.querySelectorAll('div'));
        let best = null;
        let maxScroll = 0;
        for (const div of divs) {
            if (div.scrollHeight > div.clientHeight + 50) {
                const style = window.getComputedStyle(div);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    if (div.scrollHeight > maxScroll) {
                        maxScroll = div.scrollHeight;
                        best = div;
                    }
                }
            }
        }
        return best || document.documentElement;
    };
    const c = findScrollContainer();
    if (c) c.scrollTop += c.clientHeight * 0.5;
}
"""

# JS: one-call scroll metrics + viewer total page count (for progress).
_SCROLL_INFO_JS = """
() => {
    const findScrollContainer = () => {
        const doc = document.querySelector('[role="document"]');
        if (doc) {
            let parent = doc;
            while (parent) {
                if (parent.scrollHeight > parent.clientHeight + 10) {
                    const style = window.getComputedStyle(parent);
                    if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                        return parent;
                    }
                }
                parent = parent.parentElement;
            }
        }
        const divs = Array.from(document.querySelectorAll('div'));
        let best = null;
        let maxScroll = 0;
        for (const div of divs) {
            if (div.scrollHeight > div.clientHeight + 50) {
                const style = window.getComputedStyle(div);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    if (div.scrollHeight > maxScroll) {
                        maxScroll = div.scrollHeight;
                        best = div;
                    }
                }
            }
        }
        return best || document.documentElement;
    };
    const c = findScrollContainer();
    const scrollTop = c ? c.scrollTop : 0;
    const scrollHeight = c ? c.scrollHeight : 0;
    const clientHeight = c ? c.clientHeight : 0;

    let totalPages = null;
    const sels = [
        '.drive-viewer-paginated-page-number',
        '[class*="pageCount"]',
        '[class*="page-number"]',
        '[aria-label*="Page"]',
    ];
    for (const sel of sels) {
        for (const el of document.querySelectorAll(sel)) {
            const hay = (el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '');
            const m = hay.match(/(\d+)\s*\/\s*(\d+)/);
            if (m) { totalPages = parseInt(m[2], 10); break; }
        }
        if (totalPages) break;
    }
    if (!totalPages) {
        const walker = document.evaluate(
            "//*[contains(normalize-space(text()), '/')]",
            document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        for (let i = 0; i < Math.min(walker.snapshotLength, 400); i++) {
            const t = (walker.snapshotItem(i).textContent || '').trim();
            const m = t.match(/^(\d{1,4})\s*\/\s*(\d{1,4})$/);
            if (m) { totalPages = parseInt(m[2], 10); break; }
        }
    }
    return { scrollTop, scrollHeight, clientHeight, totalPages };
}
"""

# JS: jump to an absolute fraction of the document (targeted missing-page retries).
_SCROLL_TO_JS = """
(frac) => {
    const findScrollContainer = () => {
        const doc = document.querySelector('[role="document"]');
        if (doc) {
            let parent = doc;
            while (parent) {
                if (parent.scrollHeight > parent.clientHeight + 10) {
                    const style = window.getComputedStyle(parent);
                    if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                        return parent;
                    }
                }
                parent = parent.parentElement;
            }
        }
        const divs = Array.from(document.querySelectorAll('div'));
        let best = null;
        let maxScroll = 0;
        for (const div of divs) {
            if (div.scrollHeight > div.clientHeight + 50) {
                const style = window.getComputedStyle(div);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    if (div.scrollHeight > maxScroll) {
                        maxScroll = div.scrollHeight;
                        best = div;
                    }
                }
            }
        }
        return best || document.documentElement;
    };
    const c = findScrollContainer();
    if (c) c.scrollTop = c.scrollHeight * frac;
}
"""


def _assemble_pdf_from_images(captured_dir: str, captured_by_url: dict, progress=None) -> tuple[bytes, int]:
    """Group page images by URL `page=` param (largest wins), dedupe, img2pdf.

    Raw page bytes were already spilled to `captured_dir` by the capture
    phase; this function streams one page at a time (PIL peak = one image)
    and writes JPEG files next to them for img2pdf to consume by path —
    so peak RAM stays ~1 page image instead of the whole document.
    """
    try:
        from PIL import Image
        import img2pdf
    except ImportError as exc:
        raise DownloadError(
            "Image pipeline not installed on the server", "RENDER_FAILED", 500
        ) from exc

    pages_by_num: dict[int, str] = {}   # page num -> raw file path
    unpageable: list[str] = []          # raw file paths

    for url, fname in list(captured_by_url.items()):
        path = os.path.join(captured_dir, fname)
        try:
            with open(path, "rb") as fh:
                head = fh.read(4096)
            if len(head) < 10_000:
                continue  # icons / UI chrome
        except OSError:
            continue
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "page" in qs:
            try:
                n = int(qs["page"][0])
                if n not in pages_by_num or os.path.getsize(path) > os.path.getsize(pages_by_num[n]):
                    pages_by_num[n] = path
            except (ValueError, OSError):
                unpageable.append(path)
        else:
            unpageable.append(path)

    ordered: list[str] = []
    if pages_by_num:
        ordered = [pages_by_num[n] for n in sorted(pages_by_num)]
    else:
        seen: set[str] = set()
        for p in unpageable:
            h = hashlib.md5(open(p, "rb").read(65536)).hexdigest()
            if h not in seen:
                seen.add(h)
                ordered.append(p)

    if not ordered:
        raise DownloadError("No page images could be captured", "RENDER_FAILED", 500)

    if progress:
        try:
            progress({"phase": "assemble", "pagesDone": 0, "pagesTotal": len(ordered), "scrollPct": None})
        except Exception:
            pass

    processed: list[str] = []
    for idx, path in enumerate(ordered, 1):
        try:
            with Image.open(path) as img:
                img.load()
                if img.width < 200 or img.height < 200:
                    continue
                # JPEG originals embed byte-for-byte (zero quality loss)
                if img.format == "JPEG":
                    processed.append(path)
                else:
                    # PNG/WebP: flatten alpha onto white, encode once at q95,
                    # write to disk — only one image lives in RAM at a time.
                    if img.mode in ("RGBA", "LA", "P"):
                        img = img.convert("RGBA")
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        bg.paste(img, mask=img.split()[-1])
                        img = bg
                    elif img.mode != "RGB":
                        img = img.convert("RGB")
                    out_path = os.path.join(captured_dir, f"conv_{idx:05d}.jpg")
                    img.save(out_path, format="JPEG", quality=95)
                    processed.append(out_path)
        except Exception:
            continue
        if progress and idx % 10 == 0:
            try:
                progress({"phase": "assemble", "pagesDone": idx, "pagesTotal": len(ordered), "scrollPct": None})
            except Exception:
                pass

    if not processed:
        raise DownloadError("Captured images could not be processed", "RENDER_FAILED", 500)

    pdf_bytes = img2pdf.convert(processed)
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise DownloadError("PDF exceeds the 100 MB limit", "RENDER_FAILED", 500)
    return pdf_bytes, len(processed)


def _cap_image_width(url: str, cap: int = 2000) -> str | None:
    """Rewrite a Drive viewer image URL so its width never exceeds `cap`.

    Drive serves page images sized to the viewport at deviceScaleFactor=2
    (up to w3200). A decoded w3200 bitmap is ~56MB of renderer RAM and the
    decode cache keeps a rolling window of them → OOM at ~page 45 on a
    512MB box. Capping the request at the network layer keeps decoded
    pages at ~22MB each without touching visual quality (242 DPI on A4).
    Returns the rewritten URL, or None when no rewrite is needed.
    """
    m = re.search(r"=w(\d+)-h(\d+)", url)  # googleusercontent-style suffix
    if m:
        w0, h0 = int(m.group(1)), int(m.group(2))
        if w0 <= cap:
            return None
        h1 = max(1, round(h0 * cap / w0))
        return re.sub(r"=w\d+-h\d+", f"=w{cap}-h{h1}", url, count=1)
    parsed = urllib.parse.urlparse(url)
    if "viewerng/img" not in parsed.path and "/img?" not in parsed.path:
        return None
    qpairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    w0 = next((int(v) for k, v in qpairs if k == "w" and v.isdigit()), 0)
    if not w0 or w0 <= cap:
        return None
    h0 = next((int(v) for k, v in qpairs if k == "h" and v.isdigit()), 0)
    new_q = []
    for k, v in qpairs:
        if k == "w":
            new_q.append((k, str(cap)))
        elif k == "h" and h0:
            new_q.append((k, str(max(1, round(h0 * cap / w0)))))
        else:
            new_q.append((k, v))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(new_q)))


def _capture_view_document(context, file_id: str, progress=None) -> tuple[str, dict, str]:
    """Open the Drive viewer, scroll through, capture page images to disk.

    Returns (captured_dir, captured_by_url, page_title). The caller owns
    captured_dir (assembly + cleanup) so the browser can be torn down
    before the PDF is built — this split is what keeps 512MB alive.
    `progress`, when given, is called with dicts like
    {phase, scrollPct, pagesDone, pagesTotal} as the walk advances.
    """
    page = context.new_page()
    captured_by_url: dict[str, str] = {}  # url -> filename in captured_dir
    disk_used = {"n": 0}
    captured_dir = tempfile.mkdtemp(prefix="cap_")

    # Cap page-image width at the network layer: w3200 bitmaps are what
    # kills the 512MB renderer. 2000px keeps 242 DPI (print-quality) at
    # ~22MB decoded per page instead of ~56MB.
    def _route_rewrite(route):
        try:
            rewritten = _cap_image_width(route.request.url, cap=2000)
            if rewritten:
                route.continue_(url=rewritten)
                return
        except Exception:
            pass
        try:
            route.continue_()
        except Exception:
            pass

    try:
        page.route("**/*", _route_rewrite)
    except Exception:
        pass  # interception unavailable → fall back to old behavior

    def on_response(response):
        try:
            ct = (response.headers or {}).get("content-type", "")
            if response.status != 200 or not any(
                t in ct for t in ("image/jpeg", "image/png", "image/webp")
            ):
                return
            body = response.body()
            if len(body) < 10_000:  # page renders are big; icons are tiny
                return
            # oversize single page: downscale once, keeps 2x-retina quality
            # (2500px wide ≈ 300dpi for a 21cm page) while capping RAM/disk
            if len(body) > MAX_PAGE_IMAGE_BYTES:
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(body))
                    if img.width > 2500:
                        ratio = 2500 / img.width
                        img = img.resize((2500, int(img.height * ratio)), Image.LANCZOS)
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=90)
                    body = buf.getvalue()
                except Exception:
                    pass  # keep original bytes if PIL chokes
            h = hashlib.sha256(response.url.encode()).hexdigest()[:16]
            fname = f"img_{h}.bin"
            with open(os.path.join(captured_dir, fname), "wb") as fh:
                fh.write(body)
            disk_used["n"] += len(body)
            captured_by_url[response.url] = fname
            # emergency stop flag: doc way too big for free tier. Playwright
            # swallows handler exceptions, so raise from the walk loop instead.
            if disk_used["n"] > MAX_CAPTURE_DISK_BYTES:
                disk_used["over"] = True
        except Exception:
            pass

    page.on("response", on_response)
    page_title = ""
    try:
        page.goto(
            f"https://drive.google.com/file/d/{file_id}/view",
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT,
        )
        page.wait_for_timeout(5000)  # viewer init + first pages render

        # Login redirect / error page detection
        if "accounts.google.com" in (page.url or ""):
            raise DownloadError(
                "File is not accessible (permission denied)", "NOT_ACCESSIBLE", 403
            )
        page_title = page.title() or ""
        content = page.content().lower()
        title_l = page_title.lower()
        error_markers = (
            "you need access",
            "request access",
            "you don't have access",
            "sorry, this file is private",
            "this file is private",
            "page not found",
            "error occurred",
        )
        if any(m in title_l for m in ("denied", "not found", "error occurred")) or any(
            m in content for m in error_markers
        ):
            raise DownloadError(
                "File is not accessible (not found or permission denied)",
                "NOT_ACCESSIBLE",
                403,
            )

        def _captured_page_nums() -> set[int]:
            nums = set()
            for u in captured_by_url:
                try:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
                    if "page" in qs:
                        nums.add(int(qs["page"][0]))
                except Exception:
                    pass
            return nums

        def _total_pages_from_viewer() -> int | None:
            try:
                return page.evaluate(
                    """() => {
                        const sels = [
                            '.drive-viewer-paginated-page-number',
                            '[class*="pageCount"]',
                            '[class*="page-number"]',
                            '[aria-label*="Page"]',
                        ];
                        for (const sel of sels) {
                            for (const el of document.querySelectorAll(sel)) {
                                const hay = (el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '');
                                const m = hay.match(/(\d+)\s*\/\s*(\d+)/);
                                if (m) return parseInt(m[2], 10);
                            }
                        }
                        const walker = document.evaluate(
                            "//*[contains(normalize-space(text()), '/')]",
                            document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                        for (let i = 0; i < Math.min(walker.snapshotLength, 400); i++) {
                            const t = (walker.snapshotItem(i).textContent || '').trim();
                            const m = t.match(/^(\d{1,4})\s*\/\s*(\d{1,4})$/);
                            if (m) return parseInt(m[2], 10);
                        }
                        return null;
                    }"""
                )
            except Exception:
                return None

        def _emit(phase: str, **extra):
            if not progress:
                return
            try:
                info = page.evaluate(_SCROLL_INFO_JS)
                sh = info.get("scrollHeight") or 0
                st = info.get("scrollTop") or 0
                payload = {
                    "phase": phase,
                    "scrollPct": round(min(st / sh, 1.0) * 100, 1) if sh else None,
                    "pagesDone": len(_captured_page_nums()),
                    "pagesTotal": info.get("totalPages"),
                }
                payload.update(extra)
                progress(payload)
            except Exception:
                pass

        # Scroll through the document to lazy-load every page image.
        # Steps are ~0.5 viewport so no page is skipped; after reaching the
        # bottom we compare the captured page numbers against the viewer's
        # own page count and re-scroll to any missing pages.
        stable = 0
        last_emit = 0.0
        step_i = 0
        for _ in range(SCROLL_STEPS_MAX):
            if disk_used.get("over"):
                raise DownloadError(
                    "Document too large for the free engine (image budget exceeded)",
                    "RENDER_FAILED",
                    500,
                )
            before = page.evaluate(_SCROLL_POS_JS)
            page.evaluate(_SCROLL_STEP_JS)
            page.wait_for_timeout(900)
            after = page.evaluate(_SCROLL_POS_JS)
            step_i += 1
            if step_i % 3 == 0:
                # Renderer bitmap bleed fix: Drive keeps every visited page's
                # decoded image in the DOM; force-free far-offscreen ones
                # (wide range: the scroll step is ~400px, decode window larger).
                try:
                    page.evaluate(
                        """() => {
                            let freed = 0;
                            for (const img of document.querySelectorAll('img')) {
                                const r = img.getBoundingClientRect();
                                if (r.bottom < -4000 || r.top > window.innerHeight + 4000) {
                                    if (img.src && !img.dataset.sweep) {
                                        img.dataset.sweep = '1';
                                        img.removeAttribute('src');
                                        img.srcset = '';
                                        freed++;
                                    }
                                }
                            }
                            return freed;
                        }"""
                    )
                except Exception:
                    pass
            if after - before >= 0 and (after - last_emit) > 0:  # emit per step
                _emit("render")
                last_emit = after
            if after == before:
                stable += 1
                if stable >= STABLE_ROUNDS_END:
                    break
            else:
                stable = 0

        # Retry passes: jump directly to the fractional positions of missing
        # pages (fast) instead of re-walking the whole document.
        total_pages = _total_pages_from_viewer()
        for attempt in range(3):
            if not total_pages:
                break
            have = _captured_page_nums()
            missing = sorted(n for n in range(1, total_pages + 1) if n not in have)
            if not missing:
                break
            _emit("retry", missing=len(missing))
            for n in missing:
                frac = max(0.0, min(1.0, (n - 1) / max(total_pages - 1, 1)))
                try:
                    page.evaluate(_SCROLL_TO_JS, frac)
                    page.wait_for_timeout(1600)
                except Exception:
                    continue
            page.wait_for_timeout(2000)

        page.wait_for_timeout(3000)  # in-flight images
        _emit("finalize")
    except BaseException:
        # failure path: capture owns its tempdir cleanup
        shutil.rmtree(captured_dir, ignore_errors=True)
        raise
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        page.close()

    if not captured_by_url:
        shutil.rmtree(captured_dir, ignore_errors=True)
        raise DownloadError(
            "No page images captured from the Drive viewer", "RENDER_FAILED", 500
        )

    return captured_dir, captured_by_url, page_title


def build_pdf(file_id: str, progress=None) -> tuple[bytes, str]:
    """Build PDF bytes for a Drive file ID. See module docstring."""
    if not file_id or not re.fullmatch(r"[A-Za-z0-9_-]{20,}", file_id):
        raise DownloadError("Invalid Drive file ID", "INVALID_URL", 400)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        # Tier 1: direct endpoints (works when the file is truly public)
        direct = _try_direct_download(session, file_id)
        if direct:
            return direct

        # Tier 2: headless Chromium full-document capture
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
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-back-forward-cache",
                    "--disable-software-rasterizer",
                    "--process-per-site",
                    "--renderer-process-limit=1",
                    "--js-flags=--max-old-space-size=192",
                ],
            )
            try:
                # deviceScaleFactor=2 → Drive serves ~2x resolution page
                # images (288 DPI at 1200px width — quality preserved).
                # 1200x800 keeps decoded renderer bitmaps at ~32MB/page
                # instead of ~57MB (1600px) — this is what stops the OOM.
                context = browser.new_context(
                    viewport={"width": 1200, "height": 800},
                    device_scale_factor=2,
                    user_agent=USER_AGENT,
                    accept_downloads=False,
                )
                captured_dir, captured_by_url, page_title = _capture_view_document(
                    context, file_id, progress=progress
                )
            finally:
                # Chromium freed BEFORE pdf assembly — its ~250MB never
                # overlaps with the img2pdf pass. This alone fixed the OOM.
                browser.close()

        # Chromium is gone; assemble from disk images with full RAM headroom.
        try:
            pdf_bytes, _page_count = _assemble_pdf_from_images(captured_dir, captured_by_url, progress=progress)
        finally:
            shutil.rmtree(captured_dir, ignore_errors=True)

        file_name = _clean_title_to_filename(page_title, f"{file_id}.pdf")
        return pdf_bytes, file_name
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"Playwright render failed: {exc}", "RENDER_FAILED", 500) from exc
    finally:
        session.close()
