---
name: eval-paper
description: Evaluate the paper against enabled criteria, writing candid narrative responses
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Evaluate Paper

Reads each enabled eval criterion, reads the relevant parts of the manuscript, and writes a candid narrative response. No scores, no ratings — just substantive text that helps the author understand how their paper reads.

## Usage

```
/eval-paper
```

## Prerequisites

- The paper must be registered in katz (`katz paper status` should return `"valid": true`).
- Eval criteria must be enabled (`katz eval list` should return results).
- If no criteria are enabled, run `katz eval init-catalog ` and then enable the relevant ones with `katz eval enable <name>`.

## Workflow

### 1. List enabled criteria

```bash
katz eval list
```

If empty, tell the user they need to enable criteria first.

### 2. For each criterion, evaluate

For each criterion returned by `katz eval list`:

#### a. Read the criterion

```bash
katz eval show <name>
```

Understand what the criterion is asking. Pay attention to the `scope` field — if it names a section, focus your reading there.

#### b. Read the manuscript

If the criterion has a `scope` (e.g., `abstract`, `introduction`), read that section:

```bash
katz paper section <scope>
```

Then read the corresponding lines from `.katz/versions/<commit>/paper/manuscript.md`.

If the criterion has no scope, read whatever parts of the manuscript are relevant to the question. For paper-level questions (contribution, positioning in literature), you may need to read the introduction, discussion, and conclusion.

#### c. Write a narrative response and assign a grade

Write a substantive response that addresses the criterion's question, then assign a letter grade. Guidelines for the narrative:

- **Be honest.** If something is weak, say so clearly. If something is strong, say that too. The value of this evaluation is candor, not diplomacy.
- **Be specific.** Reference specific passages, sections, or line numbers. "The abstract is clear" is not useful. "The abstract states the three main findings in lines 8-9 but omits the decomposition model" is useful.
- **Be constructive.** When identifying a weakness, suggest what would improve it.
- **Keep it concise.** 2-5 sentences per criterion is usually right. Don't pad.

Guidelines for the grade:

| Grade | Meaning |
|---|---|
| **A+** | Exemplary — could be used as a teaching example of how to do this well |
| **A** | Strong — meets the criterion clearly and completely |
| **A-** | Good — meets the criterion with minor room for improvement |
| **B+** | Adequate — meets the criterion but with notable gaps |
| **B** | Acceptable — partially meets the criterion; meaningful improvements possible |
| **B-** | Marginal — barely meets the criterion; significant improvements needed |
| **C+** | Weak — important deficiencies that should be addressed |
| **C** | Poor — the criterion is largely unmet |
| **C-** | Very poor — serious problems |
| **D/F** | Failing — the criterion is not met at all |

The grade should be calibrated against published papers in good field journals, not against perfection. Most criteria in a solid working paper should land in the A-to-B range. Grades below B- should be reserved for genuine problems. Do not grade inflate — a B+ is a real grade, not a consolation prize.

#### d. Record the response

```bash
katz eval respond --name <name> --grade "B+" --text "Your narrative response here"
```

The `--grade` flag accepts: A+, A, A-, B+, B, B-, C+, C, C-, D+, D, D-, F.

### 3. Report

After completing all criteria, report:
- How many criteria were evaluated
- A brief summary organized by category
- Run `katz eval results` to show the full set of responses
