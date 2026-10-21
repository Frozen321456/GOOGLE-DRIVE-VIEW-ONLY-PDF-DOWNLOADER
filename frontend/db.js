/* ============================================================
   db.js — IndexedDB layer for GDrive PDF Fetcher
   Store record: { id, fileName, blob, size, savedAt }

   NOTE: object-store methods (get/getAll) return IDBRequest objects,
   NOT values. We wire request.onsuccess -> capture request.result,
   then resolve on transaction complete. (Bug fix: earlier version
   resolved the request itself -> ".sort is not a function".)
   ============================================================ */

const DB_NAME = 'gdrive_fetcher';
const DB_VERSION = 1;
const STORE = 'pdfs';

/** Open (and upgrade) the database. Resolves the IDBDatabase. */
function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'id' });
        store.createIndex('savedAt', 'savedAt');
      }
    };

    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('IndexedDB open failed'));
    req.onblocked = () => reject(new Error('IndexedDB blocked by another tab'));
  });
}

/**
 * Run `fn(store)` inside a transaction.
 * If fn returns an IDBRequest, its .result is resolved after commit;
 * otherwise fn's return value passes through (writes resolve undefined).
 */
function withStore(mode, fn) {
  return openDB().then((db) => new Promise((resolve, reject) => {
    let request = null;
    const tx = db.transaction(STORE, mode);
    const store = tx.objectStore(STORE);

    try {
      request = fn(store);
    } catch (err) {
      try { db.close(); } catch { /* noop */ }
      reject(err);
      return;
    }

    let value;
    if (request && typeof request.addEventListener === 'function') {
      request.addEventListener('success', () => { value = request.result; });
      request.addEventListener('error', () => {
        try { db.close(); } catch { /* noop */ }
        reject(request.error || new Error('IndexedDB request failed'));
      });
    }

    tx.oncomplete = () => {
      db.close();
      resolve(value);
    };
    tx.onerror = () => {
      try { db.close(); } catch { /* noop */ }
      reject(tx.error || new Error('IndexedDB transaction failed'));
    };
    tx.onabort = () => {
      try { db.close(); } catch { /* noop */ }
      reject(tx.error || new Error('IndexedDB transaction aborted'));
    };
  }));
}

/** Persist a PDF blob under its Drive file ID. */
export async function savePDF(id, fileName, blob) {
  if (!id || !blob) throw new Error('savePDF: id and blob are required');
  const record = {
    id: String(id),
    fileName: String(fileName || 'document.pdf'),
    blob,
    size: blob.size,
    savedAt: Date.now(),
  };
  return withStore('readwrite', (store) => store.put(record));
}

/** Fetch one record by Drive file ID (or undefined). */
export async function getPDF(id) {
  if (!id) return undefined;
  const rec = await withStore('readonly', (store) => store.get(String(id)));
  return rec || undefined;
}

/** All saved records, newest first. */
export async function listPDFs() {
  const rows = await withStore('readonly', (store) => store.getAll());
  return (Array.isArray(rows) ? rows : []).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
}

/** Delete one record by ID. */
export async function deletePDF(id) {
  if (!id) return;
  return withStore('readwrite', (store) => store.delete(String(id)));
}
