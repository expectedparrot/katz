---
name: referee-report
description: Synthesize investigated katz issues into a narrative referee report
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Referee Report

Synthesizes the structured issue data from a katz review into a narrative referee report suitable for submission to a journal editor or sharing with authors.

## Usage

```
/referee-report
```

## Prerequisites

- The paper must be registered in katz (`katz paper status` should return `"valid": true`).
- Issues must exist and most should be investigated (not all `draft`).
- Run the data-gathering script first to produce the structured input.

## Workflow

### 1. Gather the data

Run the helper script to produce a structured summary of the review:

```bash
python <katz-skills-path>/referee-report/scripts/gather_review_data.py
```

This writes `.katz/review_data.json` containing:
- Paper metadata (title, source, sections, sentence count)
- Issue counts by state (confirmed, rejected, open, draft)
- All confirmed issues with full details and investigation notes
- All open/uncertain issues with details
- Section-level summary statistics

Read the output file to understand the full scope of the review.

### 2. Read the manuscript

Read the abstract and introduction from the canonical manuscript to understand the paper's contribution and framing. Use `katz paper section <id>` to get line ranges, then read the relevant portions.

### 3. Write the referee report

Write the report as markdown to `.katz/referee_report.md`. Follow this structure:

#### Header

```
# Referee Report: [Paper Title]

**Date:** [today]
**Paper:** [source file]
**Review method:** Automated multi-model review via katz, with manual investigation
```

#### Summary (1 paragraph)

One paragraph summarizing what the paper does, its main contribution, and its overall quality. Be specific about the methodology and findings — do not be generic.

#### Overall Assessment (2–3 paragraphs)

Your high-level evaluation. Address:
- Is the research question well-motivated?
- Is the methodology appropriate for the claims?
- Are the results convincing?
- What is the paper's main strength?
- What is the paper's main weakness?

Ground every judgment in specific evidence from the review. Do not make claims you cannot trace to a confirmed issue or a reading of the manuscript.

#### Major Concerns

List the confirmed issues that represent substantive problems — things that should be addressed before publication. Group them thematically, not by section. Each concern should:
- State the problem clearly in 1–2 sentences
- Reference the specific manuscript location (section, line range)
- Explain why it matters for the paper's contribution
- Suggest a path to resolution where possible

Typical themes to group by:
- **Unsupported claims** — conclusions stronger than the evidence
- **Internal inconsistencies** — numbers, descriptions, or claims that conflict
- **Methodological concerns** — design choices that could bias results
- **Missing information** — details needed for reproducibility or evaluation

Do NOT list every confirmed issue individually if several point to the same underlying concern. Synthesize them into a coherent narrative. Reference the underlying issue IDs in parentheses for traceability, e.g., (issues 00b5b04c, 0e02ab37).

#### Minor Concerns

Confirmed issues that are real but less consequential — unclear writing, small inconsistencies, presentation issues. These can be listed more briefly. Again, group by theme rather than listing individually.

#### Open Questions

Any uncertain/open issues that the authors could clarify. Frame these as questions, not accusations.

#### Positive Observations (optional)

If the review revealed strengths — e.g., many overclaiming flags were rejected because the paper hedges carefully, or the methodology is sound against multiple challenges — note these. Reviewers who only list problems are less useful than those who also identify what works.

### 4. Verify and finalize

Re-read the report. Check:
- Every major concern traces to at least one confirmed issue
- No confirmed issue is omitted without reason
- The tone is constructive and professional
- The report would be useful to the authors, not just the editor
- The summary accurately reflects the paper (re-read the abstract if needed)

Report the path to the finished file.
