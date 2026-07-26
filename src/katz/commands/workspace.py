"""`katz workspace` commands: standalone review workspaces."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

import typer

from ..edsl_bridge import SPOTTER_RECOMMENDED_MAX_TOKENS
from ..errors import KatzError, capture_envelopes, emit_json, fail
from ..manuscript import _provenance_sidecar_path, ventilate_markdown
from ..storage import KATZ_DIR
from .paper import _register_manuscript, paper_auto_chunk, paper_prepare


workspace_app = typer.Typer(help="Create standalone review workspaces.")

_PREPARE_SUFFIXES = {".pdf", ".tex", ".latex"}
_MARKDOWN_SUFFIXES = {".md", ".markdown"}


def _compose_step(
    steps: dict[str, Any],
    step: str,
    func: Callable[..., None],
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Run an existing command in-process, recording its envelope as a step.

    Returns the command's data on success and None on failure; a failed step
    is recorded with its error code so the composite response shows exactly
    where automation stopped.
    """
    try:
        with capture_envelopes() as envelopes:
            func(**kwargs)
    except KatzError as exc:
        steps[step] = {"status": "error", "code": exc.code, "message": exc.message}
        return None
    envelope = envelopes[-1] if envelopes else {"status": "ok", "data": {}}
    steps[step] = {"status": envelope.get("status", "ok"), "data": envelope.get("data", {})}
    if envelope.get("warnings"):
        steps[step]["warnings"] = envelope["warnings"]
    return envelope.get("data", {})


def _prepare_from_source(
    from_source: Path,
    staging: Path,
    backend: str,
    steps: dict[str, Any],
) -> Path:
    """Produce an inspected-ready ventilated Markdown bundle in staging.

    PDF and LaTeX sources go through `paper prepare`; Markdown is ventilated
    directly. Raises KatzError when preparation fails, since nothing useful
    can be built without a canonical manuscript.
    """
    suffix = from_source.suffix.lower()
    if suffix in _PREPARE_SUFFIXES:
        prepared = staging / f"{from_source.stem}.md"
        result = _compose_step(
            steps, "prepare", paper_prepare,
            source=from_source, output=prepared, backend=backend, allow_lossy=False,
        )
        if result is None:
            raise KatzError(
                f"Source preparation failed: {steps['prepare']['message']}",
                steps["prepare"]["code"],
                {"failed_step": "prepare", "source": str(from_source)},
            )
    elif suffix in _MARKDOWN_SUFFIXES:
        prepared = staging / from_source.name
        shutil.copyfile(from_source, prepared)
        input_sidecar = _provenance_sidecar_path(from_source)
        if input_sidecar.is_file():
            shutil.copyfile(input_sidecar, _provenance_sidecar_path(prepared))
        for asset in from_source.parent.iterdir():
            if asset.is_file() and asset.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
                shutil.copyfile(asset, staging / asset.name)
    else:
        raise KatzError(
            "--from accepts PDF, LaTeX, or Markdown sources",
            "validation_error",
            {"source": str(from_source), "supported": sorted(_PREPARE_SUFFIXES | _MARKDOWN_SUFFIXES)},
        )

    ventilated = prepared.with_name(f"{prepared.stem}_ventilated.md")
    text = prepared.read_text(encoding="utf-8")
    ventilated_text, lines_changed = ventilate_markdown(text)
    ventilated.write_text(ventilated_text, encoding="utf-8")
    prepared_sidecar = _provenance_sidecar_path(prepared)
    if prepared_sidecar.is_file():
        shutil.copyfile(prepared_sidecar, _provenance_sidecar_path(ventilated))
    steps["ventilate"] = {
        "status": "ok",
        "data": {"output": ventilated.name, "lines_changed": lines_changed},
    }
    return ventilated


def _inferred_source_fields(from_source: Path, backend: str) -> tuple[str, str]:
    suffix = from_source.suffix.lower()
    if suffix == ".pdf":
        return "pdf", f"paper2md-{backend}"
    if suffix in {".tex", ".latex"}:
        return "latex", "katz-paper-prepare"
    return "markdown", "ventilated"


@workspace_app.command("new")
def workspace_new(
    directory: Path = typer.Argument(..., help="Workspace directory to create (must not already exist)."),
    canonical: Optional[Path] = typer.Option(
        None, "--canonical", exists=True, file_okay=True, dir_okay=False, readable=True,
        help="Already-prepared canonical Markdown to copy in as-is.",
    ),
    from_source: Optional[Path] = typer.Option(
        None, "--from", exists=True, file_okay=True, dir_okay=False, readable=True,
        help="PDF, LaTeX, or Markdown source: prepare, ventilate, register, and package review jobs in one step.",
    ),
    backend: str = typer.Option("auto", "--backend", help="paper2md backend for --from PDF sources."),
    model: Optional[str] = typer.Option(
        None, "--model",
        help="With --from: also write models.ep for this model so the ep run command is ready.",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        help="Original source file or URL, recorded as provenance. Local files are copied into the workspace.",
    ),
    source_format: str = typer.Option("markdown", "--source-format"),
    source_method: str = typer.Option("workspace-new", "--source-method"),
) -> None:
    """Create a standalone review workspace and, with --from, package the review.

    With --canonical, copies an already-prepared Markdown manuscript into a new
    Git workspace, commits it, and registers it. With --from, additionally runs
    the local pipeline first (PDF/LaTeX conversion, ventilation) and afterward
    maps sections, enables the recommended spotters, and builds jobs.ep — then
    stops. Model execution stays outside Katz: inspect the prepared manuscript
    against its source, then authorize the suggested `ep run` yourself.
    """
    import os

    try:
        if (canonical is None) == (from_source is None):
            raise KatzError(
                "Provide exactly one of --canonical or --from",
                "validation_error",
            )
        if canonical is not None and canonical.suffix.lower() not in _MARKDOWN_SUFFIXES:
            raise KatzError(
                "The canonical manuscript must be Markdown; prepare PDF or LaTeX sources first",
                "validation_error",
                {"canonical": str(canonical), "next_action": ["katz", "paper", "prepare", str(canonical)]},
            )
        if directory.exists():
            raise KatzError(
                "Workspace directory already exists",
                "validation_error",
                {"directory": str(directory)},
            )

        steps: dict[str, Any] = {}
        with tempfile.TemporaryDirectory(prefix="katz-workspace-") as staging_dir:
            if from_source is not None:
                canonical = _prepare_from_source(from_source, Path(staging_dir), backend, steps)
                if source is None:
                    source = str(from_source)
                if source_format == "markdown" and source_method == "workspace-new":
                    source_format, source_method = _inferred_source_fields(from_source, backend)
            assert canonical is not None

            workspace = directory.resolve()
            workspace.mkdir(parents=True)

            def run_git(*args: str) -> None:
                completed = subprocess.run(
                    ["git", *args],
                    cwd=workspace,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if completed.returncode != 0:
                    raise KatzError(
                        "git command failed while creating the workspace",
                        "git_error",
                        {"command": ["git", *args], "stderr": completed.stderr.strip()[-1000:]},
                    )

            run_git("init")
            identity_probe = subprocess.run(
                ["git", "config", "user.email"],
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if identity_probe.returncode != 0 or not identity_probe.stdout.strip():
                run_git("config", "user.name", "Katz Workspace")
                run_git("config", "user.email", "katz-workspace@localhost")

            paper_dir = workspace / "paper"
            paper_dir.mkdir()
            workspace_canonical = paper_dir / canonical.name
            shutil.copyfile(canonical, workspace_canonical)
            sidecar = _provenance_sidecar_path(canonical)
            if sidecar.is_file():
                shutil.copyfile(sidecar, _provenance_sidecar_path(workspace_canonical))
            image_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
            for asset in canonical.parent.iterdir():
                if asset.is_file() and asset.suffix.lower() in image_exts:
                    shutil.copyfile(asset, paper_dir / asset.name)

            source_uri: Optional[str] = None
            source_root: Optional[str] = None
            if source is not None:
                source_path = Path(source)
                if source_path.is_file():
                    copied_source = workspace / "source" / source_path.name
                    copied_source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_path, copied_source)
                    source_root = str(copied_source.relative_to(workspace))
                else:
                    source_uri = source

            run_git("add", "-A")
            run_git("commit", "-m", "Add canonical manuscript bundle for Katz review")

            previous_cwd = Path.cwd()
            os.chdir(workspace)
            try:
                (workspace / KATZ_DIR / "versions").mkdir(parents=True, exist_ok=True)
                registration = _register_manuscript(
                    workspace_canonical,
                    source_root=source_root,
                    source_uri=source_uri,
                    source_format=source_format,
                    source_method=source_method,
                )

                jobs_ready = False
                if from_source is not None:
                    # Local, deterministic review setup. Each step is
                    # best-effort: a failure is recorded and dependents are
                    # skipped, but the created workspace is always reported.
                    from .spotter import spotter_enable, spotter_init_catalog, spotter_jobs, spotter_models

                    _compose_step(steps, "auto_chunk", paper_auto_chunk, commit=None)
                    catalog = _compose_step(steps, "spotter_catalog", spotter_init_catalog, preset="default")
                    enabled = None
                    if catalog is not None:
                        enabled = _compose_step(
                            steps, "spotter_enable", spotter_enable,
                            name=None, commit=None, recommended=True, all_spotters=False,
                        )
                    if enabled is not None:
                        jobs = _compose_step(
                            steps, "spotter_jobs", spotter_jobs,
                            output=Path("jobs.ep"), section=None, spotters=None,
                            pilot=None, from_failures=None, commit=None,
                        )
                        jobs_ready = jobs is not None
                    if model is not None:
                        _compose_step(
                            steps, "spotter_models", spotter_models,
                            models=[model], service=None,
                            max_tokens=SPOTTER_RECOMMENDED_MAX_TOKENS,
                            reasoning_effort=None, output=Path("models.ep"),
                        )
            finally:
                os.chdir(previous_cwd)

        next_steps: list[str] = []
        if from_source is not None:
            next_steps.append(
                f"Inspect {workspace / 'paper' / workspace_canonical.name} against "
                f"{from_source} before any model reviews it (reading order, tables, equations, figures)."
            )
            if jobs_ready:
                model_list = "models.ep" if model is not None else "<models.ep from `katz spotter models`>"
                next_steps.append(
                    f"cd {workspace} && ep run jobs.ep --model_list {model_list} --output results.ep"
                )
                next_steps.append(
                    "katz ingest results.ep --apply && katz report generate --output review.html"
                )
        else:
            next_steps.append(f"cd {workspace} && katz next")

        emit_json({
            "workspace": str(workspace),
            "git_initialized": True,
            "canonical": str(workspace_canonical.relative_to(workspace)),
            "source": {"root": source_root, "uri": source_uri},
            "registration": registration,
            "steps": steps,
            "jobs_ready": jobs_ready if from_source is not None else None,
            "execution_boundary": (
                "Katz packaged the review but never runs models; the `ep run` step is yours to authorize."
                if from_source is not None else None
            ),
        }, next_steps=next_steps)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
