// Base URL for all backend API calls.
//
// Empty by default: the app calls same-origin /api/*, which works behind
// nginx (Docker) and the Vite dev proxy. Deployments where the frontend is
// served from a different origin than the backend (e.g. Vercel + Railway)
// set VITE_API_BASE_URL at build time to the backend origin, e.g.
//   VITE_API_BASE_URL=https://your-backend.up.railway.app
// The backend must then allow the frontend origin via CORS_ORIGINS.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

// Operator API key. Build-time env (VITE_API_KEY) is the default; a
// localStorage override (rf_api_key) lets a deployment set the key at
// runtime without rebuilding. Every backend route is fail-closed since
// W0 — without a key every call returns 401.
export function getApiKey() {
  return (
    import.meta.env.VITE_API_KEY ||
    (typeof localStorage !== 'undefined' ? localStorage.getItem('rf_api_key') : null) ||
    ''
  );
}

// Auth headers for backend calls: the operator API key always, plus the
// per-session ownership token on session-scoped routes (stream, approve,
// outline updates, exports). Missing token -> 401; wrong token -> 403.
export function authHeaders(sessionToken = null) {
  const headers = {};
  const key = getApiKey();
  if (key) {
    headers['X-API-Key'] = key;
  }
  if (sessionToken) {
    headers['X-Session-Token'] = sessionToken;
  }
  return headers;
}

// Trigger a browser download from a completed fetch Response, using the
// filename the backend suggests via Content-Disposition.
export async function downloadResponseAsFile(response, fallbackName) {
  const blob = await response.blob();
  const contentDisposition = response.headers.get('Content-Disposition');
  let filename = fallbackName;
  if (contentDisposition) {
    const match = contentDisposition.match(/filename="?([^"]+)"?/);
    if (match) filename = match[1];
  }
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
  return filename;
}
