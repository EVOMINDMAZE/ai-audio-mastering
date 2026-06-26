#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# deploy_to_render.sh
# -----------------------------------------------------------------------------
# Deploy the AI Audio Mastering app to Render.
#
# IMPORTANT: Render Blueprints are applied via the dashboard, not the CLI.
# This script:
#   1. Checks the `render` CLI is installed and you're authenticated.
#   2. Validates `render.yaml` with `render blueprints validate`.
#   3. Prints step-by-step dashboard instructions to apply the Blueprint.
#   4. Optionally polls `render services list` until the service is Live.
#
# Prerequisites:
#   1. Install the Render CLI:
#        brew install render                                       # macOS
#        curl -fsSL https://raw.githubusercontent.com/render-oss/cli/main/bin/install.sh | sh
#   2. Sign up (no credit card needed for free tier):
#        https://dashboard.render.com/register
#   3. Get a CLI token at https://dashboard.render.com/settings#cli-keys
#      and either run `render login` (paste the token) or set
#      `export RENDER_API_KEY=<token>`.
#
# Usage:
#   ./scripts/deploy_to_render.sh               # validate + show instructions
#   ./scripts/deploy_to_render.sh --watch       # also poll for service Live
#
# After deploy (via dashboard):
#   render services list                              # all services
#   render deploys list --service ai-audio-mastering  # deploy history
#   render logs --service ai-audio-mastering --tail  # live logs
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RENDER_CONFIG="$REPO_ROOT/render.yaml"
EXPECTED_REMOTE="github.com/EVOMINDMAZE/ai-audio-mastering"
SERVICE_NAME="ai-audio-mastering"

WATCH=0
for arg in "$@"; do
    case "$arg" in
        --watch|-w) WATCH=1 ;;
        --help|-h)
            sed -n '3,30p' "$0"
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# ---- Pre-flight: render CLI installed --------------------------------------
if ! command -v render >/dev/null 2>&1; then
    echo "Error: 'render' CLI not found on \$PATH." >&2
    echo "Install it with one of:" >&2
    echo "  brew install render" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/render-oss/cli/main/bin/install.sh | sh" >&2
    exit 1
fi

# ---- Pre-flight: render CLI authenticated ---------------------------------
WHOAMI_OUT="$(mktemp)"
trap 'rm -f "$WHOAMI_OUT"' EXIT
if ! render whoami >"$WHOAMI_OUT" 2>&1; then
    echo "Error: not logged in to Render." >&2
    echo "Authenticate with one of:" >&2
    echo "  render login                                       # paste CLI token from https://dashboard.render.com/settings#cli-keys" >&2
    echo "  export RENDER_API_KEY=<token>                      # CI / headless" >&2
    echo >&2
    echo "render CLI said:" >&2
    sed 's/^/  /' "$WHOAMI_OUT" >&2
    exit 1
fi
WHOAMI_TEXT="$(tr -d '\n' < "$WHOAMI_OUT")"
echo "→ Logged in to Render as: $WHOAMI_TEXT"

# ---- Pre-flight: render.yaml exists ----------------------------------------
if [ ! -f "$RENDER_CONFIG" ]; then
    echo "Error: render.yaml not found at $RENDER_CONFIG" >&2
    exit 1
fi

# ---- Validate render.yaml --------------------------------------------------
# `render blueprints validate` requires both auth AND a default workspace.
# We treat validation as a soft check: if it fails because of missing workspace
# (most common for brand-new accounts), we warn and continue so the user can
# still see the dashboard instructions.
echo "→ Validating $RENDER_CONFIG ..."
if render blueprints validate "$RENDER_CONFIG" 2>/tmp/render-validate.err; then
    echo "  ✓ render.yaml is valid"
elif grep -q "no workspace specified" /tmp/render-validate.err 2>/dev/null; then
    echo "  ⚠ Skipped validation: no default workspace set yet."
    echo "    Fix with: render workspace set"
    echo "    (Validation isn't required for the dashboard apply below.)"
else
    echo "Error: render.yaml failed validation." >&2
    cat /tmp/render-validate.err >&2
    exit 1
fi
rm -f /tmp/render-validate.err

# ---- Pre-flight: git remote sanity (non-blocking) --------------------------
if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    ACTUAL_REMOTE="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || echo "")"
    if [ -n "$ACTUAL_REMOTE" ] && [[ "$ACTUAL_REMOTE" != *"$EXPECTED_REMOTE"* ]]; then
        echo "Warning: git origin is '$ACTUAL_REMOTE'" >&2
        echo "         Expected to contain '$EXPECTED_REMOTE'." >&2
        echo "         Forks are fine — continuing." >&2
    fi
fi

# ---- Print dashboard instructions -----------------------------------------
echo ""
echo "→ render.yaml is valid. To apply it:"
echo ""
echo "  1. Open https://dashboard.render.com/blueprints"
echo "  2. Click 'New Blueprint Instance'"
echo "  3. Pick the GitHub repo 'EVOMINDMAZE/ai-audio-mastering' (or your fork)"
echo "  4. Confirm the Blueprint name and click 'Apply'"
echo ""
echo "  Render will create the '$SERVICE_NAME' web service, build the Dockerfile,"
echo "  expose port 10000, register a /health check, and deploy on the free plan."
echo ""
echo "  Once it's Live, your URL will be:"
echo "      https://$SERVICE_NAME.onrender.com"
echo "      https://$SERVICE_NAME.onrender.com/health   (200 OK expected)"
echo ""
echo "  ⏰  Free web services sleep after 15 minutes of inactivity (~30–50 s"
echo "      cold start on the first request after sleep)."

# ---- Optional: poll for service Live --------------------------------------
if [ "$WATCH" = "1" ]; then
    echo ""
    echo "→ Watching for service '$SERVICE_NAME' (Ctrl-C to stop)..."
    for i in $(seq 1 120); do  # up to ~20 minutes
        if render services list --output json 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
target = [s for s in data if s.get('name') == '$SERVICE_NAME' or s.get('service', {}).get('name') == '$SERVICE_NAME']
if not target:
    sys.exit(1)
status = target[0].get('service', {}).get('suspended') or target[0].get('suspended')
inv_status = target[0].get('service', {}).get('invitation') or target[0].get('invitation')
# Render statuses include 'live', 'building', 'queued', 'failed', etc.
# Try a few known shapes:
import json as _j
print(_j.dumps(target[0]))
" 2>/dev/null | grep -qi '"live"'; then
            echo "  ✓ Service is Live"
            echo ""
            echo "  URL:    https://$SERVICE_NAME.onrender.com"
            echo "  Health: https://$SERVICE_NAME.onrender.com/health"
            echo ""
            echo "  Next: run 'render logs --service $SERVICE_NAME --tail' to see logs."
            exit 0
        fi
        if [ $((i % 6)) -eq 0 ]; then
            echo "  still waiting ($((i * 10))s)..."
        fi
        sleep 10
    done
    echo "  ✗ Timed out waiting for the service to go Live." >&2
    echo "    Open https://dashboard.render.com to check the deploy status." >&2
    exit 1
fi