# Katz repository operating contract

Use the CLI as the workflow source of truth:

```bash
katz guide
katz next
```

Run `katz next` after every material stage. Use the returned recommendation;
use `--help` for exact options and defaults.

## Development checks

- Install development dependencies with `python -m pip install -e '.[test]'`.
- Run `python -m compileall -q src`, `git diff --check`, and `pytest -q`.
- Code layout: `src/katz/commands/` holds one module per CLI command group;
  shared logic lives in the library modules (`storage`, `manuscript`, `latex`,
  `edsl_bridge`, `issues`, `definitions`, `assets`). `cli.py` is app assembly
  plus top-level commands and re-exports every moved name for compatibility —
  add new helpers to the owning library module, not to `cli.py`.
- When documentation changes, check local links, command wrapping, mobile table
  behavior, and the examples in `docs/index.html`.
- Update CLI help and state-aware guidance before duplicating workflow facts in
  prose.

## External execution boundary

- Katz constructs and verifies native `.jobs.ep` and `models.ep` artifacts.
- Never add a Katz wrapper that launches model calls.
- Inspect Jobs and estimated cost, run `ep check`, and obtain user approval
  before paid `ep run` execution unless that authority is explicit.
- Preserve the exact Jobs, ModelList, every Results and retry object, and their
  registration and merge provenance.

## Authentication and private material

- Let EDSL own authentication through `ep auth login`, `ep profiles current`,
  and `ep check`.
- Never print, copy, serialize, log, or commit API keys.
- Never publish `.env`, `.edsl/profiles/`, licensed respondent microdata,
  confidential reviews, unpublished original work, or provider responses that
  cannot be redistributed.

## Artifacts and audit trail

- Keep repository review state under `.katz/`.
- Put explicit run artifacts in stable, named run directories; do not use
  timestamp-only or implicit “latest” paths.
- Preserve canonical manuscripts, resolved configurations, prompts, manifests,
  `.jobs.ep`, `models.ep`, all `.results.ep` files, registrations, diagnostics,
  issue histories, and reports.
- Treat registered artifacts as evidence, not cache files.
- Never silently repair, normalize, replace, renumber, or delete registered
  results. Record retries separately and merge by stable identities.
- Do not report incomplete, null, malformed, exceptional, missing, or duplicate
  responses as negative findings.

## Katz-specific review rules

- Confirm ambiguous manuscript selection before registration.
- Prepare PDF and LaTeX inputs as canonical Markdown; inspect expanded tables,
  figures, and dependencies before committing them.
- Run the compatibility pilot before a large review.
- Audit Results against their originating Jobs before ingestion.
- Treat imported findings as drafts. Check clusters and investigate exact
  manuscript and repository context before confirming or rejecting them.
- Run `katz validate` before generating a final report.
- Keep error codes, artifact schemas, and stable identifiers backward
  compatible or provide an explicit migration.
