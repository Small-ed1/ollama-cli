#!/usr/bin/env bash
set -euo pipefail

# Synchronize the current local branch with its remote counterpart (origin/<branch>).
# This will fetch all remotes, reset the local branch to origin/<branch>, and clean untracked files.

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
if [ -z "${branch}" ]; then
  echo "Could not determine current branch" >&2
  exit 1
fi

echo "Syncing local branch '${branch}' with origin/${branch}..."
git fetch --all --prune

if git ls-remote --heads origin "${branch}" | grep -q "refs/heads/${branch}"; then
  echo "Remote origin/${branch} found. Resetting local to remote..."
  git reset --hard origin/"${branch}"
  echo 'Cleaning untracked files...'
  git clean -fd
  echo "Repository synced to origin/${branch}"
else
  echo "Remote origin/${branch} not found. Aborting." >&2
  exit 1
fi

git status -s
