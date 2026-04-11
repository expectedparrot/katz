---
name: review-paper
description: Bootstrap a full paper review using katz
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Review Paper

Orchestrates a full paper review using katz. Start here.

## Usage

```
/review-paper
```

## Workflow

Run `katz guide overview` to understand what katz can do and how to use it.

Then follow the typical workflow:

1. `katz guide skill register-paper` — register the manuscript
2. `katz guide skill chunk-paper` — add section boundaries
3. `katz guide skill configure-spotters` — choose what to look for
4. `katz guide skill edsl-find-issues` — find issues in parallel
5. `katz guide skill investigate-issues` — verify flagged issues

At each step, read the skill instructions and follow them. Use `katz guide script <path>` to inspect any scripts before running them.
