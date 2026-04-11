---
name: chunk-paper
description: Chunk the registered paper into logical sections and add them to the katz paper map
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
user-invocable: true
---

# Chunk Paper

Reads the canonical manuscript from a registered katz paper, identifies logical sections with clean IDs and titles, and appends them to the paper map using `katz paper add-sections`.

## Usage

```
/chunk-paper
```

## Prerequisites

- The paper must already be registered with katz (`katz paper status` should return `"valid": true`).
- `katz` must be on PATH.

## Workflow

### 1. Validate preconditions

1. Run `katz paper status` and confirm `"valid": true`.
2. Note the current section count. If sections already exist, ask the user whether to proceed (adding sections is append-only; duplicate IDs will be rejected).

### 2. Read the canonical markdown

The canonical manuscript is stored inside `.katz/`. To find it, run:

```bash
katz paper resolve 0 1
```

This confirms the manuscript is accessible. Then read the full manuscript from the version directory. The path is `.katz/versions/<commit>/paper/manuscript.md` where `<commit>` is from `katz paper status`.

### 3. Identify logical sections

Analyze the markdown and identify logical sections. The goal is to produce sections that are:

- **Clean**: IDs are human-readable slugs (e.g., `introduction`, `experiment-design`, `phase-1-results`), not auto-generated `span-id-page-*` artifacts from PDF conversion.
- **Logical**: Each section corresponds to a coherent unit of the paper. Large sections may be split if they cover distinct topics. Very short adjacent headings that form a single logical unit may be merged.
- **Complete**: Every byte of the document belongs to exactly one section. Sections must tile the full document with no gaps or overlaps.

Guidelines for section identification:

1. Start from the markdown headings (`#`, `##`, `###`, etc.).
2. Replace any `span-id-page-*` prefixed slugs with clean descriptive slugs based on the actual heading text.
3. For numbered sections (e.g., `## 2. Experiment Design`), use slugs like `experiment-design` (drop the number, keep the meaning).
4. Subsections (`###` and below) should keep their parent context in the slug (e.g., `phase-1-ranking-behavior`).
5. The title field should be the clean heading text (strip markdown formatting, numbering artifacts, span tags).

### 4. Compute byte ranges and build the sections array

Using Python via Bash, compute the byte offsets for each section:

```python
import json, re

# Read the canonical markdown
with open("<path to manuscript.md>", "rb") as f:
    raw = f.read()

text = raw.decode("utf-8")
lines = text.split("\n")

# For each heading, compute byte_start as:
#   sum(len(l.encode("utf-8")) + 1 for l in lines[:i])
# where i is the 0-indexed line number.
#
# byte_end for each section extends to the byte_start of the next section,
# or len(raw) for the last section.

sections = []  # ... populated from analysis ...

# Output as JSON for the katz command
print(json.dumps(sections))
```

**Important**: The byte offsets must be exact. Each section needs `id`, `title`, `byte_start`, and `byte_end`. The `line_start` and `line_end` will be computed automatically by `katz paper add-sections`.

### 5. Add sections with katz

Pass the sections JSON array to `katz paper add-sections`:

```bash
katz paper add-sections --sections '<JSON array from step 4>'
```

This appends section records to `paper_map.jsonl`. Katz will reject any duplicate section IDs.

### 6. Verify

Run `katz paper status` and confirm:
- `"valid": true`
- The section count matches the number of logical sections identified.

Then run `katz paper section <id>` for 2–3 sections to spot-check that the byte ranges resolve correctly and the text content makes sense.

Report the final section list (id, title, line range) to the user.
