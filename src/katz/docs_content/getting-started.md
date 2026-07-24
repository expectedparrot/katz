# Getting Started

This guide walks through a complete paper review using katz — from initialization
to a generated HTML report.

**At each step, check whether it's already done before re-running it.**

---

## Step 0 — Bootstrap

Run `katz status` first. It returns the current phase and recommended next steps.
If the phase is `no_katz`, begin with Step 1. Otherwise skip ahead to the appropriate step.

```bash
katz status
```

The response includes `state.phase` (where you are) and `state.checklist` (what to do next).

---

## Step 1 — Initialize

katz requires a git repository. Initialize `.katz/` at the repo root:

```bash
katz init
```

**Check:** `katz paper status` returns an error with code `invalid_commit` when no paper
is registered yet — that's expected.

---

## Step 2 — Register the Paper

The canonical manuscript must be Markdown. For a PDF, Katz delegates extraction
to `paper2md`. For LaTeX, it recursively expands `\input` and `\include`, runs
Pandoc with Citeproc, strips `\resizebox` around inlined tables, restores the
title and abstract, flattens HTML anchors, inventories tables/figures/equations,
and refuses a conversion that appears to lose tables or media:

```bash
katz paper prepare paper.pdf --output paper/manuscript.md
katz paper prepare manuscript/main.tex --output paper/manuscript.md
```

Textual `\input`/`\include` dependencies must remain inside the repository.
Graphics may live in a sibling output directory: Katz records them as external
binary assets and lets Pandoc copy them into the prepared bundle. A missing
graphic is reported as conversion loss and requires explicit `--allow-lossy`.

Ventilated prose (one sentence per line) makes issue locations more precise:

```bash
katz paper register \
  --canonical paper/manuscript.md \
  --source-format markdown \
  --source-uri https://arxiv.org/abs/2401.00000
```

**Check:**
```bash
katz paper status
# → {"commit": "abc...", "sections": 0, "sentences": 847, "valid": true}
```

If `warning` appears about non-ventilated prose, write a conservative Markdown
copy, inspect its diff, commit it, and register that committed version:

```bash
katz ventilate paper.md --output-path paper_ventilated.md
git diff --no-index paper.md paper_ventilated.md
```

`katz agent next` prefers a filename containing `ventilated` over its source
manuscript. If the derivative is untracked it proposes `git add`; if staged it
proposes a commit; only the committed file is then proposed for registration.
`katz paper register` rejects uncommitted repository files so their bytes cannot
be incorrectly associated with an older Git SHA.

---

## Step 3 — Detect Sections

Auto-detect section boundaries from markdown headings:

```bash
katz paper auto-chunk
# → {"added": 14, "total_sections": 14}
```

Verify:
```bash
katz paper sections
# → [{id: "introduction", title: "Introduction", ...}, ...]
```

Spot-check a section:
```bash
katz paper section introduction
katz paper resolve 0 200   # resolve byte range to text
```

---

## Step 4 — Configure Spotters

Initialize the built-in spotter catalog:

```bash
katz spotter init-catalog
# → {"preset": "default", "added": [...], "skipped": []}
```

List what's available:
```bash
katz spotter catalog
```

Enable the recommended review set in one operation:

```bash
katz spotter enable --recommended
```

Before a large run, build and run a small model-compatibility pilot:

```bash
katz spotter jobs --pilot 5 --output pilot.jobs.ep
ep run pilot.jobs.ep --model <model-name> --output pilot-results.ep
katz results audit pilot-results.ep --jobs pilot.jobs.ep
```

Proceed only when the audit reports `complete: true`. Null answers, malformed
objects, model exceptions, duplicate rows, and missing scenarios are failures,
not evidence that the paper has no issues.

Read a spotter before enabling to understand what it checks:
```bash
katz spotter catalog-show overclaiming
```

Verify what's enabled:
```bash
katz spotter list
```

---

## Step 5 — Build and Run an EDSL Jobs Object

Katz packages the manuscript sections and enabled spotters without making model calls:

```bash
katz spotter jobs --output jobs.ep
```

Inspect and estimate the standard EDSL object, then run it with the `ep` CLI:

```bash
ep inspect jobs.ep
ep jobs cost jobs.ep
ep run jobs.ep --model <model-name> --output results.ep
```

Finally, let Katz verify the returned quotations and create anchored draft issues:

```bash
katz results audit results.ep --jobs jobs.ep
katz spotter ingest results.ep --jobs jobs.ep
```

The complete Results object—including null findings and model provenance—remains in
`results.ep`. Read `katz docs show edsl-jobs` for all options.

---

## Step 6 — Investigate Issues

After the sweep, issues are in `draft` state. The false-positive rate is high (~5–10%
genuine). Work through them systematically.

**List all drafts:**
```bash
katz issue list --state draft
```

**Read a full issue record:**
```bash
katz issue show abc123    # use first 6+ chars of the ID
```

The record includes `location.resolved_text` — the exact manuscript text the issue
points to. Read it carefully before deciding.

**Record your verdict:**
```bash
# Genuine issue
katz issue investigate --id abc123 --verdict confirmed --notes "The paper claims X causes Y but the design only supports correlation."
katz issue update --id abc123 --state confirmed

# False positive
katz issue investigate --id abc123 --verdict rejected --notes "Context in section 4 addresses this — not a real issue."
katz issue update --id abc123 --state rejected
```

**Merge near-duplicates** (different models flagging the same thing):
```bash
katz issue list --state draft | python3 -c "
import sys, json
issues = json.load(sys.stdin)
for i in issues: print(i['id'][:8], i['title'][:60])
"
katz issue merge --ids abc123,def456,ghi789 --title "Concise merged title"
```

---

## Step 7 — Generate the Report

Once investigation is complete:

```bash
katz issue list --state confirmed    # review what's confirmed

katz report generate --output review.html
# → {"generated": true, "path": "review.html", "issues": 12}
```

Open `review.html` in a browser to see the full structured report with issue cards,
investigation verdicts, and manuscript quotes.

In a research-agent task workflow, prefer a task-local explorer path instead of
an ad hoc top-level file:

```bash
katz report generate --output writeup/artifacts/paper_explorer.html
```

Then use that HTML artifact as a linked companion from the task's
`writeup/report.md`.

---

## Step 8 — (Optional) Evaluate the Paper

For a broader quality assessment beyond issue-spotting, use eval criteria:

```bash
katz eval init-catalog
katz eval list
katz eval enable design_matches_claims
katz eval enable findings_clearly_presented
```

Then for each criterion, write a narrative response:
```bash
katz eval respond --name design_matches_claims \
  --text "The design matches the claims well. The IV strategy is convincing." \
  --grade A-
```

---

## Common Patterns

**Checking current state at any point:**
```bash
katz paper status        # registration, sections, sentences
katz spotter list        # enabled spotters
katz issue list          # all issues (any state)
```

**Reading an issue with full context:**
```bash
katz issue show <id>
# response includes location.resolved_text (the exact manuscript text)
```

**Filtering issues:**
```bash
katz issue list --state confirmed
katz issue list --section introduction
katz issue list --spotter overclaiming
```
