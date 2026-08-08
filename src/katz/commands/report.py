"""`katz report` commands: HTML review report generation."""
from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import os
import re
import tempfile
import warnings
from pathlib import Path
from typing import Any, Optional

import typer
import yaml

from ..assets import REPORT_SCRIPT
from ..errors import KatzError, emit_json, fail
from ..issues import VALID_STATES
from ..manuscript import validate_location
from ..storage import load_version, now_utc, read_json, sha256_file
from .agent import _agent_action


report_app = typer.Typer(help="Generate review reports.")


def _load_report_module() -> Any:
    if not REPORT_SCRIPT.exists():
        raise KatzError("Report generator script not found", "not_found", {"path": str(REPORT_SCRIPT)})
    spec = importlib.util.spec_from_file_location("katz_generate_review_report", REPORT_SCRIPT)
    if spec is None or spec.loader is None:
        raise KatzError("Report generator script could not be loaded", "validation_error", {"path": str(REPORT_SCRIPT)})
    module = importlib.util.module_from_spec(spec)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        spec.loader.exec_module(module)
    return module


def _report_source(text: str, path: Path) -> dict[str, Any]:
    """Validate report frontmatter/headings and return renderable body metadata."""
    if not text.strip():
        raise KatzError("Report source is empty", "report_check_failed", {"path": str(path)})
    lines = text.splitlines()
    metadata: dict[str, Any] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if closing is None:
            raise KatzError(
                "Report YAML frontmatter is not closed",
                "report_check_failed",
                {"path": str(path), "line": 1, "suggestion": "Add a closing --- line before the report body."},
            )
        try:
            loaded = yaml.safe_load("\n".join(lines[1:closing])) or {}
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            raise KatzError(
                "Report YAML frontmatter is invalid",
                "report_check_failed",
                {"path": str(path), "line": (mark.line + 2) if mark else 1, "message": str(exc)},
            ) from exc
        if not isinstance(loaded, dict):
            raise KatzError("Report YAML frontmatter must be an object", "report_check_failed", {"path": str(path)})
        metadata = loaded
        body_start = closing + 1
    h1_lines: list[int] = []
    in_fence = False
    for index, line in enumerate(lines):
        if index < body_start:
            continue
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#\s+\S", line):
            h1_lines.append(index + 1)
    if metadata.get("title") and h1_lines:
        raise KatzError(
            "Report has a YAML title and H1 body heading",
            "report_check_failed",
            {
                "path": str(path),
                "lines": h1_lines,
                "suggestion": "Keep the YAML title and change body H1 headings to H2 (replace '# ' with '## ').",
            },
        )
    if len(h1_lines) > 1:
        raise KatzError(
            "Report has multiple H1 headings",
            "report_check_failed",
            {"path": str(path), "lines": h1_lines, "suggestion": "Keep one H1 title and start body sections at H2."},
        )
    title = str(metadata.get("title") or "").strip()
    if not title and h1_lines:
        title = lines[h1_lines[0] - 1][2:].strip()
    if not title:
        title = "Referee Report"
    body_lines = lines[body_start:]
    if not metadata.get("title") and h1_lines:
        h1_index = h1_lines[0] - 1 - body_start
        if 0 <= h1_index < len(body_lines):
            body_lines = body_lines[:h1_index] + body_lines[h1_index + 1:]
    body = "\n".join(body_lines).strip()
    if not body:
        raise KatzError("Report body is empty", "report_check_failed", {"path": str(path)})
    return {"title": title, "metadata": metadata, "body": body, "h1_lines": h1_lines}


def _inline_markdown(value: str) -> str:
    escaped = html.escape(value, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def _markdown_body_html(markdown: str) -> str:
    """Render the report subset without requiring Pandoc or network assets."""
    output: list[str] = []
    paragraph: list[str] = []
    list_tag: str | None = None
    in_code = False
    code_lines: list[str] = []

    def close_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            output.append(f"</{list_tag}>")
            list_tag = None

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            close_paragraph()
            close_list()
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        unordered = re.match(r"^[-*]\s+(.+)$", stripped)
        ordered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if heading:
            close_paragraph()
            close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
        elif unordered or ordered:
            close_paragraph()
            wanted = "ul" if unordered else "ol"
            if list_tag != wanted:
                close_list()
                output.append(f"<{wanted}>")
                list_tag = wanted
            match = unordered or ordered
            output.append(f"<li>{_inline_markdown(match.group(1))}</li>")
        elif stripped.startswith("> "):
            close_paragraph()
            close_list()
            output.append(f"<blockquote>{_inline_markdown(stripped[2:])}</blockquote>")
        elif stripped in {"---", "***"}:
            close_paragraph()
            close_list()
            output.append("<hr>")
        elif not stripped:
            close_paragraph()
            close_list()
        else:
            paragraph.append(stripped)
    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    close_paragraph()
    close_list()
    return "\n".join(output)


def _ledger_hash(dest: Path) -> str:
    digest = hashlib.sha256()
    roots = [dest / "version.json", dest / "paper_map.jsonl", dest / "paper_map.json", dest / "issues", dest / "runs"]
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    for path in sorted(paths):
        digest.update(str(path.relative_to(dest)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _finalization_snapshot(dest: Path, data: dict[str, Any]) -> dict[str, Any]:
    counts = {state: 0 for state in sorted(VALID_STATES)}
    validation_errors: list[dict[str, Any]] = []
    if not data["status"].get("valid"):
        validation_errors.append({"code": "invalid_manuscript", "message": "Canonical manuscript checksum validation failed."})
    canonical = dest / str(data["status"].get("canonical") or "paper/manuscript.md")
    for issue in data["issues"]:
        state = str(issue.get("state", "draft"))
        counts[state] = counts.get(state, 0) + 1
        if state not in VALID_STATES:
            validation_errors.append({"code": "invalid_issue_state", "id": issue.get("id"), "state": state})
        issue_path = dest / "issues" / str(issue.get("id")) / "issue.json"
        location = issue.get("location")
        if isinstance(location, dict) and canonical.is_file():
            validate_location(canonical, issue_path, location, validation_errors)
        else:
            validation_errors.append({"code": "invalid_location", "id": issue.get("id"), "message": "Issue location is missing."})
    audit = data["status"].get("review_audit") or {}
    ingestion = None
    runs_dir = dest / "runs"
    if runs_dir.is_dir():
        for path in reversed(sorted(runs_dir.glob("*.json"))):
            candidate = read_json(path)
            if candidate.get("kind") == "spotter" and candidate.get("status") in {"ingested", "partial", "invalid"}:
                ingestion = candidate
                break
    expected = audit.get("expected_answers")
    valid = int(audit.get("valid_answers") or 0)
    incomplete = max(0, int(expected) - valid) if isinstance(expected, int) else None
    skipped = int((ingestion or {}).get("skipped") or 0)
    reasons: list[str] = []
    if validation_errors:
        reasons.append("ledger_validation_failed")
    if counts.get("draft", 0):
        reasons.append("draft_issues_remain")
    if not audit:
        reasons.append("coverage_not_audited")
    elif not audit.get("complete"):
        reasons.append("incomplete_model_coverage")
    if ingestion is None:
        reasons.append("ingestion_not_recorded")
    elif ingestion.get("status") != "ingested":
        reasons.append("partial_ingestion")
    if skipped:
        reasons.append("ingestion_skips_present")
    return {
        "complete": not reasons,
        "incomplete_reasons": reasons,
        "coverage": {
            "requested": expected,
            "valid": valid,
            "incomplete": incomplete,
            "fraction": audit.get("coverage"),
            "parse_failures": int(audit.get("null_answers") or 0) + int(audit.get("invalid_answers") or 0) + int(audit.get("model_exceptions") or 0),
            "missing_answers": audit.get("missing_answers"),
            "ingestion_skips": skipped,
            "anchoring_failures": skipped if skipped else 0,
            "anchoring_failures_are_upper_bound": bool(skipped),
        },
        "issues": counts,
        "validation": {"valid": not validation_errors, "errors": validation_errors},
        "provenance": {
            "results_path": audit.get("results_path"),
            "jobs_path": audit.get("jobs_path"),
            "ingestion_status": (ingestion or {}).get("status"),
            "ingestion_timestamp": (ingestion or {}).get("timestamp"),
        },
    }


def _standalone_report_html(source: dict[str, Any], snapshot: dict[str, Any], commit: str) -> str:
    title = html.escape(source["title"])
    status_class = "complete" if snapshot["complete"] else "incomplete"
    status_text = "Complete reviewed coverage" if snapshot["complete"] else "Incomplete review evidence"
    metadata = json.dumps({"commit": commit, **snapshot}, sort_keys=True).replace("<", "\\u003c")
    reasons = "" if snapshot["complete"] else "<p>" + html.escape(", ".join(snapshot["incomplete_reasons"])) + "</p>"
    body = _markdown_body_html(source["body"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:ui-serif,Georgia,serif;line-height:1.55;max-width:860px;margin:0 auto;padding:2rem;color:#172033}}
h1,h2,h3,h4{{line-height:1.2;margin-top:1.8em}} a{{color:#175cd3}} code,pre{{font-family:ui-monospace,monospace}}
pre{{overflow:auto;background:#f4f6f8;padding:1rem}} blockquote{{border-left:4px solid #98a2b3;margin-left:0;padding-left:1rem}}
.review-state{{padding:1rem;border-radius:.5rem;margin:1rem 0 2rem}} .complete{{background:#ecfdf3;border:1px solid #75e0a7}}
.incomplete{{background:#fffaeb;border:2px solid #f79009}} .meta{{font:small ui-monospace,monospace;color:#475467}}
</style></head><body><header><h1>{title}</h1><div class="review-state {status_class}"><strong>{status_text}</strong>{reasons}</div>
<p class="meta">Katz commit {html.escape(commit[:12])}</p></header><main>{body}</main>
<script type="application/json" id="katz-finalization-metadata">{metadata}</script></body></html>"""


def _write_outputs_transactionally(outputs: dict[Path, bytes]) -> None:
    temporary: dict[Path, Path] = {}
    originals: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for target, content in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[target] = target.read_bytes() if target.exists() else None
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as handle:
                handle.write(content)
                temporary[target] = Path(handle.name)
        for target, staged in temporary.items():
            if originals[target] == outputs[target]:
                staged.unlink(missing_ok=True)
                continue
            os.replace(staged, target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            previous = originals[target]
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(previous)
        raise
    finally:
        for staged in temporary.values():
            staged.unlink(missing_ok=True)


@report_app.command("generate")
def report_generate(
    output: Path = typer.Option(Path(".katz/review.html"), "--output", "-o"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Generate the HTML review report."""
    try:
        report_module = _load_report_module()
        data = report_module.collect_report_data(commit)
        html = report_module.build_html(**data)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        report_module.write_report_assets(output)
        emit_json(
            {
                "generated": True,
                "path": str(output),
                "commit": data["status"]["commit"],
                "issues": len(data["issues"]),
                "sections": len(data["sections"]),
                "evaluations": len(data["eval_results"]),
            }
        )
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@report_app.command("finalize")
def report_finalize(
    report: Path = typer.Option(..., "--report", exists=True, readable=True, help="Authored Markdown referee report."),
    html_output: Optional[Path] = typer.Option(None, "--html", help="Standalone narrative HTML output."),
    explorer: Optional[Path] = typer.Option(None, "--explorer", help="Katz issue explorer HTML output."),
    apply: bool = typer.Option(False, "--apply", help="Generate the validated artifacts; preview by default."),
    expect_plan: Optional[str] = typer.Option(None, "--expect-plan", help="Reject apply if ledger or report changed since preview."),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Validate and compile final referee-report artifacts as one bounded operation."""
    try:
        resolved, dest, _, _, _ = load_version(commit)
        source_text = report.read_text(encoding="utf-8")
        source = _report_source(source_text, report)
        narrative_path = html_output or report.with_suffix(".html")
        explorer_path = explorer or report.parent / "issues.html"
        resolved_outputs = {narrative_path.resolve(), explorer_path.resolve()}
        if len(resolved_outputs) != 2:
            raise KatzError(
                "Narrative HTML and issue explorer must use different paths",
                "validation_error",
                {"path": str(narrative_path)},
            )
        if report.resolve() in resolved_outputs:
            raise KatzError(
                "Final HTML outputs must not overwrite the Markdown report source",
                "validation_error",
                {"report": str(report)},
            )
        report_module = _load_report_module()
        explorer_logo = (explorer_path.parent / "logo.png").resolve()
        if narrative_path.resolve() == explorer_logo:
            raise KatzError(
                "Narrative HTML path conflicts with the issue explorer logo asset",
                "validation_error",
                {"html": str(narrative_path), "asset": str(explorer_logo)},
            )
        data = report_module.collect_report_data(resolved)
        snapshot = _finalization_snapshot(dest, data)
        ledger_hash = _ledger_hash(dest)
        report_hash = sha256_file(report)
        plan_payload = {
            "schema_version": 1,
            "commit": resolved,
            "ledger_sha256": ledger_hash,
            "report_sha256": report_hash,
            "html": str(narrative_path.resolve()),
            "explorer": str(explorer_path.resolve()),
        }
        plan_hash = hashlib.sha256(
            json.dumps(plan_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if expect_plan is not None and expect_plan != plan_hash:
            raise KatzError(
                "Finalization plan is stale; ledger, report, or output paths changed after preview",
                "stale_finalization_plan",
                {"expected": expect_plan, "actual": plan_hash},
            )

        manifest_path = dest / "finalizations" / f"{plan_hash}.json"
        if apply and manifest_path.is_file():
            manifest = read_json(manifest_path)
            artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
            unchanged = all(
                isinstance(item, dict)
                and Path(str(item.get("path"))).is_file()
                and sha256_file(Path(str(item["path"]))) == item.get("sha256")
                for item in artifacts
            )
            if unchanged:
                emit_json({**manifest, "mode": "replayed", "replayed": True, "manifest": str(manifest_path)})
                return

        warnings_out: list[dict[str, Any]] = []
        if not snapshot["complete"]:
            warnings_out.append({
                "code": "incomplete_review",
                "message": "Final artifacts will carry a prominent incomplete-review banner.",
                "reasons": snapshot["incomplete_reasons"],
            })
        artifacts_plan = [
            {"kind": "report_html", "path": str(narrative_path)},
            {"kind": "issue_explorer", "path": str(explorer_path)},
        ]
        if not apply:
            emit_json(
                {
                    "schema_version": 1,
                    "mode": "preview",
                    "will_mutate": False,
                    "complete": snapshot["complete"],
                    "coverage": snapshot["coverage"],
                    "issues": snapshot["issues"],
                    "validation": snapshot["validation"],
                    "provenance": snapshot["provenance"],
                    "report": {"path": str(report), "sha256": report_hash, "title": source["title"]},
                    "artifacts": artifacts_plan,
                    "plan_hash": plan_hash,
                    "next_actions": [
                        _agent_action(
                            "apply_report_finalization",
                            "Generate exactly the validated final report artifacts",
                            [
                                "katz", "report", "finalize", "--report", str(report),
                                "--html", str(narrative_path), "--explorer", str(explorer_path),
                                "--expect-plan", plan_hash, "--apply",
                            ],
                            mutates_state=True,
                        )
                    ],
                },
                warnings=warnings_out,
            )
            return

        narrative_bytes = _standalone_report_html(source, snapshot, resolved).encode("utf-8")
        explorer_data = dict(data)
        explorer_data["referee_report"] = source_text
        explorer_bytes = report_module.build_html(**explorer_data).encode("utf-8")
        artifact_records = [
            {
                "kind": "report_html",
                "path": str(narrative_path),
                "sha256": f"sha256:{hashlib.sha256(narrative_bytes).hexdigest()}",
                "bytes": len(narrative_bytes),
            },
            {
                "kind": "issue_explorer",
                "path": str(explorer_path),
                "sha256": f"sha256:{hashlib.sha256(explorer_bytes).hexdigest()}",
                "bytes": len(explorer_bytes),
            },
        ]
        manifest = {
            "schema_version": 1,
            "mode": "applied",
            "replayed": False,
            "plan_hash": plan_hash,
            "commit": resolved,
            "ledger_sha256": ledger_hash,
            "report_sha256": report_hash,
            "timestamp": now_utc(),
            "complete": snapshot["complete"],
            "incomplete_reasons": snapshot["incomplete_reasons"],
            "coverage": snapshot["coverage"],
            "issues": snapshot["issues"],
            "validation": snapshot["validation"],
            "provenance": snapshot["provenance"],
            "artifacts": artifact_records,
        }
        outputs: dict[Path, bytes] = {
            narrative_path: narrative_bytes,
            explorer_path: explorer_bytes,
            manifest_path: (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        }
        logo_path = Path(report_module.LOGO_PATH)
        if logo_path.is_file():
            outputs[explorer_path.parent / "logo.png"] = logo_path.read_bytes()
        _write_outputs_transactionally(outputs)
        emit_json({**manifest, "manifest": str(manifest_path)}, warnings=warnings_out)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except OSError as exc:
        fail(str(exc), "artifact_write_error", {"report": str(report)})
    except Exception as exc:
        fail(str(exc), "report_generation_error", {"report": str(report)})
