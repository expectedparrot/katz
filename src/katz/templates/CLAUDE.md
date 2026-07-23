# Katz manuscript-review instructions

Use Katz as the version-aware source of truth for manuscript review.

1. Run `katz agent bootstrap`, then follow only actions returned in
   `data.next_actions`.
2. Confirm the canonical manuscript before registration when more than one
   candidate exists.
3. Let EDSL own authentication. Use `ep auth login`, inspect redacted local
   state with `ep profiles current`, and run `ep check` before remote work.
   Never print or copy API-key values into prompts or Katz records.
4. Inspect Jobs before running them and preserve both `.jobs.ep` and Results
   `.ep` artifacts.
5. Preview unknown artifacts with `katz ingest PATH`; use `--apply` only after
   checking the detected contract.
6. Treat model findings and parsed human comments as drafts. Use
   `katz issue next` to investigate exact manuscript context before confirming
   or rejecting anything.
7. Do not guess manuscript anchors. Preserve repository-only review comments
   for repository investigation.
8. Ask before selecting a paid model, publishing a report, or creating external
   GitHub issues unless the user already authorized that action.
9. Run `katz validate` before generating the final report.

All Katz commands return one JSON envelope. Use the returned command arrays,
stable error codes, mutation flags, and approval flags rather than scraping
human prose.
