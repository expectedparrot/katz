"""`katz report` commands: HTML review report generation."""
from __future__ import annotations

import importlib.util
import typer
import warnings
from pathlib import Path
from typing import Any, List, Optional

from ..assets import REPORT_SCRIPT
from ..errors import KatzError, emit_json, fail
from ..issues import _full_issue_record
from ..storage import load_version, read_json, sha256_file


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


@report_app.command("generate")
def report_generate(
    output: Path = typer.Option(Path(".katz/review.html"), "--output", "-o"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Generate the HTML review report."""
    try:
        resolved, dest, version, pmap, canonical = load_version(commit)
        report_module = _load_report_module()

        issues = []
        issues_dir = dest / "issues"
        if issues_dir.is_dir():
            for issue_dir in sorted(issues_dir.iterdir()):
                if issue_dir.is_dir() and (issue_dir / "issue.json").exists():
                    issues.append(_full_issue_record(issue_dir, pmap))

        eval_criteria = report_module.load_eval_criteria(resolved)
        eval_results_records = report_module.load_eval_results(resolved)
        referee_report = report_module.load_referee_report(resolved)
        images = report_module.load_images_as_data_uris(resolved)
        source = version.get("source", {})
        if not isinstance(source, dict):
            source = {}
        audited_run = None
        if (dest / "runs").is_dir():
            for path in reversed(sorted((dest / "runs").glob("*.json"))):
                candidate = read_json(path)
                if "audit" in candidate:
                    audited_run = candidate
                    break
        status = {
            "commit": resolved,
            "source_format": source.get("format"),
            "source_root": source.get("root") or "paper",
            "source_uri": source.get("uri"),
            "canonical": version.get("canonical"),
            "sections": len(pmap.sections),
            "sentences": len(pmap.sentences),
            "figures": len(pmap.figures),
            "valid": canonical.exists() and sha256_file(canonical) == version.get("checksum") == pmap.header.get("checksum"),
            "review_audit": audited_run.get("audit") if audited_run else None,
        }
        html = report_module.build_html(
            status,
            pmap.sections,
            issues,
            canonical.read_text(encoding="utf-8"),
            eval_criteria,
            eval_results_records,
            referee_report,
            images,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        report_module.write_report_assets(output)
        emit_json(
            {
                "generated": True,
                "path": str(output),
                "commit": resolved,
                "issues": len(issues),
                "sections": len(pmap.sections),
                "evaluations": len(eval_results_records),
            }
        )
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
