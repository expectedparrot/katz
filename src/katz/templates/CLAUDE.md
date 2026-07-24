# Katz manuscript-review instructions

Use Katz as the version-aware source of truth for manuscript review.

1. Run `katz agent bootstrap`, then follow only actions returned in
   `data.next_actions`.
2. Confirm the canonical manuscript before registration when more than one
   candidate exists. Prepare PDF or LaTeX sources as Markdown first. For LaTeX,
   verify expanded input/include dependencies and the structural table/media
   audit; never pass `--allow-lossy` without user approval.
3. Let EDSL own authentication. Use `ep auth login`, inspect redacted local
   state with `ep profiles current`, and run `ep check` before remote work.
   Never print or copy API-key values into prompts or Katz records.
4. Use the five-scenario pilot returned by `katz agent next` before a large
   run. Inspect Jobs, ask before selecting a paid model, and preserve both
   `.jobs.ep` and Results `.ep` artifacts.
5. Run `katz results audit RESULTS --jobs JOBS` before interpreting a run.
   A valid `found=false` is evidence that a scenario completed; null, malformed,
   missing, duplicate, or exceptional responses are not negative findings.
6. Preview unknown artifacts with `katz ingest PATH`; use `--apply` only after
   checking the detected contract.
7. Treat model findings and parsed human comments as drafts. Use
   `katz issue next` to investigate exact manuscript context before confirming
   or rejecting anything.
8. Check `katz issue clusters` before investigating repetitive candidates.
9. Do not guess manuscript anchors. Preserve repository-only review comments
   for repository investigation.
10. Ask before selecting a paid model, publishing a report, or creating external
   GitHub issues unless the user already authorized that action.
11. Run `katz validate` before generating the final report. Never describe a
    zero-issue result as complete unless its audit shows 100% valid coverage.

All Katz commands return one JSON envelope. Use the returned command arrays,
stable error codes, mutation flags, and approval flags rather than scraping
human prose.
