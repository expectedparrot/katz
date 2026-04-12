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


def load_eval_criteria(commit):
    """Load enabled eval criteria from the version's evals/ directory."""
    evals_dir = Path(f".katz/versions/{commit}/evals")
    criteria = {}
    if evals_dir.is_dir():
        for f in sorted(evals_dir.glob("*.md")):
            content = f.read_text(encoding="utf-8")
            # Parse frontmatter
            frontmatter = {}
            body = content
            if content.startswith("---\n"):
                end = content.find("\n---\n", 4)
                if end != -1:
                    import yaml
                    try:
                        frontmatter = yaml.safe_load(content[4:end]) or {}
                    except Exception:
                        pass
                    body = content[end + 5:]
            # Extract title
            title = f.stem.replace("_", " ").title()
            for line in body.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            criteria[f.stem] = {
                "title": title,
                "category": frontmatter.get("category"),
                "scope": frontmatter.get("scope"),
                "body": body,
            }
    return criteria


def load_eval_results(commit):
    """Load eval responses from the version's eval_results/ directory."""
    results_dir = Path(f".katz/versions/{commit}/eval_results")
    results = []
    if results_dir.is_dir():
        for f in sorted(results_dir.glob("*.json")):
            results.append(json.loads(f.read_text(encoding="utf-8")))
    return results


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


def load_referee_report(commit):
    """Load the narrative referee report if it exists."""
    for name in ("referee_report.md", "REVIEW.md"):
        path = Path(f".katz/{name}")
        if path.exists():
            return path.read_text(encoding="utf-8")
        path2 = Path(f".katz/versions/{commit}/{name}")
        if path2.exists():
            return path2.read_text(encoding="utf-8")
    return None


def md_to_html_simple(md_text):
    """Minimal markdown to HTML conversion for the referee report."""
    import re
    lines = md_text.split("\n")
    html_lines = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<p></p>")
            continue
        if stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{escape(stripped[2:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h4>{escape(stripped[3:])}</h4>")
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h5>{escape(stripped[4:])}</h5>")
        elif stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            # Handle **bold** in list items
            item = escape(stripped[2:])
            item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item)
            html_lines.append(f"<li>{item}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = escape(stripped)
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
            html_lines.append(f"<p>{text}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def build_html(status, sections, issues, manuscript=None, eval_criteria=None, eval_results=None, referee_report=None):
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
        total = len(sec_issues)
        confirmed = sum(1 for i in sec_issues if i.get("state") == "confirmed")
        confirmed_html = f'<span style="color:#ef4444;font-weight:600">{confirmed}</span>' if confirmed else '<span class="issue-count zero">0</span>'
        total_html = f'<span class="issue-count">{total}</span>' if total else '<span class="issue-count zero">0</span>'
        section_rows.append(
            f'<tr><td class="sec-title"><a href="#{sid}" onclick="highlightLines({sec["line_start"]},{sec["line_end"]},null)">{escape(sec["title"])}</a></td>'
            f"<td>{confirmed_html}</td>"
            f"<td>{total_html}</td>"
            f'<td>L{sec["line_start"]}–L{sec["line_end"]}</td></tr>'
        )

    # Build issue cards grouped by section
    issue_cards = []
    for sec in sections:
        sid = sec["id"]
        sec_issues = by_section.get(sid, [])
        if not sec_issues:
            continue
        issue_cards.append(f'<h3 id="{sid}" style="cursor:pointer" onclick="highlightLines({sec["line_start"]},{sec["line_end"]})">{escape(sec["title"])}</h3>')
        for iss in sorted(sec_issues, key=lambda i: i["location"].get("line_start", 0)):
            loc = iss["location"]
            raw_body = iss.get("body", "")
            # Extract [tag] pills from the start of body
            pills_html = ""
            import re as _re
            tag_match = _re.match(r"^(\[[^\]]+\]\s*)+", raw_body)
            if tag_match:
                tags_str = tag_match.group()
                remaining_body = raw_body[len(tags_str):].strip()
                tags = _re.findall(r"\[([^\]]+)\]", tags_str)
                pills = []
                for tag in tags:
                    pills.append(f'<span class="pill">{escape(tag)}</span>')
                pills_html = '<div class="pills">' + " ".join(pills) + "</div>"
            else:
                remaining_body = raw_body
            body = escape(remaining_body)
            ls = loc.get("line_start", 0)
            le = loc.get("line_end", 0)
            lines_label = f'L{ls}–L{le}' if ls else ""

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

            is_rejected = iss['state'] == 'rejected'
            details_attr = '' if not is_rejected else ''
            card_class = 'issue-card rejected' if is_rejected else 'issue-card'

            issue_cards.append(f"""<details class="{card_class}" data-line-start="{ls}" data-line-end="{le}" {"" if is_rejected else "open"}>
  <summary>
    <div class="issue-header">
      {state_badge(iss['state'])}
      <span class="issue-title">{escape(iss['title'])}</span>
      <span class="issue-lines">{lines_label}</span>
    </div>
  </summary>
  {pills_html}
  <div class="issue-body">{body}</div>
  {inv_html}
</details>""")

    # Summary stats
    total = len(issues)
    stats_items = []
    for state in ["draft", "open", "confirmed", "resolved", "wontfix"]:
        if state_counts[state]:
            stats_items.append(f"{state_badge(state)} {state_counts[state]}")

    # Build eval section
    eval_cards = []
    if eval_results:
        eval_criteria = eval_criteria or {}
        by_category = defaultdict(list)
        for result in eval_results:
            cat = result.get("category") or "uncategorized"
            by_category[cat].append(result)

        for cat in sorted(by_category.keys()):
            cat_title = cat.replace("-", " ").replace("_", " ").title()
            eval_cards.append(f'<h3>{escape(cat_title)}</h3>')
            for result in by_category[cat]:
                crit_name = result.get("criterion", "")
                crit_info = eval_criteria.get(crit_name, {})
                crit_title = crit_info.get("title", crit_name.replace("_", " ").title())
                crit_body = crit_info.get("body", "")
                crit_lines = crit_body.strip().splitlines()
                question_lines = [l for l in crit_lines if not l.startswith("# ")]
                question_text = "\n".join(question_lines).strip()
                if len(question_text) > 400:
                    question_text = question_text[:400].rsplit(" ", 1)[0] + "..."

                response = result.get("response", "")
                grade = result.get("grade")
                scope = result.get("scope")
                scope_html = f' <span class="eval-scope">{escape(scope)}</span>' if scope else ""
                grade_html = ""
                if grade:
                    grade_colors = {
                        "A+": "#16a34a", "A": "#16a34a", "A-": "#22c55e",
                        "B+": "#2563eb", "B": "#2563eb", "B-": "#60a5fa",
                        "C+": "#f59e0b", "C": "#f59e0b", "C-": "#fbbf24",
                        "D+": "#f97316", "D": "#f97316", "D-": "#fb923c",
                        "F": "#ef4444",
                    }
                    gc = grade_colors.get(grade, "#6b7280")
                    grade_html = f' <span class="grade-pill" style="background:{gc}">{escape(grade)}</span>'

                eval_cards.append(f"""<div class="eval-card">
  <div class="eval-header">
    <span class="eval-title">{escape(crit_title)}</span>{grade_html}{scope_html}
  </div>
  <div class="eval-question">{escape(question_text)}</div>
  <div class="eval-response">{escape(response)}</div>
</div>""")

    eval_html = ""
    if eval_cards:
        eval_html = f"""
<h2 id="evaluations">Evaluations</h2>
<p class="section-desc">{len(eval_results)} criteria evaluated.</p>
{"".join(eval_cards)}
"""

    # Build referee report section
    referee_html = ""
    if referee_report:
        referee_html = f"""
<h2 id="referee-report">Referee Report</h2>
<p class="section-desc">A narrative synthesis of the review findings, written for authors or an editor.</p>
<div class="referee-report">
{md_to_html_simple(referee_report)}
</div>
"""

    # Build nav links
    nav_items = ['<a href="#summary">Summary</a>']
    if referee_report:
        nav_items.append('<a href="#referee-report">Referee Report</a>')
    if eval_cards:
        nav_items.append('<a href="#evaluations">Evaluations</a>')
    nav_items.append('<a href="#sections">Sections</a>')
    nav_items.append('<a href="#issues">Issues</a>')
    nav_html = '<nav class="nav-bar">' + ' &middot; '.join(nav_items) + '</nav>'

    # Embed manuscript as JSON for the viewer
    manuscript_json = json.dumps(manuscript or "")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review Report — {escape(status.get('source_root', 'paper'))}</title>
<script>
MathJax = {{
  tex: {{
    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
  }},
  options: {{
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'code'],
  }},
}};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" async></script>
<style>
  :root {{
    --bg: #ffffff;
    --fg: #1a1a2e;
    --muted: #6b7280;
    --border: #e5e7eb;
    --card-bg: #f9fafb;
    --accent: #2563eb;
    --quote-bg: #fef3c7;
    --hl: #fef08a;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: var(--fg);
    background: var(--bg);
    line-height: 1.6;
    display: flex;
    height: 100vh;
    overflow: hidden;
  }}

  /* Split pane layout */
  #review-pane {{
    width: 50%;
    height: 100vh;
    overflow-y: auto;
    padding: 1.5rem;
    order: 1;
  }}
  #manuscript-pane {{
    width: 50%;
    height: 100vh;
    overflow-y: auto;
    border-left: 2px solid var(--border);
    padding: 1rem;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 0.78rem;
    line-height: 1.5;
    background: #fafafa;
    order: 2;
  }}
  .ms-toolbar {{
    position: sticky;
    top: 0;
    background: #f0f0f0;
    border-bottom: 1px solid var(--border);
    padding: 0.3rem 0.75rem;
    z-index: 10;
    font-size: 0.8rem;
  }}
  .ms-toggle {{
    cursor: pointer;
    user-select: none;
    color: var(--muted);
  }}
  .ms-toggle input {{ margin-right: 0.3rem; }}
  /* Rendered view */
  #ms-rendered {{
    padding: 1rem 1.5rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 0.85rem;
    line-height: 1.7;
  }}
  .ms-h1 {{ font-size: 1.3rem; font-weight: 700; margin: 1.5rem 0 0.5rem; }}
  .ms-h2 {{ font-size: 1.1rem; font-weight: 600; margin: 1.25rem 0 0.4rem; border-bottom: 1px solid var(--border); padding-bottom: 0.2rem; }}
  .ms-h3 {{ font-size: 0.95rem; font-weight: 600; margin: 1rem 0 0.3rem; }}
  .ms-h4 {{ font-size: 0.9rem; font-weight: 600; margin: 0.75rem 0 0.25rem; }}
  .ms-p {{ margin-bottom: 0.6rem; }}
  .ms-p.highlighted {{ background: var(--hl); }}
  .ms-blank {{ height: 0.5rem; }}
  .ms-img {{ color: var(--muted); font-style: italic; margin: 0.5rem 0; padding: 0.5rem; background: var(--card-bg); border-radius: 4px; text-align: center; }}
  .ms-math {{ font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.8rem; margin: 0.3rem 0; color: #6b21a8; }}
  .ms-li {{ margin-left: 1.5rem; margin-bottom: 0.3rem; }}
  .ms-li::before {{ content: "•"; margin-left: -1rem; margin-right: 0.5rem; color: var(--muted); }}
  .ms-code {{ font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.78rem; margin: 0; padding: 0 1rem; background: var(--card-bg); }}
  .ms-note {{ font-size: 0.75rem; color: var(--muted); margin: 0.2rem 0; }}
  .ms-table {{
    border-collapse: collapse;
    font-size: 0.78rem;
    margin: 0.5rem 0;
    width: 100%;
  }}
  .ms-table th, .ms-table td {{
    border: 1px solid var(--border);
    padding: 0.25rem 0.5rem;
    text-align: left;
    vertical-align: top;
  }}
  .ms-table th {{
    background: #f0f0f0;
    font-weight: 600;
    font-size: 0.75rem;
  }}
  .ms-table td {{
    font-size: 0.75rem;
  }}
  .ms-anchor {{ display: block; height: 0; overflow: hidden; }}
  /* Source view */
  .ms-line {{
    display: flex;
    padding: 0 0.5rem;
    transition: background 0.15s;
  }}
  .ms-line.highlighted {{
    background: var(--hl);
  }}
  .ms-line:hover {{
    background: #f0f0f0;
  }}
  .ms-line.highlighted:hover {{
    background: #fde047;
  }}
  .ms-linenum {{
    color: var(--muted);
    min-width: 3.5em;
    text-align: right;
    padding-right: 1em;
    user-select: none;
    flex-shrink: 0;
  }}
  .ms-text {{
    white-space: pre-wrap;
    word-break: break-word;
    flex: 1;
  }}

  /* Review pane styles */
  .nav-bar {{
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    font-size: 0.85rem;
    margin-bottom: 1.5rem;
    padding: 0.5rem 0.75rem;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
  }}
  .nav-bar a {{
    color: var(--accent);
    text-decoration: none;
  }}
  .nav-bar a:hover {{
    text-decoration: underline;
  }}
  .about-review {{
    background: #f0f4ff;
    border: 1px solid #c7d2fe;
    border-radius: 6px;
    padding: 0.75rem 1rem;
    margin-bottom: 1.5rem;
    font-size: 0.85rem;
    line-height: 1.6;
    color: #374151;
  }}
  .about-review p {{ margin-bottom: 0.4rem; }}
  .about-review p:last-child {{ margin-bottom: 0; }}
  .section-desc {{
    color: var(--muted);
    font-size: 0.85rem;
    margin-bottom: 1rem;
    line-height: 1.5;
  }}
  .referee-report {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1rem 1.25rem;
    font-size: 0.9rem;
    line-height: 1.7;
  }}
  .referee-report h3 {{ font-size: 1.05rem; margin-top: 1.25rem; margin-bottom: 0.5rem; color: var(--fg); }}
  .referee-report h4 {{ font-size: 0.95rem; margin-top: 1rem; margin-bottom: 0.4rem; color: var(--fg); }}
  .referee-report h5 {{ font-size: 0.9rem; margin-top: 0.75rem; margin-bottom: 0.3rem; color: var(--fg); }}
  .referee-report p {{ margin-bottom: 0.5rem; }}
  .referee-report ul {{ margin: 0.3rem 0 0.5rem 1.5rem; }}
  .referee-report li {{ margin-bottom: 0.25rem; }}
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
    transition: border-color 0.15s;
  }}
  .issue-card > summary {{
    cursor: pointer;
    list-style: none;
  }}
  .issue-card > summary::-webkit-details-marker {{ display: none; }}
  .issue-card:hover {{
    border-color: var(--accent);
  }}
  .issue-card.active {{
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }}
  .issue-card.rejected {{
    opacity: 0.55;
    display: none;
  }}
  .issue-card.rejected:hover,
  .issue-card.rejected[open] {{
    opacity: 1;
  }}
  .show-rejected .issue-card.rejected {{
    display: block;
  }}
  .pills {{ display: flex; gap: 0.3rem; flex-wrap: wrap; margin-top: 0.4rem; }}
  .pill {{
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 500;
    color: var(--muted);
    background: var(--border);
    padding: 0.1em 0.5em;
    border-radius: 9999px;
  }}
  .issue-header {{ display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
  .issue-title {{ font-weight: 600; font-size: 0.9rem; }}
  .issue-lines {{ color: var(--muted); font-size: 0.8rem; margin-left: auto; }}
  .issue-body {{ font-size: 0.85rem; margin-top: 0.4rem; color: #374151; white-space: pre-wrap; }}
  .investigations {{ margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px dashed var(--border); }}
  .investigation {{ font-size: 0.85rem; margin-bottom: 0.25rem; }}
  .inv-verdict {{ text-transform: uppercase; font-size: 0.75rem; }}
  .inv-notes {{ color: #4b5563; }}
  .eval-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.6rem;
  }}
  .eval-header {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; }}
  .eval-title {{ font-weight: 600; font-size: 0.9rem; }}
  .grade-pill {{
    display: inline-block;
    color: #fff;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.1em 0.5em;
    border-radius: 9999px;
    vertical-align: middle;
    margin-left: 0.3rem;
  }}
  .eval-scope {{
    color: var(--muted);
    font-size: 0.75rem;
    background: var(--border);
    padding: 0.1em 0.4em;
    border-radius: 3px;
  }}
  .eval-question {{
    font-size: 0.8rem;
    color: var(--muted);
    font-style: italic;
    margin-bottom: 0.5rem;
    white-space: pre-wrap;
  }}
  .eval-response {{
    font-size: 0.85rem;
    color: #374151;
    white-space: pre-wrap;
    border-left: 3px solid var(--accent);
    padding-left: 0.7rem;
  }}
</style>
</head>
<body>

<div id="review-pane">

<h1>Review Report</h1>
<div class="meta">
  Source: <code>{escape(status.get('source_root', ''))}</code> &middot;
  Commit: <code>{short_commit}</code> &middot;
  {status['sections']} sections, {status['sentences']} sentences &middot;
  Generated {now}
</div>

{nav_html}

<div class="about-review">
  <p>This report contains two types of review artifacts:</p>
  <p><strong>Evaluations</strong> are advisory assessments against quality criteria — questions like
  "Does the abstract convey the findings?" or "Is the empirical strategy credible?"
  Each criterion gets a narrative response and a letter grade. These are for the author
  to consider, not problems to fix.</p>
  <p><strong>Issues</strong> are specific problems found by automated spotters scanning for
  overclaiming, logical gaps, internal contradictions, unclear writing, and similar concerns.
  Each issue is investigated and classified as confirmed, rejected, or uncertain.
  Click any issue to highlight its location in the manuscript.</p>
</div>

<h2 id="summary">Summary</h2>
<div class="stats">
  <span class="total">{total} issue{"s" if total != 1 else ""}</span>
  {"".join(stats_items)}
</div>

{referee_html}

{eval_html}

<h2 id="sections">Sections</h2>
<table>
  <thead><tr><th>Section</th><th style="color:#ef4444">Confirmed</th><th>Total</th><th>Lines</th></tr></thead>
  <tbody>
    {"".join(section_rows)}
  </tbody>
</table>

<h2 id="issues">Issues</h2>
<p class="section-desc">Click any issue to highlight its location in the manuscript.</p>
<div style="margin-bottom:0.75rem">
  <label style="font-size:0.85rem;color:var(--muted);cursor:pointer;user-select:none">
    <input type="checkbox" id="show-rejected" onchange="toggleRejected(this.checked)" style="margin-right:0.3rem">
    Show rejected ({state_counts.get('rejected', 0)})
  </label>
</div>
{"".join(issue_cards) if issue_cards else '<p style="color:var(--muted)">No issues filed yet.</p>'}

</div>

<div id="manuscript-pane">
  <div class="ms-toolbar">
    <label class="ms-toggle">
      <input type="checkbox" id="ms-mode-toggle" onchange="toggleMsMode(this.checked)">
      <span>Source view</span>
    </label>
  </div>
  <div id="ms-rendered"></div>
  <div id="ms-source" style="display:none"></div>
</div>

<script>
const manuscript = {manuscript_json};
const msSource = document.getElementById('ms-source');
const msRendered = document.getElementById('ms-rendered');
const msModeToggle = document.getElementById('ms-mode-toggle');
let currentMode = 'rendered'; // 'rendered' or 'source'

// Build source view with line numbers
const lines = manuscript.split('\\n');
lines.forEach((text, i) => {{
  const div = document.createElement('div');
  div.className = 'ms-line';
  div.id = 'ms-line-' + (i + 1);
  div.innerHTML =
    '<span class="ms-linenum">' + (i + 1) + '</span>' +
    '<span class="ms-text">' + escapeHtml(text) + '</span>';
  msSource.appendChild(div);
}});

// Build rendered view: simple markdown to HTML with line anchors
(function buildRendered() {{
  let html = '';
  let inCode = false;
  let inTable = false;
  let tableRowIdx = 0;
  lines.forEach((line, i) => {{
    const ln = i + 1;
    const anchor = '<a class="ms-anchor" id="ms-r-' + ln + '"></a>';

    if (line.startsWith('```')) {{
      inCode = !inCode;
      return;
    }}
    if (inCode) {{
      html += anchor + '<pre class="ms-code">' + escapeHtml(line) + '</pre>';
      return;
    }}
    if (line.startsWith('|')) {{
      if (!inTable) {{ html += '<table class="ms-table">'; inTable = true; tableRowIdx = 0; }}
      // Skip separator rows (|---|---|)
      if (/^\|[\s\-:|]+\|$/.test(line.trim())) {{
        tableRowIdx++;
        return;
      }}
      // Parse cells
      const cells = line.split('|').slice(1, -1);
      const tag = (tableRowIdx === 0) ? 'th' : 'td';
      html += anchor + '<tr>' + cells.map(c => {{
        let cell = renderInline(c.trim());
        // Convert <br> tags that were escaped back to real line breaks
        cell = cell.replace(/&lt;br&gt;/gi, '<br>').replace(/&lt;br\/&gt;/gi, '<br>').replace(/&lt;br\s*\/&gt;/gi, '<br>');
        return '<' + tag + '>' + cell + '</' + tag + '>';
      }}).join('') + '</tr>';
      tableRowIdx++;
      return;
    }} else if (inTable) {{
      html += '</table>';
      inTable = false;
    }}

    const trimmed = line.trim();
    if (!trimmed) {{
      html += anchor + '<div class="ms-blank"></div>';
    }} else if (trimmed.startsWith('# ')) {{
      html += anchor + '<h1 class="ms-h1">' + escapeHtml(trimmed.slice(2).replace(/\*\*/g,'')) + '</h1>';
    }} else if (trimmed.startsWith('## ')) {{
      html += anchor + '<h2 class="ms-h2">' + escapeHtml(trimmed.slice(3).replace(/\*\*/g,'')) + '</h2>';
    }} else if (trimmed.startsWith('### ')) {{
      html += anchor + '<h3 class="ms-h3">' + escapeHtml(trimmed.slice(4).replace(/\*\*/g,'')) + '</h3>';
    }} else if (trimmed.startsWith('#### ')) {{
      html += anchor + '<h4 class="ms-h4">' + escapeHtml(trimmed.slice(5).replace(/\*\*/g,'')) + '</h4>';
    }} else if (trimmed.startsWith('![')) {{
      const alt = (trimmed.match(/!\[([^\]]*)\]/) || ['','image'])[1];
      html += anchor + '<div class="ms-img">[Figure: ' + escapeHtml(alt || 'image') + ']</div>';
    }} else if (trimmed.startsWith('$$')) {{
      html += anchor + '<div class="ms-math">' + trimmed + '</div>';
    }} else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {{
      html += anchor + '<div class="ms-li">' + renderInline(trimmed.slice(2)) + '</div>';
    }} else if (trimmed.startsWith('<sup>') || trimmed.startsWith('<span')) {{
      html += anchor + '<div class="ms-note">' + escapeHtml(trimmed) + '</div>';
    }} else {{
      html += anchor + '<p class="ms-p" data-line="' + ln + '">' + renderInline(trimmed) + '</p>';
    }}
  }});
  if (inTable) html += '</table>';
  msRendered.innerHTML = html;
  // Tell MathJax to typeset the rendered view once loaded
  if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {{
    MathJax.typesetPromise([msRendered]);
  }} else {{
    document.addEventListener('DOMContentLoaded', function() {{
      if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {{
        MathJax.typesetPromise([msRendered]);
      }}
    }});
  }}
}})();

function renderInline(text) {{
  // Split on $...$ math to preserve it for MathJax
  // We protect math spans from HTML escaping
  const parts = text.split(/(\$\$[^$]+\$\$|\$[^$]+\$)/g);
  let result = '';
  for (const part of parts) {{
    if (part.startsWith('$')) {{
      // Math — pass through raw for MathJax
      result += part;
    }} else {{
      // Non-math — escape and apply markdown formatting
      let s = escapeHtml(part);
      s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
      // Strip span tags that leaked through from PDF conversion
      s = s.replace(/&lt;span[^&]*&gt;/g, '').replace(/&lt;\/span&gt;/g, '');
      result += s;
    }}
  }}
  return result;
}}

function escapeHtml(s) {{
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}

function toggleMsMode(showSource) {{
  if (showSource) {{
    msSource.style.display = '';
    msRendered.style.display = 'none';
    currentMode = 'source';
    // Re-apply highlight in source view
    if (lastHighlight) {{
      for (let i = lastHighlight[0]; i <= lastHighlight[1]; i++) {{
        const el = document.getElementById('ms-line-' + i);
        if (el) el.classList.add('highlighted');
      }}
    }}
  }} else {{
    msSource.style.display = 'none';
    msRendered.style.display = '';
    currentMode = 'rendered';
  }}
}}

let activeCard = null;
let lastHighlight = null;

function highlightLines(start, end, srcEl) {{
  // Clear previous highlights
  document.querySelectorAll('.ms-line.highlighted').forEach(el => el.classList.remove('highlighted'));
  document.querySelectorAll('.ms-p.highlighted').forEach(el => el.classList.remove('highlighted'));
  if (activeCard) activeCard.classList.remove('active');

  if (!start || !end) return;
  lastHighlight = [start, end];

  // Auto-switch to source view when clicking an issue (srcEl present)
  if (srcEl && currentMode === 'rendered') {{
    msModeToggle.checked = true;
    toggleMsMode(true);
  }}

  // Highlight lines in source view
  for (let i = start; i <= end; i++) {{
    const el = document.getElementById('ms-line-' + i);
    if (el) el.classList.add('highlighted');
  }}

  // Scroll to highlighted range in whichever view is active
  if (currentMode === 'source') {{
    const target = document.getElementById('ms-line-' + start);
    if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }} else {{
    const target = document.getElementById('ms-r-' + start);
    if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}

  // Mark the clicked card as active
  if (srcEl) {{
    activeCard = srcEl.closest('.issue-card') || srcEl;
    if (activeCard) activeCard.classList.add('active');
  }}
}}

function toggleRejected(show) {{
  document.getElementById('review-pane').classList.toggle('show-rejected', show);
}}

// Event delegation: handle clicks on issue cards
document.getElementById('review-pane').addEventListener('click', function(e) {{
  const card = e.target.closest('.issue-card');
  if (card) {{
    const ls = parseInt(card.dataset.lineStart);
    const le = parseInt(card.dataset.lineEnd);
    if (ls && le) highlightLines(ls, le, card);
    return;
  }}
}});
</script>

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
    eval_criteria = load_eval_criteria(commit)
    eval_results = load_eval_results(commit)
    referee_report = load_referee_report(commit)
    html = build_html(status, sections, issues, manuscript, eval_criteria, eval_results, referee_report)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(html)
    n_evals = len(eval_results)
    print(f"Wrote {output} ({len(issues)} issues, {len(sections)} sections, {n_evals} evaluations)")


if __name__ == "__main__":
    main()
