from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DOCS_DIR = Path(__file__).parent / "docs_content"

DOCS: dict[str, dict] = {
    "overview": {
        "title": "Package Overview",
        "summary": "What katz does, when to use it, core concepts, and storage layout.",
        "file": "overview.md",
    },
    "getting-started": {
        "title": "Getting Started",
        "summary": "Complete worked example from paper registration through issue investigation.",
        "file": "getting-started.md",
    },
    "workflow": {
        "title": "Review Workflow",
        "summary": "Phase-by-phase guide: init, register, chunk, spotters, find, investigate, report.",
        "file": "workflow.md",
    },
    "edsl-codegen": {
        "title": "EDSL Code Generation",
        "summary": "How to generate a Python EDSL review script from katz state instead of running one directly.",
        "file": "edsl-codegen.md",
    },
    "cli-reference": {
        "title": "CLI Quick Reference",
        "summary": "All commands with syntax, flags, and examples.",
        "file": "cli-reference.md",
    },
}


def load_doc(topic: str) -> str:
    meta = DOCS[topic]
    return (DOCS_DIR / meta["file"]).read_text(encoding="utf-8")


def search_docs(query: str) -> list[dict]:
    terms = re.findall(r"[A-Za-z0-9_-]+", query.lower())
    results = []
    for topic, meta in DOCS.items():
        try:
            text = load_doc(topic)
        except OSError:
            continue
        haystack = f"{topic} {meta['title']} {meta['summary']} {text}".lower()
        score = sum(haystack.count(t) for t in terms)
        if score > 0:
            snippet = ""
            for term in terms:
                idx = haystack.find(term)
                if idx >= 0:
                    start = max(0, idx - 60)
                    end = min(len(text), idx + 200)
                    snippet = text[start:end].strip()
                    break
            results.append({**meta, "topic": topic, "score": score, "snippet": snippet})
    return sorted(results, key=lambda r: r["score"], reverse=True)


# ---------------------------------------------------------------------------
# Phase inference
# ---------------------------------------------------------------------------

CHECKLISTS: dict[str, list[str]] = {
    "no_katz": [
        "Initialize katz in the repo root: `katz init`",
        "Then register the paper: `katz paper register --canonical <path/to/paper.md>`",
    ],
    "initialized": [
        "Register a paper: `katz paper register --canonical <path/to/paper.md>`",
        "The manuscript should be in ventilated prose (one sentence per line) for best results.",
    ],
    "registered": [
        "Add sections: `katz paper auto-chunk`",
        "Verify sections detected: `katz paper sections`",
    ],
    "chunked": [
        "Initialize the spotter catalog: `katz spotter init-catalog`",
        "Enable spotters for this review: `katz spotter enable overclaiming` (repeat for each)",
        "Then generate an EDSL find-issues script via `katz docs show edsl-codegen`.",
    ],
    "spotters_configured": [
        "Read `katz docs show edsl-codegen` for the code generation template.",
        "Generate a Python script using the template (sections and spotters are in the agent-start payload).",
        "Show the script to the user and ask them to run it.",
    ],
    "issues_found": [
        "Expect ~5–10% of draft issues to be genuine. Investigate each one.",
        "List drafts: `katz issue list --state draft`",
        "Read an issue: `katz issue show <id-prefix>`",
        "Record verdict: `katz issue investigate --id <id> --verdict confirmed --notes '...'`",
        "Update state: `katz issue update --id <id> --state confirmed`",
    ],
    "investigated": [
        "List confirmed issues: `katz issue list --state confirmed`",
        "Generate the HTML report: `katz report generate --output review.html`",
        "Or enable more spotters and generate another EDSL sweep for deeper coverage.",
    ],
}

NEXT_STEPS: dict[str, list[dict]] = {
    "no_katz": [
        {"label": "Initialize katz", "command": "katz init"},
    ],
    "initialized": [
        {"label": "Register paper", "command": "katz paper register --canonical <paper.md>"},
    ],
    "registered": [
        {"label": "Auto-detect sections", "command": "katz paper auto-chunk"},
        {"label": "Verify sections", "command": "katz paper sections"},
    ],
    "chunked": [
        {"label": "Init spotter catalog", "command": "katz spotter init-catalog"},
        {"label": "List available spotters", "command": "katz spotter catalog"},
        {"label": "Enable a spotter", "command": "katz spotter enable overclaiming"},
        {"label": "Read codegen guide", "command": "katz docs show edsl-codegen"},
    ],
    "spotters_configured": [
        {"label": "List enabled spotters", "command": "katz spotter list"},
        {"label": "Read EDSL codegen guide", "command": "katz docs show edsl-codegen"},
    ],
    "issues_found": [
        {"label": "List draft issues", "command": "katz issue list --state draft"},
        {"label": "Show an issue", "command": "katz issue show <id-prefix>"},
        {
            "label": "Investigate an issue",
            "command": "katz issue investigate --id <id> --verdict confirmed --notes '...'",
        },
        {"label": "Update issue state", "command": "katz issue update --id <id> --state confirmed"},
    ],
    "investigated": [
        {"label": "List confirmed issues", "command": "katz issue list --state confirmed"},
        {"label": "Generate HTML report", "command": "katz report generate --output review.html"},
    ],
}


def phase_state(cwd: Path | None = None) -> dict[str, Any]:
    """Infer review phase from .katz/ artifacts on disk."""
    cwd = cwd or Path.cwd()
    katz_dir = cwd / ".katz"

    if not katz_dir.exists():
        return {
            "phase": "no_katz",
            "project_exists": False,
            "counts": {},
            "checklist": CHECKLISTS["no_katz"],
            "recommended_next_steps": NEXT_STEPS["no_katz"],
        }

    active_file = katz_dir / "ACTIVE_VERSION"
    if not active_file.exists():
        return {
            "phase": "initialized",
            "project_exists": True,
            "katz_dir": str(katz_dir),
            "counts": {},
            "checklist": CHECKLISTS["initialized"],
            "recommended_next_steps": NEXT_STEPS["initialized"],
        }

    commit = active_file.read_text(encoding="utf-8").strip()
    if not commit or len(commit) != 40:
        return {
            "phase": "initialized",
            "project_exists": True,
            "katz_dir": str(katz_dir),
            "counts": {},
            "checklist": CHECKLISTS["initialized"],
            "recommended_next_steps": NEXT_STEPS["initialized"],
        }

    version_dir = katz_dir / "versions" / commit
    paper_map_path = version_dir / "paper_map.jsonl"

    if not paper_map_path.exists():
        return {
            "phase": "initialized",
            "project_exists": True,
            "katz_dir": str(katz_dir),
            "commit": commit,
            "counts": {},
            "checklist": CHECKLISTS["initialized"],
            "recommended_next_steps": NEXT_STEPS["initialized"],
        }

    # Count sections and sentences
    sections: list[str] = []
    sentences = 0
    try:
        for line in paper_map_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("type") == "section":
                sections.append(rec.get("id", "?"))
            elif rec.get("type") == "sentence":
                sentences += 1
    except Exception:
        pass

    if not sections:
        return {
            "phase": "registered",
            "project_exists": True,
            "katz_dir": str(katz_dir),
            "commit": commit,
            "counts": {"sections": 0, "sentences": sentences},
            "checklist": CHECKLISTS["registered"],
            "recommended_next_steps": NEXT_STEPS["registered"],
        }

    # Count enabled spotters
    spotters_dir = version_dir / "spotters"
    spotter_count = (
        len(list(spotters_dir.glob("*.md"))) if spotters_dir.is_dir() else 0
    )

    # Count issues by state
    issues_dir = version_dir / "issues"
    state_counts: dict[str, int] = {}
    if issues_dir.is_dir():
        for issue_dir in issues_dir.iterdir():
            if not issue_dir.is_dir():
                continue
            status_dir = issue_dir / "status"
            if not status_dir.is_dir():
                continue
            files = sorted(status_dir.glob("*.json"))
            if not files:
                continue
            try:
                state_val = json.loads(files[-1].read_text(encoding="utf-8")).get("state", "draft")
                state_counts[state_val] = state_counts.get(state_val, 0) + 1
            except Exception:
                pass

    total_issues = sum(state_counts.values())
    confirmed = state_counts.get("confirmed", 0)
    rejected = state_counts.get("rejected", 0)
    draft = state_counts.get("draft", 0)

    if total_issues == 0:
        phase = "spotters_configured" if spotter_count > 0 else "chunked"
    elif (confirmed + rejected) > 0:
        phase = "investigated"
    else:
        phase = "issues_found"

    counts = {
        "sections": len(sections),
        "sentences": sentences,
        "spotters_enabled": spotter_count,
        "issues_total": total_issues,
        "issues_draft": draft,
        "issues_confirmed": confirmed,
        "issues_rejected": rejected,
    }

    return {
        "phase": phase,
        "project_exists": True,
        "katz_dir": str(katz_dir),
        "commit": commit,
        "counts": counts,
        "checklist": CHECKLISTS.get(phase, []),
        "recommended_next_steps": NEXT_STEPS.get(phase, []),
    }


# ---------------------------------------------------------------------------
# EDSL script codegen
# ---------------------------------------------------------------------------

_SCRIPT_TEMPLATE = '''\
#!/usr/bin/env python3
"""
EDSL issue-finding script.
Generated from katz state: commit {commit_short}

Sections:  {n_sections}
Spotters:  {n_section_spotters} section-scope, {n_holistic_spotters} holistic
Est. calls: {n_total_calls} (2 models × scenarios)

Usage:
    python find_issues.py              # full review
    python find_issues.py --dry-run    # show scenario count
    python find_issues.py --section <id>  # single section only
"""
import argparse
import json
import subprocess
import sys
from textwrap import dedent

from edsl import Model, ModelList, QuestionFreeText, Scenario, ScenarioList

# ── Configuration ────────────────────────────────────────────────────────────
COMMIT = {commit_repr}
MANUSCRIPT = f".katz/versions/{{COMMIT}}/paper/manuscript.md"

SECTIONS = {sections_json}

SPOTTERS = {spotters_json}

MODELS = ModelList([
    Model("claude-opus-4-20250514", service_name="anthropic"),
    Model("gpt-5.4", service_name="openai", reasoning_effort="high"),
])

# ── Prompts ──────────────────────────────────────────────────────────────────
PREAMBLE = """\
Important: ignore PDF conversion artifacts (broken cross-references, missing
numerical values, table formatting issues). Focus only on substantive issues.
"""

SECTION_Q = QuestionFreeText(
    question_name="spotter_result",
    question_text=PREAMBLE + dedent("""\\
        You are reviewing a section of an academic paper for ONE specific type of issue.

        **Spotter**: {{{{ spotter_name }}}}
        {{{{ spotter_instructions }}}}

        **Section "{{{{ section_title }}}}"** (id: {{{{ section_id }}}}):
        {{{{ section_content }}}}

        If you find a genuine, substantive issue, return a JSON object:
          {{"title": "short title", "quoted_text": "exact text from section", "description": "explanation"}}
        If you find NO issue of this type, return exactly: null

        Return ONLY the JSON object or null. No other text.
    """),
)

HOLISTIC_Q = QuestionFreeText(
    question_name="spotter_result",
    question_text=PREAMBLE + dedent("""\\
        You are reviewing an academic paper as a whole for ONE specific type of issue.

        **Spotter**: {{{{ spotter_name }}}}
        {{{{ spotter_instructions }}}}

        **Full manuscript**:
        {{{{ section_content }}}}

        If you find a genuine, substantive issue, return a JSON object:
          {{"title": "short title", "quoted_text": "exact text from paper", "description": "explanation"}}
        If you find NO issue of this type, return exactly: null

        Return ONLY the JSON object or null. No other text.
    """),
)

# ── Helpers ──────────────────────────────────────────────────────────────────
def read_section_text(section):
    lines = open(MANUSCRIPT, encoding="utf-8").read().splitlines()
    return "\\n".join(lines[section["line_start"] - 1 : section["line_end"]])


def parse_issue(answer):
    import ast
    import re as _re
    if not answer or str(answer).strip() in ("null", ""):
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(str(answer).strip())
            if isinstance(parsed, dict) and parsed.get("title"):
                return parsed
        except Exception:
            pass
    m = _re.search(r"\\{{.*\\}}", str(answer), _re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group())
            if isinstance(parsed, dict) and parsed.get("title"):
                return parsed
        except Exception:
            pass
    return None


def file_issue(title, body, quoted, section_id, spotter_name):
    byte_start = byte_end = None
    if quoted:
        for length in (len(quoted), 200, 100, 60):
            if length > len(quoted):
                continue
            try:
                hits = json.loads(subprocess.check_output(
                    ["katz", "paper", "find", quoted[:length]], text=True
                ))
                if hits:
                    byte_start, byte_end = hits[0]["byte_start"], hits[0]["byte_end"]
                    break
            except Exception:
                continue
    if byte_start is None:
        try:
            sec = json.loads(subprocess.check_output(
                ["katz", "paper", "section", section_id], text=True
            ))
            byte_start, byte_end = sec["byte_start"], sec["byte_end"]
        except Exception:
            print(f"  WARNING: could not locate text for issue '{{title}}' — skipping")
            return None
    cmd = [
        "katz", "issue", "write",
        "--title", title[:120],
        "--byte-start", str(byte_start),
        "--byte-end", str(byte_end),
        "--body", body[:2000],
        "--spotter", spotter_name,
    ]
    try:
        return json.loads(subprocess.check_output(cmd, text=True))
    except Exception as exc:
        print(f"  WARNING: katz issue write failed for '{{title}}': {{exc}}")
        return None


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", help="Scan only this section ID")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sections = SECTIONS
    if args.section:
        sections = [s for s in sections if s["id"] == args.section]
        if not sections:
            print(f"Section \'{{args.section}}\' not found", file=sys.stderr)
            sys.exit(1)

    section_spotters = [s for s in SPOTTERS if s.get("scope", "section") == "section"]
    holistic_spotters = [s for s in SPOTTERS if s.get("scope") == "holistic"]
    n_models = len(MODELS)

    n_section_calls = len(sections) * len(section_spotters) * n_models
    n_holistic_calls = len(holistic_spotters) * n_models
    print(f"Sections: {{len(sections)}}, Section spotters: {{len(section_spotters)}}, "
          f"Holistic spotters: {{len(holistic_spotters)}}, Models: {{n_models}}")
    print(f"Total calls: {{n_section_calls + n_holistic_calls}}")

    if args.dry_run:
        return

    section_texts = {{s["id"]: read_section_text(s) for s in sections}}
    total_filed = 0

    # Section-scope spotters in batches of 3 sections
    BATCH = 3
    n_batches = (len(sections) + BATCH - 1) // BATCH
    for batch_idx in range(0, len(sections), BATCH):
        batch = sections[batch_idx : batch_idx + BATCH]
        batch_num = batch_idx // BATCH + 1
        if not section_spotters:
            break
        scenarios = ScenarioList([
            Scenario({{
                "section_content": section_texts[s["id"]],
                "section_id": s["id"],
                "section_title": s["title"],
                "spotter_name": sp["name"],
                "spotter_instructions": sp["content"],
            }})
            for s in batch
            for sp in section_spotters
        ])
        n_calls = len(scenarios) * n_models
        print(f"[Batch {{batch_num}}/{{n_batches}}] {{', '.join(s['id'] for s in batch)}} ({{n_calls}} calls)...")
        results = SECTION_Q.by(scenarios).by(MODELS).run()
        for r in results:
            issue = parse_issue(r["answer"]["spotter_result"])
            if not issue:
                continue
            sp_name = r["scenario"]["spotter_name"]
            model_name = r["model"]._model_
            body = f"[{{sp_name}}] [{{model_name}}] {{issue.get(\'description\', \'\')}}"
            filed = file_issue(issue["title"], body, issue.get("quoted_text"), r["scenario"]["section_id"], sp_name)
            if filed:
                total_filed += 1
                print(f"  Filed: {{issue[\'title\'][:60]}}")

    # Holistic-scope spotters on full manuscript
    if holistic_spotters:
        full_text = open(MANUSCRIPT, encoding="utf-8").read()
        scenarios = ScenarioList([
            Scenario({{
                "section_content": full_text,
                "section_id": "full-manuscript",
                "section_title": "Full Manuscript",
                "spotter_name": sp["name"],
                "spotter_instructions": sp["content"],
            }})
            for sp in holistic_spotters
        ])
        n_calls = len(scenarios) * n_models
        print(f"[Holistic] full manuscript ({{n_calls}} calls)...")
        results = HOLISTIC_Q.by(scenarios).by(MODELS).run()
        for r in results:
            issue = parse_issue(r["answer"]["spotter_result"])
            if not issue:
                continue
            sp_name = r["scenario"]["spotter_name"]
            model_name = r["model"]._model_
            body = f"[{{sp_name}}] [{{model_name}}] {{issue.get(\'description\', \'\')}}"
            filed = file_issue(issue["title"], body, issue.get("quoted_text"), "full-manuscript", sp_name)
            if filed:
                total_filed += 1
                print(f"  Filed: {{issue[\'title\'][:60]}}")

    print(f"\\nDone: {{total_filed}} issues filed to katz")


if __name__ == "__main__":
    main()
'''


def generate_edsl_script(commit: str, sections: list[dict], spotters: list[dict]) -> str:
    """Generate a complete EDSL find-issues Python script populated with katz state."""
    section_spotters = [s for s in spotters if s.get("scope", "section") == "section"]
    holistic_spotters = [s for s in spotters if s.get("scope") == "holistic"]
    n_section_calls = len(sections) * len(section_spotters) * 2
    n_holistic_calls = len(holistic_spotters) * 2

    sections_json = json.dumps(
        [{"id": s["id"], "title": s["title"], "line_start": s["line_start"], "line_end": s["line_end"]}
         for s in sections],
        indent=2,
    )
    spotters_json = json.dumps(
        [{"name": s["name"], "scope": s.get("scope", "section"), "content": s.get("content", "")}
         for s in spotters],
        indent=2,
    )

    return _SCRIPT_TEMPLATE.format(
        commit_short=commit[:8],
        commit_repr=repr(commit),
        n_sections=len(sections),
        n_section_spotters=len(section_spotters),
        n_holistic_spotters=len(holistic_spotters),
        n_total_calls=n_section_calls + n_holistic_calls,
        sections_json=sections_json,
        spotters_json=spotters_json,
    )


# ---------------------------------------------------------------------------
# Agent brief
# ---------------------------------------------------------------------------

AGENT_BRIEF = """\
## Katz Operating Rules

**Tool:** `katz` — a version-aware ledger for academic paper review artifacts.

**Repository requirement:** katz requires a git repository. All state lives in `.katz/` at
the repo root (found by walking up from the current directory).

**FIRST STEP:** Run `katz agent-start` to check the current review phase. Read the `phase`
and `checklist` fields in the response before taking any other action. Never assume what
phase the review is in without checking.

---

### Phase Sequence

| Phase | What it means | Key action |
|---|---|---|
| `no_katz` | No `.katz/` directory | `katz init` |
| `initialized` | katz init'd but no paper registered | `katz paper register --canonical <file>` |
| `registered` | Paper registered, no sections yet | `katz paper auto-chunk` |
| `chunked` | Sections exist, no spotters enabled | `katz spotter init-catalog` + enable |
| `spotters_configured` | Spotters ready, no issues yet | **Generate EDSL script** (see below) |
| `issues_found` | Issues drafted, not yet investigated | Investigate each issue |
| `investigated` | Issues confirmed/rejected | `katz report generate` |

---

### CRITICAL — EDSL Codegen, Not Direct Execution

When it's time to find issues (phase `spotters_configured`), **do NOT run
`edsl_find_issues.py` directly**. Instead, generate a custom Python script:

1. Read sections: `katz paper sections`
2. Read spotters and their content: `katz spotter list` then `katz spotter show <name>` for each
3. Read the template: `katz docs show edsl-codegen`
4. Generate a Python file from the template, substituting sections and spotters
5. Show the script to the user and ask them to review and run it
6. After the user runs it: `katz issue list --state draft`

The `agent-start` payload already includes a `codegen.script` field with a ready-to-run
generated script when the phase is `spotters_configured`.

---

### Issue Investigation

Draft issues have a high false-positive rate — expect ~5–10% to be genuine.
For each draft issue:

```bash
katz issue show <id-prefix>          # read the full record + location text
katz issue investigate --id <id> --verdict confirmed --notes "reason"
katz issue update --id <id> --state confirmed    # or rejected
```

ID resolution: issue IDs are 32-char hex. Unambiguous prefixes (≥6 chars) work.
Valid states: `draft`, `open`, `confirmed`, `rejected`, `wontfix`, `resolved`
Valid verdicts: `confirmed`, `rejected`, `uncertain`

---

### Error Recovery

| Error code | Meaning | Fix |
|---|---|---|
| `not_initialized` | `.katz/` not found | `katz init` |
| `invalid_commit` | No active version | `katz paper register --canonical <file>` |
| `not_found` (spotter) | Spotter not in catalog | `katz spotter init-catalog` first |
| `validation_error` on register | Non-UTF-8 or bad file | Check manuscript encoding |

---

### Output Format

All katz commands emit JSON. Errors use `{"error": "...", "code": "...", "details": {...}}`.
Parse `code` for structured error recovery.

### Useful Documentation

```bash
katz docs list                        # all topics
katz docs show overview               # concepts and storage layout
katz docs show getting-started        # step-by-step walkthrough
katz docs show edsl-codegen           # EDSL script generation guide
katz docs show cli-reference          # all commands
```
"""
