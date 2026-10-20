# PRD — GDrive PDF Fetcher

## 1. Product Overview

**Product Name:** GDrive PDF Fetcher
**One-liner:** A free web app that lets users download Google Drive "view-only" PDFs by rendering the preview page and rebuilding it as a downloadable PDF.

**Platform:** Web (responsive, mobile + desktop)
**Deployment Cost:** $0 (Vercel Free + Render Free + Firebase Spark + Browser Storage)

## 2. Problem Statement

Google Drive lets file owners mark PDFs as "view-only". Users can view them but cannot download. There is no official download button. Users need a way to save these PDFs for offline access — without paying for third-party tools.

## 3. Goals & Objectives

| Goal | Metric |
|---|---|
| Free forever | $0 monthly hosting cost |
| Fast UX | First-load < 3s; cached PDF < 500ms |
| Reliable | 95%+ success rate on typical view-only PDFs |
| Privacy-first | PDF stored only on user's browser, not on server |

## 4. Target Users

- Students saving shared lecture notes
- Professionals archiving shared reports
- Anyone with a view-only Drive PDF who needs offline access

## 5. User Stories

| ID | As a... | I want to... | So that... |
|---|---|---|---|
| US-1 | User | paste a Drive link and click Download | I get the PDF |
| US-2 | User | see progress while PDF is being built | I know it's working |
| US-3 | User | re-download a previously fetched PDF | I don't wait for Render to wake up |
| US-4 | User | see my download history | I can manage past files |
| US-5 | User | delete a saved PDF | I control my local storage |
| US-6 | User | see clear error messages | I know what went wrong |

## 6. Functional Requirements

### FR-1: Input & Validation
- Text input accepts full Drive URL (`https://drive.google.com/file/d/{ID}/view`) or raw file ID
- Regex extraction of file ID
- Reject empty / malformed input with inline error

### FR-2: Download Flow
- On submit, check IndexedDB cache first
- Cache hit: instant download, no backend call
- Cache miss: call Render API, receive PDF blob, save to IndexedDB, trigger download

### FR-3: Progress & Status
- States: idle, validating, checking-cache, fetching, rendering, saving, done/error
- Real-time status via Firebase RTDB listener (when backend processing)
- Progress bar + spinner

### FR-4: Local Storage (IndexedDB)
- Store `{ id, fileName, blob, size, savedAt }`
- List all saved PDFs on page load
- Re-download from list; delete individual PDFs

### FR-5: Real-time Sync (Firebase RTDB)
- Backend writes status: queued, processing, completed, failed
- Frontend listens via `onValue` and updates UI live
- Metadata only (no blobs) in RTDB

### FR-6: Error Handling
- Network error: retry button
- Backend timeout (Render cold start): "Waking server, please wait…"
- Invalid Drive link / permission denied: clear messages

## 7. Non-Functional Requirements

- Performance: cached download < 500ms; cold start handled with UI feedback
- Responsiveness: 320px mobile to 1920px desktop
- Accessibility: WCAG AA contrast; keyboard navigable
- Privacy: no PDFs stored server-side; only metadata in RTDB
- Cost: must operate within free tiers indefinitely

## 8. User Flow

Landing page → paste link → validate (invalid shows error) → check IndexedDB →
hit: instant download; miss: POST /api/download → Render builds PDF (RTDB
status updates) → return blob → save to IndexedDB → trigger download + add to
history.

## 9. UI/UX Requirements

Screens:
1. Main page — input, button, status area, saved list
2. Saved list — cards with re-download + delete

Visual style: dark gradient theme, glassmorphism card, accent gradient (indigo → sky).
States: Idle, Loading (spinner), Success, Error, Empty-list.

## 10. Out of Scope (v1)

- User accounts / login
- Cloud storage of PDFs
- Batch download
- OCR / search
- Non-PDF files
- Mobile native app

## 11. Success Metrics

- User can complete download in < 3 interactions
- Cached re-download avoids 100% of backend calls
- Zero monthly hosting cost maintained
