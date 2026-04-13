---
name: edsl-find-issues
description: Run EDSL-parallelized issue finding across all sections of a katz-registered manuscript
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# EDSL Find Issues

Runs `edsl_find_issues.py` to scan the katz-registered manuscript for issues in parallel using EDSL. The script is scope-aware: `scope: section` spotters run per-section, while `scope: holistic` spotters run once on the full manuscript. This avoids false positives from holistic spotters (e.g., `introduction_flow`) being applied to individual non-introduction sections.

Uses two frontier models by default (pass `--models 3` for three):
- Claude Opus (default)
- GPT-5.4 with reasoning_effort=high (default)
- Gemini 3.1 Pro with thinking_budget=10000 (opt-in with `--models 3`)

## Spotter selection

By default, the script uses **katz-enabled spotters** from `.katz/versions/<commit>/spotters/`. If no spotters are enabled, it falls back to 5 built-in spotters. You can override with:
- `--spotters-dir <path>` — use custom `.md` files from a directory
- `--builtin-spotters` — force use of the 5 built-in spotters

## Usage

```
/edsl-find-issues [section-id] [--spotters-dir <path>] [--builtin-spotters] [--models N]
```

- With no argument, scan all non-reference sections.
- With a section ID (e.g., `introduction`), scan only that section.

## Prerequisites

- The paper must be registered in katz (`katz paper status` should return `"valid": true`).
- Sections must exist (`"sections"` > 0 in paper status).
- `edsl` must be installed (`pip install edsl`).
- `katz` must be on PATH.

## Workflow

### 1. Validate preconditions

Run `katz paper status` and confirm `"valid": true` and that sections exist. If not, stop and tell the user to run `/register-paper` and `/chunk-paper` (or `katz paper auto-chunk`) first.

### 2. Build and run the command

```bash
# All sections, katz-enabled spotters, 2 models
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py

# Single section
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --section introduction

# 3 models (adds Gemini)
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --models 3

# Force built-in spotters
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --builtin-spotters

# Dry run (shows scenario count without calling models)
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --dry-run
```

The script will:
- Read sections directly from katz (no further chunking — sections are the unit)
- Load spotters from katz-enabled spotters, falling back to built-ins
- Parse each spotter's YAML frontmatter to determine its `scope` (section or holistic)
- Build EDSL scenarios: `(sections × section_spotters × models)` + `(1 × holistic_spotters × models)`
- Run the survey in parallel via EDSL remote runner (section spotters batched by 3 sections; holistic spotters in one batch)
- Parse results (handles both JSON and Python-repr single-quoted dicts)
- File issues via `katz issue write` with the `--spotter` field set
- Deduplicate near-identical issues (overlapping byte ranges + similar titles)

Issues are filed in `draft` state. Each issue body is tagged with the spotter and model that found it, e.g., `[overclaiming] [gpt-5.4] ...`. The `spotter` field is also set on the issue record for structured filtering.

### 3. Expect a high false-positive rate

EDSL sweeps are intentionally broad. From experience:
- A 14-spotter × 13-section × 3-model sweep (546 calls) produces ~200–250 candidate issues.
- After investigation, only **5–10% are confirmed** as genuine issues.
- Common false positives: PDF conversion artifacts, issues resolved by context in other sections, "ambiguous pronoun" that isn't ambiguous, missing caveats that appear elsewhere, and many duplicates across models.

The next step is always `/investigate-issues` to separate signal from noise.

### 4. Report

After the script completes, report:
- How many scenarios were run
- How many issues were found and filed (and how many were deduplicated)
- Run `katz issue list` to show the updated issue list
- Regenerate the HTML report: `python <katz-skills-path>/find-issues/scripts/generate_review_report.py`

### 5. Built-in spotters

The script includes 5 built-in issue spotters (used when no katz spotters are enabled):
- `logical_gaps` — argument skips a step or claim doesn't follow
- `overclaiming` — conclusions stronger than evidence supports
- `internal_contradictions` — statements that contradict each other
- `unclear_writing` — passages difficult to understand
- `methodology_errors` — problems with research design or statistics
