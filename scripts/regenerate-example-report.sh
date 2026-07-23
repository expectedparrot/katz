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

  PYTHONPATH="$repo_root/src" python -m katz.cli issue investigate \
    --id b451a62fc869487292a71a87d36c249d \
    --verdict confirmed \
    --state confirmed \
    --evidence "paper/paper_ventilated.md:97-99" \
    --notes "The Methods section calls the estimate unbiased and causal without stating the identification assumptions required for that interpretation." \
    >/dev/null
  PYTHONPATH="$repo_root/src" python -m katz.cli issue suggest \
    --id b451a62fc869487292a71a87d36c249d \
    --text "State the identification assumptions and qualify the claim as applying under those assumptions." \
    >/dev/null

  PYTHONPATH="$repo_root/src" python -m katz.cli issue investigate \
    --id 0c899e1387584a4692c9635ccbc431e1 \
    --verdict rejected \
    --state rejected \
    --notes "This candidate duplicates the confirmed causal-language issue b451a62fc869487292a71a87d36c249d at the same passage." \
    >/dev/null

  PYTHONPATH="$repo_root/src" python -m katz.cli issue investigate \
    --id 1d773d816538443fb6d573834f2f0689 \
    --verdict confirmed \
    --state confirmed \
    --evidence "paper/paper_ventilated.md:39-40" \
    --notes "The exercise example makes an unqualified causal statement, while the surrounding text does not state the assumptions under which adjustment identifies an effect." \
    >/dev/null
  PYTHONPATH="$repo_root/src" python -m katz.cli issue suggest \
    --id 1d773d816538443fb6d573834f2f0689 \
    --text "State the assumptions required for a causal interpretation, or describe the exercise example as an association." \
    >/dev/null

  PYTHONPATH="$repo_root/src" python -m katz.cli report generate \
    --output "$repo_root/docs/investigated-review.html"
)
