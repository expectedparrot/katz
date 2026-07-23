---
name: review-paper
description: Bootstrap a full paper review using katz
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Review Paper

Orchestrates a full paper review using katz. Start here.

## Usage

```
/review-paper
```

## Workflow

Begin with the read-only agent contract:

```bash
katz agent bootstrap
katz agent next
```

Follow the returned command arrays only after checking their mutation, network,
and approval flags. Call `katz agent next` again after each completed action.
Run `katz capabilities` when feature or schema discovery is needed. The
step-by-step procedure below explains the judgment behind that state machine.

Run `katz guide overview` for additional background.

Then follow the typical workflow. At each step, **check prerequisites before proceeding** — if a step is already done, skip it.

### 1. Register the paper

Check: `katz paper status` — if `"valid": true`, skip to step 2.

Otherwise: `katz guide skill register-paper` — convert the PDF to markdown and register it.

### 2. Chunk into sections

Check: `katz paper status` — if `"sections"` > 0, skip to step 3.

Quick path: `katz paper auto-chunk` — automatically detects markdown headings and creates sections. Verify with `katz paper status` and spot-check a few sections with `katz paper section <id>`.

Manual path: `katz guide skill chunk-paper` — for more control over section boundaries.

### 3. Configure spotters

Check: `katz spotter list` — if spotters are already enabled, skip to step 4.

Follow: `katz guide skill configure-spotters` — read the paper, enable relevant spotters from the catalog, and add custom ones for paper-specific concerns.

### 4. Evaluate the paper

Check: `katz eval list` — if criteria are already enabled, proceed. Otherwise:

```bash
katz eval init-catalog
```

Then enable the criteria you want (or enable all):

```bash
# Enable specific ones
katz eval enable abstract_conveys_findings
katz eval enable design_matches_claims
# ... etc
```

Follow: `katz guide skill eval-paper` — read the paper and write narrative responses for each criterion.

### 5. Find issues

Build and run the review as portable EDSL objects:

```bash
katz spotter jobs --output jobs.ep
ep run jobs.ep --model <model-name> --output results.ep
katz spotter ingest results.ep
```

`katz spotter jobs` does not choose a model or run a script. It serializes the
enabled spotters, paper sections, manuscript context, and Katz provenance into
a standard EDSL `Jobs` object. `ep run` executes that object; Katz then verifies
the returned quotations before filing draft issues.

For an unusually long remote interview, set the interview deadline explicitly:

```bash
ep run jobs.ep \
  --model <model-name> \
  --task-timeout 900 \
  --output results.ep
```

`--task-timeout` is the maximum runtime for each remotely executed interview.
It is different from `--timeout`, which only limits local status polling when
`--background --wait` is used.

### Optional: one whole-paper expert review

Use a frontier model when the review requires reconciling claims across
sections or inspecting figures with the complete manuscript in context:

```bash
katz paper review-jobs --output one-shot-review.jobs.ep
ep run one-shot-review.jobs.ep \
  --model_list frontier-max.json \
  --task-timeout 900 \
  --fresh \
  --output one-shot-review-results.ep
```

The returned referee report is preserved in EDSL Results. An agent should
ground each actionable concern with `katz paper find` and file it with
`katz issue write`; the coherent report is not automatically treated as a list
of verified issues.

### Optional: ingest a human journal review

When a referee report, editor letter, or revise-and-resubmit review already
exists, preserve the original before parsing it:

```bash
katz review add reviews/reviewer-2.md \
  --reviewer "Reviewer 2" \
  --venue "Journal name" \
  --round R1
katz review jobs <review-id> --output journal-review.jobs.ep
ep run journal-review.jobs.ep \
  --model <model-name> \
  --task-timeout 900 \
  --output journal-review-results.ep
katz review ingest journal-review-results.ep
```

The parsing job receives both the preserved review and canonical manuscript.
It is instructed to preserve the human reviewer’s meaning, exclude praise and
editorial logistics, and return exact quotations for actionable comments.
Ingestion checks the commit, review ID, and manuscript quotation, skips
ungrounded candidates, and files the rest as draft issues carrying the exact
reviewer comment and source-review provenance. Inspect every parsed draft
before confirming it. Avoid committing confidential reviewer identities or
editor-only material to a repository that may become public.

### 6. Merge duplicate issues

The EDSL sweep often produces many near-duplicates (e.g., 5 issues about the same claim from different models). Before investigating, merge them:

```bash
katz issue merge --ids <id1>,<id2>,<id3> --title "Concise merged title"
```

This creates a single parent issue and marks the children as wontfix. Read through `katz issue list --state draft` and merge issues that point to the same underlying concern.

### 7. Investigate issues

Follow: `katz guide skill investigate-issues` — review each draft issue against the manuscript. Expect ~5–10% confirmation rate.

Use `katz issue next` to retrieve one deterministic investigation packet with
the full issue, numbered manuscript context, source-review metadata, frozen
spotter procedure, allowed verdicts, and exact follow-up command shape.

For each issue, read the manuscript context, determine a verdict (confirmed/rejected/uncertain), and record it with `katz issue investigate` and `katz issue update`.

### 8. Generate issue report / explorer

Run the report generator for the detailed issue-level HTML report:

```bash
katz report generate
```

Then open `.katz/review.html` to see the full report with issue cards, investigation verdicts, and manuscript quotes.

For research-agent task workflows, prefer writing the HTML artifact directly
into the active task tree instead:

```bash
katz report generate --output writeup/artifacts/paper_explorer.html
```

This task-local HTML artifact is the standard "paper explorer" companion page
that the main report can link to.

### 9. Write referee report / task report

Follow: `katz guide skill referee-report` — synthesize the investigated issues into a narrative referee report.

This produces `.katz/referee_report.md` — a structured, professional review suitable for sharing with authors or an editor.

For research-agent task workflows, do not stop at `.katz/referee_report.md`.
Use the Katz issue ledger and referee material to write the main user-facing
task report at `writeup/report.md`, include an HTML-only related-artifacts
block linking to `artifacts/paper_explorer.html`, and compile the task report
to `writeup/report.html` and `writeup/report.pdf`.

At each step, read the skill instructions and follow them. Use `katz guide script <path>` to inspect any scripts before running them.

## End-to-end timing

A typical review of a 30-page paper with 14 spotters takes:

| Step | Time |
|------|------|
| Register + chunk | 2–5 min (PDF conversion is the bottleneck) |
| Configure spotters | 2–3 min |
| Evaluate (eval-paper) | 3–5 min |
| EDSL sweep | 5–10 min (parallelized across EDSL remote runner) |
| Investigation | 5–10 min (batch script approach) |
| Report + referee report | 2–3 min |
| **Total** | **~20–35 min** |

## (Optional) Creating a GitHub issue

After the review, you can create a single GitHub issue summarizing all confirmed issues. Use `gh issue create` with a markdown body that includes:

1. **Pipeline summary** — how many calls, candidates, confirmed/rejected/uncertain
2. **Confirmed issues grouped by category** — e.g., "Identification & Design", "Statistical Interpretation", "Methodology", "Presentation"
3. **Each issue as a checkbox** (`- [ ]`) with:
   - Title and location (section, line number)
   - One-paragraph description of the problem
   - A blockquote of the relevant manuscript text
   - A concrete suggested fix
4. **Footer** noting the tool and commit hash

### Building the issue body

Fetch full details for confirmed issues:

```bash
katz issue list --state confirmed | python3 -c "
import sys, json, subprocess
for i in json.load(sys.stdin):
    full = json.loads(subprocess.run(
        ['katz','issue','show',i['id']], capture_output=True, text=True
    ).stdout)
    print(json.dumps(full))
"
```

Then compose the markdown body using the issue titles, investigation notes, and resolved text. Group related issues into categories. Use a HEREDOC with `gh issue create`:

```bash
gh issue create \
  --title "Review: N confirmed issues from multi-model sweep" \
  --body "$(cat <<'EOF'
## Paper Review — Automated Issue Sweep

... markdown body ...

*Generated by [katz](https://github.com/expectedparrot/katz) on YYYY-MM-DD. Review commit: `abcdef12`.*
EOF
)"
```

### Category suggestions for grouping

These categories work well for empirical economics papers:

- **Identification & Experimental Design** — SUTVA, selection, randomization concerns
- **Statistical Interpretation** — null-as-no-effect, multiple testing, power
- **Methodology** — collider bias, bad controls, conditioning on post-treatment
- **Presentation** — contradictory notes, math errors, generalizability of claims

Adapt categories to the paper's content.
