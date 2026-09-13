# Deploying SAGE (frontend + backend together) — public URL

Your stack is a **Python FastAPI backend + static HTML/JS frontend**. The
simplest way to get one public link for everything is to let FastAPI serve
the frontend too. **Render** hosts this perfectly on its free plan.
(Vercel/Netlify are built for static sites & JS serverless functions — they
can't run your sklearn/pandas backend with its 48 MB model files.)

This repo is already wired for it:

- `backend/app/main.py` serves the `Frontend/` folder from the same app
  (mounted at `/`), plus a shareable dashboard link at `/app`.
- `Frontend/api.js` calls the API on the **same origin** when the page is
  loaded from any non-localhost host — zero CORS configuration needed.
- `render.yaml` (a Render Blueprint) describes the whole service.

## One-time setup: push this repo to GitHub

The repo already has a remote: `pariigdtuw2029-droid/SAGE_2026` (branch `main`).
Just make sure your latest changes are pushed:

```bash
git add -A
git commit -m "Prepare app for single-service deployment on Render"
git push origin main
```

## Deploy on Render (≈5 minutes)

1. Go to https://dashboard.render.com → sign in with GitHub.
2. **New +** → **Blueprint**.
3. Select the `SAGE_2026` repository → Render reads `render.yaml`.
4. Click **Apply**. Render will:
   - install `backend/requirements.txt`,
   - start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`,
   - give you a URL like `https://sage-app.onrender.com`.
5. Open **https://sage-app.onrender.com/app** → your dashboard. 🎉

You can rename the service in Render settings to get a nicer subdomain.

### What lives where after deploy

| URL (on your Render domain)        | What it is                    |
|------------------------------------|-------------------------------|
| `/app`                             | Dashboard (frontend)          |
| `/index.html`, `/pages/*`, `/assets/*` | Frontend files            |
| `/docs`                            | Swagger API docs              |
| `/health`                          | Health check (used by Render) |
| `/api/lots`, `/api/alerts`, …      | Backend API                   |

## Alternative: manual Render setup (no Blueprint)

If you'd rather click through the UI instead of using `render.yaml`:

- **Type:** Web Service → **Runtime:** Python 3
- **Build command:** `pip install -r backend/requirements.txt`
- **Start command:** `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- No environment variables are required (sane defaults are baked in).

## Notes for the demo data

- On the **free plan** there is no persistent disk, so the SQLite database
  resets on every redeploy. Your services fall back to demo data
  (`app/services/mock_data.py`) when the DB is empty, so the UI still works —
  good enough for a hackathon demo.
- To preload real data, add a one-off shell command on Render (or run it
  locally and commit the `.db` — not recommended) — e.g. a Build Command of:
  `pip install -r backend/requirements.txt && cd backend && python -m database init && python -m database ingest data/burn_in_dataset.csv`
  (the `shap` dependency is optional and only needed with `--explain`).
- For real persistence: create a free PostgreSQL instance on Render, set
  `DATABASE_URL` to its *Internal Database URL*, and run `python -m database init`
  once via the Render Shell.

## Free-plan caveats (expected behaviour, not bugs)

- The service **sleeps after 15 minutes** of inactivity; the first request
  afterwards takes ~50 s while it wakes up (your frontend already shows a
  "backend offline?" timeout message — just refresh once).
- 750 free instance-hours/month — one always-on service fits within that.

## Local development (unchanged)

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- Frontend: http://127.0.0.1:8000/app (or /index.html)
- API docs: http://127.0.0.1:8000/docs

## Alternative platforms

- **Railway / Fly.io** — same idea (Python web service, start command above);
  both give you a public URL.
- **Vercel / Netlify** — only suitable for the static frontend; the backend
  would need to live elsewhere, so stick with Render for one-link simplicity.
