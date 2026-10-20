/* ============================================================
   firebase-config.js — Firebase init + RTDB status subscription
   Frontend is read-only: RTDB rules allow public read on /pdfs.
   Fill values from your Firebase console (Project settings).
   Or set VITE_ / NEXT_PUBLIC_ style env at build time — this
   static site reads window.__ENV__ overrides injected by the host.
   ============================================================ */

// Values are placeholders — replace with real config (see README §Setup).
const firebaseConfig = {
  apiKey: 'VITE_FIREBASE_API_KEY',
  authDomain: 'PROJECT.firebaseapp.com',
  databaseURL: 'https://PROJECT-default-rtdb.firebaseio.com',
  projectId: 'PROJECT',
  appId: 'VITE_FIREBASE_APP_ID',
};

// Runtime override (e.g. injected by a deploy script or window.__ENV__).
if (typeof window !== 'undefined' && window.__ENV__) {
  Object.assign(firebaseConfig, window.__ENV__);
}

const BACKEND_URL =
  (typeof window !== 'undefined' && window.__ENV__ && window.__ENV__.BACKEND_URL) ||
  'VITE_BACKEND_URL';

let db = null;

/** Lazily init Firebase app + RTDB. Resolves { db } or null when unconfigured. */
export async function initFirebase() {
  if (db) return { db };

  const cfg = firebaseConfig;
  if (!cfg.databaseURL || cfg.databaseURL.includes('PROJECT-default-rtdb')) {
    console.warn('[firebase] Not configured — status sync disabled (app still works).');
    return { db: null };
  }

  try {
    const { initializeApp } = await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js');
    const { getDatabase } = await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-database.js');
    const app = initializeApp(cfg);
    db = getDatabase(app);
    return { db };
  } catch (err) {
    console.warn('[firebase] init failed, continuing without RTDB sync:', err);
    return { db: null };
  }
}

/**
 * Subscribe to /pdfs/{fileId}/status. Returns unsubscribe fn.
 * cb receives the status string: 'queued' | 'processing' | 'completed' | 'failed'
 * No-op (returns noop) when Firebase isn't configured.
 */
export function subscribeStatus(fileId, cb) {
  if (!db) return () => {};
  let unsubscribed = false;
  let ref = null;
  let off = null;

  import('https://www.gstatic.com/firebasejs/10.12.2/firebase-database.js')
    .then(({ ref, onValue }) => {
      if (unsubscribed) return;
      const statusRef = ref(db, `pdfs/${fileId}/status`);
      const handler = onValue(statusRef, (snap) => {
        const val = snap.val();
        if (typeof val === 'string' && val) cb(val);
      });
      off = handler; // onValue returns its own unsubscribe
    })
    .catch(() => {});

  return () => {
    unsubscribed = true;
    if (typeof off === 'function') off();
  };
}

export { firebaseConfig, BACKEND_URL };
