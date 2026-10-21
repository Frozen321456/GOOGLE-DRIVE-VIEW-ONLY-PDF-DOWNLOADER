# Deploy — Step by Step (Vercel + Render + Firebase)

One-time setup, ~15 minutes. All free tiers. Do these in order: Firebase
first (backend needs its values), then Render, then Vercel.

---

## Part 1 — Firebase (Realtime Database + service account)

1. Go to <https://console.firebase.google.com> → **Add project**.
   - Name: `gdrive-pdf-fetcher` (any name works)
   - Google Analytics: disable (not needed) → **Create project**.

2. **Create the Realtime Database:**
   - Left sidebar → **Build → Realtime Database** → **Create Database**.
   - Location: any (e.g. `us-central1`).
   - Start in **locked mode** (we paste real rules next).

3. **Paste the security rules:**
   - **Rules** tab → replace everything with the contents of
     [`firebase-database.rules.json`](../firebase-database.rules.json) → **Publish**.
   - Result: anyone can READ `/pdfs/{id}/status`; nobody can write via the
     web SDK. Only the backend (Admin SDK) writes.

4. **Get the web app config (for Vercel):**
   - ⚙ Project settings → **General** → scroll to **Your apps** →
     **Web app** `</>` icon → register app (no hosting needed).
   - Copy the `firebaseConfig` values shown — you need:
     `apiKey`, `authDomain`, `databaseURL`, `projectId`, `appId`.

5. **Create the service account (for Render):**
   - ⚙ Project settings → **Service accounts**.
   - Choose **Database secrets admin** (or Firebase Admin SDK +
     "Generate new private key" if preferred) → **Generate new private key**.
   - A JSON file downloads. Keep it private.
   - Base64-encode it:
     - Termux/Linux: `base64 -w0 service-account.json > sa.b64`
     - macOS: `base64 -i service-account.json -o sa.b64`
   - Copy the contents of `sa.b64` — that whole string is
     `FIREBASE_CREDENTIALS_JSON`. Delete the files after pasting.

---

## Part 2 — Render (backend)

1. Go to <https://dashboard.render.com> → **New + → Web Service**.
2. **Connect** the repo
   `Frozen321456/GOOGLE-DRIVE-VIEW-ONLY-PDF-DOWNLOADER`
   (grant GitHub access if asked).
3. Settings:
   - **Name:** `gdrive-pdf-fetcher-api` (or any)
   - **Root Directory:** `backend`
   - **Runtime:** `Docker` (auto-detected from the Dockerfile)
   - **Instance Type:** `Free`
4. **Environment variables** (Add each):

   | Key | Value |
   |---|---|
   | `FIREBASE_CREDENTIALS_JSON` | the base64 string from Part 1.5 |
   | `FIREBASE_DATABASE_URL` | `https://YOURPROJ-default-rtdb.firebaseio.com` (from 1.4) |
   | `ALLOWED_ORIGINS` | `*` for now — change after Part 3 |

5. **Create Web Service** → wait for build (~5-8 min first time).
6. Note your URL: `https://YOUR-BACKEND.onrender.com`.
   Test: open `https://YOUR-BACKEND.onrender.com/api/health` in a browser →
   should return `{"status":"ok","timestamp":...}`.

---

## Part 3 — Vercel (frontend)

1. Go to <https://vercel.com/new>.
2. **Import** the same GitHub repo.
3. Configure:
   - **Framework Preset:** `Other`
   - **Root Directory:** `frontend`
   - (Build command comes from `vercel.json` → `node scripts/gen-env.mjs`)
4. **Environment Variables** (add all six; values from Part 1.4):

   | Key | Value |
   |---|---|
   | `VITE_FIREBASE_API_KEY` | from Firebase web config |
   | `VITE_FIREBASE_AUTH_DOMAIN` | e.g. `gdrive-pdf-fetcher.firebaseapp.com` |
   | `VITE_FIREBASE_DATABASE_URL` | `https://YOURPROJ-default-rtdb.firebaseio.com` |
   | `VITE_FIREBASE_PROJECT_ID` | e.g. `gdrive-pdf-fetcher` |
   | `VITE_FIREBASE_APP_ID` | from Firebase web config |
   | `VITE_BACKEND_URL` | `https://YOUR-BACKEND.onrender.com` (from Part 2.6) |

5. **Deploy** → wait ~30s → note your URL: `https://YOUR-APP.vercel.app`.

---

## Part 4 — Post-deploy wiring (2 minutes)

1. Render dashboard → your service → **Environment** → edit
   `ALLOWED_ORIGINS` → set to `https://YOUR-APP.vercel.app` → **Save**.
   (It redeploys automatically.)
2. Vercel: no action needed if `VITE_BACKEND_URL` was already correct.
3. End-to-end test:
   - Open `https://YOUR-APP.vercel.app`
   - Paste a view-only Drive PDF link → Download.
   - Expected: progress bar → "Rendering PDF on server…" → download starts
     → file appears in "Saved PDFs".
   - Re-download: second click serves instantly from IndexedDB
     (server stays asleep — check the Render dashboard stays idle).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| First download takes ~60s | Render free tier cold start | Normal — UI shows "waking server". Keep-alive pinger optional. |
| `NOT_ACCESSIBLE` on a shared link | File really is private/deleted, or Drive bot-wall | Test the link in an incognito browser. If it asks to sign in, it won't work. |
| CORS error in console | `ALLOWED_ORIGINS` mismatch | Must match your Vercel domain exactly (https, no trailing slash). |
| Status pill stuck on "queued" | RTDB rules too strict | Re-publish `firebase-database.rules.json`. |
| Playwright render fails | Free-tier OOM on huge files | 100MB guard exists; try a smaller PDF. |
