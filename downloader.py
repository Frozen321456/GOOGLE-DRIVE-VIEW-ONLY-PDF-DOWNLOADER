"""
Textbook PDF Finder — Auto Download

Scrapes educational websites for Google Drive PDF links,
then downloads restricted/view-only PDFs automatically using
headless browser preview capture. No manual browser interaction needed.

Usage:
    python downloader.py

Dependencies:
    pip install requests beautifulsoup4 playwright img2pdf Pillow
    playwright install chromium
"""

import re
import os
import sys
import subprocess
import asyncio
import concurrent.futures
import io
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

# Optional headless download dependencies
try:
    from playwright.async_api import async_playwright
    import img2pdf
    from PIL import Image
    HEADLESS_AVAILABLE = True
except ImportError:
    HEADLESS_AVAILABLE = False

# UTF-8 emoji support on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

MAX_CONCURRENT_DOWNLOADS = 3
DOWNLOAD_DIR = "downloads"


# ==================== UTILITIES ====================

def extract_keywords(url):
    """Extract meaningful keywords from URL path for matching."""
    path = urlparse(url).path.strip('/')
    words = re.findall(r'[a-zA-Z0-9]+', path.lower())

    stop_words = {
        'for', 'and', 'the', 'to', 'a', 'in', 'of', 'with', 'by', 'on', 'at',
        'from', 'an', 'is', 'are', 'notes', 'key', 'books', 'solved', 'exercises',
        'solutions', 'guide', 'class', 'schools', 'colleges', 'level', 'primary',
        'middle', 'elementary', 'secondary', 'higher', 'study', 'material',
        'download', 'free', 'pdf', 'board', 'boards', 'post', 'category',
        'wp', 'content', 'uploads'
    }

    keywords = set()
    for w in words:
        # Normalize "5th" -> "5"
        m = re.match(r'^(\d+)(?:st|nd|rd|th)?$', w)
        norm = m.group(1) if m else w
        if norm not in stop_words and len(norm) > 1:
            keywords.add(norm)
    return keywords


def natural_sort_key(s):
    """Sort strings with embedded numbers naturally (Unit 2 before Unit 10)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def derive_folder_name(url):
    """Create a clean folder name from URL path keywords."""
    path = urlparse(url).path.strip('/')
    words = re.findall(r'[a-zA-Z0-9]+', path.lower())
    skip = {
        'www', 'com', 'net', 'org', 'http', 'https', 'wp',
        'content', 'post', 'category', 'html', 'php'
    }
    name = '-'.join(w for w in words if w not in skip and len(w) > 1)
    return name[:80] or 'drive-pdfs'


# ==================== CRAWL & DETECT SUB-PAGES ====================

def crawl_page(index_url):
    """
    Crawl the main page.
    Auto-detects unit/chapter pages or subject-level pages.
    Returns a list of content page URLs to scan for Drive PDFs.
    """
    print(f"\n📥 Fetching: {index_url}")
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Error: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    content = soup.find(class_='entry-content') or soup.find('body') or soup
    target_keywords = extract_keywords(index_url)
    domain = urlparse(index_url).netloc

    # Collect unique links
    seen = set()
    links = []
    for a in content.find_all('a'):
        href = a.get('href')
        if not href:
            continue
        href = urljoin(index_url, href)
        href = urlparse(href)._replace(query="", fragment="").geturl()
        if href not in seen:
            seen.add(href)
            text = re.sub(r'\s+', ' ', (a.text or '').replace('\n', ' ').strip())
            links.append((href, text))

    # Filter junk
    skip = ['facebook.com', 'twitter.com', 'youtube.com', 'wa.me',
            'about-us', 'contact-us', 'privacy-policy', 'blog',
            'find-home-tutors', 'academic-notes-contribution', 'online-digital-services']

    unit_re = re.compile(
        r'(unit|chapter|chap|lec|exercise|part|lesson|story|essay|letter|application)', re.I
    )

    unit_candidates = []
    subject_candidates = []

    for href, text in links:
        if not href.startswith('http') or domain not in href:
            continue
        if any(x in href.lower() for x in skip):
            continue
        if href.rstrip('/') == index_url.rstrip('/'):
            continue

        path = urlparse(href).path.strip('/')
        is_unit = bool(unit_re.search(path)) or bool(unit_re.search(text))
        cand_keywords = extract_keywords(href)
        score = len(target_keywords & cand_keywords)

        if target_keywords and score >= len(target_keywords):
            (unit_candidates if is_unit else subject_candidates).append((href, text))
        elif not target_keywords and is_unit:
            unit_candidates.append((href, text))

    # Case 1: Found unit pages directly
    if unit_candidates:
        urls = sorted(list(set(h for h, _ in unit_candidates)), key=natural_sort_key)
        print(f"✅ Found {len(urls)} unit/chapter pages.")
        return urls

    # Case 2: Found subject pages — let user pick
    if subject_candidates:
        subject_candidates = sorted(list(set(subject_candidates)), key=lambda x: x[1])
        print(f"\n📂 Found {len(subject_candidates)} subject pages:")
        for i, (href, text) in enumerate(subject_candidates, 1):
            print(f"  {i}. {text or '(no label)'}")
        print(f"  {len(subject_candidates) + 1}. ALL subjects")

        while True:
            choice = input(f"\nSelect (1-{len(subject_candidates) + 1}): ").strip()
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(subject_candidates):
                    return crawl_page(subject_candidates[idx - 1][0])
                elif idx == len(subject_candidates) + 1:
                    all_units = []
                    for url, _ in subject_candidates:
                        all_units.extend(crawl_page(url))
                    return sorted(list(set(all_units)), key=natural_sort_key)
            print("❌ Invalid choice.")

    # Case 3: Fallback — any unit-like links with lower match threshold
    fallback = []
    for href, text in links:
        if not href.startswith('http') or domain not in href:
            continue
        if any(x in href.lower() for x in skip):
            continue
        if href.rstrip('/') == index_url.rstrip('/'):
            continue
        if unit_re.search(urlparse(href).path) or unit_re.search(text or ''):
            fallback.append(href)

    if fallback:
        urls = sorted(list(set(fallback)), key=natural_sort_key)
        print(f"⚠️ Fallback: found {len(urls)} potential pages.")
        return urls

    return []


# ==================== SCAN FOR GOOGLE DRIVE IDS ====================

DRIVE_PATTERNS = [
    re.compile(r'/d/([a-zA-Z0-9_-]{25,})'),
    re.compile(r'id=([a-zA-Z0-9_-]{25,})'),
]


def scan_for_drive_ids(url):
    """Scan a page's HTML for Google Drive file IDs."""
    found = set()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            return url, found

        html = resp.text
        for p in DRIVE_PATTERNS:
            found.update(p.findall(html))

        # Also check iframes
        soup = BeautifulSoup(html, 'html.parser')
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if 'drive.google.com' in src:
                for p in DRIVE_PATTERNS:
                    found.update(p.findall(src))
    except Exception:
        pass
    return url, found


# ==================== PDF DOWNLOAD ENGINE ====================

async def capture_single_pdf(context, file_id, output_path, label=""):
    """
    Capture a Google Drive PDF by loading the preview in a headless browser
    and intercepting the rendered page images from network responses.

    Uses scroll-position detection to stop at the document end, and two-stage
    deduplication (dimension grouping + perceptual hash with hamming tolerance)
    to produce exactly one image per page.

    Returns (success: bool, page_count: int, error_message: str|None)
    """
    page = await context.new_page()
    # Use dict keyed by URL so re-fetches of the same image overwrite
    captured_by_url = {}

    async def on_response(response):
        """Intercept image responses — these are the rendered PDF pages."""
        try:
            ct = response.headers.get("content-type", "")
            is_image = any(t in ct for t in ["image/jpeg", "image/png", "image/webp"])
            if response.status == 200 and is_image:
                body = await response.body()
                # Page renders are large (>10KB), icons/UI are tiny
                if len(body) > 10_000:
                    captured_by_url[response.url] = body
        except Exception:
            pass

    page.on("response", on_response)

    try:
        # Navigate to the Google Drive viewer
        await page.goto(
            f"https://drive.google.com/file/d/{file_id}/view",
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Wait for the viewer to initialize and render the first pages
        await page.wait_for_timeout(5000)

        # Check if we landed on a login page or access denied
        page_title = await page.title()
        page_url = page.url
        if "accounts.google.com" in page_url:
            return False, 0, "Requires Google login"
        if "denied" in page_title.lower() or "not found" in page_title.lower():
            return False, 0, "Access denied or file not found"

        # Scroll through all pages to trigger lazy-loading of images.
        # Stop when the SCROLL POSITION stops changing (= reached the bottom).
        # This is more reliable than counting new image URLs, since Google Drive
        # continuously generates new URLs for re-rendered pages.
        stable_rounds = 0

        for _ in range(300):  # Safety limit
            # Get scroll position BEFORE pressing PageDown
            scroll_before = await page.evaluate('''() => {
                // Try common scroll containers in Google Drive viewer
                const containers = [
                    document.querySelector('[role="document"]'),
                    document.querySelector('[role="presentation"]'),
                    document.querySelector('.ndfHFb-c4YZDc-Wrql6b'),
                    document.documentElement
                ];
                for (const c of containers) {
                    if (c && c.scrollHeight > c.clientHeight + 10) {
                        return c.scrollTop;
                    }
                }
                return window.scrollY || document.documentElement.scrollTop || 0;
            }''')

            await page.keyboard.press("PageDown")
            await page.wait_for_timeout(500)

            # Get scroll position AFTER
            scroll_after = await page.evaluate('''() => {
                const containers = [
                    document.querySelector('[role="document"]'),
                    document.querySelector('[role="presentation"]'),
                    document.querySelector('.ndfHFb-c4YZDc-Wrql6b'),
                    document.documentElement
                ];
                for (const c of containers) {
                    if (c && c.scrollHeight > c.clientHeight + 10) {
                        return c.scrollTop;
                    }
                }
                return window.scrollY || document.documentElement.scrollTop || 0;
            }''')

            if scroll_after == scroll_before:
                stable_rounds += 1
                # Scroll position unchanged for 5 consecutive tries = at bottom
                if stable_rounds >= 5:
                    break
            else:
                stable_rounds = 0

        # Final wait for any in-flight image responses
        await page.wait_for_timeout(3000)

    except Exception as e:
        return False, 0, str(e)
    finally:
        page.remove_listener("response", on_response)
        await page.close()

    if not captured_by_url:
        return False, 0, "No page images captured from preview"

    # === TWO-STAGE DEDUPLICATION ===

    # Stage 1: Group by exact (width, height) dimensions.
    # All pages of a PDF render at identical dimensions. UI elements vary.
    # Pick the largest group at the highest resolution.
    dim_groups = {}  # (width, height) -> [img_bytes, ...]
    for img_bytes in captured_by_url.values():
        try:
            img = Image.open(io.BytesIO(img_bytes))
            if img.width < 200 or img.height < 200:
                continue
            key = (img.width, img.height)
            dim_groups.setdefault(key, []).append(img_bytes)
        except Exception:
            continue

    if not dim_groups:
        return False, 0, "No valid page images found"

    # Pick the group at the highest resolution (most pixels)
    # This avoids picking a big group of low-res thumbnails
    best_dims = max(dim_groups.keys(), key=lambda d: d[0] * d[1])
    page_images = dim_groups[best_dims]

    # Stage 2: Within the selected resolution group, deduplicate using
    # perceptual hash with hamming distance tolerance.
    # Google Drive re-renders pages as you scroll, producing visually identical
    # images with slightly different pixel values. An 8x8 average hash with
    # hamming distance <= 5 catches these re-render duplicates.
    def avg_hash(img_bytes, size=8):
        """Compute 8x8 average hash — 64-bit visual fingerprint."""
        img = Image.open(io.BytesIO(img_bytes))
        thumb = img.resize((size, size), Image.LANCZOS).convert('L')
        pixels = list(thumb.getdata())
        avg = sum(pixels) / len(pixels)
        return tuple(1 if p > avg else 0 for p in pixels)

    def hamming_distance(h1, h2):
        """Count differing bits between two hashes."""
        return sum(a != b for a, b in zip(h1, h2))

    HAMMING_THRESHOLD = 5  # Allow up to 5/64 bits difference

    unique_pages = []  # [(hash, img_bytes), ...]
    for img_bytes in page_images:
        try:
            h = avg_hash(img_bytes)
        except Exception:
            continue

        # Check if this image matches any already-captured page
        is_duplicate = False
        for i, (existing_hash, existing_bytes) in enumerate(unique_pages):
            if hamming_distance(h, existing_hash) <= HAMMING_THRESHOLD:
                # Same page — keep the larger file (higher quality)
                if len(img_bytes) > len(existing_bytes):
                    unique_pages[i] = (h, img_bytes)
                is_duplicate = True
                break

        if not is_duplicate:
            unique_pages.append((h, img_bytes))

    # Process unique page images: convert to RGB JPEG
    processed = []
    for _, img_bytes in unique_pages:
        try:
            img = Image.open(io.BytesIO(img_bytes))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=95)
            processed.append(buf.getvalue())
        except Exception:
            continue

    if not processed:
        return False, 0, "Captured images could not be processed"

    # Combine all page images into a single PDF
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(img2pdf.convert(processed))

    return True, len(processed), None


async def download_all_pdfs(file_entries, output_dir):
    """
    Download multiple Drive PDFs in parallel using async headless browsers.

    Args:
        file_entries: list of (file_id, display_name) tuples
        output_dir: path to save downloaded PDFs

    Returns:
        list of (name, file_id, success, error) result tuples
    """
    total = len(file_entries)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1200},
            user_agent=HEADERS["User-Agent"],
        )

        async def process_one(index, file_id, name):
            async with semaphore:
                output_path = Path(output_dir) / f"{name}.pdf"
                print(f"  ⬇️  [{index}/{total}] {name} downloading...")

                success, pages, error = await capture_single_pdf(
                    context, file_id, output_path, name
                )

                if success:
                    print(f"  ✅ [{index}/{total}] {name}.pdf ({pages} pages)")
                else:
                    print(f"  ❌ [{index}/{total}] {name} — {error}")

                return (name, file_id, success, error)

        tasks = [
            process_one(i + 1, fid, name)
            for i, (fid, name) in enumerate(file_entries)
        ]

        results = await asyncio.gather(*tasks)
        await browser.close()

    return list(results)


# ==================== BRAVE FALLBACK ====================

def find_brave():
    """Find Brave browser executable on Windows."""
    candidates = [
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def open_failures_in_brave(failed_results):
    """Open failed Drive file URLs in Brave as manual fallback."""
    brave = find_brave()
    urls = [f"https://drive.google.com/file/d/{fid}/view" for _, fid, _, _ in failed_results]

    if not brave:
        print("❌ Brave not found! Opening in default browser instead...")
        import webbrowser
        for url in urls:
            webbrowser.open(url)
        return

    print(f"\n🌐 Opening {len(urls)} failed PDFs in Brave...")
    subprocess.Popen([brave] + urls)
    print("✅ Tabs opened! Use your Document Preview Exporter extension to download each PDF.")


# ==================== MAIN ====================

def main():
    print("=" * 60)
    print("📚 TEXTBOOK PDF FINDER — Auto Download")
    print("=" * 60)

    if not HEADLESS_AVAILABLE:
        print("\n⚠️  Headless download dependencies not found!")
        print("  Install with:")
        print("    pip install playwright img2pdf Pillow")
        print("    playwright install chromium")
        print("\n  Falling back to browser-only mode (opens tabs in Brave).\n")

    url = input("\n🔗 Enter the main page URL: ").strip()
    if not url:
        print("❌ No URL. Exiting.")
        return

    # Step 1: Find sub-pages (units/chapters)
    pages = crawl_page(url)
    if not pages:
        print("\n❌ No content pages found. Exiting.")
        return

    print(f"\n📂 Found {len(pages)} pages to scan:")
    for i, p in enumerate(pages, 1):
        print(f"  [{i}] {p.rstrip('/').split('/')[-1]}")

    # Step 2: Scan all pages for Google Drive file IDs (parallel)
    print("\n🔍 Scanning pages for Google Drive PDFs...")
    file_entries = []  # (file_id, display_name)
    seen_ids = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(scan_for_drive_ids, p): p for p in pages}
        for future in concurrent.futures.as_completed(futures):
            try:
                scanned_url, ids = future.result()
                name = scanned_url.rstrip('/').split('/')[-1]
                if ids:
                    print(f"  ✅ {name}: {len(ids)} PDF(s)")
                    for j, fid in enumerate(sorted(ids)):
                        if fid not in seen_ids:
                            seen_ids.add(fid)
                            suffix = f"-{j + 1}" if len(ids) > 1 else ""
                            file_entries.append((fid, f"{name}{suffix}"))
                else:
                    print(f"  ⚠️ {name}: no PDFs found")
            except Exception as e:
                print(f"  ❌ Error: {e}")

    if not file_entries:
        print("\n❌ No Google Drive PDFs found. Exiting.")
        return

    # Sort entries naturally by name
    file_entries.sort(key=lambda x: natural_sort_key(x[1]))

    print(f"\n📊 Total unique PDFs found: {len(file_entries)}")

    # Step 3: Download or open in browser
    if HEADLESS_AVAILABLE:
        folder_name = derive_folder_name(url)
        output_dir = os.path.join(DOWNLOAD_DIR, folder_name)
        print(f"📂 Output folder: {output_dir}/")
        print(f"\n⬇️  Downloading {len(file_entries)} PDFs"
              f" ({MAX_CONCURRENT_DOWNLOADS} at a time)...\n")

        results = asyncio.run(download_all_pdfs(file_entries, output_dir))

        # Summary
        succeeded = [r for r in results if r[2]]
        failed = [r for r in results if not r[2]]

        print(f"\n{'=' * 60}")
        print(f"🎉 Done! {len(succeeded)}/{len(results)} PDFs saved to {output_dir}/")

        # Option C fallback: open failures in Brave
        if failed:
            print(f"\n⚠️  {len(failed)} PDF(s) failed to auto-download:")
            for name, fid, _, error in failed:
                print(f"  • {name}: {error}")

            choice = input(
                f"\n🌐 Open {len(failed)} failed PDF(s) in Brave for manual download? (y/n): "
            ).strip().lower()
            if choice in ('y', 'yes', ''):
                open_failures_in_brave(failed)
    else:
        # No headless deps — fall back to opening all in Brave
        input("\n⏎ Press Enter to open all in Brave...")
        urls = [f"https://drive.google.com/file/d/{fid}/view" for fid, _ in file_entries]

        brave = find_brave()
        if brave:
            print(f"\n🌐 Opening {len(urls)} tabs in Brave...")
            subprocess.Popen([brave] + urls)
            print("✅ Tabs opened! Use your extension to download each PDF.")
        else:
            print("❌ Brave not found! Opening in default browser...")
            import webbrowser
            for u in urls:
                webbrowser.open(u)


if __name__ == "__main__":
    main()