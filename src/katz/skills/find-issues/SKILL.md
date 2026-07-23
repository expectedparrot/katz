---
name: find-issues
description: Read the paper and file issues for problems found in the manuscript
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Find Issues

Reads the registered canonical manuscript section by section, identifies issues (errors, unclear writing, inconsistencies, missing details, etc.), and files each one with `katz issue write`.

## Usage

```
/find-issues [section-id]
```

- With no argument, scan the entire manuscript.
- With a section ID (e.g., `introduction`, `phase-2-results`), scan only that section.

## Prerequisites

- The paper must be registered and chunked (`katz paper status` should return `"valid": true` with `sections > 0`).
- `katz` must be on PATH.

## Workflow

### 1. Validate preconditions

Run `katz paper status` and confirm `"valid": true` and that sections exist. If not, stop and tell the user to run `/register-paper` and `/chunk-paper` first.

### 2. Determine scope

- If a section ID was provided, use `katz paper section <id>` to get its byte range and line range, then read just that portion of the manuscript.
- If no section ID was provided, list all sections with `katz paper status` and process each section in turn.

The canonical manuscript lives at `.katz/versions/<commit>/paper/manuscript.md` where `<commit>` comes from `katz paper status`.

### 3. Read and analyze

For each section in scope, read the manuscript text and look for issues. Focus on:

- **Factual / logical errors**: incorrect claims, contradictions between sections, math errors
- **Unclear or ambiguous writing**: sentences that are hard to parse, missing antecedents, vague quantifiers
- **Inconsistencies**: terminology that changes between sections, numbers that don't match across text and figures/tables
- **Missing information**: claims without evidence, undefined terms, methods described incompletely
- **Grammar and typos**: misspellings, subject-verb disagreement, malformed sentences
- **PDF conversion artifacts**: broken references, garbled text, orphaned span tags that hurt readability

Do NOT flag:
- Stylistic preferences or subjective rewording suggestions
- LaTeX/markdown formatting choices that are correct
- Issues in the references section unless a reference is clearly broken or duplicated

### 4. File each issue

For each issue found, locate the problematic text using `katz paper find` to get precise byte offsets. Then file the issue:

```bash
katz issue write \
  --title "<short descriptive title>" \
  --byte-start <start> \
  --byte-end <end> \
  --body "<explanation of the issue and suggested fix if applicable>"
```

Issues are created in `draft` state. Each issue gets its own directory under `issues/<id>/` with:
- `issue.json` — the immutable original record
- `status/` — append-only state changes (draft → open → confirmed → resolved, etc.)
- `investigations/` — append-only investigation records

To update an issue's state later:
```bash
katz issue update --id <id> --state <state> --reason "why"
```

To record an investigation:
```bash
katz issue investigate --id <id> --verdict confirmed --notes "explanation"
```

Valid states: `draft`, `open`, `confirmed`, `rejected`, `resolved`, `wontfix`.

Guidelines for issues:
- **Title**: Short and specific (e.g., "Typo: 'disproportionately' misspelled", "Inconsistent participant count between sections").
- **Byte range**: Should span the specific problematic text, not the entire section. Use `katz paper find "<snippet>"` to get exact byte offsets. If the text is long or not unique, use a distinctive substring.
- **Body**: Explain what the problem is and, if possible, suggest a concrete fix. Keep it concise.

### 5. Report

After scanning all sections in scope, report to the user:
- How many issues were filed
- A summary table of issues (title, section, line range)
- Run `katz issue list` to confirm the issues are recorded
