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

```bash
# All sections
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py

# Single section
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --section introduction

# Custom spotters
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --spotters-dir ./my_spotters

# Dry run (shows scenario count without calling models)
python <katz-skills-path>/edsl-find-issues/scripts/edsl_find_issues.py --dry-run
```

The script will:
- Read sections directly from katz (no further chunking — sections are the unit)
- Build EDSL scenarios for the (section × spotter × model) cross-product
- Run the survey in parallel via EDSL remote runner (batched by 3 sections to stay within payload limits)
- Parse results (handles both JSON and Python-repr single-quoted dicts)
- File issues via `katz issue write`

Issues are filed in `draft` state. Each issue body is tagged with the spotter and model that found it, e.g., `[overclaiming] [gpt-5.4] ...`.

### 3. After filing: investigate and triage

Issues from EDSL are raw — many will be false positives (especially conversion artifacts or context the model couldn't see). The next step is to investigate them:

```
/investigate-issues
```

This reviews each draft issue against the actual manuscript and code, records an investigation verdict (`confirmed`, `rejected`, or `uncertain`), and updates the issue state accordingly.

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

Custom spotters can be added as `.md` files in a directory and passed with `--spotters-dir`.
