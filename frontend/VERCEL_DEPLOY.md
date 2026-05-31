# Vercel Deployment Note

Before deploying to Vercel, update the `destination` URL in `vercel.json`
to match your Railway backend URL. The URL format is:

```
https://<your-service-name>.up.railway.app/api/:path*
```

## Steps

1. Deploy the backend to Railway first (see `README.md`)
2. Copy the Railway backend URL from the Railway dashboard
3. Update `frontend/vercel.json` `destination` with your URL
4. Deploy frontend to Vercel: connect your GitHub repo
5. Set `CORS_ORIGINS` in Railway Variables to your Vercel deployment URL
