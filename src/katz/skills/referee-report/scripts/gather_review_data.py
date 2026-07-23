#!/usr/bin/env python3
"""Gather structured review data from katz for referee report synthesis.

Reads paper metadata, all issues (with investigations), and section info.
Outputs a single JSON file (.katz/review_data.json) that an agent can use
to write a narrative referee report.
"""

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def run_katz(*args):
    result = subprocess.run(
        ["katz", *args], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def load_sections(commit):
    path = Path(f".katz/versions/{commit}/paper_map.jsonl")
    sections = []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") == "section":
                sections.append(rec)
    return sections


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else ".katz/review_data.json"

    status = run_katz("paper", "status")
    commit = status["commit"]
    sections = load_sections(commit)

    # Load manuscript for title extraction
    manuscript_path = Path(f".katz/versions/{commit}/paper/manuscript.md")
    manuscript_lines = manuscript_path.read_text(encoding="utf-8").splitlines()
    paper_title = ""
    for line in manuscript_lines[:5]:
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            # Strip span tags
            import re
            paper_title = re.sub(r"<[^>]+>", "", stripped).strip()
            break

    # Get all issues (all states)
    all_issues = run_katz("issue", "list")

    # Get full details for each issue
    full_issues = []
    for summary in all_issues:
        try:
            full = run_katz("issue", "show", summary["id"])
            # Carry section from summary if not in full
            if "section" not in full.get("location", {}):
                full.setdefault("location", {})["section"] = (
                    summary.get("location", {}).get("section")
                )
            full_issues.append(full)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue

    # Count by state
    state_counts = defaultdict(int)
    for iss in full_issues:
        state_counts[iss.get("state", "unknown")] += 1

    # Group confirmed/open issues by section
    confirmed = [i for i in full_issues if i.get("state") == "confirmed"]
    open_issues = [i for i in full_issues if i.get("state") == "open"]
    rejected = [i for i in full_issues if i.get("state") == "rejected"]

    # Build section summaries
    section_summaries = []
    issues_by_section = defaultdict(lambda: {"confirmed": [], "open": [], "rejected": 0})
    for iss in full_issues:
        sec = iss.get("location", {}).get("section", "unknown")
        if iss["state"] == "confirmed":
            issues_by_section[sec]["confirmed"].append(iss["title"])
        elif iss["state"] == "open":
            issues_by_section[sec]["open"].append(iss["title"])
        elif iss["state"] == "rejected":
            issues_by_section[sec]["rejected"] += 1

    for sec in sections:
        sid = sec["id"]
        sec_data = issues_by_section.get(sid, {"confirmed": [], "open": [], "rejected": 0})
        section_summaries.append({
            "id": sid,
            "title": sec["title"],
            "line_start": sec["line_start"],
            "line_end": sec["line_end"],
            "confirmed_count": len(sec_data["confirmed"]),
            "open_count": len(sec_data["open"]),
            "rejected_count": sec_data["rejected"],
            "confirmed_titles": sec_data["confirmed"],
        })

    # Simplify confirmed issues for the report
    confirmed_details = []
    for iss in confirmed:
        loc = iss.get("location", {})
        investigations = iss.get("investigations", [])
        latest_inv = investigations[-1] if investigations else {}
        confirmed_details.append({
            "id": iss["id"][:12],
            "title": iss["title"],
            "section": loc.get("section", "unknown"),
            "line_start": loc.get("line_start"),
            "line_end": loc.get("line_end"),
            "resolved_text": loc.get("resolved_text", "")[:300],
            "body": iss.get("body", "")[:500],
            "investigation_notes": latest_inv.get("notes", ""),
            "spotter": iss.get("spotter"),
        })

    open_details = []
    for iss in open_issues:
        loc = iss.get("location", {})
        investigations = iss.get("investigations", [])
        latest_inv = investigations[-1] if investigations else {}
        open_details.append({
            "id": iss["id"][:12],
            "title": iss["title"],
            "section": loc.get("section", "unknown"),
            "line_start": loc.get("line_start"),
            "line_end": loc.get("line_end"),
            "body": iss.get("body", "")[:500],
            "investigation_notes": latest_inv.get("notes", ""),
        })

    review_data = {
        "paper": {
            "title": paper_title,
            "source": status.get("source_root", ""),
            "commit": commit[:8],
            "sections": len(sections),
            "sentences": status.get("sentences", 0),
        },
        "review_stats": {
            "total_issues": len(full_issues),
            "confirmed": state_counts.get("confirmed", 0),
            "rejected": state_counts.get("rejected", 0),
            "open": state_counts.get("open", 0),
            "draft": state_counts.get("draft", 0),
            "wontfix": state_counts.get("wontfix", 0),
            "false_positive_rate": round(
                state_counts.get("rejected", 0) / max(1, len(full_issues)) * 100, 1
            ),
        },
        "section_summaries": section_summaries,
        "confirmed_issues": confirmed_details,
        "open_issues": open_details,
    }

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(review_data, indent=2))
    print(f"Wrote {output}")
    print(f"  {len(confirmed_details)} confirmed, {len(open_details)} open, "
          f"{state_counts.get('rejected', 0)} rejected")


if __name__ == "__main__":
    main()
