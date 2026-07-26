# katz

Katz makes manuscript review traceable: every finding stays connected to the
exact paper version and passage that prompted it, remains a draft until it is
investigated, and contributes to an auditable review rather than a pile of
disconnected comments.

![An economist parrot reviewing a manuscript beside mathematical notation and books](docs/katz-economist-parrot.png)

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

Katz prepares model review work but deliberately stops before contacting a
model provider. At that point it saves a portable `jobs.ep` file containing the
review questions and manuscript material. Review its summary and estimated
cost before approving any paid work:

```bash
ep inspect jobs.ep
ep jobs cost jobs.ep
```

After approval, EDSL runs the prepared review and saves the complete responses
and provenance in `results.ep`:

```bash
ep run jobs.ep --model_list models.ep --output results.ep
```

Then return to Katz. It will audit the saved responses, identify missing or
malformed work, turn supported findings into draft issues, and guide the review
toward investigation and reporting:

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
