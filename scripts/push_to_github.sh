#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# push_to_github.sh
# -----------------------------------------------------------------------------
# Initialize a git repo, commit everything, and push to GitHub.
#
# Designed to be runnable from a cloud IDE on iPhone (GitHub Codespaces,
# Gitpod, StackBlitz, etc.) — the only inputs are the GitHub username and
# a fine-grained Personal Access Token with `contents: write` scope on
# the target repo.
#
# Usage:
#   GITHUB_USER="yourname" GITHUB_TOKEN="ghp_xxx" ./scripts/push_to_github.sh
#
# Prerequisites: repo must already exist on GitHub (empty is fine).
# Create it at https://github.com/new — name: ai-audio-mastering, no README,
# no .gitignore, no license. Public or private, your choice.
# -----------------------------------------------------------------------------

set -euo pipefail

REPO_NAME="${REPO_NAME:-ai-audio-mastering}"
GITHUB_USER="${GITHUB_USER:?Error: GITHUB_USER env var required}"
GITHUB_TOKEN="${GITHUB_TOKEN:?Error: GITHUB_TOKEN env var required}"

# Move to repo root (one level up from scripts/)
cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
    echo "→ Initializing git repo"
    git init -b main
    git config user.name  "${GITHUB_USER}"
    git config user.email "${GITHUB_USER}@users.noreply.github.com"
fi

echo "→ Staging files (respects .gitignore)"
git add .

# Skip commit if there are no new changes
if git diff --cached --quiet; then
    echo "→ No changes to commit"
else
    echo "→ Committing"
    git commit -m "Deploy: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

REMOTE_URL="https://${GITHUB_USER}:${GITHUB_TOKEN}@github.com/${GITHUB_USER}/${REPO_NAME}.git"
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "→ Adding origin remote"
    git remote add origin "$REMOTE_URL"
else
    echo "→ Updating origin remote URL"
    git remote set-url origin "$REMOTE_URL"
fi

echo "→ Pushing to GitHub (branch: main)"
git push -u origin main

echo ""
echo "✓ Pushed successfully"
echo "  Repo:  https://github.com/${GITHUB_USER}/${REPO_NAME}"
echo ""
echo "Next: open https://huggingface.co/new-space and follow the README's"
echo "      'Deploy in 5 minutes from your iPhone' section."