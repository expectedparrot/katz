# katz — Package Overview

katz is a version-aware ledger for academic paper review artifacts. It ties manuscripts,
issues, investigations, and spotters to specific git commit SHAs, making reviews
reproducible: the same commit always resolves to the same paper state.

---

## When to Use katz

Use katz when you want to:
- Systematically review an academic paper for substantive issues
- Run parallel LLM-based review across multiple sections and spotters using EDSL
- Track which flagged issues have been investigated and confirmed vs. rejected
- Generate a structured referee report or HTML review summary
- Use paper review as a standard research-agent task with a compiled review
  deliverable and a linked HTML explorer artifact

---

## Core Concepts

### Spotters

A spotter is a review criterion with a `scope` field:

- **`section`** — applied per-section (parallelizable across the manuscript)
- **`holistic`** — applied to the full manuscript (e.g., narrative consistency, introduction flow)

Spotters live as markdown files with optional YAML frontmatter:

```markdown
---
scope: section
---
# Overclaiming

Look for conclusions stronger than the evidence supports...
```

The **spotter catalog** (`.katz/spotters/`) is shared across versions. Run
`katz spotter init-catalog` to populate it with built-in spotters, then
`katz spotter enable <name>` to activate specific spotters for the current version.

### Issues

Each issue is a flagged problem with:
- An immutable `issue.json` (title, body, byte-anchored location, spotter)
- An append-only `status/` directory (state transitions)
- An append-only `investigations/` directory (verdict records)

**States:** `draft → open → confirmed | rejected | wontfix | resolved`

Issues are anchored to byte offsets in the canonical manuscript so locations
are precise and re-resolvable.

### Paper Map

The paper map (`paper_map.jsonl`) indexes:
- **Sections** — heading boundaries with byte and line ranges
- **Sentences** — individual sentences with byte offsets

Run `katz paper auto-chunk` to auto-detect sections. Use `katz paper find <text>`
to locate text by byte offset.

### Versions

A version is a registered git commit. All artifacts (issues, spotters, paper map) are
keyed to a specific commit SHA. The active version is stored in `.katz/ACTIVE_VERSION`.

---

## Storage Layout

```
.katz/
  ACTIVE_VERSION              # current commit SHA (40 hex chars)
  spotters/                   # spotter catalog (shared across versions)
    overclaiming.md
    logical_gaps.md
    ...
  evals/                      # eval criterion catalog (shared)
  versions/
    {commit}/
      version.json            # registration metadata
      paper/
        manuscript.md         # canonical manuscript (one sentence per line)
      paper_map.jsonl         # header + sections + sentences
      spotters/               # enabled spotters for this version
      evals/                  # enabled eval criteria
      eval_results/           # eval responses
      issues/
        {id}/
          issue.json          # immutable original record
          status/             # append-only state change events
          investigations/     # append-only investigation records
          suggestions/        # suggested fixes
      chunks/                 # chunk records
```

---

## Output Format

All katz commands emit one JSON envelope to stdout:

```json
{"ok": true, "command": ["paper", "status"], "data": {...}}
{"ok": false, "command": ["paper", "status"], "error": {"code": "error_code", "message": "message here", "details": {...}}}
```

Branch on `ok`, read successful results from `data`, and structured failures
from `error`. Exit code 1 on error, 0 on success.

---

## Requirements

- Python 3.11+
- A git repository (`katz init` must be run at the repo root)
- `katz` on PATH (installed via `pip install -e .` or `pip install katz`)

---

## Quick Command Reference

```bash
katz init                             # initialize .katz/
katz paper register --canonical <f>  # register a paper
katz paper auto-chunk                 # detect sections
katz paper status                     # check registration
katz spotter init-catalog             # populate spotter catalog
katz spotter enable <name>            # enable a spotter
katz issue list --state draft         # list draft issues
katz issue investigate --id <id> --verdict confirmed --notes "..."
katz report generate                  # generate HTML report
katz status                      # bootstrap payload for agents
katz docs list                        # list documentation topics
```

See `katz docs show cli-reference` for the complete command reference.
