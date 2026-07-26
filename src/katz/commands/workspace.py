"""`katz workspace` commands: standalone review workspaces."""
from __future__ import annotations

import shutil
import subprocess
import typer
from pathlib import Path
from typing import Any, List, Optional

from ..errors import KatzError, emit_json, fail
from ..manuscript import _provenance_sidecar_path
from ..storage import KATZ_DIR
from .paper import _register_manuscript


workspace_app = typer.Typer(help="Create standalone review workspaces.")


@workspace_app.command("new")
def workspace_new(
    directory: Path = typer.Argument(..., help="Workspace directory to create (must not already exist)."),
    canonical: Path = typer.Option(..., "--canonical", exists=True, file_okay=True, dir_okay=False, readable=True),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        help="Original source file or URL, recorded as provenance. Local files are copied into the workspace.",
    ),
    source_format: str = typer.Option("markdown", "--source-format"),
    source_method: str = typer.Option("workspace-new", "--source-method"),
) -> None:
    """Create a standalone review workspace around a prepared canonical manuscript.

    Creates the directory, initializes git, copies the canonical Markdown (and a
    local --source file when given), commits the bundle, initializes .katz, and
    registers the commit as the first active version. Katz does not fetch, OCR,
    or convert the source; prepare it first with `katz paper prepare`.
    """
    import os

    try:
        if canonical.suffix.lower() not in {".md", ".markdown"}:
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
        finally:
            os.chdir(previous_cwd)

        emit_json({
            "workspace": str(workspace),
            "git_initialized": True,
            "canonical": str(workspace_canonical.relative_to(workspace)),
            "source": {"root": source_root, "uri": source_uri},
            "registration": registration,
            "next": f"cd {workspace} && katz next",
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
