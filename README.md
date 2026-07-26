# katz

## Copy and paste into a coding agent

```text
Set up Katz and help me complete a version-aware review of the manuscript in
this repository, ending with an evidence-linked report.

Install the current Katz and EDSL releases from their authoritative sources.
Verify that `katz` and `ep` resolve to those installations. Use Katz's CLI as
the workflow source of truth:

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

![An economist parrot reviewing a manuscript beside mathematical notation and books](docs/katz-economist-parrot.png)

Katz is an agent-first, version-aware ledger for manuscript review. It connects
each comment to the exact committed paper version and source range that
prompted it, preserves model and human review evidence, and turns investigated
findings into an auditable HTML report.

It produces a repository-local `.katz/` ledger, native EDSL Jobs and Results
objects, structured diagnostics, issue histories, and review reports. Model
findings remain drafts until someone investigates their quoted evidence.

The [complete worked tutorial](https://expectedparrot.github.io/katz/) follows
a public JOSS paper from registration through model review, human triage,
investigation, and reporting.

## Install and verify

Katz requires Python 3.11 or newer:

```bash
python -m pip install --upgrade \
  "edsl @ git+https://github.com/expectedparrot/edsl.git@main" \
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

Katz will propose initialization, canonical manuscript registration, section
mapping, review configuration, and Jobs construction based on repository
state. Each returned action declares whether it mutates state, uses the network,
or requires approval.

After Katz creates a Jobs package, inspect it and execute it explicitly with
EDSL:

```bash
ep inspect jobs.ep
ep jobs cost jobs.ep
ep run jobs.ep --model_list models.ep --output results.ep
```

Then return to the state-aware workflow:

```bash
katz next
```

The CLI guide and `next` response are authoritative for workflow state. Use
`katz COMMAND --help` for exact arguments and defaults.

## Principal command groups

- `katz paper` registers and queries canonical manuscripts.
- `katz spotter` configures review checks and packages section-level Jobs.
- `katz review` preserves and parses human referee reports.
- `katz results` audits native EDSL Results.
- `katz issue` records, clusters, investigates, and resolves findings.
- `katz eval` records whole-paper judgments.
- `katz report` generates the review website.
- `katz guide`, `katz next`, and `katz agent` expose machine-readable workflow
  guidance.

Every successful or failed command emits exactly one JSON envelope with
`status`, canonical `command`, `data`, `warnings`, `errors`, and `next_steps`.

## Central caveat

A locatable quotation proves provenance, not correctness. Model findings and
parsed human comments are candidates. Audit Results against their originating
Jobs, distinguish valid negative judgments from missing or malformed answers,
and investigate candidates in manuscript and repository context before
confirming them. A zero-issue review is not complete unless the audit reports
complete valid coverage.

## Learn more

- [Worked methodological tutorial](https://expectedparrot.github.io/katz/)
- [Repository operating contract](AGENTS.md)
- Run `katz guide` for the lifecycle.
- Run `katz docs list` for packaged documentation topics.
- Run `katz --help` or `katz COMMAND --help` for CLI details.
- [MIT License](LICENSE)
