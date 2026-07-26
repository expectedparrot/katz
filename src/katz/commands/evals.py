"""`katz eval` commands: criteria catalogs and recorded responses."""
from __future__ import annotations

import shutil
import typer
from pathlib import Path
from typing import Any, List, Optional

from ..assets import CATALOG_DIR
from ..definitions import VALID_GRADES, _load_collection, _parse_eval, _slugify
from ..errors import KatzError, emit_json, fail
from ..storage import (
    ensure_initialized,
    katz_root,
    load_version,
    now_utc,
    read_json,
    write_json,
)


eval_app = typer.Typer(help="Manage evaluation criteria and responses.")


@eval_app.command("init-catalog")
def eval_init_catalog(
    preset: str = typer.Option("default", "--preset"),
) -> None:
    """Populate the eval catalog (.katz/evals/) from a preset. Skips existing."""
    try:
        names = _load_collection("evals", preset)
        ensure_initialized()
        catalog_dir = katz_root() / "evals"
        catalog_dir.mkdir(parents=True, exist_ok=True)

        added = []
        skipped = []
        for slug in names:
            src_path = CATALOG_DIR / "evals" / f"{slug}.md"
            if not src_path.exists():
                raise KatzError(f"Eval '{slug}' listed in collection but file not found", "not_found", {"name": slug})
            out_path = catalog_dir / f"{slug}.md"
            if out_path.exists():
                skipped.append(slug)
                continue
            content = src_path.read_text(encoding="utf-8")
            out_path.write_text(content, encoding="utf-8")
            parsed = _parse_eval(content)
            added.append({"name": slug, "category": parsed["category"]})

        emit_json({"preset": preset, "added": added, "skipped": skipped})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("catalog")
def eval_catalog(
    category: Optional[str] = typer.Option(None, "--category"),
) -> None:
    """List available eval criteria in the catalog (.katz/evals/)."""
    try:
        ensure_initialized()
        catalog_dir = katz_root() / "evals"
        results = []
        if catalog_dir.is_dir():
            for f in sorted(catalog_dir.glob("*.md")):
                content = f.read_text(encoding="utf-8")
                parsed = _parse_eval(content)
                if category is not None and parsed["category"] != category:
                    continue
                results.append({
                    "name": f.stem,
                    "title": parsed["title"],
                    "category": parsed["category"],
                    "scope": parsed["scope"],
                })
        emit_json(results)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("catalog-show")
def eval_catalog_show(name: str) -> None:
    """Show an eval criterion from the catalog."""
    try:
        ensure_initialized()
        path = katz_root() / "evals" / f"{name}.md"
        if not path.exists():
            raise KatzError(f"Eval '{name}' not in catalog", "not_found", {"name": name})
        content = path.read_text(encoding="utf-8")
        parsed = _parse_eval(content)
        emit_json({
            "name": name,
            "category": parsed["category"],
            "scope": parsed["scope"],
            "title": parsed["title"],
            "body": parsed["body"],
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("add")
def eval_add(
    name: str = typer.Option(..., "--name"),
    question: Optional[str] = typer.Option(None, "--question"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    category: Optional[str] = typer.Option(None, "--category"),
    file: Optional[Path] = typer.Option(None, "--file", exists=True, file_okay=True, dir_okay=False, readable=True),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Add an eval criterion to the current version from a question string or file."""
    try:
        if file is None and question is None:
            raise KatzError("Provide --question or --file", "validation_error")
        if file is not None and question is not None:
            raise KatzError("Provide --question or --file, not both", "validation_error")

        resolved, dest, _, _, _ = load_version(commit)
        evals_dir = dest / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)

        slug = _slugify(name)

        if file is not None:
            content = file.read_text(encoding="utf-8")
            parsed = _parse_eval(content)
            if parsed["title"] is None:
                raise KatzError("Eval file must have a markdown heading (# Title)", "validation_error")
        else:
            title = name.replace("_", " ").replace("-", " ").title()
            fm_lines = []
            if scope:
                fm_lines.append(f"scope: {scope}")
            if category:
                fm_lines.append(f"category: {category}")
            fm = f"---\n{chr(10).join(fm_lines)}\n---\n" if fm_lines else ""
            content = f"{fm}# {title}\n\n{question}\n"

        out_path = evals_dir / f"{slug}.md"
        if out_path.exists():
            raise KatzError(f"Eval '{slug}' already exists", "validation_error", {"name": slug})
        out_path.write_text(content, encoding="utf-8")
        parsed = _parse_eval(content)
        emit_json({"name": slug, "category": parsed["category"], "scope": parsed["scope"], "path": str(out_path)})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("enable")
def eval_enable(
    name: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Enable a catalog eval criterion for the active version."""
    try:
        ensure_initialized()
        catalog_path = katz_root() / "evals" / f"{name}.md"
        if not catalog_path.exists():
            raise KatzError(f"Eval '{name}' not in catalog", "not_found", {"name": name})
        _, dest, _, _, _ = load_version(commit)
        evals_dir = dest / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)
        out_path = evals_dir / f"{name}.md"
        if out_path.exists():
            raise KatzError(f"Eval '{name}' is already enabled", "validation_error", {"name": name})
        shutil.copyfile(catalog_path, out_path)
        emit_json({"enabled": name})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("list")
def eval_list(
    category: Optional[str] = typer.Option(None, "--category"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """List enabled eval criteria for the active version."""
    try:
        _, dest, _, _, _ = load_version(commit)
        evals_dir = dest / "evals"
        results = []
        if evals_dir.is_dir():
            for f in sorted(evals_dir.glob("*.md")):
                content = f.read_text(encoding="utf-8")
                parsed = _parse_eval(content)
                if category is not None and parsed["category"] != category:
                    continue
                results.append({
                    "name": f.stem,
                    "title": parsed["title"],
                    "category": parsed["category"],
                    "scope": parsed["scope"],
                })
        emit_json(results)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("show")
def eval_show(
    name: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Show an enabled eval criterion's content."""
    try:
        _, dest, _, _, _ = load_version(commit)
        path = dest / "evals" / f"{name}.md"
        if not path.exists():
            raise KatzError(f"Eval '{name}' is not enabled", "not_found", {"name": name})
        content = path.read_text(encoding="utf-8")
        parsed = _parse_eval(content)
        emit_json({
            "name": name,
            "category": parsed["category"],
            "scope": parsed["scope"],
            "title": parsed["title"],
            "body": parsed["body"],
            "content": content,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("remove")
def eval_remove(
    name: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Remove an enabled eval criterion."""
    try:
        _, dest, _, _, _ = load_version(commit)
        path = dest / "evals" / f"{name}.md"
        if not path.exists():
            raise KatzError(f"Eval '{name}' is not enabled", "not_found", {"name": name})
        path.unlink()
        emit_json({"removed": name})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("respond")
def eval_respond(
    name: str = typer.Option(..., "--name"),
    text: str = typer.Option(..., "--text"),
    grade: Optional[str] = typer.Option(None, "--grade"),
    suggestion: Optional[str] = typer.Option(None, "--suggestion"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Record a narrative response, optional grade, and optional suggestion for an eval criterion."""
    try:
        if grade is not None and grade not in VALID_GRADES:
            raise KatzError(
                f"Invalid grade: '{grade}'",
                "validation_error",
                {"grade": grade, "valid": sorted(VALID_GRADES)},
            )
        _, dest, _, _, _ = load_version(commit)
        # Verify the criterion is enabled
        eval_path = dest / "evals" / f"{name}.md"
        if not eval_path.exists():
            raise KatzError(f"Eval '{name}' is not enabled", "not_found", {"name": name})
        parsed = _parse_eval(eval_path.read_text(encoding="utf-8"))

        results_dir = dest / "eval_results"
        results_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "criterion": name,
            "category": parsed["category"],
            "scope": parsed["scope"],
            "response": text,
            "grade": grade,
            "suggestion": suggestion,
            "timestamp": now_utc(),
        }
        out_path = results_dir / f"{name}.json"
        write_json(out_path, record)
        emit_json(record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@eval_app.command("results")
def eval_results(
    category: Optional[str] = typer.Option(None, "--category"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """List all eval responses for the active version."""
    try:
        _, dest, _, _, _ = load_version(commit)
        results_dir = dest / "eval_results"
        results = []
        if results_dir.is_dir():
            for f in sorted(results_dir.glob("*.json")):
                record = read_json(f)
                if category is not None and record.get("category") != category:
                    continue
                results.append(record)
        emit_json(results)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
