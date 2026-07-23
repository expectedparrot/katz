# Katz manuscript-review instructions

Use Katz as the version-aware source of truth for manuscript review.

1. Run `katz agent bootstrap`, then follow only actions returned in
   `data.next_actions`.
2. Confirm the canonical manuscript before registration when more than one
   candidate exists.
3. Inspect Jobs before running them and preserve both `.jobs.ep` and Results
   `.ep` artifacts.
4. Preview unknown artifacts with `katz ingest PATH`; use `--apply` only after
   checking the detected contract.
5. Treat model findings and parsed human comments as drafts. Use
   `katz issue next` to investigate exact manuscript context before confirming
   or rejecting anything.
6. Do not guess manuscript anchors. Preserve repository-only review comments
   for repository investigation.
7. Ask before selecting a paid model, publishing a report, or creating external
   GitHub issues unless the user already authorized that action.
8. Run `katz validate` before generating the final report.

All Katz commands return one JSON envelope. Use the returned command arrays,
stable error codes, mutation flags, and approval flags rather than scraping
human prose.
