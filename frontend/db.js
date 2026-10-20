/* ============================================================
   db.js — IndexedDB layer for GDrive PDF Fetcher
   Store record: { id, fileName, blob, size, savedAt }
   ============================================================ */

const DB_NAME = 'gdrive_fetcher';
const DB_VERSION = 1;
const STORE = 'pdfs';

/** Open (and upgrade) the database. Resolves the IDBDatabase. */
function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

    req.onupgradeneeded = (event) => {
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

/** Run `fn(tx)` inside a readwrite transaction; resolves fn's promise chain. */
function withStore(mode, fn) {
  return openDB().then((db) => new Promise((resolve, reject) => {
    let result;
    const tx = db.transaction(STORE, mode);
    const store = tx.objectStore(STORE);
    try {
      result = fn(store);
    } catch (err) {
      reject(err);
    }
    tx.oncomplete = () => { db.close(); resolve(result); };
    tx.onerror = () => reject(tx.error || new Error('IndexedDB transaction failed'));
    tx.onabort = () => reject(tx.error || new Error('IndexedDB transaction aborted'));
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
  return withStore('readwrite', (store) => {
    store.put(record);
  });
}

/** Fetch one record by Drive file ID (or undefined). */
export async function getPDF(id) {
  if (!id) return undefined;
  return withStore('readonly', (store) => store.get(String(id))).then((rec) => rec || undefined);
}

/** All saved records, newest first. */
export async function listPDFs() {
  const rows = await withStore('readonly', (store) => store.getAll());
  return (rows || []).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
}

/** Delete one record by ID. */
export async function deletePDF(id) {
  if (!id) return;
  return withStore('readwrite', (store) => {
    store.delete(String(id));
  });
}
