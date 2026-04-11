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

### 2. For each issue, investigate

For each issue in the set:

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

Then read the relevant portion of the canonical manuscript at `.katz/versions/<commit>/paper/manuscript.md` around the issue's line range. Read enough context (at least 20 lines before and after) to understand the claim in context.

#### c. Verify against source material

Depending on the issue type (indicated by the spotter tag in the body, e.g., `[overclaiming]`, `[logical_gaps]`):

- **Overclaiming / logical gaps**: Check if the claim is actually supported elsewhere in the paper. Read other relevant sections. Check if the evidence or methodology sections back up the claim.
- **Internal contradictions**: Find both contradicting statements and verify they actually conflict.
- **Unclear writing**: Re-read the passage and determine if it's genuinely unclear or if context resolves the ambiguity.
- **Methodology errors**: Check the experimental design sections, look at any analysis code in the repo.
- **For all types**: Check the LaTeX source (`writeup/job_prefs.tex`) for additional context that may have been lost in PDF conversion (e.g., footnotes, LaTeX macros, cross-references).

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

#### f. Update the issue state

After investigating, update the state:

- If **confirmed**: `katz issue update --id <id> --state confirmed --reason "Investigation confirmed the issue"`
- If **rejected**: `katz issue update --id <id> --state rejected --reason "Brief explanation"`
- If **uncertain**: `katz issue update --id <id> --state open --reason "Needs further review"`

### 3. Report

After investigating all issues in scope, report:

- Total investigated
- Breakdown by verdict (confirmed / rejected / uncertain)
- Summary table: issue title, section, verdict, one-line finding
- Run `katz issue list` to show the updated state distribution
