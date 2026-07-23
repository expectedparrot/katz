# katz — version-aware paper-review ledger and issue tracker
<!-- id: katz/katz -->

katz manages structured reviews of manuscripts in a git-aware ledger: canonical paper versions, section boundaries, issue spotters, findings, evaluation criteria, responses, and review reports. The agent uses it to perform a disciplined paper review that anchors comments to a specific manuscript version and tracks issues from discovery through resolution.

## Installation

Install from the repository with Python 3.11 or newer:

```bash
python -m pip install .
```

For development, install in editable mode with the test dependency and run the test suite:

```bash
python -m pip install -e '.[test]'
pytest
```

## When to use this
<!-- id: katz/when-to-use -->

- The user wants a rigorous review of a paper, report, or manuscript.
- Comments need to be tied to a specific version, section, or byte/span location.
- The review should track issues, severity, status, spotter category, and author responses.
- The user needs a final review report rather than unstructured margin notes.
- The user wants a paper review to be handled as a normal research-agent task
  with a topic/task scaffold, version-aware critique artifacts, and a compiled
  written deliverable.

## When this is a stretch (and how to adapt)
<!-- id: katz/when-stretch -->

- The user wants a quick read-through. Use katz lightly: initialize, register the paper, add only high-value issues, and generate a short report.
- The source is not in a git repository. Put the review files in a git repo first if version anchoring matters.
- The user wants literature synthesis across many papers. Use [dewey](#dewey/dewey) for corpus management and katz for deep review of individual manuscripts.
- The manuscript is changing during review. Register each meaningful version and avoid mixing comments across versions.
- The user wants rubric scoring only. Use katz evaluation criteria, but still anchor material comments as issues when they affect the score.
- The report is a generated empirical study report rather than a traditional
  manuscript. Use katz lightly as the adversarial report-critique ledger:
  register `writeup/report.md`, auto-chunk sections, enable or add spotters for
  evidence-claim alignment, simulated-evidence overclaiming, qualitative-coding
  adequacy, missing appendices, weak plot/table interpretation, unsupported
  recommendations, and reproducibility. Then implement only the issues that
  materially improve the final report.
- The report lives inside a research-agent task scaffold under a larger git
  repository. Run katz from the git repository root and register the task report
  using a repo-relative path, such as
  `sessions/topic_x/task_y/writeup/report.md`. Katz stores `.katz/` at the repo
  root by design; do not interpret that as a path mistake.

## Decision rule for the calling agent
<!-- id: katz/decision-rule -->

Before dispatching to katz, confirm:

1. There is a manuscript or report to review.
2. Version-specific issue tracking is valuable.
3. The review should produce structured findings or a report.
4. The agent can identify sections, issues, severities, and statuses.

If yes to the first three, katz is the right method.

## Inputs and elicitation
<!-- id: katz/inputs -->

### Manuscript and version
<!-- id: katz/inputs-manuscript -->

What it is: the paper file, canonical version, and section map.

How the agent elicits this:
- Ask which file is the canonical manuscript and whether it is committed in git.
- Ask whether the review should cover the full paper or specific sections.
- Ask whether section boundaries are already known or need to be added.

Default to suggest: initialize katz, register the current manuscript version, then add section boundaries before filing issues.

Fallback: if section boundaries are not available, use coarse section labels first and refine as needed.

### Review scope and criteria
<!-- id: katz/inputs-scope-criteria -->

What it is: what kind of review the user wants: methods, theory, writing, claims, reproducibility, statistics, contribution, or venue fit.

How the agent elicits this:
- Ask the user’s role: peer reviewer, coauthor, editor, advisor, or internal reviewer.
- Ask for priority areas and any rubric or venue criteria.
- Choose spotters that match the review scope.

Default to suggest: methods/identification, evidence-claim alignment, novelty, clarity, limitations, and reproducibility.

Fallback: if the user gives no scope, run a broad first pass and categorize issues by severity and type.

### Issue handling
<!-- id: katz/inputs-issues -->

What it is: how findings are recorded, prioritized, and resolved.

How the agent elicits this:
- Ask whether the report should be candid internal feedback or polished external review language.
- Ask severity conventions: blocker, major, minor, suggestion.
- Ask whether author responses or fixes will be tracked.

Default to suggest: file concrete issues with evidence, section anchor, severity, and recommended action.

Fallback: for uncertain comments, file them as questions or low-severity suggestions rather than overstating.

## Outputs
<!-- id: katz/outputs -->

katz produces:

- `.katz/` review state with registered paper versions, section maps, spotter settings, issues, evaluations, and responses.
- Issue records with anchors, statuses, severity, categories, and comments.
- Validation output for review consistency.
- Generated review reports summarizing findings and recommendations.
- In research-agent task workflows, a task-local supporting HTML artifact such
  as `writeup/artifacts/paper_explorer.html`, plus a user-facing
  `writeup/report.md` that can be compiled to HTML/PDF.

## Workflow
<!-- id: katz/workflow -->

Canonical sequence:

1. `katz init` — initialize review state in the git repository.
2. `katz paper ...` — register the manuscript and section boundaries.
3. `katz spotter ...` — load or enable relevant issue spotters.
4. `katz spotter jobs --output jobs.ep` — package spotters and manuscript content as EDSL Jobs.
5. `ep run jobs.ep --model <model> --output results.ep` — execute with EDSL.
6. `katz spotter ingest results.ep` — verify quotations and file anchored draft issues.
7. Investigate imported candidates and file any additional issues manually.
8. Add evaluation criteria or responses if the review includes scoring or revision tracking.
9. `katz validate` — check consistency of paper versions, anchors, and issues.
10. `katz report generate` — create the final review report.

When the user asks for a standard paper-review task in research-agent, keep
Katz as the primary review ledger but produce the shareable deliverable through
the task scaffold:

1. Create/select a dedicated topic and task for the paper review.
2. Run Katz from the git repository root and register the paper or task-local
   draft under review.
3. Generate the Katz HTML artifact into the active task tree, for example
   `writeup/artifacts/paper_explorer.html`.
4. Write the main user-facing review source at `writeup/report.md`.
5. Compile `writeup/report.md` through Gutenberg to `writeup/report.html` and
   `writeup/report.pdf`.

For generated research-agent reports, katz is usually an internal QA step
before Gutenberg rather than the final user deliverable. Use it to find and
track critique issues, decide which changes are worth implementing, revise the
analysis/report source, and then compile the user-facing `writeup/report.md`
through [gutenberg](#gutenberg/gutenberg). Do not substitute the katz review
report for the final empirical report unless the user explicitly asks to see
the review.

For task-scaffold reports, run from the repository root:

```bash
katz init
katz paper register --canonical sessions/topic_x/task_y/writeup/report.md --source-format markdown --source-method generated-report
katz paper auto-chunk
katz spotter init-catalog
```

Then read the registered report and its generated analysis artifacts. Spotters
and reports do not replace judgment: file concrete issues with
`katz issue write` for material problems, investigate or classify them, and only
then generate the internal Katz review report. A zero-issue report is useful
only if the agent actually reviewed the manuscript and found no material
issues.

For paper-review tasks whose main deliverable is a written review, prefer this
task-local artifact pattern:

```bash
katz report generate --output sessions/topic_x/task_y/writeup/artifacts/paper_explorer.html
```

Then write `sessions/topic_x/task_y/writeup/report.md` as the main report
source, with an HTML-only related-artifacts block that links to
`artifacts/paper_explorer.html`, and compile it through Gutenberg.

## Worked examples
<!-- id: katz/examples -->

### Reviewing a methods paper
<!-- id: katz/example-methods-paper -->

User: "Can you review this paper for methodological weaknesses?"

Agent: "Katz is appropriate because we need version-aware findings. I’ll register the current manuscript, map the Methods and Results sections, enable methods and evidence-claim spotters, then file concrete issues with severity and suggested fixes."

User: "Focus on identification and whether the claims are supported."

Agent: "I’ll prioritize identification, robustness, measurement, and claim scope, then generate a report grouped by major and minor issues."

```bash
katz init
katz paper register --canonical paper.md --source-format markdown --source-method manuscript
katz paper auto-chunk
katz spotter init-catalog
katz spotter enable identification_threats
katz issue write --byte-start 1200 --byte-end 1500 --spotter identification_threats --title "Identification assumption not defended" --body "The Methods section states the identifying assumption but does not justify why it should hold in this setting."
katz validate
katz report generate
```

Output: a structured review report with version-anchored issues.

### Tracking revision responses
<!-- id: katz/example-revision-tracking -->

```bash
katz paper register --canonical paper_revision.md --source-format markdown --source-method revision
katz issue list --state open
katz issue update --id issue_003 --state resolved --reason "Added robustness table and narrowed claim."
katz validate
katz report generate
```

Output: a revision-aware issue ledger and updated response report.

## Quick command reference
<!-- id: katz/commands -->

For full options, run `katz <subcommand> --help`.

| Command | Purpose |
|---|---|
| `katz init` | Initialize `.katz/` review state. |
| `katz ventilate ...` | Write a conservative one-sentence-per-line Markdown copy. |
| `katz paper ...` | Register papers, versions, and section maps. |
| `katz spotter ...` | Manage spotters, build EDSL Jobs, and ingest Results. |
| `katz issue ...` | File, list, show, update, and close review issues. |
| `katz eval ...` | Manage evaluation criteria and responses. |
| `katz validate` | Check ledger consistency. |
| `katz report ...` | Generate review reports. |
| `katz docs` / `guide` | Read built-in review guidance. |

## Common pitfalls
<!-- id: katz/pitfalls -->

- Reviewing a moving manuscript without registering versions makes anchors ambiguous.
- Vague issues are hard to act on; include evidence, location, impact, and recommended fix.
- Spotters are aids, not substitutes for judgment; disable irrelevant categories for focused reviews.
- Running katz from a task subdirectory and expecting task-local `.katz/`.
  Katz is repo-scoped. Use the repository root and a repo-relative canonical
  manuscript path for research-agent task reports.
- Treating imported model candidates as confirmed findings. Preserve the
  complete EDSL Results object, then investigate each draft against the paper.
- External peer-review tone differs from internal coauthor critique; elicit audience before report generation.

## Cross-references
<!-- id: katz/xrefs -->

- Upstream: [dewey](#dewey/dewey) manages a broader literature corpus before selecting a paper for deep review.
- Downstream: [gutenberg](#gutenberg/gutenberg) can compile review reports if exported as markdown.
- Adjacent methods: [messick](#messick/messick) validates synthetic-study evidence; [bewley](#bewley/bewley) supports qualitative coding of manuscript text when thematic coding is needed.

## State contract
<!-- id: katz/state -->

`.katz/` is the source of truth for paper versions, section maps, issue records, spotter settings, evaluation criteria, and responses. Paper anchors are meaningful only relative to the registered version. Agents should validate after adding or updating issues, especially across manuscript revisions.

## JSON output and error codes
<!-- id: katz/json -->

katz commands emit one JSON envelope to stdout. Successful commands return
`{"ok": true, "command": [...], "data": ...}`. Failures return
`{"ok": false, "command": [...], "error": {"code": "...", "message": "...", "details": {...}}}`
and exit with status 1. Agents should branch on `ok` and never infer success
from the shape of `data`. Treat validation failures as ledger consistency
problems: fix missing paper versions, bad anchors, unknown spotters, or issue
status mismatches, then rerun `katz validate`.
