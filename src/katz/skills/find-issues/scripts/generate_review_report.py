#!/usr/bin/env python3
"""Generate an HTML review-status report from katz data."""

import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def run_katz(*args):
    result = subprocess.run(
        ["katz", *args], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def load_sections(commit):
    """Read all section records from paper_map.jsonl."""
    path = Path(f".katz/versions/{commit}/paper_map.jsonl")
    sections = []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") == "section":
                sections.append(rec)
    return sections


def load_manuscript(commit):
    path = Path(f".katz/versions/{commit}/paper/manuscript.md")
    return path.read_text()


def get_full_issues(issue_summaries):
    """Fetch full records for each issue, merging in section from the summary."""
    issues = []
    for summary in issue_summaries:
        issue = run_katz("issue", "show", summary["id"])
        # katz issue show omits 'section' from location; carry it from the list
        section = summary.get("location", {}).get("section", "unknown")
        issue.setdefault("location", {})["section"] = section
        issues.append(issue)
    return issues


STATE_COLORS = {
    "draft": "#6b7280",
    "open": "#f59e0b",
    "confirmed": "#ef4444",
    "resolved": "#10b981",
    "wontfix": "#9ca3af",
}

STATE_LABELS = {
    "draft": "Draft",
    "open": "Open",
    "confirmed": "Confirmed",
    "resolved": "Resolved",
    "wontfix": "Won't Fix",
}


def state_badge(state):
    color = STATE_COLORS.get(state, "#6b7280")
    label = STATE_LABELS.get(state, state.title())
    return f'<span class="badge" style="background:{color}">{label}</span>'


def excerpt(text, max_len=120):
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def escape(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def resolve_text(manuscript, loc):
    """Resolve full text from line range in the manuscript."""
    line_start = loc.get("line_start")
    line_end = loc.get("line_end")
    if line_start is not None and line_end is not None and manuscript:
        lines = manuscript.split("\n")
        # line numbers are 1-based
        selected = lines[line_start - 1 : line_end]
        text = "\n".join(selected).strip()
        if len(text) > 600:
            text = text[:600].rsplit(" ", 1)[0] + "..."
        return text
    return loc.get("resolved_text", "")


def build_html(status, sections, issues, manuscript=None):
    commit = status["commit"]
    short_commit = commit[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Group issues by section
    by_section = defaultdict(list)
    for issue in issues:
        sec = issue.get("location", {}).get("section", "unknown")
        by_section[sec].append(issue)

    # Count states
    state_counts = defaultdict(int)
    for issue in issues:
        state_counts[issue["state"]] += 1

    # Build section rows
    section_rows = []
    for sec in sections:
        sid = sec["id"]
        sec_issues = by_section.get(sid, [])
        count = len(sec_issues)
        count_html = f'<span class="issue-count">{count}</span>' if count else '<span class="issue-count zero">0</span>'
        section_rows.append(
            f'<tr><td class="sec-title"><a href="#{sid}">{escape(sec["title"])}</a></td>'
            f"<td>{count_html}</td>"
            f'<td>L{sec["line_start"]}–L{sec["line_end"]}</td></tr>'
        )

    # Build issue cards grouped by section
    issue_cards = []
    for sec in sections:
        sid = sec["id"]
        sec_issues = by_section.get(sid, [])
        if not sec_issues:
            continue
        issue_cards.append(f'<h3 id="{sid}">{escape(sec["title"])}</h3>')
        for iss in sorted(sec_issues, key=lambda i: i["location"].get("line_start", 0)):
            loc = iss["location"]
            resolved = escape(resolve_text(manuscript, loc))
            body = escape(iss.get("body", ""))
            lines = f'L{loc["line_start"]}–L{loc["line_end"]}' if loc.get("line_start") else ""
            # Build investigation notes HTML
            inv_html = ""
            investigations = iss.get("investigations", [])
            if investigations:
                inv_items = []
                for inv in investigations:
                    verdict = inv.get("verdict", "")
                    notes = escape(inv.get("notes", ""))
                    verdict_color = {"confirmed": "#ef4444", "rejected": "#10b981", "uncertain": "#f59e0b"}.get(verdict, "#6b7280")
                    inv_items.append(
                        f'<div class="investigation">'
                        f'<span class="inv-verdict" style="color:{verdict_color};font-weight:600">{verdict}</span> '
                        f'<span class="inv-notes">{notes}</span>'
                        f'</div>'
                    )
                inv_html = '<div class="investigations">' + "".join(inv_items) + '</div>'

            issue_cards.append(f"""<div class="issue-card">
  <div class="issue-header">
    {state_badge(iss['state'])}
    <span class="issue-title">{escape(iss['title'])}</span>
    <span class="issue-lines">{lines}</span>
  </div>
  {f'<div class="resolved-text">{resolved}</div>' if resolved else ''}
  <div class="issue-body">{body}</div>
  {inv_html}
</div>""")

    # Summary stats
    total = len(issues)
    stats_items = []
    for state in ["draft", "open", "confirmed", "resolved", "wontfix"]:
        if state_counts[state]:
            stats_items.append(f"{state_badge(state)} {state_counts[state]}")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review Report — {escape(status.get('source_root', 'paper'))}</title>
<style>
  :root {{
    --bg: #ffffff;
    --fg: #1a1a2e;
    --muted: #6b7280;
    --border: #e5e7eb;
    --card-bg: #f9fafb;
    --accent: #2563eb;
    --quote-bg: #fef3c7;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: var(--fg);
    background: var(--bg);
    line-height: 1.6;
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 2rem; margin-bottom: 0.75rem; border-bottom: 1px solid var(--border); padding-bottom: 0.25rem; }}
  h3 {{ font-size: 1rem; margin-top: 1.5rem; margin-bottom: 0.5rem; color: var(--accent); }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .meta code {{ background: var(--card-bg); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.85em; }}
  .stats {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; font-size: 0.9rem; align-items: center; }}
  .stats .total {{ font-weight: 600; margin-right: 0.5rem; }}
  .badge {{
    display: inline-block;
    color: #fff;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.15em 0.55em;
    border-radius: 9999px;
    vertical-align: middle;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th {{ text-align: left; font-weight: 600; padding: 0.4rem 0.6rem; border-bottom: 2px solid var(--border); }}
  td {{ padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }}
  .sec-title a {{ color: var(--accent); text-decoration: none; }}
  .sec-title a:hover {{ text-decoration: underline; }}
  .issue-count {{ font-weight: 600; }}
  .issue-count.zero {{ color: var(--muted); font-weight: 400; }}
  .issue-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.6rem;
  }}
  .issue-header {{ display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
  .issue-title {{ font-weight: 600; font-size: 0.9rem; }}
  .issue-lines {{ color: var(--muted); font-size: 0.8rem; margin-left: auto; }}
  .resolved-text {{
    background: var(--quote-bg);
    border-left: 3px solid #f59e0b;
    padding: 0.35rem 0.7rem;
    margin: 0.5rem 0;
    font-size: 0.85rem;
    font-style: italic;
    border-radius: 0 4px 4px 0;
  }}
  .issue-body {{ font-size: 0.85rem; margin-top: 0.4rem; color: #374151; white-space: pre-wrap; }}
  .investigations {{ margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px dashed var(--border); }}
  .investigation {{ font-size: 0.85rem; margin-bottom: 0.25rem; }}
  .inv-verdict {{ text-transform: uppercase; font-size: 0.75rem; }}
  .inv-notes {{ color: #4b5563; }}
</style>
</head>
<body>

<h1>Review Report</h1>
<div class="meta">
  Source: <code>{escape(status.get('source_root', ''))}</code> &middot;
  Commit: <code>{short_commit}</code> &middot;
  {status['sections']} sections, {status['sentences']} sentences &middot;
  Generated {now}
</div>

<h2>Summary</h2>
<div class="stats">
  <span class="total">{total} issue{"s" if total != 1 else ""}</span>
  {"".join(stats_items)}
</div>

<h2>Sections</h2>
<table>
  <thead><tr><th>Section</th><th>Issues</th><th>Lines</th></tr></thead>
  <tbody>
    {"".join(section_rows)}
  </tbody>
</table>

<h2>Issues</h2>
{"".join(issue_cards) if issue_cards else '<p style="color:var(--muted)">No issues filed yet.</p>'}

</body>
</html>"""
    return html


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else ".katz/review.html"

    status = run_katz("paper", "status")
    commit = status["commit"]
    sections = load_sections(commit)
    issue_summaries = run_katz("issue", "list")
    issues = get_full_issues(issue_summaries)

    manuscript = load_manuscript(commit)
    html = build_html(status, sections, issues, manuscript)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(html)
    print(f"Wrote {output} ({len(issues)} issues, {len(sections)} sections)")


if __name__ == "__main__":
    main()
