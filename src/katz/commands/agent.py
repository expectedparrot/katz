"""`katz agent` commands: state machine, next actions, instructions."""
from __future__ import annotations

import json
import shutil
import subprocess
import typer
from pathlib import Path
from typing import Any, List, Optional

from ..assets import AGENT_API_VERSION, SCHEMAS_DIR, TEMPLATES_DIR
from ..edsl_bridge import SPOTTER_RECOMMENDED_MAX_TOKENS
from ..errors import KatzError, emit_json, fail
from ..issues import VALID_STATES, _load_issue
from ..storage import ACTIVE_VERSION, KATZ_DIR, current_commit, load_version, read_json


agent_app = typer.Typer(help="Discover state and next actions for coding agents.")


def _agent_action(
    action_id: str,
    purpose: str,
    command: list[str],
    *,
    mutates_state: bool,
    requires_network: bool = False,
    requires_user_approval: bool = False,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    action = {
        "id": action_id,
        "purpose": purpose,
        "command": command,
        "mutates_state": mutates_state,
        "requires_network": requires_network,
        "requires_user_approval": requires_user_approval,
    }
    if reason:
        action["reason"] = reason
    return action


def _command_available(name: str) -> bool:
    return shutil.which(name) is not None


def _dotenv_has_key(path: Path, key: str) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if stripped.startswith(f"{key}=") and stripped.split("=", 1)[1].strip():
            return True
    return False


def _ep_local_profile_state(root: Path) -> dict[str, Any]:
    """Read EDSL's redacted repository-local auth/profile state without networking."""
    if not _command_available("ep"):
        return {
            "available": False,
            "active_profile": None,
            "env_file": str(root / ".env"),
            "env_file_exists": (root / ".env").is_file(),
            "api_key_configured": False,
            "source": "unavailable",
        }
    result = subprocess.run(
        ["ep", "profiles", "current", "--env-file", ".env"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    data = data if isinstance(data, dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    configured = bool(config.get("EXPECTED_PARROT_API_KEY"))
    if not configured:
        configured = bool(
            __import__("os").environ.get("EXPECTED_PARROT_API_KEY")
            or _dotenv_has_key(root / ".env", "EXPECTED_PARROT_API_KEY")
        )
    return {
        "available": result.returncode == 0,
        "active_profile": data.get("active_profile"),
        "env_file": data.get("env_file", str(root / ".env")),
        "env_file_exists": bool(data.get("env_file_exists", (root / ".env").is_file())),
        "api_key_configured": configured,
        "expected_parrot_url": config.get("EXPECTED_PARROT_URL"),
        "source": "ep_profiles_current" if result.returncode == 0 else "environment_or_dotenv_fallback",
    }


def _manuscript_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ignored = {
        ".git", ".katz", ".venv", "node_modules", "dist", "build",
        ".claude", ".codex", ".agents", "site-packages",
    }
    ignored_names = {
        "agents.md", "claude.md", "readme.md", "contributing.md",
        "review.md", "review.html", "investigated-review.html",
    }
    preferred_names = {"paper.md", "manuscript.md", "article.md", "paper.tex", "manuscript.tex"}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        if path.suffix.lower() not in {".md", ".tex", ".pdf"}:
            continue
        relative = path.relative_to(root)
        if path.name.lower() in ignored_names or path.name.lower().endswith((".jobs.ep", "-results.ep")):
            continue
        score = 0
        if path.name.lower() in preferred_names:
            score += 100
        lowered_name = path.name.lower()
        if lowered_name.startswith(("paper.", "paper_", "manuscript.", "manuscript_", "article.", "article_")):
            score += 30
        if "ventilated" in path.stem.lower():
            score += 200
        if path.suffix.lower() in {".md", ".tex"}:
            score += 10
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 2_000:
            score += 5
        academic_markers = 0
        if path.suffix.lower() in {".md", ".tex"}:
            try:
                sample = path.read_text(encoding="utf-8", errors="replace")[:40_000].lower()
            except OSError:
                sample = ""
            academic_markers = sum(
                marker in sample
                for marker in ("abstract", "introduction", "methods", "results", "references")
            )
            score += academic_markers * 8
        reasons: list[str] = []
        if path.name.lower() in preferred_names:
            reasons.append("preferred manuscript filename")
        if academic_markers:
            reasons.append(f"contains {academic_markers} academic section markers")
        if size > 2_000:
            reasons.append("substantial document length")
        if "ventilated" in path.stem.lower():
            reasons.append("prepared ventilated derivative")
        git_status_probe = subprocess.run(
            ["git", "status", "--porcelain", "--", str(relative)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        git_status = git_status_probe.stdout[:2] if git_status_probe.returncode == 0 else ""
        tracked_probe = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(relative)],
            cwd=root,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if not git_status and tracked_probe.returncode == 0:
            version_state = "committed"
        elif git_status == "??" or (len(git_status) > 1 and git_status[1] != " "):
            version_state = "unstaged"
        else:
            version_state = "staged"
        candidates.append({
            "path": str(relative),
            "format": path.suffix.lower().lstrip("."),
            "bytes": size,
            "confidence": score,
            "confidence_reasons": reasons,
            "version_state": version_state,
        })
    return sorted(candidates, key=lambda item: (-item["confidence"], item["path"]))[:20]


def _agent_state() -> dict[str, Any]:
    git_probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if git_probe.returncode != 0:
        return {
            "schema_version": AGENT_API_VERSION,
            "phase": "repository_setup",
            "ready": False,
            "repository": {"is_git_repository": False},
            "prerequisites": {},
            "review": None,
            "next_actions": [
                _agent_action(
                    "initialize_git",
                    "Create a Git repository so manuscript versions can be anchored",
                    ["git", "init"],
                    mutates_state=True,
                    requires_user_approval=True,
                )
            ],
            "blockers": [{"code": "not_git_repo", "message": "Katz requires a Git repository."}],
        }

    root = Path(git_probe.stdout.strip())
    status_probe = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    initialized = (root / KATZ_DIR).is_dir()
    ep_profile = _ep_local_profile_state(root)
    prerequisites = {
        "katz": {"available": True},
        "ep": {
            "available": _command_available("ep"),
            "profile": ep_profile,
        },
        "git": {"available": True},
        "expected_parrot_key": {
            "configured": ep_profile["api_key_configured"],
            "source": ep_profile["source"],
            "secret_returned": False,
            "login_command": ["ep", "auth", "login"],
            "check_command": ["ep", "check"],
        },
    }
    repository = {
        "is_git_repository": True,
        "root": str(root),
        "head": None,
        "dirty": bool(status_probe.stdout.strip()),
    }
    try:
        repository["head"] = current_commit()
    except KatzError:
        pass

    if not initialized:
        return {
            "schema_version": AGENT_API_VERSION,
            "phase": "katz_setup",
            "ready": False,
            "repository": repository,
            "prerequisites": prerequisites,
            "review": {"initialized": False, "manuscript_candidates": _manuscript_candidates(root)},
            "next_actions": [
                _agent_action("katz_init", "Initialize the Katz ledger", ["katz", "init"], mutates_state=True)
            ],
            "blockers": [],
        }

    active_path = root / KATZ_DIR / ACTIVE_VERSION
    if not active_path.is_file():
        candidates = _manuscript_candidates(root)
        actions = []
        if candidates and candidates[0]["confidence"] >= 25:
            candidate = candidates[0]
            if candidate["format"] in {"pdf", "tex", "latex"}:
                output = str(Path(candidate["path"]).with_suffix(".md"))
                actions.append(_agent_action(
                    "prepare_manuscript",
                    "Prepare the likely PDF or LaTeX manuscript as reviewable Markdown and figure assets",
                    ["katz", "paper", "prepare", candidate["path"], "--output", output],
                    mutates_state=True,
                    requires_user_approval=True,
                    reason="Katz anchors findings to canonical UTF-8 text; source documents must be prepared first.",
                ))
            elif candidate["version_state"] == "unstaged":
                actions.append(_agent_action(
                    "stage_canonical_manuscript",
                    "Stage the prepared canonical manuscript before registration",
                    ["git", "add", "--", candidate["path"]],
                    mutates_state=True,
                    reason="Katz versions are Git commits; registration must not attach uncommitted text to the current HEAD.",
                ))
            elif candidate["version_state"] == "staged":
                actions.append(_agent_action(
                    "commit_canonical_manuscript",
                    "Commit the prepared canonical manuscript before registration",
                    ["git", "commit", "-m", "Add canonical manuscript for Katz review"],
                    mutates_state=True,
                    requires_user_approval=True,
                    reason="Registration anchors the canonical bytes to the resulting Git commit.",
                ))
            else:
                actions.append(_agent_action(
                    "register_manuscript",
                    "Register the most likely canonical manuscript after confirming it",
                    [
                        "katz", "paper", "register", "--canonical", candidate["path"],
                        "--source-format", candidate["format"],
                        "--source-method", "agent-selected-repository-source",
                    ],
                    mutates_state=True,
                    requires_user_approval=len(candidates) > 1,
                    reason="Candidate ranking is heuristic; confirm the canonical source.",
                ))
        return {
            "schema_version": AGENT_API_VERSION,
            "phase": "manuscript_registration",
            "ready": False,
            "repository": repository,
            "prerequisites": prerequisites,
            "review": {"initialized": True, "active_version": None, "manuscript_candidates": candidates},
            "next_actions": actions,
            "blockers": [] if candidates else [{"code": "no_manuscript_candidate", "message": "No Markdown, TeX, or PDF candidate was found."}],
        }

    resolved, dest, version, pmap, canonical = load_version(None)
    spotters = sorted(path.stem for path in (dest / "spotters").glob("*.md")) if (dest / "spotters").is_dir() else []
    reviews = list((dest / "reviews").glob("*/review.json")) if (dest / "reviews").is_dir() else []
    issue_records = [
        _load_issue(path.parent)
        for path in sorted((dest / "issues").glob("*/issue.json"))
    ] if (dest / "issues").is_dir() else []
    issue_counts = {state: sum(record.get("state") == state for record in issue_records) for state in sorted(VALID_STATES)}
    run_records = [
        read_json(path) for path in sorted((dest / "runs").glob("*.json"))
    ] if (dest / "runs").is_dir() else []
    latest_run = run_records[-1] if run_records else None
    review = {
        "initialized": True,
        "active_version": resolved,
        "canonical": version.get("canonical"),
        "canonical_exists": canonical.is_file(),
        "sections": len(pmap.sections),
        "sentences": len(pmap.sentences),
        "figures": len(pmap.figures),
        "enabled_spotters": spotters,
        "human_reviews": len(reviews),
        "issues": issue_counts,
        "runs": {
            "count": len(run_records),
            "latest": latest_run,
        },
    }
    actions: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    if not canonical.is_file():
        phase = "repair"
        blockers.append({"code": "canonical_missing", "message": "The registered canonical manuscript is missing."})
    elif not pmap.sections:
        phase = "section_mapping"
        actions.append(_agent_action(
            "auto_chunk", "Map reviewable manuscript sections", ["katz", "paper", "auto-chunk"], mutates_state=True
        ))
    elif issue_counts.get("draft", 0):
        phase = "investigation"
        actions.append(_agent_action(
            "next_issue", "Get the next complete investigation packet", ["katz", "issue", "next"], mutates_state=False
        ))
    elif issue_counts.get("confirmed", 0) or issue_counts.get("open", 0):
        phase = "reporting"
        actions.extend([
            _agent_action("validate", "Validate anchors and ledger consistency", ["katz", "validate"], mutates_state=False),
            _agent_action("generate_report", "Generate a human-readable review report", ["katz", "report", "generate", "--output", "review.html"], mutates_state=True),
        ])
    elif latest_run and latest_run.get("status") == "ingested":
        phase = "reporting"
        actions.extend([
            _agent_action("validate", "Validate the completed review ledger", ["katz", "validate"], mutates_state=False),
            _agent_action("generate_report", "Generate a report, including an explicit zero-issue result when applicable", ["katz", "report", "generate", "--output", "review.html"], mutates_state=True),
        ])
    elif not spotters:
        phase = "review_configuration"
        catalog_names = sorted(path.stem for path in (root / KATZ_DIR / "spotters").glob("*.md"))
        if not catalog_names:
            actions.append(
                _agent_action("init_spotter_catalog", "Install reusable review procedures", ["katz", "spotter", "init-catalog"], mutates_state=True)
            )
        else:
            actions.extend([
                _agent_action(
                    "enable_recommended_spotters",
                    "Enable the recommended manuscript review procedures",
                    ["katz", "spotter", "enable", "--recommended"],
                    mutates_state=True,
                ),
                _agent_action("list_spotter_catalog", "Inspect available review procedures", ["katz", "spotter", "catalog"], mutates_state=False),
            ])
    else:
        phase = "automated_review"
        if latest_run and latest_run.get("status") == "packaged":
            expected_results = Path(str(latest_run.get("expected_results_path", "")))
            jobs_path = Path(str(latest_run.get("jobs_path", "jobs.ep")))
            if expected_results.is_file():
                if latest_run.get("pilot"):
                    actions.append(_agent_action(
                        "audit_pilot_results",
                        "Prove that the selected model returned valid structured answers before a full run",
                        ["katz", "results", "audit", str(expected_results), "--jobs", str(jobs_path)],
                        mutates_state=True,
                    ))
                else:
                    actions.append(_agent_action(
                        "preview_ingestion",
                        "Audit and preview the completed EDSL Results before mutating the ledger",
                        ["katz", "ingest", str(expected_results)],
                        mutates_state=False,
                    ))
            else:
                models_file = root / "models.ep"
                if not models_file.exists():
                    actions.append(_agent_action(
                        "build_spotter_models",
                        "Create a ModelList with an adequate token budget for free-text spotter runs",
                        ["katz", "spotter", "models", "--model", "<model-name>",
                         "--max-tokens", str(SPOTTER_RECOMMENDED_MAX_TOKENS), "--output", "models.ep"],
                        mutates_state=True,
                        reason=(
                            "Free-text verdicts truncate under the provider default and `ep run` "
                            f"has no --max-tokens flag, so set max_tokens >= {SPOTTER_RECOMMENDED_MAX_TOKENS} "
                            "in a ModelList before running."
                        ),
                    ))
                actions.append(_agent_action(
                    "run_jobs",
                    "Execute the packaged review with the prepared ModelList (paid, remote)",
                    ["ep", "run", str(jobs_path), "--model_list", "models.ep",
                     "--output", str(expected_results)],
                    mutates_state=True,
                    requires_network=True,
                    requires_user_approval=True,
                    reason=(
                        "Runs every spotter scenario. Use the ModelList from "
                        "`katz spotter models` so free-text answers are not truncated before "
                        "their JSON verdict (which the audit reports as unparseable_answer)."
                    ),
                ))
                actions.append(_agent_action(
                    "inspect_jobs", "Inspect the packaged EDSL job before execution",
                    ["ep", "inspect", str(jobs_path)],
                    mutates_state=False,
                ))
        else:
            pilot_succeeded = bool(
                latest_run
                and latest_run.get("kind") == "spotter_pilot"
                and latest_run.get("status") == "audited"
            )
            actions.append(_agent_action(
                "build_review_jobs" if pilot_succeeded else "build_pilot_jobs",
                "Package enabled spotters and manuscript sections"
                if pilot_succeeded else "Build a five-scenario model compatibility pilot",
                ["katz", "spotter", "jobs", "--output", "jobs.ep"]
                if pilot_succeeded else ["katz", "spotter", "jobs", "--pilot", "5", "--output", "pilot.jobs.ep"],
                mutates_state=True,
            ))

        needs_remote_run = bool(
            latest_run
            and latest_run.get("status") == "packaged"
            and not Path(str(latest_run.get("expected_results_path", ""))).is_file()
        )
        if needs_remote_run and not prerequisites["ep"]["available"]:
            blockers.append({"code": "edsl_cli_missing", "message": "Install EDSL so the `ep` command is available."})
            actions.append(_agent_action(
                "install_edsl", "Install the EDSL command-line interface",
                ["python", "-m", "pip", "install", "edsl"],
                mutates_state=True, requires_network=True, requires_user_approval=True,
            ))
        elif needs_remote_run and not prerequisites["expected_parrot_key"]["configured"]:
            blockers.append({"code": "expected_parrot_key_missing", "message": "Configure EXPECTED_PARROT_API_KEY before remote execution."})
            actions.append(_agent_action(
                "expected_parrot_login",
                "Authenticate through EDSL and store repository-local configuration",
                ["ep", "auth", "login"],
                mutates_state=True,
                requires_network=True,
                requires_user_approval=True,
                reason="This opens the Expected Parrot login flow and writes authentication configuration to .env.",
            ))
        elif needs_remote_run:
            jobs_path = str(latest_run.get("jobs_path"))
            results_path = str(latest_run.get("expected_results_path"))
            actions.extend([
                _agent_action(
                    "check_expected_parrot",
                    "Validate Expected Parrot URL reachability and authentication before a paid run",
                    ["ep", "check"],
                    mutates_state=False,
                    requires_network=True,
                ),
                _agent_action(
                    "run_review_jobs", "Run the portable EDSL review package",
                    ["ep", "run", jobs_path, "--model", "<model-name>", "--output", results_path],
                    mutates_state=True, requires_network=True, requires_user_approval=True,
                    reason="The model choice affects cost and review behavior.",
                ),
            ])
    return {
        "schema_version": AGENT_API_VERSION,
        "phase": phase,
        "ready": not blockers,
        "repository": repository,
        "prerequisites": prerequisites,
        "review": review,
        "next_actions": actions,
        "blockers": blockers,
    }


@agent_app.command("status")
def agent_status() -> None:
    """Return the current review phase, blockers, and valid next actions."""
    try:
        emit_json(_agent_state())
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@agent_app.command("bootstrap")
def agent_bootstrap() -> None:
    """Inspect prerequisites and propose setup actions without changing state."""
    try:
        state = _agent_state()
        state["mode"] = "read_only_bootstrap"
        state["applied"] = []
        emit_json(state)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@agent_app.command("next")
def agent_next() -> None:
    """Return the single highest-priority safe next action."""
    try:
        state = _agent_state()
        actions = state.get("next_actions", [])
        emit_json({
            "schema_version": AGENT_API_VERSION,
            "phase": state.get("phase"),
            "ready": state.get("ready"),
            "action": actions[0] if actions else None,
            "alternatives": actions[1:],
            "blockers": state.get("blockers", []),
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@agent_app.command("instructions")
def agent_instructions(
    target: Optional[str] = typer.Argument(None, help="codex or claude"),
    output: Optional[Path] = typer.Option(None, "--output"),
    write: bool = typer.Option(False, "--write", help="Write native AGENTS.md and CLAUDE.md files."),
    content: bool = typer.Option(True, "--content/--no-content", help="Include template Markdown in the JSON response."),
) -> None:
    """Return or write native repository instructions for a coding agent."""
    try:
        if target is None and not write:
            raise KatzError("Specify codex or claude, or pass --write", "validation_error")
        if write and target is None:
            written: list[dict[str, Any]] = []
            for normalized, filename in (("codex", "AGENTS.md"), ("claude", "CLAUDE.md")):
                destination = Path(filename)
                if destination.exists():
                    written.append({"target": normalized, "path": filename, "status": "already_exists"})
                    continue
                markdown = (TEMPLATES_DIR / filename).read_text(encoding="utf-8")
                destination.write_text(markdown, encoding="utf-8")
                written.append({"target": normalized, "path": filename, "status": "written", "bytes": len(markdown.encode("utf-8"))})
            emit_json({"schema_version": AGENT_API_VERSION, "written": written})
            return
        assert target is not None
        normalized = target.lower()
        filenames = {"codex": "AGENTS.md", "claude": "CLAUDE.md"}
        if normalized not in filenames:
            raise KatzError("Target must be codex or claude", "validation_error", {"target": target})
        template_path = TEMPLATES_DIR / filenames[normalized]
        if not template_path.is_file():
            raise KatzError("Agent instruction template is missing", "not_found", {"target": target})
        markdown = template_path.read_text(encoding="utf-8")
        written_path = None
        if write and output is None:
            output = Path(filenames[normalized])
        if output is not None:
            if output.exists():
                raise KatzError("Refusing to overwrite an existing instruction file", "validation_error", {"output": str(output)})
            output.write_text(markdown, encoding="utf-8")
            written_path = str(output)
        response = {
            "schema_version": AGENT_API_VERSION,
            "target": normalized,
            "suggested_filename": filenames[normalized],
            "written": written_path,
            "bytes": len(markdown.encode("utf-8")),
        }
        if content:
            response["markdown"] = markdown
        emit_json(response)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@agent_app.command("schema")
def agent_schema(name: str) -> None:
    """Return one bundled JSON Schema by filename or stem."""
    normalized = name if name.endswith(".json") else f"{name}.schema.json"
    path = SCHEMAS_DIR / normalized
    try:
        resolved = path.resolve()
        resolved.relative_to(SCHEMAS_DIR.resolve())
    except (OSError, ValueError):
        fail("Schema not found", "not_found", {"name": name})
        return
    if not resolved.is_file():
        fail("Schema not found", "not_found", {
            "name": name,
            "available": sorted(item.name for item in SCHEMAS_DIR.glob("*.json")),
        })
        return
    emit_json({"name": resolved.name, "schema": json.loads(resolved.read_text(encoding="utf-8"))})
