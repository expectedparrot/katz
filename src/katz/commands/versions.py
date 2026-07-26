"""`katz version` commands: build info, list, checkout, diff."""
from __future__ import annotations

import typer
from pathlib import Path
from typing import Any, List, Optional

from ..assets import AGENT_API_VERSION, PACKAGE_DIR
from ..errors import KatzError, emit_json, fail
from ..issues import _issue_count
from ..storage import (
    PaperMap,
    active_commit,
    active_version_path,
    ensure_initialized,
    katz_root,
    load_version,
    read_json,
    resolve_commit,
)
from katz import __version__


version_app = typer.Typer(
    help="Inspect and switch registered manuscript versions.",
    invoke_without_command=True,
)


@version_app.callback()
def version_root(ctx: typer.Context) -> None:
    """Report the installed Katz build, or manage registered versions via subcommands."""
    if ctx.invoked_subcommand is not None:
        return
    emit_json({
        "version": __version__,
        "package_path": str(PACKAGE_DIR),
        "agent_api_version": AGENT_API_VERSION,
        "required_capabilities": [
            "agent_next_actions",
            "paper_prepare",
            "ventilated_candidate_preference",
            "committed_canonical_guard",
            "latex_dependency_expansion",
            "latex_structural_audit",
            "latex_section_provenance",
            "results_audit",
            "fail_closed_spotter_ingestion",
            "issue_clusters",
            "issue_edit_events",
            "issue_carry_forward",
            "agent_instructions_write",
            "version_management",
            "repair",
            "workspace_new",
            "multi_model_agreement",
        ],
    })


@version_app.command("list")
def version_list() -> None:
    """List registered manuscript versions, oldest first."""
    try:
        ensure_initialized()
        try:
            active = active_commit()
        except KatzError:
            active = None
        records: list[dict[str, Any]] = []
        versions_dir = katz_root() / "versions"
        for path in sorted(versions_dir.iterdir()) if versions_dir.is_dir() else []:
            if not path.is_dir() or not (path / "version.json").exists():
                continue
            version = read_json(path / "version.json")
            records.append({
                "commit": path.name,
                "registered_at": version.get("registered_at"),
                "source_format": (version.get("source") or {}).get("format"),
                "issue_count": _issue_count(path),
                "current": path.name == active,
            })
        records.sort(key=lambda item: (item.get("registered_at") or "", item["commit"]))
        emit_json(records)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@version_app.command("checkout")
def version_checkout(sha: str = typer.Argument(..., help="Registered commit SHA or unambiguous prefix.")) -> None:
    """Point ACTIVE_VERSION at another registered commit."""
    try:
        resolved = resolve_commit(sha)
        try:
            previous = active_commit()
        except KatzError:
            previous = None
        active_version_path().write_text(resolved + "\n", encoding="utf-8")
        emit_json({"checked_out": True, "commit": resolved, "previous": previous})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@version_app.command("diff")
def version_diff(
    sha_a: str = typer.Argument(..., help="From version: commit SHA or unambiguous prefix."),
    sha_b: str = typer.Argument(..., help="To version: commit SHA or unambiguous prefix."),
    limit: int = typer.Option(200, "--limit", min=1, help="Maximum change records to return."),
) -> None:
    """Section-aware diff between two registered canonical manuscripts."""
    import difflib

    try:
        from_commit, _, _, from_pmap, from_canonical = load_version(sha_a)
        to_commit, _, _, to_pmap, to_canonical = load_version(sha_b)
        from_lines = from_canonical.read_text(encoding="utf-8").splitlines()
        to_lines = to_canonical.read_text(encoding="utf-8").splitlines()

        def section_for_line(pmap: PaperMap, line_number: int) -> str | None:
            for section in pmap.sections:
                if not isinstance(section, dict):
                    continue
                if section.get("line_start", 0) <= line_number <= section.get("line_end", -1):
                    return section.get("id")
            return None

        changes: list[dict[str, Any]] = []
        total_changes = 0
        modified_sections: set[str] = set()
        matcher = difflib.SequenceMatcher(a=from_lines, b=to_lines, autojunk=False)
        for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
            if tag == "equal":
                continue
            span = max(a_end - a_start, b_end - b_start)
            for offset in range(span):
                a_line = a_start + offset + 1 if a_start + offset < a_end else None
                b_line = b_start + offset + 1 if b_start + offset < b_end else None
                if a_line is not None and b_line is not None:
                    change_type = "changed"
                elif b_line is not None:
                    change_type = "added"
                else:
                    change_type = "removed"
                section = (
                    section_for_line(to_pmap, b_line)
                    if b_line is not None
                    else section_for_line(from_pmap, a_line)
                )
                if section:
                    modified_sections.add(section)
                total_changes += 1
                if len(changes) >= limit:
                    continue
                change: dict[str, Any] = {"type": change_type, "section": section}
                if a_line is not None:
                    change["from_line"] = a_line
                    change["before"] = from_lines[a_line - 1]
                if b_line is not None:
                    change["to_line"] = b_line
                    change["after"] = to_lines[b_line - 1]
                changes.append(change)

        to_section_ids = [
            section.get("id") for section in to_pmap.sections
            if isinstance(section, dict) and section.get("id")
        ]
        emit_json({
            "from": from_commit,
            "to": to_commit,
            "identical": not total_changes,
            "modified_sections": sorted(modified_sections),
            "unchanged_sections": [
                section_id for section_id in to_section_ids if section_id not in modified_sections
            ],
            "change_count": total_changes,
            "truncated": total_changes > len(changes),
            "changes": changes,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
