# Vercel Deployment Note

The frontend resolves its backend from the `VITE_API_BASE_URL` environment
variable, which is baked into the bundle at build time. When unset, the app
calls same-origin `/api/*` (the Docker/nginx setup).

Vercel rewrites cannot read environment variables, so there is no longer a
hardcoded backend URL in `vercel.json` — the target is fully configurable per
deployment via env.

## Steps

1. Deploy the backend to Railway first (see `README.md`)
2. Connect the GitHub repo to Vercel and set **Root Directory** to `frontend/`
3. In Vercel project settings → Environment Variables, set:
   - `VITE_API_BASE_URL` = your Railway backend URL,
     e.g. `https://<your-service-name>.up.railway.app` (no trailing slash
     needed; one is stripped automatically)
4. Deploy the frontend: push to GitHub, or run `vercel` from `frontend/`
5. Set `CORS_ORIGINS` in Railway Variables to your Vercel deployment URL —
   the frontend now calls the backend cross-origin, and the backend rejects
   origins that are not listed
