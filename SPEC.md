# katz - technical specification

**Version:** 0.3
**Status:** Draft

---

## Table of contents

1. [Philosophy](#1-philosophy)
2. [Operating model](#2-operating-model)
3. [Storage layout](#3-storage-layout)
4. [Canonical manuscript bundle](#4-canonical-manuscript-bundle)
5. [version.json](#5-versionjson)
6. [paper_map.json](#6-paper_mapjson)
7. [Data schemas](#7-data-schemas)
8. [Byte anchoring](#8-byte-anchoring)
9. [Validation and repair](#9-validation-and-repair)
10. [CLI reference](#10-cli-reference)
11. [JSON output](#11-json-output)

---

## 1. Philosophy

katz is a version-aware ledger for paper review artifacts. It stores
canonical manuscript representations, issue findings, chunk definitions,
symbol tables, and investigation records keyed to specific commits of a
paper repository.

katz does not convert manuscripts, generate issues, decide chunking
strategies, build symbol tables, or run analyses. Those jobs belong to
agents and external tools. katz registers prepared manuscript bundles,
validates their structure, stores them immutably by commit, hydrates
anchored records from the canonical manuscript, and makes the result
queryable.

Four principles guide every design decision:

**Infrastructure only.** katz has no opinions about how issues are
found, how chunks are defined, or what fields an issue should carry. It
provides stable storage and a queryable interface. Workflow, pipeline,
and strategy belong to the agent.

**Git-native.** Every version is keyed to a full git commit SHA.
Findings are pinned to a specific state of the paper. There is no
ambiguity about what text an issue refers to.

**Byte-anchored.** Every finding references source text via half-open
byte ranges into the canonical manuscript at a specific commit.
Resolved text is cached from the actual file. Agents do not generate
quote text directly.

**Agent-first output.** All commands output JSON by default.
Human-readable output is available via `--human` and is the exception.

---

## 2. Operating model

katz operates in two contexts: standalone workspaces and existing paper
repositories. In both contexts, the core operation is registration of a
prepared canonical manuscript bundle.

### Prepared bundle

Before katz registers a version, an agent or external converter prepares:

- `manuscript.md`: canonical markdown, one prose sentence per line
- `paper_map.json`: index, checksum, and provenance for `manuscript.md`

katz validates this bundle, copies it into `.katz/versions/{commit}/`,
writes `version.json`, and updates `.katz/ACTIVE_VERSION`.

### Standalone mode

The user has a paper file or URL and wants to review it outside an
existing paper repository. An agent or wrapper first converts the source
into a prepared bundle. katz then creates a self-contained workspace,
initializes git, commits the source bundle, and registers that commit.

```bash
katz review --canonical manuscript.md --paper-map paper_map.json
katz review --source paper.pdf --canonical manuscript.md --paper-map paper_map.json
katz review --source https://arxiv.org/abs/2301.00001 --canonical manuscript.md --paper-map paper_map.json
```

katz:

1. Creates a workspace directory
2. Initializes a git repo inside it
3. Copies the prepared bundle into the workspace
4. Commits the bundle
5. Initializes `.katz/`
6. Registers the commit as the first active version

The source file or URL is recorded as provenance. katz does not fetch,
OCR, parse, or convert the source.

### Repo mode

The user has an existing paper repository they are actively writing in.
katz fits into their workflow without modifying manuscript source files.

```bash
cd ~/papers/no-last-mile
katz init
katz paper register --canonical /tmp/katz-bundle/manuscript.md \
                    --paper-map /tmp/katz-bundle/paper_map.json \
                    --source-root writeup/main.tex
```

katz reads the current git `HEAD`, validates the prepared bundle, and
stores it in `.katz/versions/{commit}/`. The author controls when to
register a version. Not every commit needs one.

---

## 3. Storage layout

```text
.katz/
  ACTIVE_VERSION              active full commit sha, one line
  versions/
    {commit}/
      version.json            registration metadata
      paper/
        manuscript.md         canonical one-sentence-per-line markdown
      paper_map.json          section index, sentence index, provenance
      symbol_table.json       extracted notation, written by agent
      chunks/
        {uuid}.json           chunk definitions, written by agent or CLI
      issues/
        {uuid}.json           issue records, written by agent or CLI
      investigations/
        {uuid}.json           investigation records, written by agent or CLI
```

### Notes

**`paper/manuscript.md`** is always a single file. Multi-file sources
are collapsed before registration by the agent or external converter.
Binary figure files are not stored by katz.

**`ACTIVE_VERSION`** contains the full commit SHA of the currently active
katz version. All commands without `--commit` operate against this
version.

**`{commit}`** is the full git commit SHA of the paper repo at the time
the version was registered. In standalone mode this is the SHA of the
workspace commit that contains the prepared bundle. In repo mode it is
the SHA of the author's repository `HEAD` at registration time.

**Agent writes directly.** Agents may write issue, investigation, chunk,
and symbol files directly into the appropriate directories. Direct
writes are considered untrusted until `katz validate` passes. Derived
location fields can be populated by CLI writes or by `katz repair`.

---

## 4. Canonical manuscript bundle

The canonical bundle is the input to registration. katz validates and
stores the bundle but does not create it.

### `paper/manuscript.md`

The canonical manuscript is:

- **Markdown**: converted from the source format before registration
- **Ventilated prose**: prose sentences split to one sentence per line
- **Block-preserving**: display math, equations, tables, lists, figure
  environments, and code blocks are preserved as blocks
- **Single file**: multi-file sources are collapsed before registration
- **UTF-8**: byte ranges must resolve to valid UTF-8

### Source formats

Common external conversion paths include:

| Format | Typical external path |
|--------|------------------------|
| Markdown | ventilate |
| LaTeX | flatex++ -> pandoc -> markdown |
| PDF | OCR -> markdown -> ventilate |
| DOCX | mammoth -> markdown -> ventilate |
| HTML / arXiv | markdownify -> ventilate |

These are conventions, not katz responsibilities.

### LaTeX convention

For LaTeX sources, an external converter may use `flatex++`, a patched
version of `flatex`, to:

1. Collapse `\input{}` and `\include{}` calls recursively into a single
   `.tex` file, preserving relative paths
2. Ventilate prose while preserving math environments, tables, figures,
   and verbatim content as blocks
3. Handle LaTeX-specific sentence boundary cases such as `\ref{}`,
   `\cite{}`, `\eqref{}`, abbreviations followed by `~`, and escaped
   periods

The resulting canonical manuscript is markdown. The original conversion
method is recorded in `paper_map.json`.

---

## 5. version.json

`version.json` records registration metadata for a version.

```json
{
  "schema_version": 1,
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "registered_at": "2026-03-10T08:00:00Z",
  "canonical": "paper/manuscript.md",
  "paper_map": "paper_map.json",
  "checksum": "sha256:3f8a...",
  "source": {
    "format": "latex",
    "root": "writeup/main.tex",
    "uri": null,
    "method": "flatex++ + pandoc",
    "files_collapsed": [
      "writeup/main.tex",
      "writeup/sections/intro.tex",
      "writeup/sections/model.tex"
    ]
  },
  "parent_commit": "89abcdef0123456789abcdef0123456789abcdef"
}
```

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | version schema revision |
| `commit` | string | full 40-character git SHA |
| `registered_at` | string | ISO 8601 timestamp with timezone |
| `canonical` | string | path to manuscript relative to version dir |
| `paper_map` | string | path to paper map relative to version dir |
| `checksum` | string | sha256 of canonical manuscript |
| `source` | object | source provenance |
| `parent_commit` | string or null | previous registered commit if known |

### Source object

| Field | Type | Description |
|-------|------|-------------|
| `format` | string | `latex`, `pdf`, `docx`, `md`, `html`, `arxiv`, or `unknown` |
| `root` | string or null | source root path relative to repo, when applicable |
| `uri` | string or null | source URL, when applicable |
| `method` | string | free text conversion description |
| `files_collapsed` | array | source files collapsed into the canonical manuscript |

---

## 6. paper_map.json

`paper_map.json` is the index that makes the manuscript queryable. It is
supplied at registration time and validated by katz.

```json
{
  "schema_version": 1,
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "canonical": "paper/manuscript.md",
  "checksum": "sha256:3f8a...",
  "source": {
    "format": "latex",
    "root": "writeup/main.tex",
    "uri": null,
    "method": "flatex++ + pandoc",
    "files_collapsed": [
      "writeup/main.tex",
      "writeup/sections/intro.tex",
      "writeup/sections/model.tex"
    ]
  },
  "sections": [
    {
      "id": "intro",
      "title": "Introduction",
      "byte_start": 0,
      "byte_end": 4821,
      "line_start": 1,
      "line_end": 64
    },
    {
      "id": "03-model",
      "title": "The Model",
      "byte_start": 4821,
      "byte_end": 18404,
      "line_start": 65,
      "line_end": 228
    }
  ],
  "sentences": [
    {
      "index": 0,
      "byte_start": 0,
      "byte_end": 95,
      "line_start": 1,
      "line_end": 1
    },
    {
      "index": 1,
      "byte_start": 95,
      "byte_end": 202,
      "line_start": 2,
      "line_end": 2
    }
  ],
  "figures": [
    {
      "id": "fig1",
      "byte_start": 2103,
      "byte_end": 2209,
      "line_start": 34,
      "line_end": 37,
      "source_file": "figures/fig1.pdf"
    }
  ],
  "provenance_map": []
}
```

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | paper map schema revision |
| `commit` | string | full 40-character git SHA this map was built against |
| `canonical` | string | path to manuscript relative to version dir |
| `checksum` | string | sha256 of canonical manuscript |
| `source.format` | string | `latex`, `pdf`, `docx`, `md`, `html`, `arxiv`, or `unknown` |
| `source.root` | string or null | source root path relative to repo, when applicable |
| `source.uri` | string or null | source URL, when applicable |
| `source.method` | string | free text conversion description |
| `source.files_collapsed` | array | source files collapsed into the canonical manuscript |
| `sections` | array | section boundaries |
| `sentences` | array | sentence index |
| `figures` | array | figure references, empty if none |
| `provenance_map` | array | optional canonical-to-source range mappings |

### Section object

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | stable slug |
| `title` | string | section title |
| `byte_start` | int | inclusive start offset |
| `byte_end` | int | exclusive end offset |
| `line_start` | int | 1-indexed start line |
| `line_end` | int | 1-indexed end line |

### Sentence object

| Field | Type | Description |
|-------|------|-------------|
| `index` | int | 0-indexed position in document |
| `byte_start` | int | inclusive start offset |
| `byte_end` | int | exclusive end offset |
| `line_start` | int | 1-indexed start line |
| `line_end` | int | 1-indexed end line |

### Provenance map object

`provenance_map` is optional but recommended when the converter can
produce it. It maps canonical byte ranges back to source files.

```json
{
  "canonical": {
    "byte_start": 4821,
    "byte_end": 4901,
    "line_start": 65,
    "line_end": 65
  },
  "source": {
    "file": "writeup/sections/model.tex",
    "line_start": 12,
    "line_end": 13
  },
  "method": "flatex++"
}
```

---

## 7. Data schemas

All stored records use `schema_version: 1` and full 40-character commit
SHAs. Record ids are 32-character lowercase hex UUID strings without
hyphens.

### 7.1 Location object

Locations are shared by issues, chunks, and symbols.

```json
{
  "byte_start": 14832,
  "byte_end": 14902,
  "line_start": 45,
  "line_end": 47,
  "resolved_text": "Suppose alpha in (0, 1), gamma > 0, r > 0, delta_k in (0, 1), eta > 0, and kappa > 0.",
  "contains_math": true
}
```

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `byte_start` | int | inclusive byte offset into `manuscript.md` |
| `byte_end` | int | exclusive byte offset into `manuscript.md` |
| `line_start` | int | 1-indexed start line |
| `line_end` | int | 1-indexed end line |
| `resolved_text` | string | cached from the byte range |
| `contains_math` | bool | true if resolved text contains math markup |

When writing directly, agents may provide only `byte_start` and
`byte_end`. `katz repair` can populate the derived fields.

### 7.2 Issue

```json
{
  "schema_version": 1,
  "id": "3f2a1b4c5d6e7f8091a2b3c4d5e6f708",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "state": "draft",
  "title": "Undefined parameter kappa in Theorem 1",
  "body": "kappa appears in the theorem hypothesis but is not defined in the model setup.",
  "location": {
    "byte_start": 14832,
    "byte_end": 14902,
    "line_start": 45,
    "line_end": 47,
    "resolved_text": "Suppose alpha in (0, 1), gamma > 0, r > 0, delta_k in (0, 1), eta > 0, and kappa > 0.",
    "contains_math": true
  },
  "created_at": "2026-03-10T08:22:11Z",
  "updated_at": "2026-03-10T08:22:11Z",
  "meta": {}
}
```

#### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | issue schema revision |
| `id` | string | 32-character lowercase hex UUID |
| `commit` | string | full SHA of the version this issue belongs to |
| `state` | string | `draft`, `confirmed`, `rejected`, or `resolved` |
| `title` | string | short description |
| `body` | string | full explanation in markdown |
| `location` | object | location object |
| `created_at` | string | ISO 8601 timestamp with timezone |
| `updated_at` | string | ISO 8601 timestamp with timezone |
| `meta` | object | open agent metadata |

#### meta conventions

`meta` is open. katz does not validate or enforce its contents. The
following field names are conventional and are filterable via
`--meta key=value`.

| Key | Values | Meaning |
|-----|--------|---------|
| `severity` | `major`, `moderate`, `minor`, or null | issue severity |
| `category` | string or null | issue type |
| `source` | string or null | how it was found |
| `model` | string or null | model that generated the issue |
| `agreement` | float 0-1 or null | vote agreement across models |
| `parent_issue_id` | string or null | issue id in a previous version |

### 7.3 Investigation

```json
{
  "schema_version": 1,
  "id": "8a3f1d9e5b6c708192a3b4c5d6e7f809",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "issue_id": "3f2a1b4c5d6e7f8091a2b3c4d5e6f708",
  "round": 1,
  "verdict": "confirmed",
  "findings": "Searched manuscript for kappa and found no definition before Theorem 1.",
  "created_at": "2026-03-10T08:45:03Z",
  "meta": {}
}
```

#### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | investigation schema revision |
| `id` | string | 32-character lowercase hex UUID |
| `commit` | string | full SHA |
| `issue_id` | string | id of the issue being investigated |
| `round` | int | 1-indexed; multiple rounds allowed per issue |
| `verdict` | string | `confirmed`, `rejected`, or `inconclusive` |
| `findings` | string | evidence trail in markdown |
| `created_at` | string | ISO 8601 timestamp with timezone |
| `meta` | object | open agent metadata |

### 7.4 Chunk

```json
{
  "schema_version": 1,
  "id": "c1d2e3f40516273849a0b1c2d3e4f506",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "index": 1,
  "location": {
    "byte_start": 4821,
    "byte_end": 9204,
    "line_start": 65,
    "line_end": 118,
    "resolved_text": "...",
    "contains_math": false
  },
  "created_at": "2026-03-10T08:10:00Z",
  "meta": {}
}
```

#### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | chunk schema revision |
| `id` | string | 32-character lowercase hex UUID |
| `commit` | string | full SHA |
| `index` | int | 0-indexed ordering across chunks |
| `location` | object | location object |
| `created_at` | string | ISO 8601 timestamp with timezone |
| `meta` | object | open agent metadata |

The agent decides what ranges to chunk. katz validates byte ranges and
can hydrate derived location fields.

### 7.5 Symbol

```json
{
  "schema_version": 1,
  "symbol": "delta_k",
  "aliases": ["delta_k", "\\delta_k"],
  "definition": "depreciation rate of capability stock",
  "defined_at": {
    "byte_start": 5204,
    "byte_end": 5262,
    "line_start": 11,
    "line_end": 11,
    "resolved_text": "...",
    "contains_math": true
  },
  "first_use_at": {
    "byte_start": 5204,
    "byte_end": 5262,
    "line_start": 11,
    "line_end": 11,
    "resolved_text": "...",
    "contains_math": true
  },
  "occurrences": []
}
```

#### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | symbol schema revision |
| `symbol` | string | canonical form, unicode or LaTeX |
| `definition` | string | one-sentence definition |

All other fields are optional. `defined_at`, `first_use_at`, and
`occurrences` use location objects.

`symbol_table.json` is an array of symbol objects. It is written by the
agent. katz reads it to answer `katz symbol` commands and validates it
when requested.

---

## 8. Byte anchoring

Every finding is anchored to the canonical manuscript via byte ranges.
All ranges are half-open:

```text
[byte_start, byte_end)
```

`byte_start` is inclusive. `byte_end` is exclusive. A one-byte span is
valid when `byte_end == byte_start + 1`.

### Agent workflow

1. Agent calls `katz paper find <text>` or `katz paper resolve` to
   locate a passage and get candidate byte ranges
2. Agent selects the correct range
3. Agent calls a CLI write command, or writes JSON directly with at
   least `byte_start` and `byte_end`
4. katz hydrates `resolved_text`, `line_start`, `line_end`, and
   `contains_math` during CLI writes or `katz repair`

The agent does not construct `resolved_text` itself. This prevents quote
hallucination by construction for CLI-hydrated records.

### Range validation

On any validation or CLI write that includes a location, katz checks:

- `byte_start >= 0`
- `byte_end > byte_start`
- `byte_end <= file_size` of `paper/manuscript.md` at this commit
- bytes in `[byte_start, byte_end)` are valid UTF-8
- derived `resolved_text`, when present, equals the bytes resolved from
  the canonical manuscript
- derived line numbers, when present, match the byte range

### Drift detection

When a new version is registered, `paper_map.json` and `version.json`
include a checksum of `manuscript.md`. If an agent wants to check
whether an issue's flagged text is still present in a later version, it
can:

1. Read the issue's `resolved_text`
2. Call `katz paper find <resolved_text> --commit <new_sha>`
3. If found: text persists and the issue may still be valid
4. If not found: text was removed or changed, and the agent decides what
   to do

katz provides the primitive. The agent decides the policy.

---

## 9. Validation and repair

Direct filesystem writes are allowed, but validation is explicit.

### `katz validate`

`katz validate` checks a version for:

- valid `.katz` layout
- `ACTIVE_VERSION` points to a registered full SHA
- `version.json` is present and matches the version directory
- `paper_map.json` is present and matches `version.json`
- canonical manuscript checksum matches both metadata files
- all JSON files match their schemas
- all ids are unique within their record type
- all commits in records match their containing version
- all issue, chunk, and symbol location ranges are valid
- derived location fields match the canonical manuscript when present
- all investigations reference existing issues in the same commit
- `symbol_table.json` is an array when present

### `katz repair`

`katz repair` performs deterministic repairs only:

- populate missing `resolved_text`, `line_start`, `line_end`, and
  `contains_math` for valid locations
- rewrite stale derived location fields from the canonical manuscript
- create missing empty directories under a version
- create a missing empty `symbol_table.json`

`katz repair` does not invent issue bodies, change states, alter byte
ranges, or modify agent-defined metadata.

---

## 10. CLI reference

### Global flags

```text
--commit <sha>    target a specific version; prefixes accepted if unambiguous
--human           human-readable output; default is JSON
```

Internally, katz canonicalizes all accepted SHA prefixes to full
40-character SHAs in stored JSON.

### katz review

```bash
katz review --canonical <path> --paper-map <path> [--source <path-or-url>]
```

Standalone mode entry point. Creates a workspace, initializes git,
commits the prepared bundle, initializes `.katz/`, validates the bundle,
and registers the first active version.

### katz init

```bash
katz init
```

Repo mode entry point. Initializes `.katz/` in the current directory.
Requires an existing git repo.

### katz paper

```bash
katz paper register --canonical <path> --paper-map <path> [--source-root <path>] [--source-uri <url>]
```

Register a new version from the current git `HEAD`. Validates and stores
the prepared bundle, writes `version.json`, and updates `ACTIVE_VERSION`.

```bash
katz paper status
```

```json
{
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "source_format": "latex",
  "source_root": "writeup/main.tex",
  "source_uri": null,
  "canonical": "paper/manuscript.md",
  "sections": 12,
  "sentences": 487,
  "figures": 5,
  "valid": true
}
```

```bash
katz paper section <id>
```

```json
{
  "id": "03-model",
  "title": "The Model",
  "byte_start": 4821,
  "byte_end": 18404,
  "line_start": 65,
  "line_end": 228
}
```

```bash
katz paper resolve <byte-start> <byte-end>
```

```json
{
  "byte_start": 14832,
  "byte_end": 14902,
  "line_start": 45,
  "line_end": 47,
  "resolved_text": "Suppose alpha in (0, 1), gamma > 0, r > 0, delta_k in (0, 1), eta > 0, and kappa > 0.",
  "contains_math": true,
  "section": "03-model"
}
```

```bash
katz paper find <text> [--mode exact|normalized] [--ignore-case] [--limit <n>]
```

```json
[
  {
    "byte_start": 14832,
    "byte_end": 14902,
    "line_start": 45,
    "line_end": 47,
    "section": "03-model",
    "resolved_text": "Suppose alpha in (0, 1)..."
  },
  {
    "byte_start": 61204,
    "byte_end": 61252,
    "line_start": 312,
    "line_end": 312,
    "section": "appendix",
    "resolved_text": "where kappa > 0 is defined as before."
  }
]
```

```bash
katz paper sentences [--section <id>] [--from-line N] [--to-line N]
```

Returns the sentence index, optionally filtered. Used by agents
computing chunk boundaries.

```json
[
  {
    "index": 64,
    "byte_start": 4821,
    "byte_end": 4902,
    "line_start": 65,
    "line_end": 65
  },
  {
    "index": 65,
    "byte_start": 4902,
    "byte_end": 4999,
    "line_start": 66,
    "line_end": 66
  }
]
```

### katz version

```bash
katz version list
```

```json
[
  {
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "registered_at": "2026-03-01T10:00:00Z",
    "issue_count": 28,
    "current": false
  },
  {
    "commit": "89abcdef0123456789abcdef0123456789abcdef",
    "registered_at": "2026-03-10T08:00:00Z",
    "issue_count": 31,
    "current": true
  }
]
```

```bash
katz version checkout <sha>
```

Updates `ACTIVE_VERSION` to the given registered commit. SHA prefixes
are accepted if unambiguous.

```bash
katz version diff <sha-a> <sha-b>
```

```json
{
  "from": "0123456789abcdef0123456789abcdef01234567",
  "to": "89abcdef0123456789abcdef0123456789abcdef",
  "modified_sections": ["03-model", "04-results"],
  "unchanged_sections": ["intro", "related-work", "discussion", "conclusion", "appendix"],
  "changes": [
    {
      "section": "03-model",
      "line_start": 88,
      "line_end": 88,
      "type": "changed",
      "before": "The depreciation rate delta_k in (0,1) is...",
      "after": "The depreciation rate delta_k in (0,1) captures..."
    },
    {
      "section": "03-model",
      "line_start": 103,
      "line_end": 103,
      "type": "added",
      "after": "Three channels contribute to depreciation:..."
    }
  ]
}
```

### katz issue

```bash
katz issue list [--state draft|confirmed|rejected|resolved] \
                [--section <id>] \
                [--meta <key>=<value>]
```

```json
[
  {
    "id": "3f2a1b4c5d6e7f8091a2b3c4d5e6f708",
    "state": "draft",
    "title": "Undefined parameter kappa in Theorem 1",
    "location": {
      "line_start": 45,
      "line_end": 47,
      "section": "03-model"
    },
    "meta": {
      "category": "undefined-symbol",
      "severity": null
    }
  }
]
```

```bash
katz issue show <id>
```

Returns the full issue record.

```bash
katz issue write --title <str> \
                 --byte-start <n> \
                 --byte-end <n> \
                 --body <str> \
                 [--state <state>] \
                 [--meta <json>]
```

CLI writes hydrate all derived location fields and return the full issue
record.

```bash
katz issue update <id> [--state <state>] [--title <str>] [--body <str>] [--meta <json>]
```

Meta is merged. Existing fields not mentioned are preserved.

```bash
katz issue patch <id> <field> <value>
```

Sets a single meta field. Value is parsed as JSON if valid, otherwise
treated as a string.

```bash
katz issue patch 3f2a1b4c5d6e7f8091a2b3c4d5e6f708 severity major
katz issue patch 3f2a1b4c5d6e7f8091a2b3c4d5e6f708 parent_issue_id 9c1d2e3f40516273849a0b1c2d3e4f506
```

```bash
katz issue export [--format json|jsonl|markdown] [--state <state>]
```

Exports issues for handoff to authors or external review tools.

### katz investigation

```bash
katz investigation list [--issue <id>]
katz investigation show <id>
katz investigation write --issue <id> \
                         --round <n> \
                         --verdict confirmed|rejected|inconclusive \
                         --findings <str> \
                         [--meta <json>]
```

### katz chunk

```bash
katz chunk list
katz chunk show <id>
katz chunk write --byte-start <n> --byte-end <n> \
                 [--index <n>] \
                 [--meta <json>]
katz chunk clear
```

`katz chunk clear` removes all chunk files for the current version. It
is used when an agent wants to rechunk with a different strategy.

### katz symbol

```bash
katz symbol list
katz symbol show <symbol>
katz symbol check
```

`katz symbol check` cross-references the symbol table against the
sentence index and reports likely symbols found in the manuscript text
that are not present in `symbol_table.json`.

### katz validate

```bash
katz validate [--commit <sha>]
```

Returns validation results without modifying files.

```json
{
  "valid": false,
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "errors": [
    {
      "code": "stale_resolved_text",
      "path": ".katz/versions/0123456789abcdef0123456789abcdef01234567/issues/3f2a1b4c5d6e7f8091a2b3c4d5e6f708.json",
      "message": "resolved_text does not match manuscript bytes"
    }
  ],
  "warnings": []
}
```

### katz repair

```bash
katz repair [--commit <sha>] [--check]
```

Hydrates or rewrites deterministic derived fields. With `--check`, katz
reports planned repairs without writing files.

### katz doctor

```bash
katz doctor
```

Checks the current workspace, active version, registered versions, and
common environmental problems. `doctor` may call `validate`, but it is
intended for broad diagnostics rather than schema validation alone.

---

## 11. JSON output

All commands output JSON by default. Pass `--human` for formatted
plain-text output suitable for reading at a terminal.

### Conventions

- All timestamps are ISO 8601 with timezone, for example
  `2026-03-10T08:22:11Z`
- All stored commits are full 40-character SHAs
- CLI inputs may use SHA prefixes only when unambiguous
- All UUIDs are 32-character lowercase hex strings without hyphens
- All byte ranges are half-open: `[byte_start, byte_end)`
- Null fields are included explicitly in full record outputs
- Summary and list outputs may omit fields to keep responses compact
- Arrays are arrays even when empty
- Errors are returned with a non-zero exit code

### Error shape

```json
{
  "error": "Byte range is outside manuscript bounds",
  "code": "invalid_range",
  "details": {
    "byte_start": 14832,
    "byte_end": 999999,
    "file_size": 81204
  }
}
```

### Error codes

| Code | Meaning |
|------|---------|
| `not_found` | Entity does not exist |
| `invalid_commit` | SHA is not registered as a katz version |
| `ambiguous_commit` | SHA prefix matches multiple registered versions |
| `invalid_range` | Byte range out of bounds or invalid UTF-8 |
| `checksum_mismatch` | Metadata checksum does not match manuscript |
| `no_paper_map` | `paper_map.json` missing for this version |
| `no_version_manifest` | `version.json` missing for this version |
| `validation_error` | Required field missing or wrong type |
| `dangling_reference` | A record references a missing record |
| `stale_resolved_text` | Cached text does not match manuscript bytes |
| `repair_required` | Record is valid enough to repair but not fully hydrated |
