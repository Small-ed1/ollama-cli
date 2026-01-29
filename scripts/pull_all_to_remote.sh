#!/usr/bin/env bash
set -euo pipefail

## Pull all local branches to their remote counterparts (origin/<branch>), if present.
## This will not touch branches that have no corresponding remote branch.
## WARNING: This is destructive for local changes on any branch that tracks origin/<branch>.

current=$(git rev-parse --abbrev-ref HEAD)
echo "Current branch: ${current}"

## Quick guard: abort if there are unstaged changes in the working tree
if ! git diff --quiet --ignore-submodules -- ; then
  echo "Uncommitted changes detected. Commit or stash them before running this script."
  exit 1
fi

echo "Fetching all remotes..."
git fetch --all --prune

for b in $(git for-each-ref --format='%(refname:short)' refs/heads/); do
  if [ "$b" = "HEAD" ]; then
    continue
  fi
  if git show-ref --verify --quiet refs/remotes/origin/"${b}"; then
    echo "Resetting local branch '${b}' to origin/${b}"
    git checkout "${b}"
    git reset --hard origin/"${b}"
  else
    echo "Skipping '${b}': origin/${b} not found"
  fi
done

git checkout "${current}"
git status -s
