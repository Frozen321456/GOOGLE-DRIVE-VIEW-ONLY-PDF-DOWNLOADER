/* ============================================================
   app.js — pdfrail UI wiring + download-flow orchestration

   Flow: validate → cache check → POST /api/jobs → poll status →
   fetch result → save to IndexedDB → trigger download.
   The job API returns 202 immediately, so huge documents never hit
   the proxy timeout — render continues server-side while we poll.
   ============================================================ */

import { savePDF, getPDF, listPDFs, deletePDF } from './db.js';
import { initFirebase, subscribeStatus, BACKEND_URL } from './firebase-config.js';

/* ---------------- backend base ---------------- */
const BASE = (() => {
  const b = (BACKEND_URL || '').trim();
  if (b && !b.includes('VITE_BACKEND_URL')) return b.replace(/\/+$/, '');
  return ''; // same-origin fallback
})();

/* ---------------- DOM refs ---------------- */
const $ = (sel) => document.querySelector(sel);
const form = $('#fetch-form');
const input = $('#drive-input');
const downloadBtn = $('#download-btn');
const inputError = $('#input-error');
const inputWrap = document.querySelector('.input-wrap');
const statusArea = $('#status-area');
const stageLabel = $('#stage-label');
const stageDot = $('#stage-dot');
const pctLabel = $('#pct-label');
const barFill = $('#bar-fill');
const stepsEl = $('#steps');
const statusActions = $('#status-actions');
const logBox = $('#log-box');
const clearLogBtn = $('#clear-log');
const savedListEl = $('#saved-list');
const savedEmptyEl = $('#saved-empty');
const savedCountEl = $('#saved-count');
const healthDot = $('#health-dot');
const healthLabel = $('#health-label');

/* ---------------- state ---------------- */
let busy = false;
let activeJobId = null;
let pollTimer = null;
let unsubscribeStatus = null;
let pollElapsed = 0;

const STEP_ORDER = ['validate', 'cache', 'queue', 'render', 'output'];

/* ---------------- logging ---------------- */
function log(msgText, level = 'info') {
  const line = document.createElement('div');
  line.className = 'line';
  const t = document.createElement('span');
  t.className = 't';
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');
  t.textContent = `${hh}:${mm}:${ss}`;
  const lvl = document.createElement('span');
  lvl.className = `lvl ${level === 'success' ? 'ok' : level}`;
  lvl.textContent = { info: 'INFO', success: 'OK', warn: 'WARN', error: 'ERR' }[level] || 'INFO';
  const msg = document.createElement('span');
  msg.className = 'msg';
  msg.textContent = msgText;
  line.append(t, lvl, msg);
  logBox.appendChild(line);
  logBox.scrollTop = logBox.scrollHeight;
}

/* ---------------- helpers ---------------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function setProgress(pct) {
  pctLabel.textContent = pct == null ? '' : `${Math.round(pct)}%`;
  if (pct != null) barFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function setStage(text, pct, state = 'busy') {
  statusArea.hidden = false;
  stageLabel.textContent = text;
  stageDot.className = `stage-dot${state === 'error' ? ' is-error' : ''}${state === 'done' ? ' is-done' : ''}`;
  barFill.className = `bar-fill${state === 'error' ? ' is-error' : ''}${state === 'done' ? ' is-done' : ''}`;
  if (!state || state === 'busy') {
    barFill.classList.add('bar-fill'); // keep sheen
  }
  setProgress(pct);
}

function setSteps(activeIdx, failedAt = null) {
  [...stepsEl.children].forEach((li, i) => {
    li.classList.remove('done', 'active', 'failed');
    if (failedAt != null && i === failedAt) li.classList.add('failed');
    else if (i < activeIdx) li.classList.add('done');
    else if (i === activeIdx) li.classList.add('active');
  });
}

function setBusy(on) {
  busy = on;
  downloadBtn.disabled = on;
  input.disabled = on;
  downloadBtn.innerHTML = on ? 'Working…' : 'Fetch <span class="arrow">→</span>';
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

/** Friendly message from a fetch/HTTP error. */
function errorToMessage(httpCode, bodyCode, fallback) {
  if (httpCode === 429) return 'Too many requests — wait a minute and retry.';
  if (httpCode === 403) return 'This file is not accessible (private or deleted).';
  if (httpCode === 502 || httpCode === 504) return 'Engine is waking up (free tier cold start). Retrying is free.';
  if (bodyCode === 'INVALID_URL') return 'Please paste a valid Drive link.';
  if (bodyCode === 'NOT_ACCESSIBLE') return 'This file is not accessible (private or deleted).';
  if (bodyCode === 'RENDER_FAILED') return 'Failed to capture the document — try again.';
  if (bodyCode === 'NOT_FOUND') return 'Job expired on the server — fetch again.';
  if (bodyCode === 'NOT_READY') return 'Still rendering…';
  return fallback || 'Something went wrong — try again.';
}

/* ---------------- validation ---------------- */
/** extractFileId(input) → string | null */
export function extractFileId(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  if (!s) return null;
  if (s.length < 20 || s.length > 128) return null;

  const patterns = [
    /drive\.google\.com\/file\/d\/([A-Za-z0-9_-]{20,})/,
    /drive\.google\.com\/(?:open|uc|viewer)\?(?:[^#]*&)?id=([A-Za-z0-9_-]{20,})/,
    /docs\.google\.com\/(?:document|spreadsheets|presentation|drawings)\/d\/([A-Za-z0-9_-]{20,})/,
    /^([A-Za-z0-9_-]{25,})$/,
  ];
  for (const re of patterns) {
    const m = s.match(re);
    if (m) return m[1];
  }
  return null;
}

/* ---------------- Firebase status (optional) ---------------- */
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

/* ---------------- job API ---------------- */
function jobUrl(path) {
  return BASE ? `${BASE}${path}` : path;
}

/** POST /api/jobs → { jobId } */
async function createJob(fileId) {
  let resp;
  try {
    resp = await fetch(jobUrl('/api/jobs'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: `https://drive.google.com/file/d/${fileId}/view` }),
    });
  } catch (err) {
    if (err instanceof TypeError) {
      const e = new Error('network');
      e.userMessage = 'Network error — check connection and retry.';
      throw e;
    }
    throw err;
  }

  const ctype = resp.headers.get('content-type') || '';
  if (ctype.includes('application/json')) {
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const e = new Error(body.error || `HTTP ${resp.status}`);
      e.httpCode = resp.status;
      e.bodyCode = body.code;
      e.userMessage = errorToMessage(resp.status, body.code, body.error);
      throw e;
    }
    return body; // { jobId, status, reused? }
  }
  const e = new Error(`Unexpected response (${resp.status})`);
  e.userMessage = errorToMessage(resp.status, null, null);
  throw e;
}

/** GET /api/jobs/:id → status body */
async function getJob(jobId) {
  const resp = await fetch(jobUrl(`/api/jobs/${jobId}`));
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const e = new Error(body.error || `HTTP ${resp.status}`);
    e.httpCode = resp.status;
    e.bodyCode = body.code;
    e.userMessage = errorToMessage(resp.status, body.code, body.error);
    throw e;
  }
  return body;
}

/** GET /api/jobs/:id/result → { blob, fileName } */
async function getJobResult(jobId) {
  const resp = await fetch(jobUrl(`/api/jobs/${jobId}/result`));
  if (!resp.ok) {
    let bodyCode = null; let detail = null;
    try {
      const body = await resp.json();
      bodyCode = body.code; detail = body.error;
    } catch { /* ignore */ }
    const e = new Error(detail || `HTTP ${resp.status}`);
    e.httpCode = resp.status;
    e.bodyCode = bodyCode;
    e.userMessage = errorToMessage(resp.status, bodyCode, detail);
    throw e;
  }
  const blob = await resp.blob();
  const disp = resp.headers.get('content-disposition') || '';
  const m = disp.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
  const fileName = m ? decodeURIComponent(m[1].replace(/"/g, '')) : `${jobId}.pdf`;
  return { blob, fileName };
}

/** Poll job until terminal state. Resolves with final status body. */
async function pollJob(jobId, onTick) {
  const started = Date.now();
  let delay = 1500;

  for (;;) {
    await sleep(delay);
    delay = Math.min(delay + 500, 4000); // back off gently
    const body = await getJob(jobId);
    pollElapsed = Math.round((Date.now() - started) / 1000);
    if (onTick) onTick(body, pollElapsed);
    if (body.status === 'completed' || body.status === 'failed') return body;
    // no hard cap: server enqueues and renders as fast as it can; user can
    // always navigate away and the PDF lands in their "Saved" list on next
    // visit ONLY if we complete here — so keep polling while tab is open.
  }
}

/* ---------------- main flow ---------------- */
async function handleDownload() {
  if (busy) return;
  const raw = input.value;

  // validate
  setBusy(true);
  statusActions.hidden = true;
  setSteps(0);
  setStage('Validating link', 6);

  const fileId = extractFileId(raw);
  if (!fileId) {
    showInputError('Please paste a valid Drive link or file ID');
    setSteps(0, 0);
    setStage('Invalid link', 0, 'error');
    setBusy(false);
    return;
  }
  clearInputError();
  log(`resolving share link → ${fileId}`, 'info');
  setSteps(1);

  try {
    // cache
    setStage('Checking local cache', 12);
    let rec = null;
    try {
      rec = await getPDF(fileId);
    } catch (err) {
      log(`cache read failed: ${err.message}`, 'warn');
    }

    if (rec && rec.blob) {
      setSteps(5, null);
      setStage('Done — from cache', 100, 'done');
      triggerBlobDownload(rec.blob, rec.fileName);
      log(`cache hit → ${rec.fileName} (${formatBytes(rec.size)})`, 'success');
      log('download complete (cached, zero backend calls)', 'ok');
      return;
    }
    log('not cached — sending to engine', 'info');
    setSteps(2);

    // queue
    setStage('Contacting engine', 20);
    const job = await createJob(fileId);
    activeJobId = job.jobId;
    log(`job ${job.jobId} ${job.reused ? 'reused (already running)' : 'queued'}`, 'info');
    setStage(job.reused ? 'Already rendering — following' : 'Queued on engine', 28);
    setSteps(3);

    // render (poll; RTDB live status enriches the label when available)
    await watchStatus(fileId, (status) => {
      if (!busy || !activeJobId) return;
      if (status === 'processing') setStage('Capturing pages', 45);
    });

    const final = await pollJob(activeJobId, (body, elapsed) => {
      const pct = body.status === 'processing' ? Math.min(45 + elapsed * 0.5, 88) : 35;
      setStage(
        body.status === 'processing' ? `Capturing pages · ${elapsed}s` : 'Waiting for slot',
        pct,
      );
      setSteps(3);
    });
    stopWatching();

    if (final.status === 'failed') {
      throw Object.assign(new Error(final.error || 'render failed'), {
        bodyCode: final.code,
        userMessage: errorToMessage(null, final.code, final.error),
      });
    }

    // output
    setSteps(4);
    setStage('Downloading result', 92);
    const { blob, fileName } = await getJobResult(activeJobId);

    try {
      await savePDF(fileId, fileName, blob);
      log(`saved to cache: ${fileName} (${formatBytes(blob.size)})`, 'ok');
    } catch (err) {
      log(`could not cache: ${err.message}`, 'warn');
    }

    setSteps(5, null);
    setStage('Done', 100, 'done');
    triggerBlobDownload(blob, fileName);
    log(`downloaded ${fileName} (${formatBytes(blob.size)})`, 'ok');
    renderSavedList();
  } catch (err) {
    stopWatching();
    const msg = err.userMessage || errorToMessage(err.httpCode, err.bodyCode, err.message);
    setStage(msg, 100, 'error');
    setSteps(busyStepForError(), busyStepForError());
    log(msg, 'error');
    showRetry();
  } finally {
    stopWatching();
    setBusy(false);
    activeJobId = null;
  }
}

function busyStepForError() {
  // highlight the step we died on (best effort from last setSteps call)
  const active = stepsEl.querySelector('li.active');
  return active ? [...stepsEl.children].indexOf(active) : 3;
}

function showRetry() {
  statusActions.hidden = false;
  statusActions.innerHTML = '';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'Retry';
  btn.addEventListener('click', () => {
    statusActions.hidden = true;
    handleDownload();
  });
  statusActions.appendChild(btn);
}

function showInputError(msgText) {
  inputError.textContent = msgText;
  inputError.hidden = false;
  inputWrap.classList.add('is-invalid');
  input.focus();
}

function clearInputError() {
  inputError.hidden = true;
  inputWrap.classList.remove('is-invalid');
}

/* ---------------- saved list ---------------- */
async function renderSavedList() {
  let rows = [];
  try {
    rows = await listPDFs();
  } catch (err) {
    log(`could not read cache: ${err.message}`, 'warn');
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
    meta.textContent = `${formatBytes(rec.size)} · ${formatWhen(rec.savedAt)}`;

    info.append(name, meta);

    const actions = document.createElement('div');
    actions.className = 'saved-actions';

    const dl = document.createElement('button');
    dl.type = 'button';
    dl.textContent = 'Re-download';
    dl.addEventListener('click', () => {
      if (rec.blob instanceof Blob) {
        triggerBlobDownload(rec.blob, rec.fileName);
        log(`re-downloaded ${rec.fileName} from cache.`, 'ok');
      } else {
        log('stored record is missing its blob.', 'error');
      }
    });

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn-danger-quiet';
    del.textContent = 'Delete';
    del.addEventListener('click', async () => {
      try {
        await deletePDF(rec.id);
        log(`deleted ${rec.fileName} from cache.`, 'info');
        renderSavedList();
      } catch (err) {
        log(`delete failed: ${err.message}`, 'error');
      }
    });

    actions.append(dl, del);
    li.append(info, actions);
    savedListEl.appendChild(li);
  }
}

/* ---------------- engine health pill ---------------- */
async function checkHealth() {
  healthLabel.textContent = 'checking engine…';
  healthDot.className = 'status-dot is-wait';
  try {
    const resp = await fetch(jobUrl('/api/health'), { signal: AbortSignal.timeout(8000) });
    if (resp.ok) {
      healthLabel.textContent = 'engine running';
      healthDot.className = 'status-dot';
      return;
    }
    throw new Error(String(resp.status));
  } catch {
    healthLabel.textContent = 'engine waking (free tier)';
    healthDot.className = 'status-dot is-down';
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
checkHealth();
log('Ready. Paste a Drive link to begin.', 'info');
