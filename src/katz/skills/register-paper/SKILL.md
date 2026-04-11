---
name: register-paper
description: Convert the paper PDF to canonical markdown and register it with katz
allowed-tools: Read, Write, Bash, Glob, Grep
user-invocable: true
---

# Register Paper

Converts the paper PDF to canonical markdown using `paper2md` and registers the result with `katz paper register`. Katz auto-generates sentence segmentation from the markdown.

## Usage

```
/register-paper
```

## Prerequisites

- The repo must have a `.katz` directory (run `katz init` if not).
- The working tree must be clean and committed (the registration is pinned to HEAD).
- `paper2md` and `katz` must be on PATH.

## Workflow

### 1. Validate preconditions

1. Confirm `.katz/` exists in the repo root. If not, run `katz init`.
2. Confirm there are no uncommitted changes by running `git status --porcelain`. If there are changes, stop and tell the user they need to commit first — `katz paper register` pins to the current HEAD commit.

### 2. Convert PDF to markdown

1. Run `paper2md` on the paper PDF:

```bash
paper2md writeup/job_prefs.pdf --output writeup/job_prefs_md
```

2. The output directory `writeup/job_prefs_md/` will contain `paper.md` and extracted figure PNGs.

### 3. Register with katz

Katz now auto-generates sentence segmentation from the canonical markdown. No `paper_map.json` is needed. Run:

```bash
katz paper register \
  --canonical writeup/job_prefs_md/paper.md \
  --source-format pdf \
  --source-method paper2md \
  --source-root writeup/job_prefs.pdf
```

This will:
- Read the markdown and segment it into sentences automatically
- Write `paper_map.jsonl` (a typed JSONL ledger with header + sentence records)
- Pin the registration to the current HEAD commit

### 4. Verify

Run `katz paper status` and confirm the registration succeeded. Report the output to the user, including the sentence count.

### 5. Cleanup

The `writeup/job_prefs_md/` directory is an intermediate artifact. Do NOT commit it or add it to git — it is regenerated on each registration. If `writeup/job_prefs_md/` is already in `.gitignore`, leave it. If not, suggest adding it.
