# TRD — GDrive PDF Fetcher

## 1. System Architecture

User browser (frontend on Vercel; Firebase JS SDK; IndexedDB)
  -> POST /api/download  -> Render backend (Flask + Playwright; builds PDF,
     writes RTDB status, returns blob)
  -> onValue(status)     -> Firebase RTDB /pdfs/{id}/status (metadata only)

## 2. Technology Stack

| Layer | Tech | Free tier |
|---|---|---|
| Frontend hosting | Vercel Hobby | 100GB bandwidth/mo |
| Frontend | Vanilla HTML/CSS/JS (ES modules) | - |
| Backend hosting | Render Free (Docker) | 750 hrs/mo |
| Backend runtime | Python 3.11 + Flask + Gunicorn | - |
| Browser automation | Playwright (Chromium) | - |
| Real-time DB | Firebase RTDB (Spark) | 1GB storage, 10GB DL/mo, 100 conns |
| Local storage | IndexedDB | browser quota |
| Auth (v1) | None | - |

## 3. Repository Structure

gdrive-pdf-fetcher/
  frontend/   index.html, style.css, app.js, firebase-config.js, db.js,
              vercel.json, .env.example
  backend/    app.py, downloader.py, firebase_client.py, requirements.txt,
              Dockerfile, .env.example
  docs/       PRD.md, TRD.md
  firebase-database.rules.json
  README.md

## 4. Data Models

### 4.1 Firebase RTDB Schema

{
  "pdfs": {
    "{fileId}": {
      "status": "queued | processing | completed | failed",
      "fileName": "document.pdf",
      "size": 123456,
      "error": null,
      "requestedAt": 1700000000000,
      "completedAt": 1700000015000
    }
  }
}

Rules (firebase-database.rules.json): public read on /pdfs/$fileId, no
public writes. Backend uses Admin SDK (bypasses rules). Frontend only reads.

### 4.2 IndexedDB Schema

DB `gdrive_fetcher` v1, object store `pdfs` (keyPath `id`):
{ id, fileName, blob: Blob, size, savedAt }

## 5. API Specification

### POST /api/download

Request: { "url": "https://drive.google.com/file/d/FILE_ID/view" }
(also accepts { "fileId": "..." })

Response 200 (binary):
  Content-Type: application/pdf
  Content-Disposition: attachment; filename="document.pdf"

Errors: { "error": "message", "code": "INVALID_URL | NOT_ACCESSIBLE |
RENDER_FAILED" } with matching 4xx/5xx status.

### GET /api/health

{ "status": "ok", "timestamp": 1700000000000 } — used for keep-alive.

## 6. Frontend Specification

Modules: app.js (orchestration), db.js (IndexedDB CRUD), firebase-config.js
(Firebase init + RTDB subscribe), style.css (dark glassmorphism theme).

Key contracts:
- db.js: savePDF(id, fileName, blob); getPDF(id); listPDFs(); deletePDF(id)
- app.js: extractFileId(input); handleDownload(); triggerBlobDownload(blob,
  fileName); watchStatus(fileId, onUpdate)
- firebase-config.js: initFirebase(); subscribeStatus(fileId, cb)

Config: placeholder values are replaced at deploy time via Vercel env vars /
window.__ENV__ injection. Without Firebase configured the app degrades
gracefully (no live status, everything else works).

## 7. Backend Specification

app.py: CORS whitelist, ID validation, RTDB queued/processing writes,
Playwright build, completed/failed writes, blob response. Never writes PDFs
to disk.

downloader.py: build_pdf(file_id) -> (bytes, filename). Tier 1 direct
HTTP endpoints (uc?export=download / view); Tier 2 headless Chromium over
the /preview page with print-to-PDF. Permission-wall detection raises
NOT_ACCESSIBLE.

requirements.txt / Dockerfile per repo. Gunicorn: --workers 1 --threads 4,
--timeout 120 (free tier: 512MB RAM, one Chromium).

## 8. Security

| Concern | Mitigation |
|---|---|
| CORS | whitelist Vercel domain (ALLOWED_ORIGINS) |
| Rate limiting | Flask-Limiter 10 req/min per IP on /api/download |
| RTDB writes | Admin SDK only; public rules are read-only |
| Secrets | env vars only; .env never committed |
| Input validation | regex-extract + format-check Drive ID before Playwright |
| SSRF | only drive.google.com URLs are constructed server-side |

## 9. Deployment Pipeline

Vercel (frontend): import repo, root=frontend/, preset=Other, set env vars
(VITE_*, see frontend/.env.example), deploy.

Render (backend): new Web Service, root=backend/, runtime=Docker, instance
Free, env vars ALLOWED_ORIGINS + FIREBASE_CREDENTIALS_JSON +
FIREBASE_DATABASE_URL, deploy.

Firebase: create project, enable Realtime Database (test mode initially),
paste firebase-database.rules.json, create service account (RTDB Admin),
base64 the JSON into FIREBASE_CREDENTIALS_JSON, copy databaseURL into
Vercel env.

Post-deploy wiring: set ALLOWED_ORIGINS to the Vercel URL and redeploy
Render; set VITE_BACKEND_URL to the Render URL and redeploy Vercel.

## 10. Error Codes

| Code | HTTP | Meaning | UI message |
|---|---|---|---|
| INVALID_URL | 400 | Bad Drive link | Please paste a valid Drive link |
| NOT_ACCESSIBLE | 403 | File private/deleted | This file is not accessible |
| RENDER_FAILED | 500 | Playwright error | Failed to render PDF, try again |
| TIMEOUT | 504 | 120s exceeded | Server took too long, retry |
| RATE_LIMITED | 429 | Too many requests | Too many requests, wait 1 min |

## 11. Testing Strategy

- Unit (frontend): extractFileId + IndexedDB helpers (Vitest or node --test)
- Unit (backend): URL validation, RTDB writes (mocked), error codes (pytest)
- Integration (manual): full flow on sample Drive PDFs
- E2E (Playwright Test): browser flow with 3 sample links
- Load (k6, local): 20 concurrent requests — expect free-tier throttling

## 12. Known Limitations

1. Render cold start: after 15 min idle the first request can take ~60s.
   UI shows a waking-server message; a keep-alive pinger is optional.
2. 512MB RAM: very large PDFs may crash the container; 100MB guard.
3. No persistent server storage: every fetch re-renders unless cached
   in the browser.
4. Per-browser cache: IndexedDB is not shared across devices.
5. No auth: public app; add Firebase Auth in v2.
6. Legal: only works on files the user has view access to. Respect copyright.

## 13. Build Phases (as executed)

1. Scaffold monorepo  2. Frontend static UI  3. IndexedDB layer
4. Frontend logic     5. Backend skeleton   6. Playwright integration
7. Firebase RTDB      8. Deploy (manual, console-driven)
9. Harden (rate limit, validation, error codes)
