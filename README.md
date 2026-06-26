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

## License

MIT — see [LICENSE](./LICENSE).