# CLI Quick Reference

All commands emit JSON by default. Errors use `{"error": "...", "code": "...", "details": {...}}`.

---

## Top-Level Commands

```bash
katz init                             # Initialize .katz/ in current git repo
katz validate [--commit <sha>]        # Validate version integrity
katz status [--no-guide]         # Bootstrap payload with phase + codegen script
```

---

## `katz paper` — Manuscript Management

```bash
katz paper register \
  --canonical <file>          # path to markdown manuscript (required)
  [--source-format markdown]  # markdown (default), tex, latex
  [--source-uri <url>]        # e.g. https://arxiv.org/abs/...
  [--source-root <name>]      # short name for the paper
  [--source-method <how>]     # how the PDF was converted
  [--source-meta '{"k":"v"}'] # extra metadata JSON

katz paper status                     # check registration + counts
katz paper sections [--commit <sha>]  # list all sections
katz paper section <id>               # show one section
katz paper sentences [--section <id>] [--from-line N] [--to-line N]
katz paper resolve <byte_start> <byte_end>  # resolve byte range to text
katz paper find <text> [--mode exact] [--ignore-case] [--limit 20]

katz paper auto-chunk [--commit <sha>]     # auto-detect sections from headings
katz paper add-sections --sections '[...]' # append sections manually
```

---

## `katz spotter` — Review Criteria

```bash
katz spotter init-catalog [--preset default]   # populate from built-in catalog
katz spotter catalog [--scope section|holistic] # list available spotters
katz spotter catalog-show <name>               # show a spotter's content

katz spotter enable <name> [--commit <sha>]    # enable for current version
katz spotter list [--scope section|holistic]   # list enabled spotters
katz spotter show <name> [--commit <sha>]      # show enabled spotter content
katz spotter remove <name>                     # remove enabled spotter
katz spotter add \
  --name <name> \
  --scope section|holistic \
  --description "..." \
  [--investigation "..."]    # add custom spotter to catalog + auto-enable
```

---

## `katz issue` — Issue Records

```bash
katz issue write \
  --title "Short title" \
  --byte-start N \
  --byte-end N \
  --body "Full description" \
  [--state draft] \
  [--spotter <name>] \
  [--artifacts "file1.py,file2.py"] \
  [--meta '{"key":"value"}']

katz issue list \
  [--state draft|open|confirmed|rejected|wontfix|resolved] \
  [--section <id>] \
  [--spotter <name>] \
  [--meta "key=value"]

katz issue show <id-prefix>
katz issue show --ids <id1>,<id2>,<id3>

katz issue update \
  --id <id-prefix> \
  --state <state> \
  [--reason "..."]

katz issue investigate \
  --id <id-prefix> \
  --verdict confirmed|rejected|uncertain \
  [--notes "..."] \
  [--evidence "..."] \
  [--state confirmed]        # also update state in same call

katz issue suggest \
  --id <id-prefix> \
  --text "Suggested fix..."

katz issue merge \
  --ids <id1>,<id2>,<id3> \
  [--title "Merged title"] \
  [--body "Combined body"]
```

**Issue states:** `draft` → `open` → `confirmed` | `rejected` | `wontfix` | `resolved`

**Verdicts:** `confirmed`, `rejected`, `uncertain`

---

## `katz eval` — Evaluation Criteria

```bash
katz eval init-catalog [--preset default]      # populate from built-in catalog
katz eval catalog [--category <cat>]            # list available criteria
katz eval catalog-show <name>                   # show a criterion's content

katz eval enable <name>                         # enable for current version
katz eval list [--category <cat>]               # list enabled criteria
katz eval show <name>                           # show enabled criterion
katz eval remove <name>                         # remove enabled criterion

katz eval add \
  --name <name> \
  [--question "..."] \
  [--scope <scope>] \
  [--category <cat>] \
  [--file <path>]

katz eval respond \
  --name <name> \
  --text "Narrative response..." \
  [--grade A+|A|A-|...|F] \
  [--suggestion "..."]

katz eval results [--category <cat>]            # list all responses
```

---

## `katz report` — HTML Report

```bash
katz report generate \
  [--output .katz/review.html] \
  [--commit <sha>]
```

---

## `katz docs` — Documentation

```bash
katz docs list                        # list all topics
katz docs show <topic>                # show a topic's markdown
katz docs search <query>              # search across all docs

# Available topics:
#   overview         — concepts, storage layout, output format
#   getting-started  — step-by-step review walkthrough
#   workflow         — phase-by-phase reference
#   edsl-codegen     — EDSL script generation guide
#   cli-reference    — this document
```

---

## `katz guide` — Skills Reference

```bash
katz guide overview          # show OVERVIEW.md
katz guide skills            # list all skills
katz guide skill <name>      # show a skill's SKILL.md
katz guide script <path>     # show a script file (e.g. edsl-find-issues/edsl_find_issues.py)
```

---

## Tips

**ID resolution:** Issue IDs are 32-char hex. Pass 6+ chars of an unambiguous prefix.

**Commit resolution:** Pass a full 40-char SHA or an unambiguous prefix. Defaults to
the active version if omitted.

**Byte ranges:** All locations are half-open `[byte_start, byte_end)` byte offsets into
the canonical manuscript. Use `katz paper find <text>` to look up offsets.

**Filtering by section:** The `--section` flag filters by the section ID from `katz paper sections`.
