// gen-env.mjs — Vercel build step: turns VITE_* env vars into env.js
// so the static site gets real config at runtime (window.__ENV__).
// Without this, "Other"-preset static deploys would ship placeholders.
import { writeFileSync } from 'node:fs';

const keys = [
  'VITE_FIREBASE_API_KEY',
  'VITE_FIREBASE_AUTH_DOMAIN',
  'VITE_FIREBASE_DATABASE_URL',
  'VITE_FIREBASE_PROJECT_ID',
  'VITE_FIREBASE_APP_ID',
  'VITE_BACKEND_URL',
];

const env = {};
for (const k of keys) {
  const v = process.env[k];
  if (v && v.trim()) env[k.replace(/^VITE_/, '')] = v.trim();
}

// Drop keys that would break Firebase init (placeholder-looking values).
for (const k of Object.keys(env)) {
  if (!env[k] || env[k].startsWith('VITE_') || env[k].includes('PROJECT-default-rtdb')) {
    delete env[k];
  }
}

writeFileSync(
  new URL('../env.js', import.meta.url),
  `window.__ENV__ = ${JSON.stringify(env)};\n`
);
console.log('[gen-env] wrote env.js with keys:', Object.keys(env).join(', ') || '(none)');
