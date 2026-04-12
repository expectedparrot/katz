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

#### c. Write a narrative response

Write a substantive response that addresses the criterion's question. Guidelines:

- **Be honest.** If something is weak, say so clearly. If something is strong, say that too. The value of this evaluation is candor, not diplomacy.
- **Be specific.** Reference specific passages, sections, or line numbers. "The abstract is clear" is not useful. "The abstract states the three main findings in lines 8-9 but omits the decomposition model" is useful.
- **Be constructive.** When identifying a weakness, suggest what would improve it.
- **Don't grade.** No scores, no "7/10", no "good/fair/poor." Just describe what you see and what it means.
- **Keep it concise.** 2-5 sentences per criterion is usually right. Don't pad.

#### d. Record the response

```bash
katz eval respond --name <name> --text "Your narrative response here"
```

### 3. Report

After completing all criteria, report:
- How many criteria were evaluated
- A brief summary organized by category
- Run `katz eval results` to show the full set of responses
