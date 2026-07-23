#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
fixture="$repo_root/examples/causal-curve-review/.katz"
worktree="$(mktemp -d)"
trap 'rm -rf "$worktree"' EXIT

git -C "$worktree" init --quiet
cp -R "$fixture" "$worktree/.katz"

(
  cd "$worktree"
  PYTHONPATH="$repo_root/src" python -m katz.cli report generate \
    --output "$repo_root/docs/review.html"
)
