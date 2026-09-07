# Review evidence and execution plans

Katz constructs native EDSL artifacts and never executes inference. Use `katz
next` after each stage. Inspect Jobs, estimated cost, and `ep check`, and obtain
authority for paid execution before running `ep run`.

## Freeze the requested execution

Build the ModelList and preserve a plan before executing each Jobs package:

```bash
katz spotter models --model <model-name> --output models.ep
katz spotter jobs --pilot 5 --output pilot.jobs.ep --models models.ep
# Inspect scope/cost and approve the pilot before external execution.
ep run pilot.jobs.ep --model_list models.ep --output pilot-results.ep
katz results audit pilot-results.ep --jobs pilot.jobs.ep
katz next
```

`--models` writes `<jobs.ep>.katz.json` with Jobs and survey fingerprints, exact
model configurations, and the expected iteration count. For an already exported
package, use:

```bash
katz results plan --jobs jobs.ep --models models.ep
```

Use `--iterations N` when registering a repeated execution plan, and configure
that same repetition count in the external runner. Repetitions count as distinct
observations. Changing a plan requires a new Jobs export; it never overwrites an
existing plan. When models are embedded in Jobs, `--models` is unnecessary.

## Audit v2 migration

Spotter audit now returns `katz.spotter-results-audit.v2`. Legacy answer fields and
existing diagnostic field names remain supported. `complete` now requires proof
of all requested observations, including model parameters, service, agent, and
iteration. Model-free Jobs without a saved plan require `--models models.ep` on
`results audit` or ingestion. Without this evidence, `model_coverage_known` and
`complete` are false, even when every returned answer is valid. Old saved audit
records and Results are never rewritten.

For routed Jobs, an interview is complete only when its router and every selected
detail answer are valid. Unselected details count as `intentional_skips`, never
as negative findings. `expected_answers` and `valid_answers` count interviews;
`valid_positive_findings` counts findings, including multiple findings in an
interview. A wrong manuscript commit is rejected even with `--allow-partial`.

`--allow-partial` is an explicit recovery option, not proof of a complete review.
Final reports remain marked incomplete until coverage and anchoring are resolved.
Registered run artifacts are copied unchanged into checksum-addressed storage
under the version's `artifacts/` directory. Reports bind coverage to the selected
run and verify preserved artifact checksums.

## Retry and merge

```bash
katz results failures results.ep --jobs jobs.ep
katz results retry results.ep --jobs jobs.ep --output-dir retries/compatibility
```

The retry manifest emits a separate native Jobs package and explicit external
execution command for each failed or missing observation. The source Jobs,
Results, prompts, scenarios, models, and agents are retained. Execute approved
retry commands using their specified result paths, then merge into a new file:

```bash
katz results merge results.ep retries/compatibility/<retry-results.ep> \
  --jobs jobs.ep --retry-plan retries/compatibility/<retry.jobs.ep.retry.json> \
  --output merged.results.ep
katz results audit merged.results.ep --jobs jobs.ep
katz ingest merged.results.ep --jobs jobs.ep --apply
```

Repeat `--retry-plan` for each retry package. A merge keeps explicit source-row
selection provenance and refuses conflicting valid answers. Original files are
never changed. For repeated executions, the derivative merge records the mapping
from the retry's local iteration zero to its original iteration. Preserve the
specified retry result paths so this mapping is unambiguous.

The older `spotter jobs --from-failures` option supports one failed observation
and now requires originating `--jobs`; it never reconstructs a retry from current
spotter definitions. Use `results retry` for multiple failed observations.

## Additional review designs

```bash
katz spotter jobs --multiple-findings --models models.ep --output findings.jobs.ep
katz spotter compare-jobs --left methods --right results \
  --spotters internal_contradictions --models models.ep --output comparison.jobs.ep
katz issue verification-jobs --models models.ep --output verification.jobs.ep
```

Multiple findings use `{"schema_version": 2, "findings": [...]}`; the existing
single `found` verdict remains supported. Each finding has a title, exact quote,
and explanation. Paired comparisons receive both named section passages. Require
both sides of a contradiction in `evidence_quotes`.

After external verification execution:

```bash
katz issue verification-ingest verification-results.ep --jobs verification.jobs.ep
katz issue next
```

Verifier outputs are proposals. Exact evidence and the candidate revision are
checked before proposals are preserved. They do not change issue states; the
reviewer investigates and records the final decision.

## Report-only and human-review ingestion

Pass originating `--jobs` and, when not embedded/planned, `--models` to
`report review-ingest` and `review ingest`. Report-only ingestion binds each
response to the exact report and image checksums and validates verdict and summary.
Unbound legacy report responses can still be normalized, but are marked incomplete.
Human-review ingestion validates the entire batch before creating issues and
requires complete coverage unless `--allow-partial` is explicit.

## Registration and reporting

An identical registration is a no-op that preserves mappings and issue history.
Conflicting manuscript or image content requires a new committed version.
Canonical bytes, including CRLF and non-ASCII text, are preserved throughout
anchoring. Image references must resolve inside the canonical manuscript bundle;
nested directories are preserved, and unrelated sibling images are not attached.

`katz report generate` produces a draft issue explorer. Investigate imported
findings, author the referee report, then validate and finalize:

```bash
katz issue next
katz validate
katz report finalize --report writeup/report.md
```

Apply the concrete finalization command returned by that preview. A repeated
identical ingestion is counted separately from an anchoring failure, so replay
alone does not make the final report incomplete.
