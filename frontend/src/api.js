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
