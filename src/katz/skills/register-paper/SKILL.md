---
name: register-paper
description: Register the paper source with katz — directly from TeX (preferred) or converted from PDF
allowed-tools: Read, Write, Bash, Glob, Grep
user-invocable: true
---

# Register Paper

Registers the paper manuscript with `katz paper register`. Katz auto-generates sentence
segmentation from the manuscript text.

**If the source is a `.tex` file, register it directly — no conversion needed.**
Katz handles TeX syntax natively: it skips comments, structural commands, and non-prose
environments, and extracts prose sentences from ventilated lines.

Fall back to PDF conversion only if no `.tex` source is available.

## Usage

```
/register-paper
```

## Prerequisites

- The repo must have a `.katz` directory (run `katz init` if not).
- `paper2md` must be on PATH (for `.pdf` fallback only).
- `katz` must be on PATH.

## Ventilated prose

Katz works at the sentence level. For accurate tracking, the manuscript should be
**ventilated**: one sentence per line, with no hard line-wrapping inside sentences.

**Good — ventilated:**
```
We propose a novel framework for causal inference.
The framework extends prior work in two key directions.
First, it relaxes the positivity assumption.
```

**Bad — not ventilated:**
```
We propose a novel framework for causal inference. The framework extends prior work in
two key directions. First, it relaxes the positivity assumption.
```

Check ventilation before registering. For Markdown, use `katz ventilate input.md
--output-path output.md`, inspect the diff, and commit the derivative before
registering it. For TeX, encourage the user to reformat it directly so each
sentence is on its own line. After registration, katz will emit a warning if it
detects likely non-ventilated lines.

## Workflow

### 1. Validate preconditions

1. Confirm `.katz/` exists in the repo root. If not, run `katz init`.
2. A clean working tree is recommended but not required — `katz paper register` pins to
   the current HEAD commit regardless.

### 2. Find the paper source

Look for the main `.tex` file first, then fall back to PDF.

**Preferred — LaTeX source:**
- `writeup/<name>.tex`
- `paper/<name>.tex`
- Root directory

Use Glob (`**/*.tex`) to find candidates. The main file is typically the one that
contains `\begin{document}`. If multiple `.tex` files exist, check which one is the
root document (it will have `\documentclass` and `\begin{document}`).

**Fallback — PDF:**
- `writeup/<name>.pdf`
- `paper/<name>.pdf`

Only use PDF if no `.tex` source is available in the repo.

### 3. Prepare the manuscript

#### From LaTeX source (preferred — no conversion needed)

Register the `.tex` file directly. Before registering, check that the prose is
ventilated (one sentence per line). Open the main `.tex` file and look at a few
paragraphs. If sentences run together on the same line, tell the user:

> This manuscript is not ventilated. Katz tracks issues at the sentence level,
> so it works best when each sentence is on its own line. Would you like to
> ventilate it now? This is a simple formatting change in the `.tex` source.

If they agree, go through the prose sections and split sentences onto separate lines.
Leave math environments, figure/table environments, and command lines unchanged.

#### From PDF (fallback)

```bash
paper2md writeup/paper.pdf --output writeup/paper_md
```

The output directory will contain `paper.md` and extracted figure PNGs.
Check the output for ventilation — `paper2md` often produces one sentence per line,
but verify before registering.

### 4. Register with katz

```bash
# From LaTeX source (direct — no conversion)
katz paper register \
  --canonical writeup/main.tex \
  --source-format tex \
  --source-method direct \
  --source-root writeup/main.tex

# From PDF (fallback)
katz paper register \
  --canonical writeup/paper_md/paper.md \
  --source-format pdf \
  --source-method paper2md \
  --source-root writeup/paper.pdf
```

Adjust the paths to match what you found in step 2. This will:
- Read the manuscript and segment it into sentences automatically
- Write `paper_map.jsonl` (a typed JSONL ledger with header + sentence records)
- Pin the registration to the current HEAD commit

If the output contains a `"warning"` field about non-ventilated lines, flag it to
the user and offer to help ventilate before proceeding.

### 5. Verify

Run `katz paper status` and confirm the registration succeeded. Report the output to
the user, including the sentence count.

### 6. Cleanup (PDF path only)

If you used PDF conversion, the output directory (e.g., `writeup/paper_md/`) is an
intermediate artifact. Do NOT commit it or add it to git — it is regenerated on each
registration. If it is already in `.gitignore`, leave it. If not, suggest adding it.

For direct TeX registration, there is nothing to clean up.
