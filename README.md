# GDrive PDF Fetcher

Download Google Drive **view-only** PDFs by rendering the preview page and
rebuilding it as a downloadable PDF. Free forever — Vercel + Render + Firebase
Spark free tiers. PDFs are stored only in your browser (IndexedDB), never on
a server.

> Legal: only works on files you already have view access to. Respect
> copyright — for personal offline use.

## How it works

1. Paste a Drive link (or raw file ID) and hit **Download PDF**.
2. The app checks your local cache (IndexedDB) first — cache hits download
   instantly with zero backend calls.
3. On a cache miss, the Render backend tries direct Drive endpoints first,
   then falls back to headless Chromium: it opens the Drive preview page and
   prints it back out as a real PDF.
4. The blob is saved to your browser and downloaded. Metadata (status only)
   goes to Firebase RTDB so the UI can show live progress.

## Repo layout

```
gdrive-pdf-fetcher/
├── frontend/            # static site → Vercel
│   ├── index.html       # UI (dark glassmorphism theme)
│   ├── style.css
│   ├── app.js           # download flow orchestration
│   ├── db.js            # IndexedDB helpers
│   ├── firebase-config.js
│   └── vercel.json
├── backend/             # Flask + Playwright → Render (Docker)
│   ├── app.py           # /api/download, /api/health
│   ├── downloader.py    # build_pdf(): direct HTTP → Chromium print
│   ├── firebase_client.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── cli/                 # original CLI tool (preserved, see below)
│   ├── app.py           # interactive CLI entry
│   ├── downloader.py    # crawler + headless capture engine
│   ├── requirements.txt
│   └── static/index.html
├── docs/                # PRD + TRD (build spec)
└── firebase-database.rules.json
```

## Setup

> **Deploying now?** Follow [docs/DEPLOY.md](docs/DEPLOY.md) — a click-by-click
> walkthrough (Firebase → Render → Vercel, with every field, env var and
> troubleshooting row).

### 1. Firebase (RTDB status sync — optional but recommended)

1. console.firebase.google.com → create project.
2. Build → Realtime Database → Create (test mode).
3. Rules → paste `firebase-database.rules.json` (public read, no public write).
4. Project settings → Service accounts → Generate new private key
   (Database secrets admin) → base64-encode the JSON file:
   `base64 -w0 service-account.json` (Linux) or
   `base64 -i service-account.json` (macOS).
5. Note the databaseURL (`https://PROJECT-default-rtdb.firebaseio.com`).

### 2. Backend on Render

1. render.com → New → Web Service → connect this repo → Root directory:
   `backend` → Runtime: Docker → Instance: Free.
2. Env vars:
   - `ALLOWED_ORIGINS` = your Vercel URL (set after step 3)
   - `FIREBASE_CREDENTIALS_JSON` = the base64 string from step 1
   - `FIREBASE_DATABASE_URL` = your databaseURL
3. Deploy → note `https://YOUR-BACKEND.onrender.com`.

### 3. Frontend on Vercel

1. vercel.com → Add New → Project → import this repo → Root directory:
   `frontend` → Framework preset: Other.
2. Env vars (values from Firebase console):
   - `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`,
     `VITE_FIREBASE_DATABASE_URL`, `VITE_FIREBASE_PROJECT_ID`,
     `VITE_FIREBASE_APP_ID`
   - `VITE_BACKEND_URL` = `https://YOUR-BACKEND.onrender.com`
3. Deploy → note `https://YOUR-APP.vercel.app`.

### 4. Post-deploy wiring

- Render: set `ALLOWED_ORIGINS` to your Vercel URL → redeploy.
- Vercel: `VITE_BACKEND_URL` must already point at Render → redeploy.

## Local development

Frontend (any static server):

```bash
cd frontend
python3 -m http.server 5173
# open http://localhost:5173
```

Backend (needs Python 3.11 + Playwright Chromium):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
cp .env.example .env   # fill values (or leave empty to skip RTDB sync)
python app.py          # http://localhost:5000/api/health
```

## API

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/api/download` | POST | `{ "url": "<drive url>" }` or `{ "fileId": "<id>" }` | `application/pdf` blob (sync — small docs) |
| `/api/jobs` | POST | same body | `202 { "jobId", "status" }` — async render, use for any size |
| `/api/jobs/:id` | GET | — | `{ "status": "queued\|processing\|completed\|failed", "fileName?", "size?", "error?" }` |
| `/api/jobs/:id/result` | GET | — | `application/pdf` when ready (409 while rendering) |
| `/api/health` | GET | — | `{ "status": "ok", "timestamp": ... }` |

### Large documents

Rendering runs in a background thread after `POST /api/jobs` returns, so no
proxy timeout applies — a 500-page scan just takes longer, with the UI
showing live progress while polling. Identical in-flight requests are
deduplicated (second POST returns the same jobId), and finished results stay
fetchable for 15 minutes.

### How capture works

Drive's viewer renders one page at a time, so `page.pdf()` prints a single
page. Instead, the engine opens `/view`, scrolls the whole document to
lazy-load every page image, intercepts those images from the network layer,
groups them by `page=` parameter, and reassembles them with img2pdf — one
clean PDF, full document, correct order.

Error JSON: `{ "error": "message", "code": "INVALID_URL" |
"NOT_ACCESSIBLE" | "RENDER_FAILED" }`.

## Known limitations

- **Render cold start:** after ~15 min idle the first request takes ~60s.
  The UI shows a "waking server" state; ping `/api/health` on a schedule to
  keep it warm (optional; watch free tier hours).
- **512MB RAM:** very large PDFs may fail; there is a 100MB response guard.
- **No server-side persistence:** every uncached fetch re-renders.
- **Cache is per-browser:** IndexedDB does not sync across devices.
- **No auth (v1):** the app is public; add Firebase Auth in v2.
- **View-only means view-only-ish:** if the owner revokes access or the file
  is DRM-protected, this will fail with NOT_ACCESSIBLE.

## CLI tool (v0, preserved)

Before this web app, this repo shipped a Python CLI that scrapes educational
sites for Drive PDF links and captures view-only PDFs via headless Chromium
(scroll-capture → img2pdf). It still works standalone:

```bash
cd cli
pip install -r requirements.txt && playwright install chromium
python app.py
```

See [cli/README.md](cli/README.md) for its full guide. The web app in
`frontend/` + `backend/` is the successor (single-link web flow, browser
caching, deployable free-tier stack); the CLI keeps its bulk-crawler niche.

## Docs

- [docs/PRD.md](docs/PRD.md) — product requirements
- [docs/TRD.md](docs/TRD.md) — technical spec, deployment pipeline, error codes
