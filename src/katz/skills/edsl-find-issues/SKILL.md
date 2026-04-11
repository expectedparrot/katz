---
name: edsl-find-issues
description: Run EDSL-parallelized issue finding across all sections of a katz-registered manuscript
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# EDSL Find Issues

Runs `edsl_find_issues.py` to scan the katz-registered manuscript for issues in parallel using EDSL. Each (section × issue-spotter × model) combination runs as a separate EDSL scenario, enabling full parallelism across the matrix.

Uses three frontier models with thinking/reasoning maxed out:
- Claude Opus
- GPT-5.4 (reasoning_effort=high)
- Gemini 3.1 Pro (thinking_budget=10000)

## Usage

```
/edsl-find-issues [section-id] [--spotters-dir <path>]
```

- With no argument, scan all non-reference sections.
- With a section ID (e.g., `introduction`), scan only that section.
- With `--spotters-dir`, use custom spotter `.md` files instead of built-ins.

## Prerequisites

- The paper must be registered in katz (`katz paper status` should return `"valid": true`).
- `edsl` must be installed (`pip install edsl`).
- `katz` must be on PATH.

## Workflow

### 1. Validate preconditions

Run `katz paper status` and confirm `"valid": true` and that sections exist. If not, stop and tell the user to run `/register-paper` and `/chunk-paper` first.

### 2. Build and run the command

**Preferred**: Use katz-enabled spotters (configured via `/configure-spotters`) rather than the 5 built-in spotters. The enabled spotters are stored as `.md` files in the version's spotters directory:

```bash
# Find the spotters directory
COMMIT=$(katz paper status | python3 -c "import sys,json; print(json.load(sys.stdin)['commit'])")
SPOTTERS_DIR=".katz/versions/${COMMIT}/spotters"

# Dry run first to see the matrix size
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py \
  --spotters-dir "$SPOTTERS_DIR" --dry-run

# Run the full sweep
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py \
  --spotters-dir "$SPOTTERS_DIR"
```

**Fallback**: Without `--spotters-dir`, the script uses 5 built-in spotters:

```bash
# All sections with built-in spotters
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py

# Single section
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --section introduction
```

The script will:
- Read sections directly from katz (no further chunking — sections are the unit)
- Build EDSL scenarios for the (section × spotter × model) cross-product
- Run the survey in parallel via EDSL remote runner (batched by 3 sections to stay within payload limits)
- Parse results (handles both JSON and Python-repr single-quoted dicts)
- File issues via `katz issue write`

Issues are filed in `draft` state. Each issue body is tagged with the spotter and model that found it, e.g., `[overclaiming] [gpt-5.4] ...`.

### 3. Expect a high false-positive rate

EDSL sweeps are intentionally broad. From experience:
- A 14-spotter × 13-section × 3-model sweep (546 calls) produces ~200–250 candidate issues.
- After investigation, only **5–10% are confirmed** as genuine issues.
- Common false positives: PDF conversion artifacts, issues resolved by context in other sections, "ambiguous pronoun" that isn't ambiguous, missing caveats that appear elsewhere, and many duplicates across models.

The next step is always `/investigate-issues` to separate signal from noise.

### 4. Report

After the script completes, report:
- How many scenarios were run
- How many issues were found and filed
- Run `katz issue list` to show the updated issue list
- Optionally regenerate the HTML report: `python <katz-skills-path>/find-issues/scripts/generate_review_report.py`

### 5. Built-in spotters

The script includes 5 built-in issue spotters:
- `logical_gaps` — argument skips a step or claim doesn't follow
- `overclaiming` — conclusions stronger than evidence supports
- `internal_contradictions` — statements that contradict each other
- `unclear_writing` — passages difficult to understand
- `methodology_errors` — problems with research design or statistics

Custom spotters can be added as `.md` files in a directory and passed with `--spotters-dir`. The recommended approach is to use `/configure-spotters` to curate spotters via katz, then pass the version's spotters directory to the script.
