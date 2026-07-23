# EDSL Code Generation

Rather than running a pre-built script, katz agents **generate a Python script**
tailored to the current paper and show it to the user before running.

This approach keeps the user in control, makes the review reproducible, and
produces a script the user can customize (change models, add prompting, adjust scope).

---

## Why Codegen?

- **Transparency:** The user sees exactly what will run before any LLM calls are made
- **Reproducibility:** The generated script captures the exact sections and spotters used
- **Customization:** Users can edit models, batch size, prompt text, or scope before running
- **Safety:** No surprise costs — `--dry-run` shows the call count before committing

---

## The Fast Path: `agent-start`

When the review phase is `spotters_configured`, `katz status` includes a
`codegen.script` field with a complete, ready-to-run Python script:

```bash
katz status
```

Extract the script from `codegen.script`, save it, and show it to the user:

```python
import json, subprocess
payload = json.loads(subprocess.check_output(["katz", "agent-start"]))
if payload.get("codegen", {}).get("available"):
    script = payload["codegen"]["script"]
    with open("find_issues.py", "w") as f:
        f.write(script)
    print("Script saved as find_issues.py")
    print(f"Sections: {len(payload['codegen']['sections'])}")
    print(f"Spotters: {len(payload['codegen']['spotters'])}")
```

---

## Building the Script Manually

If you need to regenerate or customize the script, read the state directly:

**1. Read sections:**
```bash
katz paper sections
# → [{"id": "introduction", "title": "Introduction", "line_start": 1, "line_end": 45}, ...]
```

**2. Read spotters with content:**
```bash
katz spotter list
# → [{"name": "overclaiming", "scope": "section", ...}, ...]

katz spotter show overclaiming
# → {"name": "overclaiming", "scope": "section", "content": "---\nscope: section\n---\n# Overclaiming\n..."}
```

**3. Use the template below**, substituting the sections and spotters.

---

## Script Template

```python
#!/usr/bin/env python3
"""
EDSL issue-finding script.
Sections: <N>   Spotters: <N> section-scope, <N> holistic
"""
import argparse, json, subprocess, sys
from textwrap import dedent
from edsl import Model, ModelList, QuestionFreeText, Scenario, ScenarioList

# ── Configuration ─────────────────────────────────────────────────────────────
COMMIT = "<40-char-commit-sha>"
MANUSCRIPT = f".katz/versions/{COMMIT}/paper/manuscript.md"

# Populate from: katz paper sections
SECTIONS = [
    {"id": "introduction", "title": "Introduction", "line_start": 1, "line_end": 45},
    {"id": "data",         "title": "Data",         "line_start": 46, "line_end": 102},
    # ...
]

# Populate from: katz spotter list + katz spotter show <name>
SPOTTERS = [
    {"name": "overclaiming",   "scope": "section",  "content": "# Overclaiming\n\nLook for..."},
    {"name": "logical_gaps",   "scope": "section",  "content": "# Logical Gaps\n\nLook for..."},
    {"name": "intro_flow",     "scope": "holistic", "content": "# Introduction Flow\n\nCheck..."},
]

MODELS = ModelList([
    Model("claude-opus-4-20250514", service_name="anthropic"),
    Model("gpt-5.4", service_name="openai", reasoning_effort="high"),
])

# ── Prompts ────────────────────────────────────────────────────────────────────
PREAMBLE = """\
Important: ignore PDF conversion artifacts (broken cross-references, missing numbers,
table formatting). Focus only on substantive issues.
"""

SECTION_Q = QuestionFreeText(
    question_name="spotter_result",
    question_text=PREAMBLE + dedent("""\
        You are reviewing a section of an academic paper for ONE specific type of issue.

        **Spotter**: {{ spotter_name }}
        {{ spotter_instructions }}

        **Section "{{ section_title }}"** (id: {{ section_id }}):
        {{ section_content }}

        If you find a genuine, substantive issue, return JSON:
          {"title": "short title", "quoted_text": "exact text", "description": "explanation"}
        If no issue found, return exactly: null
    """),
)

HOLISTIC_Q = QuestionFreeText(
    question_name="spotter_result",
    question_text=PREAMBLE + dedent("""\
        You are reviewing an academic paper as a whole for ONE specific type of issue.

        **Spotter**: {{ spotter_name }}
        {{ spotter_instructions }}

        **Full manuscript**:
        {{ section_content }}

        If you find a genuine issue, return JSON:
          {"title": "short title", "quoted_text": "exact text", "description": "explanation"}
        If no issue found, return exactly: null
    """),
)

# ── Helpers ────────────────────────────────────────────────────────────────────
def read_section_text(section):
    lines = open(MANUSCRIPT, encoding="utf-8").read().splitlines()
    return "\n".join(lines[section["line_start"] - 1 : section["line_end"]])

def parse_issue(answer):
    import ast, re
    if not answer or str(answer).strip() in ("null", ""):
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(str(answer).strip())
            if isinstance(parsed, dict) and parsed.get("title"):
                return parsed
        except Exception:
            pass
    m = re.search(r"\{.*\}", str(answer), re.DOTALL)
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
            print(f"  WARNING: could not locate '{title}' — skipping")
            return None
    try:
        return json.loads(subprocess.check_output([
            "katz", "issue", "write",
            "--title", title[:120],
            "--byte-start", str(byte_start),
            "--byte-end", str(byte_end),
            "--body", body[:2000],
            "--spotter", spotter_name,
        ], text=True))
    except Exception as exc:
        print(f"  WARNING: issue write failed for '{title}': {exc}")
        return None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", help="Scan only this section ID")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sections = SECTIONS
    if args.section:
        sections = [s for s in sections if s["id"] == args.section]
        if not sections:
            print(f"Section '{args.section}' not found", file=sys.stderr)
            sys.exit(1)

    section_spotters = [s for s in SPOTTERS if s.get("scope", "section") == "section"]
    holistic_spotters = [s for s in SPOTTERS if s.get("scope") == "holistic"]
    n_models = len(MODELS)

    print(f"Sections: {len(sections)}, Section spotters: {len(section_spotters)}, "
          f"Holistic spotters: {len(holistic_spotters)}, Models: {n_models}")
    print(f"Total calls: {len(sections)*len(section_spotters)*n_models + len(holistic_spotters)*n_models}")

    if args.dry_run:
        return

    section_texts = {s["id"]: read_section_text(s) for s in sections}
    total_filed = 0

    # Section-scope spotters in batches of 3 sections
    for batch_start in range(0, len(sections), 3):
        batch = sections[batch_start : batch_start + 3]
        if not section_spotters:
            break
        scenarios = ScenarioList([
            Scenario({
                "section_content": section_texts[s["id"]],
                "section_id": s["id"],
                "section_title": s["title"],
                "spotter_name": sp["name"],
                "spotter_instructions": sp["content"],
            })
            for s in batch for sp in section_spotters
        ])
        results = SECTION_Q.by(scenarios).by(MODELS).run()
        for r in results:
            issue = parse_issue(r["answer"]["spotter_result"])
            if not issue:
                continue
            sp_name = r["scenario"]["spotter_name"]
            model_name = r["model"]._model_
            body = f"[{sp_name}] [{model_name}] {issue.get('description', '')}"
            if file_issue(issue["title"], body, issue.get("quoted_text"),
                          r["scenario"]["section_id"], sp_name):
                total_filed += 1
                print(f"  Filed: {issue['title'][:60]}")

    # Holistic-scope spotters on full manuscript
    if holistic_spotters:
        full_text = open(MANUSCRIPT, encoding="utf-8").read()
        scenarios = ScenarioList([
            Scenario({
                "section_content": full_text,
                "section_id": "full-manuscript",
                "section_title": "Full Manuscript",
                "spotter_name": sp["name"],
                "spotter_instructions": sp["content"],
            })
            for sp in holistic_spotters
        ])
        results = HOLISTIC_Q.by(scenarios).by(MODELS).run()
        for r in results:
            issue = parse_issue(r["answer"]["spotter_result"])
            if not issue:
                continue
            sp_name = r["scenario"]["spotter_name"]
            model_name = r["model"]._model_
            body = f"[{sp_name}] [{model_name}] {issue.get('description', '')}"
            if file_issue(issue["title"], body, issue.get("quoted_text"),
                          "full-manuscript", sp_name):
                total_filed += 1
                print(f"  Filed: {issue['title'][:60]}")

    print(f"\nDone: {total_filed} issues filed to katz")

if __name__ == "__main__":
    main()
```

---

## Customization Options

**Change models:**
```python
MODELS = ModelList([
    Model("claude-opus-4-20250514", service_name="anthropic"),
    Model("gpt-5.4", service_name="openai", reasoning_effort="high"),
    Model("gemini-3.1-pro-preview", service_name="google", thinking_budget=10000),
])
```

**Focus on one section type:**
```bash
python find_issues.py --section results
python find_issues.py --section introduction
```

**Exclude sections from the sweep** (edit `SECTIONS` to remove):
```python
SKIP = {"references", "appendix", "acknowledgments"}
SECTIONS = [s for s in SECTIONS if s["id"] not in SKIP]
```

**Adjust the prompt preamble** for a specific paper's conversion artifacts:
```python
PREAMBLE = """\
Note: This paper uses [specific notation]. LaTeX macros starting with \\\\cmd
are intentional shorthand, not errors.
"""
```

---

## After Running the Script

```bash
katz issue list --state draft         # see all filed issues
katz issue list --state draft --section results   # filter by section
katz issue list --state draft --spotter overclaiming  # filter by spotter
```

Proceed to investigation: `katz docs show getting-started` Step 6.
