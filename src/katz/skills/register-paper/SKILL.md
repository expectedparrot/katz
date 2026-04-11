---
name: register-paper
description: Convert the paper source to canonical markdown and register it with katz
allowed-tools: Read, Write, Bash, Glob, Grep
user-invocable: true
---

# Register Paper

Converts the paper source to canonical markdown and registers the result with `katz paper register`. Katz auto-generates sentence segmentation from the markdown.

The preferred source is the **main `.tex` file** — it preserves LaTeX macros, cross-references, math, and footnotes that are lost or garbled in PDF conversion. Fall back to PDF only if no `.tex` file is available.

## Usage

```
/register-paper
```

## Prerequisites

- The repo must have a `.katz` directory (run `katz init` if not).
- `pandoc` must be on PATH (for `.tex` → markdown conversion).
- `paper2md` must be on PATH (for `.pdf` fallback).
- `katz` must be on PATH.

## Workflow

### 1. Validate preconditions

1. Confirm `.katz/` exists in the repo root. If not, run `katz init`.
2. A clean working tree is recommended but not required — `katz paper register` pins to the current HEAD commit regardless.

### 2. Find the paper source

Look for the main `.tex` file first, then fall back to PDF.

**Preferred — LaTeX source:**
- `writeup/<name>.tex`
- `paper/<name>.tex`
- Root directory

Use Glob (`**/*.tex`) to find candidates. The main file is typically the one that contains `\begin{document}`. If multiple `.tex` files exist, check which one is the root document (it will have `\documentclass` and `\begin{document}`).

**Fallback — PDF:**
- `writeup/<name>.pdf`
- `paper/<name>.pdf`

Only use PDF if no `.tex` source is available in the repo.

### 3. Convert to markdown

#### From LaTeX (preferred)

Use `pandoc` to convert the main `.tex` file to markdown:

```bash
pandoc writeup/paper.tex \
  -f latex -t markdown \
  --wrap=none \
  -o writeup/paper_md/paper.md
```

Create the output directory first if needed. Key `pandoc` flags:
- `--wrap=none` — prevents line wrapping that breaks sentence segmentation
- `-f latex` — parse as LaTeX
- `-t markdown` — output GitHub-flavored markdown

If the `.tex` file uses `\input{}` or `\include{}` for sub-files, pandoc will resolve them automatically if run from the correct directory.

#### From PDF (fallback)

```bash
paper2md writeup/paper.pdf --output writeup/paper_md
```

The output directory will contain `paper.md` and extracted figure PNGs.

### 4. Register with katz

```bash
# From LaTeX source
katz paper register \
  --canonical writeup/paper_md/paper.md \
  --source-format tex \
  --source-method pandoc \
  --source-root writeup/paper.tex

# From PDF (fallback)
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
