---
name: suggest-fixes
description: Generate actionable suggestions for confirmed issues and low-grade evaluations
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Suggest Fixes

Generates actionable narrative suggestions for confirmed issues and evaluations that scored below A-. Runs as a separate step after investigation and evaluation, so the agent has full context.

## Usage

```
/suggest-fixes
```

## Prerequisites

- Issues should be investigated (`katz issue list --state confirmed` returns results).
- Evaluations should be completed (`katz eval results` returns results).

## Workflow

### 1. Load confirmed issues

```bash
katz issue list --state confirmed
```

For each confirmed issue:

1. Read the full issue with `katz issue show <id>`
2. Read the manuscript context around the issue's line range
3. Write a specific, actionable suggestion for how to fix the problem
4. Record with `katz issue suggest --id <id> --text "..."`

### 2. Load low-grade evaluations

```bash
katz eval results
```

Filter for evaluations with grades below A- (B+, B, B-, C+, C, C-, D, F). For each:

1. Read the criterion with `katz eval show <name>`
2. Read the evaluation response and grade
3. Read the relevant manuscript section
4. Write a specific suggestion for how to improve the grade
5. Re-record with `katz eval respond --name <name> --text "..." --grade "..." --suggestion "..."`
   (preserve the existing text and grade, add the suggestion)

### 3. Guidelines for writing suggestions

- **Be specific.** "Improve the abstract" is useless. "Add a sentence after line 8 stating that no prior work has examined LLM responses for life preference revelation" is useful.
- **Be actionable.** The author should be able to act on the suggestion without further interpretation. Name the section, the line, the specific text.
- **Be realistic.** Don't suggest redoing the entire study. Suggest changes to the manuscript that address the identified concern.
- **Propose language when possible.** If the fix is a wording change, suggest the new wording. "Consider replacing 'potentially less prone to biases' with 'less noisy, though whether they are also less biased remains an open question.'"
- **Prioritize.** Not every confirmed issue needs an equally detailed suggestion. Focus effort on the issues that matter most for the paper's contribution.

### 4. Report

After generating suggestions, regenerate the HTML report to see them:

```bash
python <katz-skills-path>/find-issues/scripts/generate_review_report.py
```

Suggestions appear with a green left border and lightbulb icon in both issue and eval cards.
