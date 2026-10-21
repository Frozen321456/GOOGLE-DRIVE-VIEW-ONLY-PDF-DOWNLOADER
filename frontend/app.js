/* ============================================================
   app.js — UI wiring + download-flow orchestration
   Status flow: idle → validating → checking-cache → fetching →
                rendering → saving → done | error
   ============================================================ */

import { savePDF, getPDF, listPDFs, deletePDF } from './db.js';
import { initFirebase, subscribeStatus, BACKEND_URL } from './firebase-config.js';

/* ---------------- DOM refs ---------------- */
const $ = (sel) => document.querySelector(sel);
const form = $('#fetch-form');
const input = $('#drive-input');
const downloadBtn = $('#download-btn');
const btnLabel = downloadBtn.querySelector('.btn-label');
const btnSpinner = downloadBtn.querySelector('.btn-spinner');
const inputError = $('#input-error');
const statusArea = $('#status-area');
const statusText = $('#status-text');
const statusSpinner = $('#status-spinner');
const progressBar = $('#progress-bar');
const progressTrack = progressBar.parentElement;
const statusActions = $('#status-actions');
const savedListEl = $('#saved-list');
const savedEmptyEl = $('#saved-empty');
const savedCountEl = $('#saved-count');
const logBox = $('#log-box');
const clearLogBtn = $('#clear-log');

/* ---------------- state ---------------- */
let busy = false;
let unsubscribeStatus = null;

/* ---------------- logging ---------------- */
function log(msg, level = 'info') {
  const line = document.createElement('p');
  line.className = `log-line log-line--${level}`;
  const time = document.createElement('span');
  time.className = 'log-line--time';
  const now = new Date();
  time.textContent = `[${now.toLocaleTimeString()}] `;
  line.appendChild(time);
  line.appendChild(document.createTextNode(msg));
  logBox.appendChild(line);
  logBox.scrollTop = logBox.scrollHeight;
}

/* ---------------- helpers ---------------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function setStatus(text, { progress = null, state = 'busy' } = {}) {
  statusArea.hidden = false;
  statusText.textContent = text;
  if (progress !== null) {
    progressTrack.setAttribute('aria-valuenow', String(Math.round(progress)));
    progressBar.style.width = `${progress}%`;
  }
  statusSpinner.hidden = state === 'done' || state === 'error';
  statusSpinner.className = `spinner${state === 'done' ? ' spinner--done' : ''}${state === 'error' ? ' spinner--error' : ''}`;
}

function setBusy(on) {
  busy = on;
  downloadBtn.disabled = on;
  input.disabled = on;
  if (on) {
    btnLabel.textContent = 'Working…';
    btnSpinner.hidden = false;
  } else {
    btnLabel.textContent = 'Download PDF';
    btnSpinner.hidden = true;
  }
}

/** triggerBlobDownload(blob, fileName) — programmatic browser download. */
function triggerBlobDownload(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function formatBytes(n) {
  if (!Number.isFinite(n)) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function formatWhen(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleString();
}

function isAbort(err) {
  return err && (err.name === 'AbortError' || err.name === 'TimeoutError');
}

/** Friendly message from a fetch/HTTP error. */
function errorToMessage(err, httpCode, bodyCode) {
  if (httpCode === 429) return 'Too many requests — wait a minute and retry.';
  if (httpCode === 403) return 'This file is not accessible (private or deleted).';
  if (httpCode === 504) return 'Server took too long (cold start can take ~60s). Please retry.';
  if (bodyCode === 'INVALID_URL') return 'Please paste a valid Drive link.';
  if (bodyCode === 'NOT_ACCESSIBLE') return 'This file is not accessible (private or deleted).';
  if (bodyCode === 'RENDER_FAILED') return 'Failed to render PDF — try again.';
  if (isAbort(err)) return 'Server took too long (cold start can take ~60s). Please retry.';
  if (err instanceof TypeError) return 'Network error — check connection and retry.';
  return (err && err.message) || 'Something went wrong — try again.';
}

/* ---------------- validation ---------------- */
/** extractFileId(input) → string | null */
export function extractFileId(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  if (!s) return null;
  if (s.length < 20 || s.length > 128) return null;

  // URL forms: /file/d/{id}/, /open?id=, /uc?id=, Docs editors, or raw ID
  const patterns = [
    /drive\.google\.com\/file\/d\/([A-Za-z0-9_-]{20,})/,
    /drive\.google\.com\/(?:open|uc|viewer)\?(?:[^#]*&)?id=([A-Za-z0-9_-]{20,})/,
    /docs\.google\.com\/(?:document|spreadsheets|presentation|drawings)\/d\/([A-Za-z0-9_-]{20,})/,
    /^([A-Za-z0-9_-]{25,})$/, // raw file ID (captured for m[1])
  ];
  for (const re of patterns) {
    const m = s.match(re);
    if (m) return m[1];
  }
  return null;
}

/* ---------------- Firebase status (optional) ---------------- */
/** watchStatus(fileId, onUpdate) → unsubscribe */
async function watchStatus(fileId, onUpdate) {
  try { await initFirebase(); } catch { /* ignore */ }
  const unsub = subscribeStatus(fileId, (status) => {
    try { onUpdate(status); } catch { /* UI errors must not break flow */ }
  });
  unsubscribeStatus = unsub;
  return unsub;
}

function stopWatching() {
  if (typeof unsubscribeStatus === 'function') {
    try { unsubscribeStatus(); } catch { /* noop */ }
  }
  unsubscribeStatus = null;
}

/* ---------------- render path ---------------- */
/**
 * Request the PDF from the Render backend. Returns { blob, fileName }.
 * Thrown errors always carry .userMessage.
 */
async function fetchFromBackend(fileId, { signal }) {
  const base = (BACKEND_URL || '').trim();
  const usable = base && !base.includes('VITE_BACKEND_URL');
  const url = usable
    ? `${base.replace(/\/+$/, '')}/api/download`
    : '/api/download'; // same-origin fallback (dev proxy / Vercel rewrite)

  let resp;
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: `https://drive.google.com/file/d/${fileId}/view` }),
      signal,
    });
  } catch (err) {
    if (isAbort(err)) {
      const e = new Error('timeout');
      e.userMessage = 'Server took too long (cold start can take ~60s). Please retry.';
      throw e;
    }
    const e = new TypeError('network');
    e.userMessage = 'Network error — check connection and retry.';
    throw e;
  }

  const ctype = resp.headers.get('content-type') || '';
  if (!resp.ok || ctype.includes('application/json')) {
    let bodyCode = null;
    let detail = null;
    try {
      const body = await resp.json();
      bodyCode = body && body.code;
      detail = body && body.error;
    } catch { /* non-JSON error body */ }
    const e = new Error(detail || `HTTP ${resp.status}`);
    e.httpCode = resp.status;
    e.bodyCode = bodyCode;
    e.userMessage = errorToMessage(null, resp.status, bodyCode);
    throw e;
  }

  const blob = await resp.blob();
  const disp = resp.headers.get('content-disposition') || '';
  const m = disp.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
  const fileName = m ? decodeURIComponent(m[1].replace(/"/g, '')) : `${fileId}.pdf`;
  return { blob, fileName };
}

/* ---------------- main flow ---------------- */
/** handleDownload() — orchestrates the full download flow. */
async function handleDownload() {
  if (busy) return;
  const raw = input.value;

  // validating
  const fileId = extractFileId(raw);
  if (!fileId) {
    showInputError('Please paste a valid Drive link or file ID');
    return;
  }
  clearInputError();

  setBusy(true);
  statusActions.hidden = true;
  try {
    // checking-cache
    setStatus('Checking local cache…', { progress: 10 });
    let rec;
    try {
      rec = await getPDF(fileId);
    } catch (err) {
      log(`IndexedDB read failed: ${err.message}`, 'warn');
    }

    if (rec && rec.blob) {
      // cache hit — instant, no backend call
      triggerBlobDownload(rec.blob, rec.fileName);
      log(`Cache hit → ${rec.fileName} (${formatBytes(rec.size)})`, 'success');
      setStatus('Done — downloaded from cache (no backend call).', { progress: 100, state: 'done' });
      return;
    }

    // cache miss → backend
    setStatus('Not cached — contacting render server…', { progress: 20 });
    await watchStatus(fileId, (status) => {
      const map = {
        queued: 'Queued on server…',
        processing: 'Capturing all pages on server…',
        completed: 'Server finished rendering…',
        failed: 'Server reported failure.',
      };
      if (map[status]) setStatus(map[status], { progress: status === 'completed' ? 90 : 55 });
    });

    setStatus('Fetching from server…', { progress: 30 });
    const { blob, fileName } = await fetchFromBackend(fileId, { signal: AbortSignal.timeout(180000) });

    // saving
    setStatus('Saving to browser storage…', { progress: 95 });
    try {
      await savePDF(fileId, fileName, blob);
      log(`Saved to cache: ${fileName} (${formatBytes(blob.size)})`, 'success');
    } catch (err) {
      log(`Could not cache: ${err.message}`, 'warn');
    }

    // done
    setStatus('Done — PDF ready.', { progress: 100, state: 'done' });
    triggerBlobDownload(blob, fileName);
    log(`Downloaded ${fileName} (${formatBytes(blob.size)})`, 'success');
    await renderSavedList();
  } catch (err) {
    stopWatching();
    setStatus(err.userMessage || errorToMessage(err), { progress: 100, state: 'error' });
    log(err.userMessage || err.message || 'Download failed', 'error');
    showRetry();
  } finally {
    stopWatching();
    setBusy(false);
  }
}

function showRetry() {
  statusActions.hidden = false;
  statusActions.innerHTML = '';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn btn-ghost btn-sm';
  btn.textContent = 'Retry';
  btn.addEventListener('click', () => {
    statusActions.hidden = true;
    handleDownload();
  });
  statusActions.appendChild(btn);
}

function showInputError(msg) {
  inputError.textContent = msg;
  inputError.hidden = false;
  input.setAttribute('aria-invalid', 'true');
  input.focus();
}

function clearInputError() {
  inputError.hidden = true;
  input.removeAttribute('aria-invalid');
}

/* ---------------- saved list ---------------- */
async function renderSavedList() {
  let rows = [];
  try {
    rows = await listPDFs();
  } catch (err) {
    log(`Could not read cache: ${err.message}`, 'warn');
    return;
  }

  savedListEl.innerHTML = '';
  savedCountEl.hidden = rows.length === 0;
  savedCountEl.textContent = String(rows.length);
  savedEmptyEl.hidden = rows.length > 0;

  for (const rec of rows) {
    const li = document.createElement('li');
    li.className = 'saved-item';

    const info = document.createElement('div');
    info.className = 'saved-info';

    const name = document.createElement('span');
    name.className = 'saved-name';
    name.textContent = rec.fileName;
    name.title = rec.fileName;

    const meta = document.createElement('span');
    meta.className = 'saved-meta';
    meta.textContent = `${formatBytes(rec.size)} · ${formatWhen(rec.savedAt)} · ${rec.id.slice(0, 12)}…`;

    info.append(name, meta);

    const actions = document.createElement('div');
    actions.className = 'saved-actions';

    const dl = document.createElement('button');
    dl.type = 'button';
    dl.className = 'btn btn-ghost btn-sm';
    dl.textContent = 'Re-download';
    dl.addEventListener('click', () => {
      if (rec.blob instanceof Blob) {
        triggerBlobDownload(rec.blob, rec.fileName);
        log(`Re-downloaded ${rec.fileName} from cache.`, 'success');
      } else {
        log('Stored record is missing its blob.', 'error');
      }
    });

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn btn-danger btn-sm';
    del.textContent = 'Delete';
    del.addEventListener('click', async () => {
      try {
        await deletePDF(rec.id);
        log(`Deleted ${rec.fileName} from cache.`, 'info');
        renderSavedList();
      } catch (err) {
        log(`Delete failed: ${err.message}`, 'error');
      }
    });

    actions.append(dl, del);
    li.append(info, actions);
    savedListEl.appendChild(li);
  }
}

/* ---------------- events ---------------- */
form.addEventListener('submit', (e) => {
  e.preventDefault();
  handleDownload();
});

clearLogBtn.addEventListener('click', () => { logBox.innerHTML = ''; });

input.addEventListener('input', clearInputError);

/* ---------------- boot ---------------- */
initFirebase().catch(() => {});
renderSavedList();
log('Ready. Paste a Drive link to begin.', 'info');
