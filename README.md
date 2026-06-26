---
title: AI Audio Mastering
emoji: 🎚️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Master and analyze AI-generated music in the browser.
---

# AI Audio Mastering

A browser-based audio mastering tool with two flows:

1. **Analyze & Master** — upload a track, get an analysis dashboard (LUFS, true peak,
   spectral balance, tempo, key) and render the song through 11 mastering presets
   (Streaming, Loud, Warm Vinyl, Podcast, Acoustic, EDM, Bass Boosted, Slowed,
   Extra Slowed, Sped Up, Reverb). All variants are returned as 24-bit WAV for
   instant A/B comparison in the player.
2. **Just Bass Boost** — drop a track, get a single bass-boosted WAV as a direct
   browser download. No preview, no queue.

## Stack

- **Frontend**: React + TypeScript + Vite (built into the image and served by
  FastAPI as static assets)
- **Backend**: FastAPI + librosa + pedalboard
- **Audio DSP**: HPF → 3-band EQ → Compressor → Pitch/Time/Reverb (viral
  presets) → LUFS normalization → True-peak brick-wall limiter

---

## 🚀 Deploy in 5 minutes from your iPhone (no laptop)

The fastest path: **GitHub → Hugging Face Spaces auto-sync**. Every time you
push to GitHub, HF rebuilds the Space automatically. You can edit files from
Safari or the GitHub mobile app and your public URL updates within ~1 min.

### Step 1 — Push this repo to GitHub

From any terminal (laptop, Codespaces, GitHub Codespace on iPhone, etc.):

```bash
git init -b main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/ai-audio-mastering.git
git push -u origin main
```

> Don't have a terminal on your iPhone? Use **github.dev** (open any repo in
> Safari → press the `.` key) — it's a full VS Code editor in the browser.
> You can also use the **GitHub Mobile app** to create the empty repo and
> then push from a cloud IDE.

### Step 2 — Create the Hugging Face Space

In Safari, open **https://huggingface.co/new-space** and fill in:

- **Space name**: `ai-audio-mastering` (must match your GitHub repo name)
- **License**: MIT
- **SDK**: Docker
- **Space hardware**: CPU basic (free)
- **Visibility**: Public
- Tap **Create Space**

### Step 3 — Connect the Space to your GitHub repo

In your new Space (open it from your profile → Spaces):

1. Tap the **Files** tab
2. Tap **Add file** → **Sync with GitHub repo** (if you don't see this
   option, the Space is too new — wait 30 seconds and refresh)
3. Tap **Authorize** → log in to GitHub → grant access to the
   `ai-audio-mastering` repo
4. Pick the repo and `main` branch
5. Tap **Sync**

HF will pull your repo, see the `Dockerfile` + YAML front-matter in this
README, and start building. Watch the **Logs** tab in your Space — the build
takes ~3-5 minutes the first time (npm install + pip install).

When it finishes, your Space URL is:

```
https://YOUR_USERNAME-ai-audio-mastering.hf.space
```

Open it in Safari and you'll see the app.

### Step 4 — Update the app from your iPhone

Now the magic: **every commit to `main` triggers a fresh HF build** within
~60 seconds. To update the app:

- **Edit in browser**: open your repo at `github.com/YOUR_USERNAME/ai-audio-mastering`
  → tap any file → pencil icon → edit → **Commit changes**
- **Or use the GitHub Mobile app**: edit, commit, push from your phone

HF auto-rebuilds, the Space restarts, your public URL serves the new code.
No laptop required.

---

## Local development

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd backend && uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173 — the Vite dev server proxies `/api` to the backend.

## Direct git-push to HF (no GitHub middleman)

If you don't want to involve GitHub, you can push directly to HF Spaces:

```bash
git remote add space https://huggingface.co/spaces/YOUR_USERNAME/ai-audio-mastering
git push space main
```

You'll be prompted for your HF username and write token. Same result, no
auto-sync — you have to push manually each time.

## Limitations of the free tier

- **Ephemeral storage**: rendered WAVs are not persisted across container
  restarts. Free tier restart does not wipe the disk but a manual rebuild does.
- **No auth**: the Space is public and rate-limit-less. Suitable for personal
  testing; not for production multi-tenant use.
- **CPU-bound**: heavy `librosa.effects.time_stretch` calls take ~5–15 s on the
  free CPU tier for a 3-minute track.
- **No file persistence between requests**: each upload is processed in isolation.

---

## Deploy to Koyeb (always-on, free, no credit card)

Hugging Face Spaces sleeps after 48h of inactivity. If you want the app to
stay up 24/7 without paying, deploy the same Dockerfile to **Koyeb** —
free-tier nano instances run forever with no card on file.

See the full comparison in [`.trae/specs/free-cloud-hosting-options/spec.md`](.trae/specs/free-cloud-hosting-options/spec.md).

### Step 1 — Install the Koyeb CLI

```bash
# macOS
brew install koyeb/tap/koyeb

# Linux / anywhere
curl -fsSL https://raw.githubusercontent.com/koyeb/koyeb-cli/main/install.sh | bash
```

Verify: `koyeb --version`.

### Step 2 — Log in to Koyeb

```bash
koyeb login       # opens your browser for GitHub OAuth
```

Or, headless / CI:

```bash
export KOYEB_TOKEN=<your-token>   # create at https://app.koyeb.com/account/settings/api
```

### Step 3 — Deploy

From the repo root:

```bash
./scripts/deploy_to_koyeb.sh
```

The script validates auth, deploys [`koyeb.yaml`](koyeb.yaml) (Dockerfile
build, port `7860`, `/health` check, `nano` instance, Frankfurt region), and
prints the public URL when the service becomes `HEALTHY`.

Your app will be live at:

```
https://ai-audio-mastering-<your-koyeb-username>.koyeb.app
```

### Step 4 — Manage the service

```bash
koyeb service get    ai-audio-mastering   # status + URL
koyeb service logs   ai-audio-mastering   # tail build & runtime logs
koyeb service rollback ai-audio-mastering # roll back to previous deploy
koyeb service delete ai-audio-mastering   # tear it down
```

Pushes to the `main` branch of the connected GitHub repo auto-trigger a
rebuild — same DX as the HF Spaces path.

### Caveats (Koyeb free tier)

- **256 MB RAM / shared vCPU** — fine for the analysis + mastering endpoints
  on tracks up to ~5 min; very long tracks may need to be uploaded in chunks
  (the [`chunked-download`](.trae/specs/chunked-download/spec.md) spec already
  handles this client-side).
- **No persistent disk** — mastered WAVs are returned as a direct download
  and not stored between requests. Set `SUPABASE_ENABLED=true` in
  [`koyeb.yaml`](koyeb.yaml) and provide `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`
  env vars to persist results to your own Supabase bucket.
- **One region** — `fra` (Frankfurt) is the default free-tier region.
- **No custom domain on free tier** — you'll get a `*.koyeb.app` subdomain.

---

## Deploy to Render (free, no credit card)

> ⚠️ **Caveat**: Render free web services **sleep after 15 minutes of
> inactivity**. The first request after sleep takes ~30–50 s. For an
> always-on free deploy, see [Northflank](https://northflank.com) instead
> (separate spec not yet written). HF Spaces has a 48 h sleep.

Koyeb's free tier was paywalled when this README was written. **Render** is
the next-easiest "no credit card" option that runs the same Dockerfile.

### Step 1 — Install the Render CLI

```bash
brew install render                                                 # macOS
# or:
curl -fsSL https://raw.githubusercontent.com/render-oss/cli/main/bin/install.sh | sh
```

Verify: `render --version` (returns `Render CLI vX.Y.Z`).

### Step 2 — Sign up (no credit card)

Go to https://dashboard.render.com/register and create a free account.
Pick the **Free** plan — Render only asks for a card when you upgrade.

### Step 3 — Get a CLI key

1. Open https://dashboard.render.com/settings#cli-keys
2. Click "Create CLI key"
3. Copy the token.
4. Authenticate:

```bash
render login    # paste the token when prompted
# or, headless / CI:
export RENDER_API_KEY=<paste-token-here>
```

### Step 4 — Validate and apply

```bash
./scripts/deploy_to_render.sh            # validate + print dashboard steps
# optional --watch flag polls until the service is Live:
./scripts/deploy_to_render.sh --watch
```

The script validates [`render.yaml`](render.yaml), then walks you through
the dashboard Blueprint apply:

1. Open https://dashboard.render.com/blueprints
2. Click "New Blueprint Instance"
3. Pick the GitHub repo `EVOMINDMAZE/ai-audio-mastering`
4. Confirm the Blueprint name and click "Apply"

Render creates `ai-audio-mastering` as a free web service, builds the
Dockerfile, exposes port 10000, registers a `/health` check.

Your URL:

```
https://ai-audio-mastering.onrender.com
https://ai-audio-mastering.onrender.com/health   (200 OK)
```

### Step 5 — Manage the service

```bash
render services list                              # list all services
render deploys list --service ai-audio-mastering  # deploy history
render logs --service ai-audio-mastering --tail   # live logs
render deploys trigger --service ai-audio-mastering   # rebuild now
```

### Caveats (Render free tier)

- **15-min sleep** — first request after sleep takes ~30–50 s.
- **No persistent disk** — same as HF Spaces / Koyeb; mastered WAVs come back
  as a direct download.
- **No custom domain on free tier** — you'll get a `*.onrender.com` subdomain.
- **One region** — defaults to `oregon` (override in `render.yaml` if needed).

### Verified live URL (2026-06-26)

```
https://ai-audio-mastering.onrender.com       → React SPA (200 OK)
https://ai-audio-mastering.onrender.com/health → {"status":"ok","version":"0.1.0"} (200 OK)
```

> **Two env-var gotchas** baked into [`render.yaml`](render.yaml) — don't undo them:
>
> 1. `CORS_ORIGINS` is `["*"]` (JSON-encoded array), **not** `"*"`. Bare `*` causes
>    `pydantic_settings.sources.SettingsError` because pydantic-settings JSON-decodes
>    `List[str]` fields *before* the field validator runs.
> 2. `FRONTEND_DIST=/app/backend/frontend_dist` overrides the wrong default
>    `_BACKEND_DIR = Path(__file__).resolve().parent` in
>    [`backend/app/main.py`](backend/app/main.py) line 35 — without it, the SPA
>    mount is silently skipped and `/` returns 404.

## License

MIT — see [LICENSE](./LICENSE).