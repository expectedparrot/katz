from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import typer

from katz import __version__
from .commands.agent import (  # noqa: F401  (re-exported for compatibility)
    _agent_action,
    _agent_state,
    _command_available,
    _dotenv_has_key,
    _ep_local_profile_state,
    _manuscript_candidates,
    agent_app,
    agent_bootstrap,
    agent_instructions,
    agent_next,
    agent_schema,
    agent_status,
)
from .commands.docs import (  # noqa: F401  (re-exported for compatibility)
    _load_docs_module,
    docs_app,
    docs_list,
    docs_search,
    docs_show,
)
from .commands.evals import (  # noqa: F401  (re-exported for compatibility)
    eval_add,
    eval_app,
    eval_catalog,
    eval_catalog_show,
    eval_enable,
    eval_init_catalog,
    eval_list,
    eval_remove,
    eval_respond,
    eval_results,
    eval_show,
)
from .commands.guide import (  # noqa: F401  (re-exported for compatibility)
    available_skills,
    guide_app,
    guide_overview,
    guide_root,
    guide_script,
    guide_skill,
    guide_skills,
)
from .commands.issue import (  # noqa: F401  (re-exported for compatibility)
    issue_app,
    issue_carry_forward,
    issue_clusters,
    issue_investigate,
    issue_investigate_batch,
    issue_list,
    issue_merge,
    issue_merge_suggest,
    issue_next,
    issue_patch,
    issue_show,
    issue_suggest,
    issue_update,
    issue_write,
)
from .commands.paper import (  # noqa: F401  (re-exported for compatibility)
    _register_manuscript,
    paper_add_sections,
    paper_app,
    paper_auto_chunk,
    paper_find,
    paper_prepare,
    paper_register,
    paper_resolve,
    paper_review_jobs,
    paper_section,
    paper_sections,
    paper_sentences,
    paper_status,
)
from .commands.report import (  # noqa: F401  (re-exported for compatibility)
    _load_report_module,
    report_app,
    report_generate,
)
from .commands.results import (  # noqa: F401  (re-exported for compatibility)
    results_app,
    results_audit,
    results_failures,
    results_sample,
)
from .commands.review import (  # noqa: F401  (re-exported for compatibility)
    _parse_json_array_answer,
    _review_dir,
    review_add,
    review_app,
    review_ingest,
    review_jobs,
    review_list,
)
from .commands.spotter import (  # noqa: F401  (re-exported for compatibility)
    spotter_add,
    spotter_app,
    spotter_catalog,
    spotter_catalog_show,
    spotter_enable,
    spotter_ingest,
    spotter_init_catalog,
    spotter_jobs,
    spotter_list,
    spotter_models,
    spotter_remove,
    spotter_show,
)
from .commands.versions import (  # noqa: F401  (re-exported for compatibility)
    version_app,
    version_checkout,
    version_diff,
    version_list,
    version_root,
)
from .commands.workspace import (  # noqa: F401  (re-exported for compatibility)
    workspace_app,
    workspace_new,
)
from .definitions import (  # noqa: F401  (re-exported for compatibility)
    VALID_GRADES,
    VALID_SCOPES,
    _load_collection,
    _parse_eval,
    _parse_spotter,
    _slugify,
)
from .issues import (  # noqa: F401  (re-exported for compatibility)
    VALID_STATES,
    _apply_issue_edits,
    _full_issue_record,
    _issue_count,
    _issue_dir,
    _issue_dir_for_id,
    _issue_duplicate_clusters,
    _latest_status,
    _list_investigations,
    _list_issue_edits,
    _load_issue,
    _resolve_issue_id,
)
from .manuscript import (  # noqa: F401  (re-exported for compatibility)
    _DERIVED_LOCATION_FIELDS,
    _plan_location_repair,
)
from .assets import (  # noqa: F401  (re-exported for compatibility)
    SKILLS_DIR,
    CATALOG_DIR,
    REPORT_SCRIPT,
    SCHEMAS_DIR,
    TEMPLATES_DIR,
    AGENT_API_VERSION,
)
from .storage import (  # noqa: F401  (re-exported for compatibility)
    KATZ_DIR,
    ACTIVE_VERSION,
    PaperMap,
    read_json,
    write_json,
    read_jsonl,
    write_jsonl,
    append_jsonl,
    load_paper_map,
    paper_map_from_legacy,
    sha256_file,
    repo_root,
    current_commit,
    katz_root,
    active_version_path,
    active_commit,
    resolve_commit,
    version_dir,
    ensure_initialized,
    source_from_header,
    load_version,
    now_utc,
    event_filename,
    write_event_json,
    record_run,
    _latest_packaged_run,
    parse_meta,
)
from .manuscript import (  # noqa: F401  (re-exported for compatibility)
    _MATH_ENVS,
    _TEX_SKIP_ENVS,
    _TEX_STRUCTURAL_RE,
    _SENTENCE_BOUNDARY_RE,
    _SENTENCE_SPLIT_RE,
    _count_non_ventilated_lines,
    ventilate_markdown,
    segment_sentences,
    line_bounds,
    contains_math,
    resolve_location,
    section_for_range,
    validate_location,
    _provenance_sidecar_path,
    _load_provenance_sidecar,
    _quote_matches,
    _locate_quoted_text,
)
from .latex import (  # noqa: F401  (re-exported for compatibility)
    _LATEX_INCLUDE_RE,
    _LATEX_GRAPHICS_RE,
    _tex_code_and_comment,
    _expand_latex_source,
    _latex_source_inventory,
    _markdown_table_count,
    _balanced_brace_group,
    _strip_resizebox_wrappers,
    _restore_latex_front_matter,
    _LATEX_INLINE_MARKER_RE,
    _LATEX_HEADING_RE,
    _section_provenance_from_expanded,
    _flatten_html_anchors,
    _prepare_latex,
)
from .edsl_bridge import (  # noqa: F401  (re-exported for compatibility)
    SPOTTER_QUESTION_TEXT,
    SPOTTER_VERDICT_SUFFIX,
    SPOTTER_RECOMMENDED_MAX_TOKENS,
    ECONOMICS_REVIEW_QUESTION_TEXT,
    _edsl_imports,
    _expected_results_path,
    _save_and_verify_ep,
    JOURNAL_REVIEW_PARSE_PROMPT,
    _result_value,
    _answer_is_found,
    _scenario_key,
    _coerce_spotter_answer,
    _spotter_answer_error,
    _audit_spotter_results,
    _resolve_audit_jobs,
    _group_positive_findings,
)
from .errors import (  # re-exported for back-compat
    KatzError,
    _command_argv,
    configure_output,
    emit_json,
    fail,
)


app = typer.Typer(help="Version-aware ledger for paper review artifacts.")
app.add_typer(paper_app, name="paper")
app.add_typer(issue_app, name="issue")
app.add_typer(spotter_app, name="spotter")
app.add_typer(eval_app, name="eval")
app.add_typer(docs_app, name="docs")
app.add_typer(guide_app, name="guide")
app.add_typer(report_app, name="report")
app.add_typer(review_app, name="review")
app.add_typer(agent_app, name="agent")
app.add_typer(results_app, name="results")
app.add_typer(version_app, name="version")
app.add_typer(workspace_app, name="workspace")


@app.callback()
def output_options(
    human: bool = typer.Option(
        False,
        "--human",
        help="Render readable Rich tables and summaries instead of the JSON envelope.",
    ),
) -> None:
    """Configure the CLI output mode."""
    configure_output(human=human)


# ---------------------------------------------------------------------------
# JSONL utilities
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sentence segmentation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Core helpers (unchanged API)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Initialize .katz in the current git repository."""
    try:
        root = repo_root() / KATZ_DIR
        (root / "versions").mkdir(parents=True, exist_ok=True)
        result = {
            "initialized": True,
            "path": str(root),
            "active_version": None,
        }
        emit_json(result)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@app.command()
def ventilate(
    input_path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, readable=True),
    output_path: Path = typer.Option(..., "--output-path"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing output file."),
) -> None:
    """Write a conservatively ventilated Markdown copy (one sentence per line)."""
    try:
        source = input_path.resolve()
        destination = output_path.resolve()
        if source == destination:
            raise KatzError(
                "Input and output paths must differ",
                "validation_error",
                {"input_path": str(source), "output_path": str(destination)},
            )
        if input_path.suffix.lower() not in {".md", ".markdown"}:
            raise KatzError(
                "Ventilation currently supports Markdown files only",
                "validation_error",
                {"input_path": str(input_path), "supported_extensions": [".md", ".markdown"]},
            )
        if output_path.exists() and not force:
            raise KatzError(
                "Output path already exists; pass --force to overwrite it",
                "validation_error",
                {"output_path": str(output_path)},
            )

        text = input_path.read_text(encoding="utf-8")
        ventilated, lines_changed = ventilate_markdown(text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(ventilated, encoding="utf-8")
        input_sidecar = _provenance_sidecar_path(input_path)
        if input_sidecar.is_file():
            # Keep conversion provenance travelling with the derivative so
            # registration can pick it up without re-running `paper prepare`.
            shutil.copyfile(input_sidecar, _provenance_sidecar_path(output_path))
        emit_json({
            "ventilated": True,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "format": "markdown",
            "lines_changed": lines_changed,
            "lines_before": len(text.splitlines()),
            "lines_after": len(ventilated.splitlines()),
            "remaining_non_ventilated_lines": _count_non_ventilated_lines(ventilated),
            "checksum": sha256_file(output_path),
        })
    except UnicodeDecodeError as exc:
        fail(
            "Input file must be UTF-8",
            "validation_error",
            {"input_path": str(input_path), "start": exc.start},
        )
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@app.command("next")
def next_command() -> None:
    """Inspect preserved artifacts and return the next review action."""
    agent_next()


@app.command("capabilities")
def capabilities() -> None:
    """Describe Katz's agent API, schemas, integrations, and safety properties."""
    schema_names = sorted(path.name for path in SCHEMAS_DIR.glob("*.json")) if SCHEMAS_DIR.is_dir() else []
    emit_json({
        "package_version": __version__,
        "schema_version": AGENT_API_VERSION,
        "agent_api": {
            "commands": [
                "katz agent bootstrap", "katz agent status", "katz agent next",
                "katz agent instructions --write", "katz agent instructions codex",
                "katz agent instructions claude", "katz agent schema NAME",
                "katz capabilities", "katz ingest PATH", "katz issue next",
                "katz results audit RESULTS --jobs JOBS", "katz results failures RESULTS",
                "katz issue clusters",
            ],
            "action_fields": [
                "id", "purpose", "command", "mutates_state", "requires_network",
                "requires_user_approval", "reason",
            ],
        },
        "output_modes": {
            "default": "json",
            "human": "katz --human COMMAND [ARGS]...",
        },
        "ingestion": ["spotter_results", "journal_review_results", "jobs_package", "humanize_results", "narrative_review"],
        "integrations": {
            "edsl": _command_available("ep"),
            "expected_parrot": True,
            "github_via_gh": _command_available("gh"),
        },
        "safety": {
            "bootstrap_is_read_only": True,
            "unified_ingest_previews_by_default": True,
            "external_writes_require_explicit_agent_authority": True,
            "issue_ingestion_is_idempotent": True,
            "spotter_ingestion_fails_closed": True,
            "zero_issue_requires_complete_coverage": True,
            "api_keys_are_never_returned": True,
        },
        "schemas": schema_names,
    })


@app.command()
def validate(commit: Optional[str] = typer.Option(None, "--commit")) -> None:
    """Validate a katz version without modifying files."""
    try:
        resolved, dest, version, pmap, canonical = load_version(commit)
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if version.get("commit") != resolved:
            errors.append({"code": "validation_error", "path": str(dest / "version.json"), "message": "commit mismatch"})
        if pmap.header.get("commit") != resolved:
            errors.append({"code": "validation_error", "path": str(dest / "paper_map.jsonl"), "message": "commit mismatch"})
        if not canonical.exists():
            errors.append({"code": "not_found", "path": str(canonical), "message": "canonical manuscript is missing"})
        else:
            checksum = sha256_file(canonical)
            if version.get("checksum") != checksum or pmap.header.get("checksum") != checksum:
                errors.append(
                    {
                        "code": "checksum_mismatch",
                        "path": str(canonical),
                        "message": "checksum metadata does not match manuscript",
                    }
                )

        issue_ids: set[str] = set()
        issues_dir = dest / "issues"
        if issues_dir.is_dir():
            for issue_dir in sorted(issues_dir.iterdir()):
                if not issue_dir.is_dir():
                    continue
                issue_json = issue_dir / "issue.json"
                if not issue_json.exists():
                    errors.append({"code": "not_found", "path": str(issue_json), "message": "issue.json missing in issue directory"})
                    continue
                try:
                    record = read_json(issue_json)
                except KatzError as exc:
                    errors.append({"code": exc.code, "path": str(issue_json), "message": exc.message})
                    continue
                if record.get("commit") != resolved:
                    errors.append({"code": "validation_error", "path": str(issue_json), "message": "commit mismatch"})
                if isinstance(record.get("id"), str):
                    issue_ids.add(record["id"])
                location = record.get("location")
                if isinstance(location, dict) and canonical.exists():
                    validate_location(canonical, issue_json, location, errors)
                else:
                    errors.append({"code": "validation_error", "path": str(issue_json), "message": "record location is missing"})
                # Validate status files
                for status_file in sorted((issue_dir / "status").glob("*.json")) if (issue_dir / "status").is_dir() else []:
                    try:
                        status_rec = read_json(status_file)
                    except KatzError as exc:
                        errors.append({"code": exc.code, "path": str(status_file), "message": exc.message})
                        continue
                    if status_rec.get("state") not in VALID_STATES:
                        errors.append({"code": "validation_error", "path": str(status_file), "message": f"invalid state: {status_rec.get('state')}"})
                # Validate investigation files
                for inv_file in sorted((issue_dir / "investigations").glob("*.json")) if (issue_dir / "investigations").is_dir() else []:
                    try:
                        read_json(inv_file)
                    except KatzError as exc:
                        errors.append({"code": exc.code, "path": str(inv_file), "message": exc.message})
                # Validate edit events
                for edit_file in sorted((issue_dir / "edits").glob("*.json")) if (issue_dir / "edits").is_dir() else []:
                    try:
                        edit_rec = read_json(edit_file)
                    except KatzError as exc:
                        errors.append({"code": exc.code, "path": str(edit_file), "message": exc.message})
                        continue
                    if not isinstance(edit_rec.get("fields"), dict):
                        errors.append({"code": "validation_error", "path": str(edit_file), "message": "edit event must contain a fields object"})

        for record_path in sorted((dest / "chunks").glob("*.json")) if (dest / "chunks").is_dir() else []:
            try:
                record = read_json(record_path)
            except KatzError as exc:
                errors.append({"code": exc.code, "path": str(record_path), "message": exc.message})
                continue
            if record.get("commit") != resolved:
                errors.append({"code": "validation_error", "path": str(record_path), "message": "commit mismatch"})

        symbol_table = dest / "symbol_table.json"
        if symbol_table.exists():
            try:
                symbols = json.loads(symbol_table.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append({"code": "validation_error", "path": str(symbol_table), "message": exc.msg})
            else:
                if not isinstance(symbols, list):
                    errors.append(
                        {"code": "validation_error", "path": str(symbol_table), "message": "symbol_table must be an array"}
                    )
        else:
            warnings.append({"code": "repair_required", "path": str(symbol_table), "message": "symbol_table.json missing"})

        emit_json({"valid": not errors, "commit": resolved, "errors": errors, "warnings": warnings})
        if errors:
            raise typer.Exit(1)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@app.command()
def repair(
    commit: Optional[str] = typer.Option(None, "--commit"),
    check: bool = typer.Option(False, "--check", help="Report planned repairs without writing files."),
) -> None:
    """Deterministically hydrate derived fields and recreate missing scaffolding.

    Repair never invents record content, changes states, or alters byte ranges;
    it only recomputes derived location fields from the canonical manuscript and
    recreates missing empty directories and an empty symbol_table.json.
    """
    try:
        resolved, dest, _, _, canonical = load_version(commit)
        if not canonical.exists():
            raise KatzError(
                "Canonical manuscript is missing; repair cannot hydrate locations",
                "not_found",
                {"canonical": str(canonical)},
            )
        planned: list[dict[str, Any]] = []
        unrepairable: list[dict[str, Any]] = []

        for directory in ["issues", "chunks"]:
            if not (dest / directory).is_dir():
                planned.append({
                    "path": str(dest / directory),
                    "repairable": True,
                    "action": "create_directory",
                })
        symbol_table = dest / "symbol_table.json"
        if not symbol_table.exists():
            planned.append({
                "path": str(symbol_table),
                "repairable": True,
                "action": "create_empty_symbol_table",
            })

        location_repairs: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
        record_paths = list(sorted((dest / "issues").glob("*/issue.json"))) if (dest / "issues").is_dir() else []
        record_paths += list(sorted((dest / "chunks").glob("*.json"))) if (dest / "chunks").is_dir() else []
        for record_path in record_paths:
            record = read_json(record_path)
            location = record.get("location")
            if not isinstance(location, dict):
                continue
            plan = _plan_location_repair(canonical, record_path, location)
            if plan is None:
                continue
            if not plan["repairable"]:
                unrepairable.append(plan)
                continue
            planned.append(plan)
            location_repairs.append((record_path, record, plan))

        if not check:
            for directory in ["issues", "chunks"]:
                (dest / directory).mkdir(parents=True, exist_ok=True)
            if not symbol_table.exists():
                symbol_table.write_text("[]\n", encoding="utf-8")
            for record_path, record, plan in location_repairs:
                record["location"].update(plan["hydrated"])
                write_json(record_path, record)

        emit_json({
            "commit": resolved,
            "check": check,
            "repaired": bool(planned) and not check,
            "planned_repairs": planned,
            "unrepairable": unrepairable,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


# ---------------------------------------------------------------------------
# Eval commands
# ---------------------------------------------------------------------------


def _detect_ingest_source(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".rst"}:
        return {
            "kind": "narrative_review",
            "object_type": "text",
            "supported_apply": False,
            "recommended_command": ["katz", "review", "add", str(path)],
            "reason": "Register the source review first so provenance is preserved before parsing.",
        }
    if suffix != ".ep":
        return {
            "kind": "unknown",
            "object_type": None,
            "supported_apply": False,
            "recommended_command": None,
            "reason": "Katz currently detects EDSL .ep packages and human-review text files.",
        }
    try:
        from edsl import Jobs, Results
    except ImportError as exc:
        raise KatzError("EDSL is required to inspect .ep objects", "dependency_error") from exc
    try:
        results = Results.git.load(path)
    except Exception:
        try:
            jobs = Jobs.git.load(path)
        except Exception as exc:
            raise KatzError("Unable to load .ep package as Jobs or Results", "validation_error", {"path": str(path)}) from exc
        return {
            "kind": "jobs_package",
            "object_type": "Jobs",
            "supported_apply": False,
            "question_names": list(jobs.survey.question_names),
            "scenario_count": len(jobs.scenarios),
            "recommended_command": ["ep", "run", str(path), "--model", "<model-name>", "--output", "results.ep"],
            "reason": "Jobs packages must be executed by EDSL before Katz can ingest findings.",
        }
    answer_keys: set[str] = set()
    scenario_keys: set[str] = set()
    for result in results:
        try:
            answer = result["answer"]
            scenario = result["scenario"]
        except (KeyError, TypeError):
            continue
        if isinstance(answer, dict):
            answer_keys.update(str(key) for key in answer)
        else:
            try:
                answer_keys.update(str(key) for key in dict(answer))
            except Exception:
                pass
        if isinstance(scenario, dict):
            scenario_keys.update(str(key) for key in scenario)
        else:
            try:
                scenario_keys.update(str(key) for key in dict(scenario))
            except Exception:
                pass
    if "spotter_result" in answer_keys:
        kind = "spotter_results"
        supported = True
        recommended = ["katz", "spotter", "ingest", str(path)]
    elif "journal_review_issues" in answer_keys:
        kind = "journal_review_results"
        supported = True
        recommended = ["katz", "review", "ingest", str(path)]
    elif "economic_review" in answer_keys:
        kind = "whole_paper_review_results"
        supported = False
        recommended = ["ep", "results", "select", "--file", str(path), "--column", "answer.economic_review"]
    elif "issue_id" in scenario_keys:
        kind = "humanize_results"
        supported = False
        recommended = ["katz", "guide", "skill", "review-paper"]
    else:
        kind = "unknown_results"
        supported = False
        recommended = None
    return {
        "kind": kind,
        "object_type": "Results",
        "supported_apply": supported,
        "result_count": len(results),
        "answer_keys": sorted(answer_keys),
        "scenario_keys": sorted(scenario_keys),
        "recommended_command": recommended,
        "reason": {
            "whole_paper_review_results": "A coherent referee report requires agent judgment before individual concerns are filed.",
            "humanize_results": "Human triage decisions require explicit label validation before ledger mutations.",
            "unknown_results": "No supported Katz ingestion contract was detected.",
        }.get(kind),
    }


@app.command("ingest")
def ingest(
    path: Path = typer.Argument(..., exists=True, readable=True),
    apply: bool = typer.Option(False, "--apply", help="Apply a supported ingestion after previewing its detected contract."),
    allow_partial: bool = typer.Option(False, "--allow-partial", help="Ingest valid rows despite incomplete coverage; records a partial run."),
    state: str = typer.Option("draft", "--state"),
    commit: Optional[str] = typer.Option(None, "--commit"),
    jobs: Optional[Path] = typer.Option(None, "--jobs", exists=True, readable=True),
) -> None:
    """Detect review artifacts safely; preview by default and mutate only with --apply."""
    try:
        detection = _detect_ingest_source(path)
        if detection.get("kind") == "spotter_results":
            _, dest, _, _, _ = load_version(commit)
            jobs_path = _resolve_audit_jobs(dest, path, jobs)
            audit = _audit_spotter_results(path, jobs_path)
            audit.pop("_rows", None)
            detection["audit"] = audit
            detection["supported_apply"] = bool(audit["complete"] or allow_partial)
            if not audit["complete"]:
                detection["reason"] = (
                    "Spotter Results are incomplete or cannot be matched to their originating Jobs. "
                    "Katz will not interpret missing answers as negative findings."
                )
        if not apply:
            command = detection.get("recommended_command")
            apply_action = None
            if detection.get("supported_apply"):
                apply_action = _agent_action(
                    "apply_ingestion",
                    "Apply the detected, version-checked ingestion contract",
                    ["katz", "ingest", str(path), "--apply", "--state", state]
                    + (["--jobs", str(jobs)] if jobs is not None else [])
                    + (["--allow-partial"] if allow_partial else []),
                    mutates_state=True,
                )
            emit_json({
                "schema_version": AGENT_API_VERSION,
                "mode": "preview",
                "path": str(path),
                "detection": detection,
                "will_mutate": False,
                "next_actions": [apply_action] if apply_action else (
                    [_agent_action("recommended_followup", "Continue with the detected artifact", command, mutates_state=False)]
                    if command else []
                ),
            })
            return
        if not detection.get("supported_apply"):
            raise KatzError(
                "Detected artifact does not support automatic application",
                "unsupported_ingestion",
                {"detection": detection},
            )
        if detection["kind"] == "spotter_results":
            # Pass every option explicitly when delegating to a Typer command.
            # Otherwise omitted parameters retain their OptionInfo declaration
            # objects, which must never reach business or path-handling code.
            spotter_ingest(
                path,
                state=state,
                commit=commit,
                jobs=jobs,
                allow_partial=allow_partial,
            )
            return
        if detection["kind"] == "journal_review_results":
            review_ingest(path, state=state, commit=commit)
            return
        raise KatzError("No ingestion handler is available", "unsupported_ingestion", {"detection": detection})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


# ---------------------------------------------------------------------------
# Report commands
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Guide commands
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Docs commands
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    app()
