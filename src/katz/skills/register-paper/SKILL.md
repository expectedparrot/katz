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
- `paper2md` and `katz` must be on PATH.

## Workflow

### 1. Validate preconditions

1. Confirm `.katz/` exists in the repo root. If not, run `katz init`.
2. A clean working tree is recommended but not required — `katz paper register` pins to the current HEAD commit regardless.

### 2. Find the paper PDF

Look for the manuscript PDF in the repo. Common locations:

- `writeup/<name>.pdf`
- `paper/<name>.pdf`
- Root directory

Use `find . -name "*.pdf" -not -path "./.katz/*"` or Glob to locate it.

### 3. Convert PDF to markdown

Run `paper2md` on the paper PDF. Use the PDF stem for the output directory:

```bash
paper2md writeup/paper.pdf --output writeup/paper_md
```

The output directory will contain `paper.md` and extracted figure PNGs.

### 4. Register with katz

Katz auto-generates sentence segmentation from the canonical markdown. Run:

```bash
katz paper register \
  --canonical writeup/paper_md/paper.md \
  --source-format pdf \
  --source-method paper2md \
  --source-root writeup/paper.pdf
```

Adjust the paths to match what you found in step 2. This will:
- Read the markdown and segment it into sentences automatically
- Write `paper_map.jsonl` (a typed JSONL ledger with header + sentence records)
- Pin the registration to the current HEAD commit

### 5. Verify

Run `katz paper status` and confirm the registration succeeded. Report the output to the user, including the sentence count.

### 6. Cleanup

The output directory (e.g., `writeup/paper_md/`) is an intermediate artifact. Do NOT commit it or add it to git — it is regenerated on each registration. If it is already in `.gitignore`, leave it. If not, suggest adding it.
