---
name: investigate-issues
description: Review open katz issues, investigate them against the manuscript and code, and record findings
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Investigate Issues

Reviews katz issues, investigates each one against the manuscript source and codebase, records investigation findings, and updates issue state.

## Usage

```
/investigate-issues [issue-id]
/investigate-issues [--state draft]
/investigate-issues [--section introduction]
```

- With an issue ID, investigate only that issue.
- With `--state`, investigate all issues in that state (default: `draft`).
- With `--section`, investigate only issues in that section.

## Prerequisites

- The paper must be registered in katz (`katz paper status` should return `"valid": true`).
- Issues must exist (`katz issue list` should return results).

## Workflow

### 1. Select issues to investigate

Run `katz issue list` with appropriate filters to get the set of issues. If no argument is given, default to `katz issue list --state draft`.

### 2. Read the full manuscript first

Before investigating individual issues, read the **entire** canonical manuscript at `.katz/versions/<commit>/paper/manuscript.md`. Also locate the LaTeX source (look for the main `.tex` file — it preserves macros, cross-references, and footnotes that PDF conversion loses). Having the full paper in context is essential — many flagged issues are resolved by content in other sections.

### 3. Batch investigation (recommended for large sets)

When there are more than five draft issues, use Katz's stale-safe batch interface:

1. Read the full manuscript and LaTeX source to build understanding.
2. Generate a bounded packet containing full issue records, anchored context,
   revision tokens, and duplicate-cluster summaries:
   ```bash
   katz issue next --limit 10 --output investigation-packet.json
   ```
3. Write `verdicts.json` using the packet's `response_schema`. Preserve the
   packet ID, commit, manuscript hash, issue IDs, and revision tokens exactly.
4. Preview the complete transaction, then apply it only if every decision is
   accepted:
   ```bash
   katz issue investigate-batch --input verdicts.json
   katz issue investigate-batch --input verdicts.json --apply
   ```
   Validation is atomic by default. Do not use `--allow-partial` unless the
   valid subset is intentionally independent of rejected entries. Exact replay
   is safe and does not append duplicate events.
5. Repeat with a new packet until `remaining` is zero. Use a distinct output
   filename for every packet so evidence is not overwritten.

#### Common false-positive categories to reject in bulk

The EDSL sweep produces many duplicates and non-issues. These categories are almost always false positives:

- **"Ambiguous pronoun reference"** — usually clear in context
- **"Causal language without identification"** — reject if the paper IS an RCT
- **Abstract completeness** — abstracts summarize; missing caveats belong in the body
- **"Missing economic magnitudes"** — reject if paper reports both pp and % effects
- **"Table not self-contained" / "Missing R-squared"** — PDF conversion artifacts or standard econometric practice
- **"Equilibrium not discussed"** in non-discussion sections — reject if discussed in later sections
- **Duplicate concerns** — many issues flag the same underlying problem from different models. Confirm ONE canonical instance, reject the rest as duplicates.

#### Deduplication strategy

Multiple models often flag the same underlying concern with different titles. When investigating:
1. Identify clusters of issues about the same underlying concern (e.g., 5 issues all about "null interpreted as no effect" in the employer section).
2. **Confirm the best-articulated instance** as the canonical issue.
3. **Reject the duplicates** with notes like "Duplicate of confirmed issue [id]. This concern is addressed under the canonical instance."

### 4. Individual investigation (for small sets or deep dives)

For small sets or when investigating specific issues:

#### a. Read the issue

```bash
katz issue show <issue-id>
```

This returns the full issue record including `title`, `body`, `location` (with `resolved_text`, `line_start`, `line_end`, `section`), and any prior `status_history` and `investigations`.

#### b. Read the surrounding manuscript context

Use the issue's `location.section` to get the section info:

```bash
katz paper section <section-id>
```

Then read the relevant portion of the canonical manuscript around the issue's line range. Read enough context (at least 20 lines before and after) to understand the claim in context.

#### c. Verify against source material

Depending on the issue type (indicated by the spotter tag in the body, e.g., `[overclaiming]`, `[logical_gaps]`):

- **Overclaiming / logical gaps**: Check if the claim is actually supported elsewhere in the paper. Read other relevant sections. Check if the evidence or methodology sections back up the claim.
- **Internal contradictions**: Find both contradicting statements and verify they actually conflict.
- **Unclear writing**: Re-read the passage and determine if it's genuinely unclear or if context resolves the ambiguity.
- **Methodology errors**: Check the experimental design sections, look at any analysis code in the repo.
- **For all types**: Check the LaTeX source for additional context that may have been lost in PDF conversion (e.g., footnotes, LaTeX macros, cross-references).

#### d. Determine verdict

Reach one of three verdicts:

- **confirmed**: The issue is real and should be addressed.
- **rejected**: The issue is not real (e.g., it's a PDF conversion artifact, the context resolves it, or the claim is actually supported).
- **uncertain**: Cannot determine from available information.

#### e. Record the investigation

```bash
katz issue investigate \
  --id <issue-id> \
  --verdict <confirmed|rejected|uncertain> \
  --notes "Explanation of findings and reasoning"
```

The investigation command also updates state: confirmed → `confirmed`, rejected
→ `rejected`, and uncertain → `open`. Use `--state` only for an intentional
override.

### 5. Report

After investigating all issues in scope, report:

- Total investigated
- Breakdown by verdict (confirmed / rejected / uncertain)
- Summary table: issue title, section, verdict, one-line finding
- Run `katz issue list` to show the updated state distribution

### 6. Typical confirmation rates

From experience, expect roughly **5–10% of EDSL-flagged issues to be confirmed** after investigation. A 546-call sweep may produce 200+ candidates but only 10–15 genuine issues. This is by design — the sweep is intentionally broad to avoid missing real issues, and investigation is where signal is separated from noise.
