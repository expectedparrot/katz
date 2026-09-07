"""`katz results` commands: audit, sample, and failure inspection."""
from __future__ import annotations

import typer
from pathlib import Path
from typing import Any, List, Optional

from ..edsl_bridge import _audit_spotter_results, _resolve_audit_jobs
from ..errors import KatzError, emit_json, fail
from ..storage import _latest_packaged_run, load_version, record_run


results_app = typer.Typer(help="Audit and inspect EDSL review Results.")


@results_app.command("audit")
def results_audit(
    results_path: Path = typer.Argument(..., exists=True, readable=True),
    jobs: Optional[Path] = typer.Option(None, "--jobs", exists=True, readable=True),
    commit: Optional[str] = typer.Option(None, "--commit"),
    models: Optional[Path] = None,
) -> None:
    """Audit coverage and structured-answer validity before ingestion."""
    try:
        resolved, dest, _, _, _ = load_version(commit)
        packaged = _latest_packaged_run(dest, results_path)
        resolved_jobs = _resolve_audit_jobs(dest, results_path, jobs)
        audit = _audit_spotter_results(results_path, resolved_jobs, models, expected_commit=resolved)
        audit.pop("_rows", None)
        audit["ingestion_allowed"] = audit["complete"]
        if resolved_jobs is None:
            audit["blocker"] = {
                "code": "originating_jobs_required",
                "message": "Pass --jobs so Katz can prove scenario coverage.",
            }
        record_run(
            dest,
            "spotter_pilot" if packaged and packaged.get("pilot") else "spotter",
            "audited" if audit["complete"] else "invalid",
            results_path=str(results_path.resolve()),
            jobs_path=str(resolved_jobs.resolve()) if resolved_jobs else None,
            audit=audit,
        )
        emit_json(audit)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"results": str(results_path)})


@results_app.command("sample")
def results_sample(
    results_path: Path = typer.Argument(..., exists=True, readable=True),
    valid: int = typer.Option(5, "--valid", min=1),
) -> None:
    """Return a compact sample of valid structured spotter answers."""
    try:
        audit = _audit_spotter_results(results_path)
        rows = [
            {key: value for key, value in row.items() if key != "valid"}
            for row in audit.pop("_rows") if row["valid"]
        ][:valid]
        emit_json({"results": str(results_path), "count": len(rows), "rows": rows})
    except Exception as exc:
        fail(str(exc), "edsl_error", {"results": str(results_path)})


@results_app.command("failures")
def results_failures(
    results_path: Path = typer.Argument(..., exists=True, readable=True),
    limit: int = typer.Option(20, "--limit", min=1),
    jobs: Optional[Path] = typer.Option(None, "--jobs", exists=True, readable=True),
    commit: Optional[str] = typer.Option(None, "--commit"),
    models: Optional[Path] = None,
) -> None:
    """Return compact null, schema-invalid, and model-exception rows.

    Pass --jobs (as `results audit` accepts) to also report scenarios that never
    returned any row, so the full set of things to re-run is visible at once.
    """
    try:
        resolved, dest, _, _, _ = load_version(commit)
        resolved_jobs = _resolve_audit_jobs(dest, results_path, jobs)
        audit = _audit_spotter_results(results_path, resolved_jobs, models, expected_commit=resolved)
        rows = [
            {key: value for key, value in row.items() if key != "answer"}
            for row in audit.pop("_rows") if not row["valid"]
        ][:limit]
        emit_json({
            "results": str(results_path),
            "jobs": str(resolved_jobs) if resolved_jobs is not None else None,
            "count": len(rows),
            "rows": rows,
            "missing_scenarios": audit.get("missing_scenarios"),
            "failed_observations": audit.get("failed_observations"),
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"results": str(results_path)})


@results_app.command("plan")
def results_plan(
    jobs: Path = typer.Option(..., "--jobs", exists=True, dir_okay=False),
    models: Optional[Path] = typer.Option(None, "--models", exists=True, dir_okay=False),
    iterations: int = typer.Option(1, "--iterations", min=1),
) -> None:
    """Freeze expected models and repetitions before external ep run."""
    from ..edsl_bridge import register_execution_plan
    try:
        emit_json(register_execution_plan(jobs, models, iterations))
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@results_app.command("retry")
def results_retry(
    results: Path = typer.Argument(..., exists=True),
    jobs: Path = typer.Option(..., "--jobs", exists=True),
    output_dir: Path = typer.Option(..., "--output-dir"),
    models: Optional[Path] = None,
) -> None:
    """Export exact failed observations; preserves native Jobs and retry provenance."""
    from ..edsl_bridge import build_retry_packages
    try:
        emit_json(build_retry_packages(results, jobs, output_dir, models))
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@results_app.command("merge")
def results_merge(
    inputs: List[Path] = typer.Argument(..., exists=True),
    jobs: Path = typer.Option(..., "--jobs", exists=True),
    output: Path = typer.Option(..., "--output"),
    retry_plan: List[Path] = typer.Option([], "--retry-plan", exists=True),
    models: Optional[Path] = None,
) -> None:
    """Write new merged Results with explicit selection provenance; never modifies inputs."""
    from ..edsl_bridge import merge_retry_results
    try:
        emit_json(merge_retry_results(inputs, jobs, output, retry_plan, models))
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
