# Google Drive View-Only PDF Downloader

A Python CLI tool that scrapes educational websites for Google Drive PDF links and **automatically downloads restricted/view-only PDFs** using headless browser preview capture. No manual browser interaction needed.

> [!IMPORTANT]
> **MUST READ BEFORE USING:**
> To use the fully automated headless download engine, you **MUST** run:
> ```bash
> playwright install chromium
> ```
> after installing the Python dependencies. Without it, the script will fall back to opening tabs in your browser for manual downloading.


## How It Works

* **Subject Crawling Mode (Bulk):** Paste the URL of an educational website's index page. The script dynamically crawls and finds all unit/chapter sub-pages, scans them in parallel for Google Drive view-only PDF IDs, and downloads them in parallel.
* **Direct Mode (Single PDF):** Paste a direct Google Drive viewer URL or file ID directly. The script detects it, bypasses the crawling/scanning phase, retrieves the document's real filename dynamically from the preview title page, and downloads the PDF immediately.

### Why Headless Browser?

Many educational PDFs on Google Drive have **download restrictions** enabled by the owner. Standard tools (`gdown`, `curl`, `wget`) cannot download these files. This tool uses an invisible (headless) Chromium browser to:
- Load the Google Drive preview page
- Capture the rendered page images as Google scrolls through the document
- Combine all page images into a proper PDF file

## Prerequisites

### 1. Install Python Dependencies

```bash
pip install requests beautifulsoup4 playwright img2pdf Pillow
```

### 2. Install Chromium for Playwright

```bash
playwright install chromium
```

> This downloads a Chromium browser binary (~150MB, one-time). It runs invisibly — no browser window opens during download.

### 3. Browser Fallback (Optional)

If any PDFs fail to auto-download, the tool offers to open them in **Brave** browser where you can use the [Document Preview Exporter](https://chromewebstore.google.com/detail/document-preview-exporter/npapjbliocdhineglcjkmmmaddpgeono) extension to save them manually.

## Usage

```bash
python downloader.py
```

You can paste either:
1. An index page of an educational website to download all listed PDFs.
2. A direct Google Drive sharing link (e.g. `https://drive.google.com/file/d/.../view`).
3. An exact Google Drive file ID (e.g. `1nDKKBVuYK-4PZsgT9EbydLl5VDxuct05`).

### Example Output

```
============================================================
📚 GOOGLE DRIVE VIEW-ONLY PDF DOWNLOADER
============================================================

🔗 Enter the main page URL: https://example.com/class-10-notes/english/

📥 Fetching: https://example.com/class-10-notes/english/
✅ Found 12 unit/chapter pages.

📂 Found 12 pages to scan:
  [1] unit-1
  [2] unit-2
  ...

🔍 Scanning pages for Google Drive PDFs...
  ✅ unit-1: 1 PDF(s)
  ✅ unit-2: 1 PDF(s)
  ...

📊 Total unique PDFs found: 12
📂 Output folder: downloads/class-10-english/

⬇️  Downloading 12 PDFs (3 at a time)...
  ✅ [1/12] unit-1.pdf (15 pages)
  ✅ [2/12] unit-2.pdf (8 pages)
  ⬇️  [3/12] unit-3.pdf downloading...
  ...

🎉 Done! 12/12 PDFs saved to downloads/class-10-english/
```

### Multi-Level Navigation

If the URL is a landing page with multiple subjects, the script shows a selection menu:

```
📂 Found 5 subject pages:
  1. English
  2. Math
  3. Science
  4. Urdu
  5. Pakistan Studies
  6. ALL subjects

Select (1-6):
```

## Project Structure

```
textbook_downloader/
├── downloader.py      # Main script (crawl + headless download)
├── requirements.txt   # Python dependencies
├── downloads/         # Auto-created output folder
├── log-book.md        # Development log
├── LICENSE            # MIT License
└── README.md          # This file
```

## Fallback Mode

If `playwright`/`img2pdf`/`Pillow` are not installed, the tool falls back to the old behavior: opening all PDF links as tabs in Brave browser for manual download using the Document Preview Exporter extension.

## License

MIT License — see [LICENSE](LICENSE) for details.
