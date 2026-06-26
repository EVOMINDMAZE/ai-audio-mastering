#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# deploy_to_koyeb.sh
# -----------------------------------------------------------------------------
# One-command deploy of the AI Audio Mastering app to a free-tier Koyeb
# service. Builds the local Dockerfile via the Koyeb CLI and waits for the
# deployment to become healthy, then prints the public URL.
#
# Prerequisites:
#   1. Install the Koyeb CLI:
#        brew install koyeb/tap/koyeb          # macOS
#        curl -fsSL https://raw.githubusercontent.com/koyeb/koyeb-cli/main/install.sh | bash
#   2. Log in (one of):
#        koyeb login                          # opens browser for GitHub OAuth
#        export KOYEB_TOKEN=<token>           # from https://app.koyeb.com/account/settings/api
#
# Usage:
#   ./scripts/deploy_to_koyeb.sh
#
# After deploy:
#   koyeb services logs    ai-audio-mastering        # tail build/runtime logs
#   koyeb services get     ai-audio-mastering        # status + public URL
#   koyeb services redeploy ai-audio-mastering       # rebuild from current files
#   koyeb services update  ai-audio-mastering ...    # change env/region/scale
#   koyeb services delete  ai-audio-mastering        # tear down
# -----------------------------------------------------------------------------

set -euo pipefail

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPECTED_REMOTE="github.com/EVOMINDMAZE/ai-audio-mastering"

# Service identity — kept in sync with koyeb.yaml (used as documentation only).
APP_NAME="ai-audio-mastering"
SERVICE_NAME="ai-audio-mastering"
FULL_NAME="$APP_NAME/$SERVICE_NAME"
DOCKERFILE="Dockerfile"

# ---- Pre-flight: koyeb CLI installed ---------------------------------------
if ! command -v koyeb >/dev/null 2>&1; then
    echo "Error: 'koyeb' CLI not found on \$PATH." >&2
    echo "Install it with one of:" >&2
    echo "  brew install koyeb/tap/koyeb" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/koyeb/koyeb-cli/main/install.sh | bash" >&2
    exit 1
fi

# ---- Pre-flight: koyeb CLI authenticated -----------------------------------
# `koyeb whoami` returns non-zero and prints an error if no profile / token is
# available. Capture output so we don't leak the CLI's error unless auth fails.
WHOAMI_OUT="$(mktemp)"
trap 'rm -f "$WHOAMI_OUT"' EXIT
if ! koyeb whoami >"$WHOAMI_OUT" 2>&1; then
    echo "Error: not logged in to Koyeb." >&2
    echo "Authenticate with one of:" >&2
    echo "  koyeb login                      # interactive GitHub OAuth" >&2
    echo "  export KOYEB_TOKEN=<token>       # from https://app.koyeb.com/account/settings/api" >&2
    echo >&2
    echo "koyeb CLI said:" >&2
    sed 's/^/  /' "$WHOAMI_OUT" >&2
    exit 1
fi
echo "→ Logged in to Koyeb as: $(tr -d '\n' < "$WHOAMI_OUT")"

# ---- Pre-flight: Dockerfile exists -----------------------------------------
if [ ! -f "$REPO_ROOT/$DOCKERFILE" ]; then
    echo "Error: $DOCKERFILE not found at $REPO_ROOT/$DOCKERFILE" >&2
    exit 1
fi

# ---- Pre-flight: git remote points at the expected repo --------------------
# Non-blocking warning only — forks are fine.
if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    ACTUAL_REMOTE="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || echo "")"
    if [ -n "$ACTUAL_REMOTE" ] && [[ "$ACTUAL_REMOTE" != *"$EXPECTED_REMOTE"* ]]; then
        echo "Warning: git origin is '$ACTUAL_REMOTE'" >&2
        echo "         Expected to contain '$EXPECTED_REMOTE'." >&2
        echo "         Forks are fine — continuing." >&2
    fi
fi

# ---- Deploy -----------------------------------------------------------------
# Flags mirror `koyeb.yaml` (which is kept as documentation):
#   - archive-builder=docker           → use the Dockerfile
#   - archive-docker-dockerfile        → path within the archive
#   - ports 7860:http                  → public port + protocol
#   - checks 7860:http:/health         → HTTP healthcheck on /health
#   - env KEY=VALUE (×5)               → env block from koyeb.yaml
#   - regions fra                      → free-tier region
#   - instance-type nano               → free-tier instance (256 MB)
#   - min-scale/max-scale 1            → single always-on instance
#   - wait                             → block until HEALTHY (or 5 min timeout)
echo "→ Deploying $REPO_ROOT to $FULL_NAME (waiting for HEALTHY)..."
koyeb deploy "$REPO_ROOT" "$FULL_NAME" \
    --archive-builder docker \
    --archive-docker-dockerfile "$DOCKERFILE" \
    --type web \
    --ports 7860:http \
    --checks 7860:http:/health \
    --env APP_ENV=production \
    --env PORT=7860 \
    --env JOB_TMP_DIR=/tmp/audio_jobs \
    --env CORS_ORIGINS='*' \
    --env SUPABASE_ENABLED=false \
    --regions fra \
    --instance-type nano \
    --min-scale 1 \
    --max-scale 1 \
    --wait

# ---- Print the resulting URL ------------------------------------------------
echo ""
echo "→ Service status:"
koyeb services describe "$SERVICE_NAME" --app "$APP_NAME" 2>/dev/null || \
koyeb services get "$SERVICE_NAME" --app "$APP_NAME" 2>/dev/null || true

PUBLIC_HOST=""
# `koyeb services describe` / `get` prints "Hostname: <host>" on one of the rows.
if command -v koyeb >/dev/null 2>&1; then
    PUBLIC_HOST="$(koyeb services describe "$SERVICE_NAME" --app "$APP_NAME" 2>/dev/null | awk '/Hostname:[[:space:]]+/{print $2; exit}' || true)"
    if [ -z "$PUBLIC_HOST" ]; then
        PUBLIC_HOST="$(koyeb services get "$SERVICE_NAME" --app "$APP_NAME" 2>/dev/null | awk '/Hostname:[[:space:]]+/{print $2; exit}' || true)"
    fi
fi

echo ""
if [ -n "$PUBLIC_HOST" ]; then
    echo "✓ Deploy healthy"
    echo "  URL:    https://$PUBLIC_HOST"
    echo "  Health: https://$PUBLIC_HOST/health"
else
    echo "✓ Deploy finished — run 'koyeb services get $SERVICE_NAME --app $APP_NAME' to see the URL."
fi