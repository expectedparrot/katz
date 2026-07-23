# katz — version-aware paper-review ledger and issue tracker
<!-- id: katz/katz -->

![An economist parrot reviewing a manuscript beside mathematical notation and books](docs/katz-economist-parrot.png)

Katz is primarily an agent-facing tool for reviewing papers without losing the connection between a comment and the exact manuscript version that prompted it. It stores canonical paper versions, section boundaries, review instructions, model and human findings, investigations, suggested fixes, evaluations, and generated reports in a Git-aware ledger. CLI commands return structured JSON so agents can inspect state and decide explicitly what to do next.

The workflow has four stages:

1. Put the paper into a canonical, committed form and map its reviewable sections.
2. Run broad or targeted review passes—either section by section or as a one-shot whole-paper review—and preserve the complete EDSL Jobs and Results objects.
3. Let an agent or human investigate proposed findings, reject false positives, merge duplicates, and suggest concrete responses.
4. Promote confirmed findings into reports or GitHub issues, then track how a later manuscript revision addresses them.

The [full HTML tutorial on GitHub Pages](https://expectedparrot.github.io/katz/) follows a real JOSS paper through this process, including figures, model review, human triage, and report generation.

## Copy this into your coding agent

GitHub places a copy button in the upper-right corner of this prompt. Start
Codex, Claude Code, or another coding agent in the repository containing the
paper, then copy and send the complete block:

```text
Install Katz and EDSL directly from GitHub:

python -m pip install "katz @ git+https://github.com/expectedparrot/katz.git"
python -m pip install "edsl @ git+https://github.com/expectedparrot/edsl.git"

Verify both CLIs:

katz --help
ep --help

Use Katz to review the manuscript in this repository. Begin with:

katz agent bootstrap

Treat Katz's JSON envelopes as the source of truth. Execute an action only when
its mutation, network, cost, and approval flags are consistent with my
authorization. After every action, run `katz agent next` and continue following
`data.next_actions` until no action remains or my approval is required.

Rules:
- Never display, copy, or commit API keys.
- If authentication is missing, run `ep auth login`. If the existing `.env` or
  EDSL profile is configured, do not log in again.
- Run `ep check` before paid model execution.
- Confirm an ambiguous manuscript choice before registration.
- A PDF is not canonical review text. Follow Katz's `paper prepare` action,
  inspect the extracted Markdown, figures, and tables, and commit the canonical
  source before registration.
- Inspect every generated `.jobs.ep` package and preserve both Jobs and Results
  artifacts.
- Ask me before choosing a paid model, launching a paid run, publishing a
  report, or creating GitHub issues unless I already authorized that action.
- Run the small compatibility pilot proposed by Katz before a large review.
- Audit Results against their originating Jobs. A structured `found=false` is
  a completed negative judgment; null, malformed, exceptional, missing, or
  duplicate responses are failures and must never be reported as “no issues.”
- Do not use `--allow-partial` unless I explicitly approve a partial recovery.
- Treat detected findings as drafts. Check `katz issue clusters`, then use
  `katz issue next` to investigate manuscript and repository context before
  confirming, rejecting, or leaving an issue open.
- Run `katz validate` before generating the final HTML report.
- Never describe a zero-issue review as complete unless its audit reports 100%
  valid coverage.

Persist the repository-native instructions when Katz proposes it, or run:

katz agent instructions --write
```

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

Install the EDSL CLI as well. Let EDSL own Expected Parrot authentication and
repository-local `.env` configuration:

```bash
python -m pip install "edsl @ git+https://github.com/expectedparrot/edsl.git"
ep auth login
ep profiles current
ep check
```

`ep auth login` opens the browser login flow and stores the resulting key in
the current repository’s `.env`; `ep profiles current` reports redacted local
configuration; and `ep check` verifies URL reachability and authentication.
For multiple Expected Parrot environments, use `ep profiles create`, `list`,
`set`, and `check`. Keep `.env` and `.edsl/profiles/` out of public commits.
Katz bootstrap consumes only EDSL’s redacted profile state and never returns
the key.

## Use Katz directly with Codex or Claude Code

Katz does not require a special Codex or Claude Code integration. Start either
coding agent in the Git repository containing the manuscript and tell it to use
Katz’s packaged guide. The agent can inspect every command before acting, while
Katz’s JSON responses provide machine-readable state between steps.

The shortest reliable agent loop is:

```bash
katz agent bootstrap
katz agent next
```

`bootstrap` is read-only. It reports Git state, Katz initialization, ranked
manuscript candidates, EDSL’s redacted active profile, authentication
prerequisites, blockers, and
complete command arrays for valid next actions. `agent next` returns only the
highest-priority action plus alternatives. After executing an authorized action,
the agent calls `agent next` again.

Other agent-facing primitives are:

```bash
katz capabilities                    # supported contracts and schema versions
katz agent status                    # complete phase and next-action state
katz issue next                      # one full investigation packet
katz ingest artifact.ep              # detect and preview; never mutates by default
katz ingest results.ep --apply       # apply a supported detected contract
katz results audit results.ep --jobs jobs.ep
katz results failures results.ep     # compact null/schema/model failures
katz agent instructions codex        # return the bundled AGENTS.md template
katz agent instructions claude       # return the bundled CLAUDE.md template
katz agent instructions --write      # write AGENTS.md and CLAUDE.md
```

Every proposed action says whether it mutates state, uses the network, or needs
user approval. The versioned JSON Schemas for envelopes, actions, and agent
status ship in `katz/schemas/`.

Katz runs a small compatibility pilot before proposing a large automated
review. It audits the resulting structured answers against the originating
Jobs package. Full ingestion fails closed if answers are null, malformed,
duplicated, missing, or produced for unexpected scenarios. A valid
`found=false` is a completed negative judgment; a null answer is not.

For Codex:

```bash
cd path/to/paper-repository
codex 'Use Katz to review this manuscript. First run `katz guide skill review-paper`
and follow that procedure. Inspect the repository and `katz paper status` before
changing anything. Preserve EDSL Jobs and Results, keep proposed findings as
drafts until investigated, and show me the final HTML report.'
```

For Claude Code:

```bash
cd path/to/paper-repository
claude 'Use Katz to review this manuscript. First run `katz guide skill review-paper`
and follow that procedure. Inspect the repository and `katz paper status` before
changing anything. Preserve EDSL Jobs and Results, keep proposed findings as
drafts until investigated, and show me the final HTML report.'
```

The prompt can name the canonical file and review priorities when they are
known—for example, “register `paper/paper.md` and prioritize identification,
claim support, and figures.” Otherwise the agent should identify the likely
manuscript and ask before making a choice that changes the review’s scope.

To process an existing referee report, add this to either prompt:

```text
The human-written review is reviews/reviewer-2.md. Preserve it with
`katz review add`, build and inspect the parsing Jobs object, run it with EDSL,
inspect the returned Results, and use `katz review ingest` to file only
manuscript-grounded comments. Preserve repository-only comments for separate
investigation rather than forcing them onto manuscript text.
```

The agent should run `ep run ... --task-timeout 900` for a whole-paper frontier
review that may exceed the normal remote interview deadline. This differs from
`--timeout`, which controls only local polling with `--background --wait`.
Creating GitHub issues or publishing artifacts is an external write; ask the
user before doing it unless that action was already explicitly requested.

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
6. `katz results audit results.ep --jobs jobs.ep` — prove response validity and coverage.
7. `katz spotter ingest results.ep --jobs jobs.ep` — verify quotations and file anchored draft issues.
7. Optionally run `katz paper review-jobs --output one-shot-review.jobs.ep` to package the complete manuscript and figures for one frontier-model referee review.
8. Investigate imported candidates and file any additional issues from the one-shot report or human review.
   A journal report can be preserved with `katz review add`, converted into a
   manuscript-grounded parsing job with `katz review jobs`, and filed as draft
   candidates with `katz review ingest`.
9. Add evaluation criteria or responses if the review includes scoring or revision tracking.
10. `katz validate` — check consistency of paper versions, anchors, and issues.
11. `katz report generate` — create the final review report.

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
katz spotter enable --recommended
katz spotter jobs --pilot 5 --output pilot.jobs.ep
# Run and audit the pilot before creating and running the complete jobs.ep.
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
| `katz review ...` | Preserve human journal reports, build parsing Jobs, and ingest grounded comments. |
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
