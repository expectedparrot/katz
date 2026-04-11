# katz — paper review ledger

katz is a version-aware ledger for paper review artifacts. It stores manuscripts, issues, investigations, and spotters keyed to git commits.

## Typical workflow

1. **Register the paper**: Convert PDF to markdown, register with katz
2. **Chunk the paper**: Add section boundaries for the manuscript
3. **Configure spotters**: Read the paper, decide what to look for, enable appropriate spotters
4. **Find issues**: Run spotters across the manuscript (parallelized via EDSL or sequentially)
5. **Investigate issues**: Verify each flagged issue against the full manuscript and code (expect ~5–10% confirmation rate)
6. **(Optional) Report**: Generate an HTML review report and/or file a GitHub issue

## Key concepts

- **Spotter catalog** (`.katz/spotters/`): Available review criteria, shared across versions. Initialize with `katz spotter init-catalog`.
- **Enabled spotters** (per-version): Subset of the catalog selected for this review. Enable with `katz spotter enable <name>`.
- **Spotter scopes**: `section` spotters run on one section at a time (parallelizable). `holistic` spotters need the full manuscript.
- **Issues** use a directory-per-issue layout with append-only `status/` and `investigations/` subdirectories. State is never overwritten.
- **Issue states**: draft, open, confirmed, rejected, resolved, wontfix.

## Available skills

Run `katz guide skills` to list all available skills with descriptions.
Run `katz guide skill <name>` to read a specific skill's full instructions.
Run `katz guide script <skill-name>/<path>` to read a script file.

## Quick command reference

```
katz paper status                     # check registration
katz paper section <id>               # get section details
katz paper find <text>                # locate text by byte offset
katz spotter init-catalog             # populate spotter catalog
katz spotter catalog                  # list available spotters
katz spotter enable <name>            # enable for this review
katz spotter list                     # list enabled spotters
katz issue write --title ... --byte-start ... --byte-end ... --body ...
katz issue update --id <id> --state confirmed --reason "..."
katz issue investigate --id <id> --verdict confirmed --notes "..."
katz issue list [--state ...] [--section ...] [--spotter ...]
katz issue show <id>                  # full record with history
```
