# katz

Version-aware ledger for paper review artifacts. Stores canonical manuscript representations, issue findings, investigations, and issue spotters keyed to specific git commits.

katz does not convert manuscripts, generate issues, or run analyses. It provides stable storage and a queryable interface. Workflow belongs to agents and external tools.

## Install

```bash
pip install -e .
```

## Quick start

```bash
cd ~/papers/my-paper
katz init
katz paper register \
  --canonical manuscript.md \
  --source-format pdf \
  --source-root writeup/paper.pdf

# Add section boundaries
katz paper add-sections --sections '[{"id":"intro","title":"Introduction","byte_start":0,"byte_end":5000}]'

# Populate the spotter catalog with defaults
katz spotter init-catalog --preset default

# Browse and enable spotters for this review
katz spotter catalog
katz spotter enable overclaiming
katz spotter enable logical_gaps
katz spotter enable unclear_writing

# File an issue
katz issue write \
  --title "Unsupported causal claim" \
  --byte-start 3200 --byte-end 3280 \
  --body "Causal language used but only correlational evidence presented." \
  --spotter overclaiming

# Investigate and update
katz issue investigate --id <id> --verdict confirmed --notes "Checked results section, no causal identification."
katz issue update --id <id> --state confirmed --reason "Investigation confirmed"
```

## Storage layout

```
.katz/
  ACTIVE_VERSION                    active commit SHA
  spotters/                         catalog (available to all versions)
    overclaiming.md
    logical_gaps.md
    identification_threats.md
    ...
  versions/
    {commit}/
      version.json                  registration metadata
      paper/
        manuscript.md               canonical one-sentence-per-line markdown
      paper_map.jsonl               section + sentence index
      symbol_table.json             notation definitions (written by agent)
      spotters/
        overclaiming.md             issue spotter definitions
        logical_gaps.md
      issues/
        {id}/
          issue.json                immutable original record
          status/
            20260411T145504_534957.json   state changes (append-only)
            20260411T145505_155781.json
          investigations/
            20260411T145504_844804.json   investigation records (append-only)
      chunks/
        {id}.json                   chunk definitions (written by agent)
```

### Design principles

- **Append-only**: Status changes and investigations are never overwritten. Each is a new file. Current state = latest file in `status/`. Full history is always preserved.
- **Git-native**: Every version is keyed to a full git commit SHA.
- **Byte-anchored**: Every finding references source text via half-open byte ranges `[byte_start, byte_end)` into the canonical manuscript.
- **Agent-first**: All commands output JSON.

## CLI reference

### katz init

```bash
katz init
```

Initialize `.katz/` in the current git repository.

### katz paper

```bash
katz paper register --canonical <path> [--source-format pdf] [--source-root writeup/paper.pdf]
```

Register a canonical manuscript for the current HEAD commit. Auto-generates sentence segmentation.

```bash
katz paper status
```

Show paper metadata: commit, sections, sentences, validity.

```bash
katz paper add-sections --sections '<json-array>'
```

Append section boundary records to the paper map.

```bash
katz paper section <id>
```

Show one section's byte range, line range, and title.

```bash
katz paper sentences [--section <id>] [--from-line N] [--to-line N]
```

Return the sentence index, optionally filtered.

```bash
katz paper resolve <byte-start> <byte-end>
```

Resolve a byte range into text, line numbers, and section.

```bash
katz paper find <text> [--ignore-case] [--limit N]
```

Find text in the canonical manuscript. Returns byte offsets.

### katz spotter

Spotters define what to look for during review. There are two layers:

- **Catalog** (`.katz/spotters/`) — all available spotters, shared across versions
- **Active** (`.katz/versions/<commit>/spotters/`) — spotters enabled for a specific review

#### Catalog management

```bash
katz spotter init-catalog [--preset social-science]
```

Populate the catalog with default spotters. The `social-science` preset includes 13 spotters covering overclaiming, logical gaps, statistical errors, methodology, writing clarity, identification threats, and more.

```bash
katz spotter catalog [--scope section|holistic]
```

List all available spotters in the catalog.

```bash
katz spotter catalog-show <name>
```

Show a catalog spotter's full description and investigation instructions.

#### Enabling spotters for a review

```bash
katz spotter enable <name>
```

Copy a spotter from the catalog into the active version. Only enabled spotters are used during review.

```bash
katz spotter add --name "prompt_sensitivity" \
  --scope section \
  --description "Check whether results depend on specific prompt wording." \
  --investigation "Check if alternative prompts are tested."
```

Add a custom paper-specific spotter directly to the active version (not the catalog).

```bash
katz spotter add --file my_spotter.md [--name custom_slug]
```

Add from a markdown file. Validates frontmatter (scope, heading).

```bash
katz spotter list [--scope section|holistic]
```

List spotters enabled for the active version.

```bash
katz spotter show <name>
```

Show an enabled spotter's parsed content (scope, description, investigation instructions).

```bash
katz spotter remove <name>
```

Remove a spotter from the active version.

### katz issue

Issues use a directory-per-issue layout with append-only subdirectories for status changes and investigations.

#### Valid states

`draft` | `open` | `confirmed` | `rejected` | `resolved` | `wontfix`

#### Write

```bash
katz issue write \
  --title "Short description" \
  --byte-start 3200 --byte-end 3280 \
  --body "Explanation of the issue." \
  [--spotter overclaiming] \
  [--state draft] \
  [--meta '{"severity": "major"}']
```

Creates `issues/<id>/issue.json` (immutable) and an initial status record in `issues/<id>/status/`. The `--spotter` flag validates that the named spotter is registered.

#### Update state

```bash
katz issue update --id <id> --state confirmed [--reason "Investigation confirmed"]
```

Appends a new file to `issues/<id>/status/`. Never overwrites prior state.

#### Investigate

```bash
katz issue investigate \
  --id <id> \
  --verdict confirmed \
  [--notes "Checked the LaTeX source, claim is not supported."] \
  [--evidence '["line 45: no causal design", "appendix omits robustness check"]']
```

Appends a new file to `issues/<id>/investigations/`. Verdicts: `confirmed`, `rejected`, `uncertain`.

#### Show

```bash
katz issue show <id>
```

Returns the full issue record with current state (derived from latest status file), `status_history` (all state changes), and `investigations` (all investigation records).

```json
{
  "schema_version": 2,
  "id": "4a4ea277e3f84cd1895e787179e7dd72",
  "commit": "6f1dba8d...",
  "title": "Unsupported causal claim",
  "body": "...",
  "spotter": "overclaiming",
  "location": {
    "byte_start": 3200,
    "byte_end": 3280,
    "line_start": 25,
    "line_end": 25,
    "resolved_text": "This proves that...",
    "contains_math": false
  },
  "created_at": "2026-04-11T14:50:39Z",
  "meta": {},
  "state": "confirmed",
  "status_history": [
    {"state": "draft", "reason": "created", "timestamp": "2026-04-11T14:50:39Z"},
    {"state": "open", "reason": "triaged", "timestamp": "2026-04-11T14:50:50Z"},
    {"state": "confirmed", "reason": "investigation confirmed", "timestamp": "2026-04-11T14:51:02Z"}
  ],
  "investigations": [
    {"verdict": "confirmed", "timestamp": "2026-04-11T14:50:55Z", "notes": "..."}
  ]
}
```

#### List

```bash
katz issue list [--state confirmed] [--section intro] [--spotter overclaiming] [--meta severity=major]
```

Returns issue summaries with current state, spotter, section, and meta. All filters are optional and combinable.

### katz validate

```bash
katz validate [--commit <sha>]
```

Validates the version's structure: manuscript checksum, issue directories, status files, investigation files, and chunk records. Returns `{"valid": true/false, ...}`.
