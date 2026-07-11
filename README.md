# Textbook PDF Finder

A lightweight Python script that scrapes educational websites for Google Drive PDF links and opens them all in your browser at once. Designed to work with the **[Document Preview Exporter](https://chromewebstore.google.com/detail/document-preview-exporter/npapjbliocdhineglcjkmmmaddpgeono)** browser extension to download view-only PDFs from Google Drive.

## How It Works

1. **Paste a URL** — Give it the main page of a textbook/notes website
2. **Auto-crawl** — It finds all unit/chapter sub-pages automatically
3. **Extract links** — Scans every sub-page for Google Drive file IDs (in parallel)
4. **Open in browser** — Opens all PDF preview links in Brave as tabs at once
5. **Download** — Use the Document Preview Exporter extension on each tab to save the PDF

## Prerequisites

### 1. Install Python Dependencies

```bash
pip install requests beautifulsoup4
```

### 2. Install the Browser Extension

Install the **Document Preview Exporter for Google Drive** extension:

- **Chrome**: [Chrome Web Store](https://chromewebstore.google.com/detail/document-preview-exporter/npapjbliocdhineglcjkmmmaddpgeono)
- **Brave**: Same link — Brave supports Chrome extensions

> This extension lets you download view-only/restricted PDFs from Google Drive previews. Without it, you'd only be able to view but not save the files.

### 3. Browser

The script auto-detects **Brave** on Windows. If Brave is not found, it falls back to your default browser.

## Usage

```bash
python downloader.py
```

Then paste the main page URL when prompted:

```
============================================================
📚 TEXTBOOK PDF FINDER — Scrape & Open in Brave
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

⏎ Press Enter to open all in Brave...

🌐 Opening 12 tabs in Brave...
✅ Tabs opened! Use your extension to download each PDF.
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

## How the Extension Works

Once all tabs are open in your browser:

1. Go to each tab (Google Drive preview page)
2. Click the **Document Preview Exporter** extension icon
3. Enter a filename
4. Click download — the extension exports the preview as a PDF

## Project Structure

```
textbook_downloader/
├── downloader.py      # Main script
├── requirements.txt   # Python dependencies
├── LICENSE            # MIT License
└── README.md          # This file
```

## License

MIT License — see [LICENSE](LICENSE) for details.
