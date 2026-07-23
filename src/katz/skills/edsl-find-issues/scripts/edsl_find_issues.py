#!/usr/bin/env python3
"""Find issues in a katz-registered manuscript using EDSL parallel review.

Runs the cross-product of (section × spotter × model) via EDSL with two
frontier models (Claude Opus, GPT-5.4) by default, all with thinking/reasoning
maxed out. Files each discovered issue with `katz issue write`.

Usage:
    python edsl_find_issues.py                        # all sections
    python edsl_find_issues.py --section introduction  # one section
    python edsl_find_issues.py --spotters-dir ./spotters  # custom spotters
    python edsl_find_issues.py --models 3             # use 3 models (adds Gemini)
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from edsl import Model, ModelList, QuestionFreeText, Scenario, ScenarioList

PREAMBLE = """\
**Important context**: This text was automatically converted from a PDF to markdown.
As a result, you should IGNORE the following artifacts — they are NOT real issues:
- Missing numerical values (e.g., blank percentages) — these are LaTeX macros
  that were stripped during conversion
- Broken cross-references or citation formatting — pandoc/PDF conversion artifacts
- Page number references like (page-22-0) or anchor tags — conversion artifacts
- Standard mathematical notation used without explicit definition
- Table formatting issues — conversion artifacts

Focus ONLY on substantive issues in the dimension you are asked about.
Do NOT flag formatting, undefined standard notation, or conversion artifacts.
"""

BUILTIN_SPOTTERS = [
    {
        "name": "logical_gaps",
        "content": dedent("""\
            # Logical Gaps

            Look for places where the argument skips a step or a claim does not
            follow from the premises.

            Pay special attention to:
            - Claims presented as following from evidence that doesn't actually support them
            - Missing intermediate steps in an argument chain
            - Unstated assumptions required for a conclusion to hold
            - Non-sequiturs where the topic shifts without logical connection
        """),
    },
    {
        "name": "overclaiming",
        "content": dedent("""\
            # Overclaiming

            Look for conclusions or claims that are stronger than the evidence supports.

            Pay special attention to:
            - Causal language when only correlational evidence is presented
            - Generalizations beyond the study's population or setting
            - Results described as "significant" or "large" without adequate justification
            - Abstract or conclusion claims not backed by the results section
            - Cherry-picking favorable results while downplaying unfavorable ones
        """),
    },
    {
        "name": "internal_contradictions",
        "content": dedent("""\
            # Internal Contradictions

            Look for statements within the text that contradict each other.

            Pay special attention to:
            - Numbers or statistics that differ between the text, tables, and figures
            - Claims in one section that conflict with claims in another
            - Notation used inconsistently (same symbol for different things,
              or different symbols for the same thing)
            - Assumptions stated in the model that are violated in the empirical work
        """),
    },
    {
        "name": "unclear_writing",
        "content": dedent("""\
            # Unclear Writing

            Look for sentences or passages that are difficult to understand.

            Pay special attention to:
            - Ambiguous pronoun references (unclear antecedents)
            - Sentences that are too long or convoluted to parse on first reading
            - Technical terms used without definition
            - Missing context that a reader would need to follow the argument
            - Vague quantifiers ("some", "many", "often") where precision is needed
        """),
    },
    {
        "name": "methodology_errors",
        "content": dedent("""\
            # Methodology Errors

            Look for problems with the research design or statistical analysis.

            Pay special attention to:
            - Sampling or selection issues that could bias results
            - Missing controls or confounders
            - Inappropriate statistical tests for the data type
            - Insufficient sample sizes or power for claimed effects
            - Circular reasoning where the outcome is built into the design
        """),
    },
]


ALL_MODELS = [
    lambda: Model(
        "claude-opus-4-20250514",
        service_name="anthropic",
        # thinking param breaks EDSL remote runner; Opus reasons well without it
    ),
    lambda: Model(
        "gpt-5.4",
        service_name="openai",
        reasoning_effort="high",
    ),
    lambda: Model(
        "gemini-3.1-pro-preview",
        service_name="google",
        thinking_budget=10000,
    ),
]


def build_models(n_models=2):
    """Build ModelList with n frontier models, thinking/reasoning maxed out.

    Default is 2 (Claude Opus + GPT-5.4). Pass 3 to add Gemini.
    """
    return ModelList([factory() for factory in ALL_MODELS[:n_models]])


def run_katz(*args):
    result = subprocess.run(["katz", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def load_sections_from_katz():
    """Get all section records from paper_map.jsonl."""
    status = run_katz("paper", "status")
    commit = status["commit"]
    paper_map = Path(f".katz/versions/{commit}/paper_map.jsonl")
    sections = []
    with open(paper_map) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") == "section":
                sections.append(rec)
    return sections, status


def read_section_text(commit, section):
    """Read the manuscript text for a given section's line range."""
    manuscript = Path(f".katz/versions/{commit}/paper/manuscript.md")
    lines = manuscript.read_text(encoding="utf-8").splitlines()
    start = section["line_start"] - 1  # 1-indexed to 0-indexed
    end = section["line_end"]
    return "\n".join(lines[start:end])


def _parse_spotter_scope(content):
    """Extract the scope field from a spotter's YAML frontmatter.

    Returns 'section' or 'holistic'.  Defaults to 'section'.
    """
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            import yaml
            try:
                fm = yaml.safe_load(content[4:end]) or {}
                return fm.get("scope", "section")
            except Exception:
                pass
    return "section"


def load_spotters(spotters_dir):
    """Load issue spotter definitions from markdown files.

    Each spotter dict has 'name', 'content', and 'scope' keys.
    """
    spotters = []
    for f in sorted(Path(spotters_dir).glob("*.md")):
        content = f.read_text(encoding="utf-8")
        spotters.append({
            "name": f.stem,
            "content": content,
            "scope": _parse_spotter_scope(content),
        })
    return spotters


def parse_issue_json(text):
    """Parse an issue JSON object from LLM output. Returns dict or None.

    EDSL often returns Python-repr strings (single quotes) rather than JSON,
    so we try ast.literal_eval as a fallback after json.loads.
    """
    if isinstance(text, dict):
        return text if text.get("title") else None
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.lower() == "null" or stripped == "":
        return None

    # Try the full string, then try extracting a {...} substring
    candidates = [stripped]
    obj_match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if obj_match and obj_match.group() != stripped:
        candidates.append(obj_match.group())

    for candidate in candidates:
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(candidate)
                if isinstance(parsed, dict) and parsed.get("title"):
                    return parsed
                if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                    return parsed[0] if parsed[0].get("title") else None
            except (json.JSONDecodeError, ValueError, SyntaxError):
                continue
    return None


def file_issue(title, body, quoted_text, section_id, spotter_name=None):
    """Use katz paper find + katz issue write to file an issue."""
    byte_start, byte_end = None, None

    # Try to locate the quoted text in the manuscript
    if quoted_text:
        snippet = quoted_text.strip()
        # Try progressively shorter prefixes for fuzzy matching
        for length in (len(snippet), 200, 100, 60):
            if length > len(snippet):
                continue
            try:
                results = run_katz("paper", "find", snippet[:length])
                if results:
                    byte_start = results[0]["byte_start"]
                    byte_end = results[0]["byte_end"]
                    break
            except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
                continue

    # Fallback: use the full section range (not just first 100 bytes)
    if byte_start is None:
        try:
            sec_info = run_katz("paper", "section", section_id)
            byte_start = sec_info["byte_start"]
            byte_end = sec_info["byte_end"]
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            print(f"  WARNING: Could not locate text for issue '{title}' — skipping")
            return None

    cmd = [
        "katz", "issue", "write",
        "--title", title[:120],
        "--byte-start", str(byte_start),
        "--byte-end", str(byte_end),
        "--body", body[:2000],
    ]
    if spotter_name:
        cmd.extend(["--spotter", spotter_name])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # If spotter not registered in katz, retry without --spotter
        if spotter_name and "not registered" in result.stderr:
            cmd = [c for c in cmd if c != "--spotter" and c != spotter_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  WARNING: katz issue write failed for '{title}': {result.stderr.strip()}")
            return None
    return json.loads(result.stdout)


def results_to_issues(results):
    """Iterate over EDSL Results, parse issues, and file them with katz.

    Each result row contains:
      - result["answer"]["spotter_result"]: the LLM's JSON or null response
      - result["scenario"]["section_id"]: which section was reviewed
      - result["scenario"]["spotter_name"]: which spotter was applied
      - result["model"]._model_: which model produced the result
    """
    issues_found = 0
    issues_filed = 0

    for result in results:
        answer = result["answer"]["spotter_result"]
        section_id = result["scenario"]["section_id"]
        spotter_name = result["scenario"]["spotter_name"]
        model_name = result["model"]._model_

        issue_data = parse_issue_json(answer)
        if not issue_data:
            continue

        issues_found += 1
        title = issue_data.get("title", "Untitled issue")
        quoted = issue_data.get("quoted_text", "")
        desc = issue_data.get("description", "")
        body = f"[{spotter_name}] [{model_name}] {desc}"

        filed = file_issue(title, body, quoted, section_id, spotter_name=spotter_name)
        if filed:
            issues_filed += 1
            print(f"  [{section_id}] [{model_name}] {title}")

    return issues_found, issues_filed


def _title_words(title):
    """Normalize a title to a set of lowercase content words."""
    stop = {"a", "an", "the", "is", "are", "in", "of", "for", "and", "or", "to", "with", "from", "on", "by", "not", "no"}
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", title.lower()).split() if w not in stop and len(w) > 2}


def dedup_issues():
    """Mark near-duplicate draft issues as wontfix.

    Two issues are duplicates if they share overlapping byte ranges and
    have >50% word overlap in their titles. Keeps the first-filed issue.
    """
    try:
        issues = run_katz("issue", "list", "--state", "draft")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return 0

    # Get full records to access byte ranges
    full_issues = []
    for iss in issues:
        try:
            full = run_katz("issue", "show", iss["id"])
            full_issues.append(full)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue

    seen = []  # (byte_start, byte_end, title_words, id)
    deduped = 0
    for iss in sorted(full_issues, key=lambda i: i.get("created_at", "")):
        loc = iss.get("location", {})
        bs = loc.get("byte_start", -1)
        be = loc.get("byte_end", -1)
        tw = _title_words(iss.get("title", ""))

        is_dup = False
        for s_bs, s_be, s_tw, _ in seen:
            # Check byte range overlap
            overlap = max(0, min(be, s_be) - max(bs, s_bs))
            span = max(1, min(be - bs, s_be - s_bs))
            if overlap / span < 0.5:
                continue
            # Check title similarity
            if not tw or not s_tw:
                continue
            jaccard = len(tw & s_tw) / len(tw | s_tw)
            if jaccard > 0.5:
                is_dup = True
                break

        if is_dup:
            try:
                subprocess.run(
                    ["katz", "issue", "update", "--id", iss["id"],
                     "--state", "wontfix", "--reason", "Duplicate of earlier issue"],
                    capture_output=True, text=True, check=True,
                )
                deduped += 1
            except subprocess.CalledProcessError:
                pass
        else:
            seen.append((bs, be, tw, iss["id"]))

    return deduped


def main():
    parser = argparse.ArgumentParser(description="EDSL-parallel issue finder for katz")
    parser.add_argument("--section", help="Scan only this section ID")
    parser.add_argument("--spotters-dir", help="Directory of .md spotter files")
    parser.add_argument("--builtin-spotters", action="store_true",
                        help="Use built-in spotters instead of katz-enabled ones")
    parser.add_argument("--models", type=int, default=2,
                        help="Number of models to use (default: 2; max: 3)")
    parser.add_argument("--dry-run", action="store_true", help="Print scenarios without running")
    args = parser.parse_args()

    # Load paper and sections
    all_sections, status = load_sections_from_katz()
    commit = status["commit"]

    skip_sections = {"references", "title-and-abstract"}
    if args.section:
        sections = [s for s in all_sections if s["id"] == args.section]
        if not sections:
            print(f"Error: section '{args.section}' not found", file=sys.stderr)
            sys.exit(1)
    else:
        sections = [s for s in all_sections if s["id"] not in skip_sections]

    # Load spotters: explicit dir > katz-enabled > built-in
    if args.spotters_dir:
        spotters = load_spotters(args.spotters_dir)
    elif args.builtin_spotters:
        spotters = BUILTIN_SPOTTERS
    else:
        # Default: use katz-enabled spotters for the current version
        version_spotters_dir = Path(f".katz/versions/{commit}/spotters")
        if version_spotters_dir.is_dir() and any(version_spotters_dir.glob("*.md")):
            spotters = load_spotters(version_spotters_dir)
            print(f"Using {len(spotters)} katz-enabled spotters from {version_spotters_dir}")
        else:
            spotters = BUILTIN_SPOTTERS
            print("No katz-enabled spotters found; using 5 built-in spotters")

    # Separate section-scope and holistic-scope spotters.
    # Section spotters run per-section; holistic spotters run once on the full manuscript.
    section_spotters = [s for s in spotters if s.get("scope", "section") == "section"]
    holistic_spotters = [s for s in spotters if s.get("scope", "section") == "holistic"]

    # Read section text
    section_texts = {}
    for sec in sections:
        section_texts[sec["id"]] = read_section_text(commit, sec)

    # Build full manuscript text for holistic spotters
    full_manuscript = None
    if holistic_spotters:
        manuscript_path = Path(f".katz/versions/{commit}/paper/manuscript.md")
        full_manuscript = manuscript_path.read_text(encoding="utf-8")

    models = build_models(n_models=min(args.models, len(ALL_MODELS)))
    n_models = len(models)
    n_section_calls = len(sections) * len(section_spotters) * n_models
    n_holistic_calls = len(holistic_spotters) * n_models
    n_total_calls = n_section_calls + n_holistic_calls

    print(f"Paper: {status['source_root']} @ {commit[:8]}")
    print(f"Sections: {len(sections)}, Section spotters: {len(section_spotters)}, Holistic spotters: {len(holistic_spotters)}, Models: {n_models}")
    print(f"Section calls: {len(sections)} × {len(section_spotters)} × {n_models} = {n_section_calls}")
    if holistic_spotters:
        print(f"Holistic calls: {len(holistic_spotters)} × {n_models} = {n_holistic_calls}")
    print(f"Total calls: {n_total_calls}")
    for m in models:
        print(f"  - {m._model_}")
    print()

    if args.dry_run:
        print(f"Would run {n_total_calls} total calls.")
        return

    q_section = QuestionFreeText(
        question_name="spotter_result",
        question_text=PREAMBLE + dedent("""\
            You are reviewing a section of an academic paper. Your task is to look
            for ONE specific type of issue, described below.

            **Issue spotter instructions**:
            {{ spotter_instructions }}

            **Section "{{ section_title }}"** (id: {{ section_id }}):
            {{ section_content }}

            Apply the issue spotter instructions to this section.
            If you find a genuine, substantive issue, return a JSON object:
              {"title": "short title", "quoted_text": "exact text from section", "description": "explanation"}
            If you find NO issue of this type, return exactly: null

            Return ONLY the JSON object or null. No other text.
        """),
    )

    q_holistic = QuestionFreeText(
        question_name="spotter_result",
        question_text=PREAMBLE + dedent("""\
            You are reviewing an academic paper as a whole. Your task is to look
            for ONE specific type of issue, described below. This requires reading
            across multiple sections.

            **Issue spotter instructions**:
            {{ spotter_instructions }}

            **Full manuscript**:
            {{ section_content }}

            Apply the issue spotter instructions to the manuscript as a whole.
            If you find a genuine, substantive issue, return a JSON object:
              {"title": "short title", "quoted_text": "exact text from paper", "description": "explanation"}
            If you find NO issue of this type, return exactly: null

            Return ONLY the JSON object or null. No other text.
        """),
    )

    # --- Run section-scope spotters in batches ---
    BATCH_SIZE = 3
    total_found = 0
    total_filed = 0

    if section_spotters:
        n_batches = (len(sections) + BATCH_SIZE - 1) // BATCH_SIZE
        for batch_start in range(0, len(sections), BATCH_SIZE):
            batch = sections[batch_start : batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            batch_names = ", ".join(s["id"] for s in batch)

            scenarios = ScenarioList([
                Scenario({
                    "section_content": section_texts[sec["id"]],
                    "section_id": sec["id"],
                    "section_title": sec["title"],
                    "spotter_name": spotter["name"],
                    "spotter_instructions": spotter["content"],
                })
                for sec in batch
                for spotter in section_spotters
            ])

            n_calls = len(scenarios) * n_models
            print(f"[Batch {batch_num}/{n_batches}] {batch_names} ({n_calls} calls)...")
            results = q_section.by(scenarios).by(models).run()
            found, filed = results_to_issues(results)
            total_found += found
            total_filed += filed

    # --- Run holistic spotters once on the full manuscript ---
    if holistic_spotters and full_manuscript:
        # Use "full-manuscript" as a virtual section ID for holistic spotters.
        # file_issue will fall back to the full paper range if text lookup fails.
        holistic_scenarios = ScenarioList([
            Scenario({
                "section_content": full_manuscript,
                "section_id": "full-manuscript",
                "section_title": "Full Manuscript",
                "spotter_name": spotter["name"],
                "spotter_instructions": spotter["content"],
            })
            for spotter in holistic_spotters
        ])

        n_calls = len(holistic_scenarios) * n_models
        print(f"[Holistic] full manuscript ({n_calls} calls)...")
        results = q_holistic.by(holistic_scenarios).by(models).run()
        found, filed = results_to_issues(results)
        total_found += found
        total_filed += filed

    # Deduplicate issues that overlap in byte range and have similar titles
    if total_filed > 0:
        deduped = dedup_issues()
        if deduped:
            print(f"Deduplicated: merged {deduped} near-duplicate issues")

    print(f"\nDone: {total_found} issues found, {total_filed} filed to katz")


if __name__ == "__main__":
    main()
