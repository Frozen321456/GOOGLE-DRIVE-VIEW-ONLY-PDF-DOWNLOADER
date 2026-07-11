"""
Textbook PDF Finder — Scrape & Open in Brave

Scrapes educational websites for Google Drive PDF links,
then opens them all in Brave browser at once.
Use with the "Document Preview Exporter" extension to download view-only PDFs.

Usage:
    python downloader.py

Dependencies:
    pip install requests beautifulsoup4
"""

import re
import os
import sys
import subprocess
import concurrent.futures
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

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


# ==================== OPEN IN BRAVE ====================

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


def open_all_in_brave(urls):
    """Open all URLs as tabs in Brave browser."""
    brave = find_brave()
    if not brave:
        print("❌ Brave not found! Opening in default browser instead...")
        import webbrowser
        for url in urls:
            webbrowser.open(url)
        return

    print(f"\n🌐 Opening {len(urls)} tabs in Brave...")
    subprocess.Popen([brave] + urls)
    print("✅ Tabs opened! Use your extension to download each PDF.")


# ==================== MAIN ====================

def main():
    print("=" * 60)
    print("📚 TEXTBOOK PDF FINDER — Scrape & Open in Brave")
    print("=" * 60)

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
    drive_urls = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(scan_for_drive_ids, p): p for p in pages}
        for future in concurrent.futures.as_completed(futures):
            try:
                scanned_url, ids = future.result()
                name = scanned_url.rstrip('/').split('/')[-1]
                if ids:
                    print(f"  ✅ {name}: {len(ids)} PDF(s)")
                    for fid in ids:
                        drive_urls.append(f"https://drive.google.com/file/d/{fid}/view")
                else:
                    print(f"  ⚠️ {name}: no PDFs found")
            except Exception as e:
                print(f"  ❌ Error: {e}")

    if not drive_urls:
        print("\n❌ No Google Drive PDFs found. Exiting.")
        return

    # Remove duplicates, preserve order
    seen = set()
    unique_urls = []
    for u in drive_urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)
    drive_urls = unique_urls

    print(f"\n📊 Total unique PDFs found: {len(drive_urls)}")
    input("\n⏎ Press Enter to open all in Brave...")

    # Step 3: Open all in Brave
    open_all_in_brave(drive_urls)


if __name__ == "__main__":
    main()