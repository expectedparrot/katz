# CLI Quick Reference

All commands emit one JSON envelope by default. For a Rich terminal summary,
place the global option before the command: `katz --human COMMAND ...`.

```json
{"status": "ok", "command": "katz paper status", "data": {...}, "warnings": [], "errors": [], "next_steps": []}
```

Failures use the same top-level contract:

```json
{"status": "error", "command": "katz paper status", "data": {}, "warnings": [], "errors": [{"code": "...", "message": "...", "context": {}}], "next_steps": []}
```

---

## Top-Level Commands

```bash
katz init                             # Initialize .katz/ in current git repo
katz ventilate <input.md> --output-path <output.md> [--force]
katz validate [--commit <sha>]        # Validate version integrity
katz repair [--commit <sha>] [--check]  # Hydrate derived fields, recreate scaffolding
katz next                             # Highest-priority next action (alias of agent next)
katz version                          # Installed build, agent API version, capabilities
```

---

## `katz version` — Registered Versions

```bash
katz version list                     # registered versions with issue counts + active flag
katz version checkout <sha>           # point ACTIVE_VERSION at another registered commit
katz version diff <sha-a> <sha-b> [--limit 200]  # section-aware manuscript diff
```

`diff` reports `modified_sections`, `unchanged_sections`, and per-line
`changed`/`added`/`removed` records with before/after text.

---

## `katz workspace` — Standalone Review Workspaces

```bash
katz workspace new <dir> \
  --canonical <prepared.md> \
  [--source <path-or-url>] \
  [--source-format markdown] \
  [--source-method workspace-new]

katz workspace new <dir> \
  --from <paper.pdf|main.tex|draft.md> \
  [--backend auto|marker|pymupdf] \
  [--model <model-name>]
```

With `--canonical`: creates the directory, initializes git, copies the
prepared Markdown (plus a local `--source` file and sibling images), commits
the bundle, initializes `.katz/`, and registers the commit as the first
active version.

With `--from`: additionally converts PDF/LaTeX sources first (`paper prepare`),
ventilates, then after registration maps sections, enables the recommended
spotters, and builds `jobs.ep` (plus `models.ep` when `--model` is given).
Each composed step's envelope is recorded under `steps`; failures are recorded
and dependent steps skipped rather than aborting the created workspace. The
command never executes model calls — it stops at the `ep run` boundary, and
its `next_steps` require inspecting the converted manuscript first.

---

## `katz paper` — Manuscript Management

```bash
katz paper register \
  --canonical <file>          # path to markdown manuscript (required)
  [--source-format markdown]
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
katz paper review-jobs --output one-shot-review.jobs.ep  # whole paper + figures

katz paper auto-chunk [--commit <sha>]     # auto-detect sections from headings
katz paper prepare paper.pdf --output paper.md
katz paper prepare manuscript/main.tex --output paper.md
# PDF uses paper2md; LaTeX expands input/include and uses Pandoc with a
# structural table/figure audit. --allow-lossy requires deliberate acceptance.
# LaTeX preparation also writes <output>.provenance.json mapping each section
# heading to the source file that supplied it; ventilate copies the sidecar and
# register stores it, so `paper sections` can report source_file per section.
katz paper add-sections --sections '[...]' # append sections manually
```

Repository-local canonical files must be committed and clean. Prepared
ventilated derivatives receive candidate priority; the agent state proposes
stage, commit, and registration as separate transitions.

LaTeX text dependencies cannot cross the repository boundary. Existing
`\includegraphics` files may: they are recorded as external binary assets and
copied into the prepared media bundle. Missing graphics are blocking conversion
warnings unless `--allow-lossy` is explicitly authorized.

---

## `katz spotter` — Review Criteria

```bash
katz spotter init-catalog [--preset default]   # populate from built-in catalog
katz spotter enable --recommended              # activate the default set
katz spotter jobs --pilot 5 --output pilot.jobs.ep
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

katz spotter jobs \
  [--output jobs.ep] \
  [--section <id>] \
  [--spotters <name1>,<name2>] \
  [--pilot 5] \
  [--commit <sha>]            # build a standard EDSL Jobs package

katz spotter ingest results.ep \
  [--jobs jobs.ep] \
  [--allow-partial] \
  [--state draft] \
  [--commit <sha>]            # audit, verify, and file valid EDSL findings
```

Multi-model runs: findings from different models that flag overlapping text for
the same spotter are merged into one issue whose `meta.agreement` records the
fraction of run models that flagged it (`meta.edsl_models` lists them).

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
  [--state <state>] \
  [--title "..."] [--body "..."] [--meta '{"k":"v"}'] \
  [--reason "..."]
# State changes append status records; title/body/meta changes append edit
# events. issue.json is never rewritten, so history stays inspectable.

katz issue patch <id-prefix> <field> <value>
# Set one meta field (value parsed as JSON when valid, else stored as string).

katz issue carry-forward \
  --to <sha> [--from <sha>] \
  [--states confirmed,open] \
  [--apply]
# Check which issues' anchored text survives in another registered version.
# Read-only by default; --apply files draft issues in the target version with
# meta.parent_issue_id. Ambiguous and missing passages are reported, not guessed.

katz issue clusters                   # suggest overlapping/duplicate candidates

katz issue investigate \
  --id <id-prefix> \
  --verdict confirmed|rejected|uncertain \
  [--notes "..."] \
  [--evidence "..."] \
  [--state confirmed]        # also update state in same call

katz issue next \
  [--limit 10] \
  [--output investigation-packet.json]
# Return deterministic, stale-safe packets with manuscript context, issue
# revision tokens, and duplicate-cluster metadata. The default limit is 1.

katz issue investigate-batch \
  --input verdicts.json \
  [--apply] \
  [--allow-partial]
# Preview and validate by default. --apply writes the same per-issue events as
# the single-item command. Atomic validation is the default; exact replay is a
# successful no-op. --allow-partial applies only decisions that validate.

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

## `katz review` — Human Journal Reviews

```bash
katz review add review.md \
  [--reviewer "Reviewer 2"] [--venue "Journal"] [--round R1]
katz review list
katz review jobs <review-id> --output journal-review.jobs.ep
katz review ingest journal-review-results.ep [--state draft]
```

`add` preserves the original human report with the active manuscript version.
`jobs` attaches that report and the canonical manuscript to an EDSL parsing job.
`ingest` files only comments whose proposed quotation resolves in the manuscript.

---

## `katz agent` — Machine-readable Workflow Discovery

```bash
katz agent bootstrap                 # read-only prerequisite and repository scan
katz agent status                    # phase, blockers, review state, next actions
katz agent next                      # highest-priority action and alternatives
katz agent instructions codex        # return AGENTS.md template
katz agent instructions claude       # return CLAUDE.md template
katz agent instructions --write      # write both repository-native templates
katz capabilities                    # contracts, schemas, integrations, safety
katz ingest <path>                   # detect and preview an artifact
katz ingest <path> --apply           # apply supported Results ingestion
katz issue next                      # complete packet for one draft issue
```

Agent actions contain a command array plus `mutates_state`, `requires_network`,
and `requires_user_approval` booleans. `agent bootstrap` and bare `ingest` are
read-only. Unified ingestion currently applies spotter Results and parsed
journal-review Results; Jobs, whole-paper reports, and Humanize Results return
the appropriate inspection or judgment step instead.

Authentication is delegated to EDSL:

```bash
ep auth login          # browser login; stores repository-local .env
ep profiles current    # redacted active profile and file configuration
ep profiles list       # available repository-local profiles
ep profiles set NAME   # update EDSL's managed .env block
ep check               # verify URL reachability and authentication
```

`katz agent bootstrap` consumes the redacted `ep profiles current` response.
It never returns a key. When authentication is missing, the proposed action is
`ep auth login`; before a paid run, the state machine proposes `ep check`.

Results are audited before ingestion:

```bash
katz results audit results.ep --jobs jobs.ep
katz results sample results.ep --valid 5
katz results failures results.ep
```

Ingestion fails closed unless every expected scenario has a valid structured
answer. Use `--allow-partial` only to preserve valid rows from a damaged run;
the run remains partial and reports cannot present it as a complete review.

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
#   edsl-jobs        — EDSL Jobs and Results workflow
#   cli-reference    — this document
```

---

## `katz guide` — Skills Reference

```bash
katz guide overview          # show OVERVIEW.md
katz guide skills            # list all skills
katz guide skill <name>      # show a skill's SKILL.md
katz guide script <path>     # show a bundled helper script
```

---

## Tips

**ID resolution:** Issue IDs are 32-char hex. Pass 6+ chars of an unambiguous prefix.

**Commit resolution:** Pass a full 40-char SHA or an unambiguous prefix. Defaults to
the active version if omitted.

**Byte ranges:** All locations are half-open `[byte_start, byte_end)` byte offsets into
the canonical manuscript. Use `katz paper find <text>` to look up offsets.

**Filtering by section:** The `--section` flag filters by the section ID from `katz paper sections`.
