"""`katz report` commands: HTML review report generation."""
from __future__ import annotations

import importlib.util
import warnings
from pathlib import Path
from typing import Any, Optional

import typer

from ..assets import REPORT_SCRIPT
from ..errors import KatzError, emit_json, fail


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
