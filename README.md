# katz

Katz is a command-line tool that stores manuscript-review state in a
repository-local ledger. It registers a canonical manuscript at a Git commit,
maps its sections and source ranges, records model and human findings as issues
anchored to those ranges, tracks investigation and resolution history, and
generates reports from the stored issue state.

Katz can:

- register Markdown manuscripts and prepared PDF or LaTeX sources against Git
  versions;
- run issue spotters across sections in a map-reduce review or apply holistic
  checks to the complete manuscript;
- ingest existing referee reports and review results from other sources;
- cluster overlapping findings and guide investigation against manuscript and
  repository context;
- record decisions, suggested fixes, evaluations, and revision history; and
- generate navigable review reports from the issue ledger.

Katz also provides a lightweight pre-delivery review for an already-authored
research report. It does not initialize the manuscript ledger or chunk the
report. Instead it detects the analysis type, selects relevant report
spotters, attaches `report.md` and its local images to one model-free Jobs
package, and normalizes reviews from an externally selected ModelList:

```bash
katz report review-plan --report writeup/report.md
ep models create --model <frontier-a> --model <frontier-b> --model <frontier-c> --output report-review.models.ep
katz report review-jobs --report writeup/report.md --models report-review.models.ep --output report-review.jobs.ep
ep inspect report-review.jobs.ep
ep jobs cost report-review.jobs.ep
# approve the exact cost
ep run report-review.jobs.ep --output report-review.results.ep
katz report review-ingest --results report-review.results.ep --report writeup/report.md --output analysis/report-review.json
```

Use `--analysis-type` to override detection and repeat `--image` for local
images not referenced by Markdown. This one-shot quality gate is distinct from
Katz's evidence-linked academic referee workflow.

![An economist parrot reviewing a manuscript beside mathematical notation and books](docs/katz-economist-parrot.png)

## Quick start: from a PDF to a review report

Someone sent you a PDF and you need to review it. After [installing Katz and
`ep`](#install-and-verify):

```bash
uv tool install paper2md      # PDF -> Markdown extraction, once
katz workspace new pdf-review --from paper.pdf --model gpt-4.1-mini
```

That one command converts the PDF, ventilates the Markdown, builds a Git
workspace, registers the manuscript, maps its sections, enables the
recommended review spotters, and packages the model review as `jobs.ep` +
`models.ep` — recording every step in its response, then stopping at the two
decisions that stay yours:

```bash
# 1. Inspect pdf-review/paper/paper_ventilated.md against the PDF
#    (reading order, tables, equations, figures — extraction is lossy).
cd pdf-review
# 2. Authorize the model run (`ep auth login` once beforehand).
ep run jobs.ep --model_list models.ep --output results.ep
katz ingest results.ep --apply
katz report generate --output review.html
```

The generated report links every finding to the exact passage that triggered
it ([example report](https://expectedparrot.github.io/katz/investigated-review.html)).
`--from` also accepts LaTeX and Markdown sources; inside an existing paper
repository, use `katz init` + `katz paper register` instead. The
[worked tutorial](https://expectedparrot.github.io/katz/) explains every step
and artifact behind the shortcut.

## Copy and paste into a coding agent

```text
Set up Katz and help me complete a version-aware review of the manuscript in
this repository, ending with an evidence-linked report.

Install `uv` if it is not already available:

python -m pip install --user --upgrade uv

Use `uv` to install Katz in an isolated Python 3.11+ tool environment. Include
EDSL's `ep` executable in the same environment:

uv tool install --python 3.11 --upgrade --force \
  --with-executables-from "edsl @ git+https://github.com/expectedparrot/edsl.git@main" \
  "katz @ git+https://github.com/expectedparrot/katz.git@main"

Verify the installed package, capabilities, and both executable interfaces:

katz version
katz capabilities
katz --help
ep --help

Stop if Katz resolves to an unexpected Python environment or the required
capabilities are absent. Let EDSL own Expected Parrot authentication and local
profile setup:

ep auth login
ep profiles current
ep check

If an existing redacted profile reports valid authentication, do not log in
again. Never print or inspect the underlying key value.

Use Katz's CLI as the workflow source of truth:

katz guide
katz next

Run `katz next` again after every material stage and follow its recommendation.
Confirm an ambiguous manuscript choice before registration. Prepare PDF or
LaTeX sources as canonical Markdown and preserve their source provenance.

Katz may construct and verify `.jobs.ep` artifacts, but model execution belongs
to an explicit external `ep run`. Inspect the jobs and estimated cost, run
`ep check`, and stop for my approval before choosing a paid model or launching
paid inference unless I already authorized it.

Never print, copy, serialize, or commit credentials. Preserve the canonical
manuscript, resolved review configuration, prompts, Jobs, every Results and
retry object, registrations, audit diagnostics, issue history, and final
report. Never silently normalize, replace, delete, or describe incomplete
results as complete.

Continue until the review and report are complete or my input or approval is
required.
```

<!-- id: katz/katz -->

The [complete worked tutorial](https://expectedparrot.github.io/katz/) follows
a public JOSS paper from registration through model review, human triage,
investigation, and reporting.

## Install and verify

Katz requires Python 3.11 or newer:

```bash
python -m pip install --user --upgrade uv
uv tool install --python 3.11 --upgrade --force \
  --with-executables-from "edsl @ git+https://github.com/expectedparrot/edsl.git@main" \
  "katz @ git+https://github.com/expectedparrot/katz.git@main"
katz version
katz capabilities
ep --help
```

For development:

```bash
python -m pip install -e '.[test]'
pytest -q
```

EDSL owns Expected Parrot authentication. Keep `.env` and `.edsl/profiles/`
private:

```bash
ep auth login
ep profiles current
ep check
```

## Five-minute start

From the Git repository containing the manuscript:

```bash
katz guide
katz next
```

Katz will identify the manuscript, preserve the exact version being reviewed,
divide it into reviewable sections, and help choose the checks to apply. Each
recommended action says whether it changes files, uses the network, or requires
approval.

Katz returns JSON by default for coding agents. To inspect the same state as a
formatted terminal summary, place `--human` before the command:

```bash
katz --human next
```

When the review reaches a step that may contact a model provider, the coding
agent will show the proposed scope and cost and ask for approval. After the
review runs, Katz checks whether every requested check completed, records
supported findings as drafts, and guides their investigation and reporting.
The reader does not need to manage the intermediate execution files.

The CLI guide and `next` response are authoritative for workflow state. Use
`katz COMMAND --help` for exact arguments and defaults.

## Central caveat

A locatable quotation identifies where a finding came from; it does not prove
that the finding is correct. Model findings and parsed human comments remain
candidates until they are investigated in manuscript and repository context.
Missing or failed review checks are not negative findings, so an incomplete
review cannot support a claim that no issues were found.

## Learn more

- [Worked methodological tutorial](https://expectedparrot.github.io/katz/)
- [Repository operating contract](AGENTS.md)
- Run `katz guide` for the lifecycle.
- Run `katz docs list` for packaged documentation topics.
- Run `katz --help` or `katz COMMAND --help` for CLI details.
- [MIT License](LICENSE)
