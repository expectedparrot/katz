"""`katz spotter` commands: catalogs, enablement, jobs, models, ingest."""
from __future__ import annotations

import hashlib
import json
import shutil
import typer
import uuid
from pathlib import Path
from typing import Any, List, Optional

from ..assets import CATALOG_DIR
from ..definitions import VALID_SCOPES, _load_collection, _parse_spotter, _slugify
from ..edsl_bridge import (
    SPOTTER_QUESTION_TEXT,
    SPOTTER_RECOMMENDED_MAX_TOKENS,
    SPOTTER_VERDICT_SUFFIX,
    _answer_is_found,
    _audit_spotter_results,
    _coerce_spotter_answer,
    _edsl_imports,
    _expected_results_path,
    _group_positive_findings,
    _resolve_audit_jobs,
    _result_value,
    _save_and_verify_ep,
)
from ..errors import KatzError, emit_json, fail
from ..issues import VALID_STATES, _issue_dir
from ..manuscript import _locate_quoted_text, resolve_location
from ..storage import (
    ensure_initialized,
    katz_root,
    load_version,
    now_utc,
    read_json,
    record_run,
    write_event_json,
    write_json,
)


spotter_app = typer.Typer(help="Manage issue spotters.")


@spotter_app.command("init-catalog")
def spotter_init_catalog(
    preset: str = typer.Option("default", "--preset"),
) -> None:
    """Populate the spotter catalog (.katz/spotters/) from a preset. Skips existing."""
    try:
        names = _load_collection("spotters", preset)
        ensure_initialized()
        catalog_dir = katz_root() / "spotters"
        catalog_dir.mkdir(parents=True, exist_ok=True)

        added = []
        skipped = []
        for slug in names:
            src_path = CATALOG_DIR / "spotters" / f"{slug}.md"
            if not src_path.exists():
                raise KatzError(f"Spotter '{slug}' listed in collection but file not found", "not_found", {"name": slug})
            out_path = catalog_dir / f"{slug}.md"
            if out_path.exists():
                skipped.append(slug)
                continue
            content = src_path.read_text(encoding="utf-8")
            out_path.write_text(content, encoding="utf-8")
            parsed = _parse_spotter(content)
            added.append({"name": slug, "scope": parsed["scope"]})

        emit_json({"preset": preset, "added": added, "skipped": skipped})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("catalog")
def spotter_catalog(
    scope: Optional[str] = typer.Option(None, "--scope"),
) -> None:
    """List available spotters in the catalog (.katz/spotters/)."""
    try:
        ensure_initialized()
        catalog_dir = katz_root() / "spotters"
        results = []
        if catalog_dir.is_dir():
            for f in sorted(catalog_dir.glob("*.md")):
                content = f.read_text(encoding="utf-8")
                parsed = _parse_spotter(content)
                if scope is not None and parsed["scope"] != scope:
                    continue
                results.append({
                    "name": f.stem,
                    "title": parsed["title"],
                    "scope": parsed["scope"],
                    "has_investigation": parsed["investigation"] is not None,
                })
        emit_json(results)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("catalog-show")
def spotter_catalog_show(name: str) -> None:
    """Show a spotter from the catalog."""
    try:
        ensure_initialized()
        path = katz_root() / "spotters" / f"{name}.md"
        if not path.exists():
            raise KatzError(f"Spotter '{name}' not in catalog", "not_found", {"name": name})
        content = path.read_text(encoding="utf-8")
        parsed = _parse_spotter(content)
        emit_json({
            "name": name,
            "scope": parsed["scope"],
            "title": parsed["title"],
            "description": parsed["description"],
            "investigation": parsed["investigation"],
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("add")
def spotter_add(
    name: str = typer.Option(..., "--name"),
    scope: str = typer.Option("section", "--scope"),
    description: str = typer.Option(..., "--description"),
    investigation: Optional[str] = typer.Option(None, "--investigation"),
) -> None:
    """Create a new spotter in the catalog and auto-enable it for the active version."""
    try:
        if scope not in VALID_SCOPES:
            raise KatzError(f"Invalid scope: '{scope}'", "validation_error", {"scope": scope, "valid": sorted(VALID_SCOPES)})
        slug = _slugify(name)
        ensure_initialized()

        # Build the spotter markdown content
        title = name.replace("_", " ").replace("-", " ").title()
        lines = [
            f"---",
            f"scope: {scope}",
            f"---",
            f"# {title}",
            f"",
            description,
        ]
        if investigation:
            lines.extend(["", "## Investigation", "", investigation])
        content = "\n".join(lines) + "\n"

        # Write to catalog
        catalog_dir = katz_root() / "spotters"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        catalog_path = catalog_dir / f"{slug}.md"
        if catalog_path.exists():
            raise KatzError(f"Spotter '{slug}' already exists in catalog", "validation_error", {"name": slug})
        catalog_path.write_text(content, encoding="utf-8")

        # Also enable for the active version
        try:
            _, dest, _, _, _ = load_version(None)
            spotters_dir = dest / "spotters"
            spotters_dir.mkdir(parents=True, exist_ok=True)
            version_path = spotters_dir / f"{slug}.md"
            if not version_path.exists():
                shutil.copyfile(catalog_path, version_path)
        except KatzError:
            pass  # No active version — catalog-only is fine

        emit_json({"added": slug, "scope": scope, "catalog": str(catalog_path)})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("enable")
def spotter_enable(
    name: Optional[str] = typer.Argument(None),
    commit: Optional[str] = typer.Option(None, "--commit"),
    recommended: bool = typer.Option(False, "--recommended", help="Enable the built-in default review set."),
    all_spotters: bool = typer.Option(False, "--all", help="Enable every spotter currently in the catalog."),
) -> None:
    """Enable a catalog spotter for the active version (copies it from catalog to version)."""
    try:
        ensure_initialized()
        _, dest, _, _, _ = load_version(commit)
        spotters_dir = dest / "spotters"
        spotters_dir.mkdir(parents=True, exist_ok=True)
        selected = sum(bool(value) for value in (name, recommended, all_spotters))
        if selected != 1:
            raise KatzError(
                "Choose exactly one name, --recommended, or --all",
                "validation_error",
            )
        catalog_dir = katz_root() / "spotters"
        if recommended:
            names = _load_collection("spotters", "default")
        elif all_spotters:
            names = sorted(path.stem for path in catalog_dir.glob("*.md"))
        else:
            names = [str(name)]
        enabled: list[str] = []
        already_enabled: list[str] = []
        for selected_name in names:
            catalog_path = catalog_dir / f"{selected_name}.md"
            if not catalog_path.exists():
                raise KatzError(f"Spotter '{selected_name}' not in catalog", "not_found", {"name": selected_name})
            out_path = spotters_dir / f"{selected_name}.md"
            if out_path.exists():
                already_enabled.append(selected_name)
                continue
            shutil.copyfile(catalog_path, out_path)
            enabled.append(selected_name)
        if name is not None:
            emit_json({"enabled": name, "already_enabled": bool(already_enabled)})
        else:
            emit_json({
                "selection": "recommended" if recommended else "all",
                "enabled": enabled,
                "already_enabled": already_enabled,
                "enabled_count": len(enabled),
            })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("list")
def spotter_list(
    scope: Optional[str] = typer.Option(None, "--scope"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """List registered spotters."""
    try:
        _, dest, _, _, _ = load_version(commit)
        spotters_dir = dest / "spotters"
        results = []
        if spotters_dir.is_dir():
            for f in sorted(spotters_dir.glob("*.md")):
                content = f.read_text(encoding="utf-8")
                parsed = _parse_spotter(content)
                if scope is not None and parsed["scope"] != scope:
                    continue
                results.append({
                    "name": f.stem,
                    "title": parsed["title"],
                    "scope": parsed["scope"],
                    "has_investigation": parsed["investigation"] is not None,
                    "chars": len(content),
                })
        emit_json(results)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("show")
def spotter_show(
    name: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Return a spotter's parsed content."""
    try:
        _, dest, _, _, _ = load_version(commit)
        path = dest / "spotters" / f"{name}.md"
        if not path.exists():
            raise KatzError(f"Spotter '{name}' does not exist", "not_found", {"name": name})
        content = path.read_text(encoding="utf-8")
        parsed = _parse_spotter(content)
        emit_json({
            "name": name,
            "scope": parsed["scope"],
            "title": parsed["title"],
            "description": parsed["description"],
            "investigation": parsed["investigation"],
            "content": content,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("remove")
def spotter_remove(
    name: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Remove a registered spotter."""
    try:
        _, dest, _, _, _ = load_version(commit)
        path = dest / "spotters" / f"{name}.md"
        if not path.exists():
            raise KatzError(f"Spotter '{name}' does not exist", "not_found", {"name": name})
        path.unlink()
        emit_json({"removed": name})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("jobs")
def spotter_jobs(
    output: Path = typer.Option(Path("jobs.ep"), "--output", "-o"),
    section: Optional[str] = typer.Option(None, "--section"),
    spotters: Optional[str] = typer.Option(None, "--spotters", help="Comma-separated enabled spotter names"),
    pilot: Optional[int] = typer.Option(None, "--pilot", min=1, help="Build a small deterministic compatibility pilot."),
    from_failures: Optional[Path] = typer.Option(
        None, "--from-failures", exists=True, readable=True,
        help="Repackage only the scenarios that did not return a valid answer in this Results file.",
    ),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Build an EDSL Jobs package from enabled spotters and manuscript content."""
    try:
        if output.suffix != ".ep":
            raise KatzError("--output must use the .ep extension", "validation_error", {"output": str(output)})
        if output.exists():
            raise KatzError(f"{output} already exists", "validation_error", {"output": str(output)})

        Jobs, Scenario, ScenarioList, QuestionDict = _edsl_imports()
        resolved, dest, _, pmap, canonical = load_version(commit)
        content = canonical.read_text(encoding="utf-8")
        enabled_dir = dest / "spotters"
        requested = {name.strip() for name in spotters.split(",") if name.strip()} if spotters else None

        definitions: list[dict[str, Any]] = []
        for path in sorted(enabled_dir.glob("*.md")) if enabled_dir.is_dir() else []:
            if requested is not None and path.stem not in requested:
                continue
            parsed = _parse_spotter(path.read_text(encoding="utf-8"))
            definitions.append({"name": path.stem, "content": path.read_text(encoding="utf-8"), **parsed})
        if requested is not None:
            missing = sorted(requested - {item["name"] for item in definitions})
            if missing:
                raise KatzError("Some requested spotters are not enabled", "not_found", {"spotters": missing})
        if not definitions:
            raise KatzError("No enabled spotters found", "not_found", {"commit": resolved})

        selected_sections = pmap.sections
        if section is not None:
            selected_sections = [item for item in pmap.sections if item.get("id") == section]
            if not selected_sections:
                raise KatzError(f"Section '{section}' not found", "not_found", {"section": section})

        section_map = "\n".join(
            f"- {item.get('title', item['id'])} (lines {item.get('line_start', '?')}–{item.get('line_end', '?')})"
            for item in pmap.sections
        )
        abstract_section = next(
            (item for item in pmap.sections if "abstract" in str(item.get("id", "")).lower()),
            None,
        )
        abstract_text = ""
        if abstract_section is not None:
            abstract_text = content.encode("utf-8")[
                int(abstract_section["byte_start"]):int(abstract_section["byte_end"])
            ].decode("utf-8")
        paper_context = (
            "Section map:\n" + section_map
            + ("\n\nAbstract:\n" + abstract_text if abstract_text else "")
        )

        scenarios: list[Any] = []
        for definition in definitions:
            if definition["scope"] == "section":
                for item in selected_sections:
                    byte_start = int(item["byte_start"])
                    byte_end = int(item["byte_end"])
                    scenarios.append(Scenario({
                        "katz_commit": resolved,
                        "spotter_name": definition["name"],
                        "spotter_scope": "section",
                        "section_id": item["id"],
                        "section_title": item.get("title", item["id"]),
                        "byte_start": byte_start,
                        "byte_end": byte_end,
                        "review_target": f'section "{item.get("title", item["id"])}"',
                        "spotter_instructions": definition["content"],
                        "paper_context": paper_context,
                        "manuscript_content": content.encode("utf-8")[byte_start:byte_end].decode("utf-8"),
                    }))
            else:
                scenarios.append(Scenario({
                    "katz_commit": resolved,
                    "spotter_name": definition["name"],
                    "spotter_scope": "holistic",
                    "section_id": None,
                    "section_title": "Complete manuscript",
                    "byte_start": 0,
                    "byte_end": len(content.encode("utf-8")),
                    "review_target": "the complete manuscript",
                    "spotter_instructions": definition["content"],
                    "paper_context": "This scenario contains the complete manuscript.",
                    "manuscript_content": content,
                }))
        if pilot is not None:
            scenarios = scenarios[:pilot]

        if from_failures is not None:
            # Keep only scenarios whose (spotter, section) did not already return a
            # valid answer — i.e. null/invalid/truncated rows AND scenarios that
            # never returned at all — so the re-run covers exactly the gap.
            prior = _audit_spotter_results(from_failures)
            valid_identities = {
                (row["scenario"].get("spotter_name"), row["scenario"].get("section_id"))
                for row in prior["_rows"] if row["valid"]
            }
            scenarios = [
                scenario for scenario in scenarios
                if (dict(scenario).get("spotter_name"), dict(scenario).get("section_id"))
                not in valid_identities
            ]
            if not scenarios:
                raise KatzError(
                    "No failing scenarios to re-run; the Results file is already valid for these spotters/sections",
                    "validation_error",
                    {"from_failures": str(from_failures)},
                )

        from edsl.questions import QuestionFreeText

        question = QuestionFreeText(
            question_name="spotter_result",
            question_text=SPOTTER_QUESTION_TEXT + SPOTTER_VERDICT_SUFFIX,
        )
        job = Jobs(survey=question.to_survey()).by(ScenarioList(scenarios))
        saved = _save_and_verify_ep(job, output)
        expected_results = _expected_results_path(output)
        record_run(
            dest, "spotter", "packaged",
            jobs_path=str(output.resolve()),
            expected_results_path=str(expected_results.resolve()),
            question="spotter_result",
            scenario_count=len(scenarios),
            spotters=[item["name"] for item in definitions],
            pilot=pilot,
        )
        section_jobs = sum(1 for scenario in scenarios if scenario["spotter_scope"] == "section")
        holistic_jobs = len(scenarios) - section_jobs
        emit_json({
            "object_type": "Jobs",
            "output": str(output),
            "commit": resolved,
            "question": "spotter_result",
            "spotters": [item["name"] for item in definitions],
            "scenario_count": len(scenarios),
            "expected_model_calls": (
                f"{len(scenarios)} × the number of models in the external ModelList"
            ),
            "model_specifications": "Provided by the explicit models.ep ModelList.",
            "likely_cost": "Provider-dependent; inspect the ModelList and ep estimate before approval.",
            "inference": "external",
            "section_scenarios": section_jobs,
            "holistic_scenarios": holistic_jobs,
            "pilot": pilot is not None,
            "estimated_prompt_characters": sum(
                len(str(dict(scenario).get("manuscript_content", "")))
                + len(str(dict(scenario).get("paper_context", "")))
                + len(str(dict(scenario).get("spotter_instructions", "")))
                for scenario in scenarios
            ),
            "answer_contract": {
                "question_type": "free_text_with_json_verdict",
                "verdict_keys": ["found", "title", "quoted_text", "description"],
                "parser": "katz._coerce_spotter_answer",
                "rationale": "Free-text reasoning followed by a fenced JSON verdict; avoids null answers and schema-forced false negatives on deliberation-heavy spotters.",
                "recommended_max_tokens": SPOTTER_RECOMMENDED_MAX_TOKENS,
                "token_budget_note": (
                    f"Set max_tokens >= {SPOTTER_RECOMMENDED_MAX_TOKENS} via a ModelList; the "
                    "provider default truncates long issue-finding answers before the JSON "
                    "verdict, which the audit reports as unparseable_answer."
                ),
                "pilot_required_before_large_run": len(scenarios) > 20,
            },
            "saved": saved,
            "next": (
                f"katz spotter models --model <model-name> --max-tokens "
                f"{SPOTTER_RECOMMENDED_MAX_TOKENS} --output models.ep && "
                f"ep run {output} --model_list models.ep --output {expected_results}"
            ),
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"output": str(output)})


@spotter_app.command("models")
def spotter_models(
    models: List[str] = typer.Option(..., "--model", help="Model name; repeat for multiple models."),
    service: Optional[str] = typer.Option(None, "--service", help="Inference service for all models (e.g. anthropic, openai)."),
    max_tokens: int = typer.Option(SPOTTER_RECOMMENDED_MAX_TOKENS, "--max-tokens", min=1),
    reasoning_effort: Optional[str] = typer.Option(None, "--reasoning-effort"),
    output: Path = typer.Option(Path("models.ep"), "--output", "-o"),
) -> None:
    """Write an EDSL ModelList package with an adequate max_tokens for free-text
    spotter runs, so `ep run <jobs> --model_list <output>` is executable.

    Free-text verdicts truncate under the provider default; `ep run` has no
    --max-tokens flag, so the budget must travel in the ModelList.
    """
    try:
        if output.suffix != ".ep":
            raise KatzError("--output must use the .ep extension", "validation_error", {"output": str(output)})
        if output.exists():
            raise KatzError(f"{output} already exists", "validation_error", {"output": str(output)})
        _edsl_imports()
        from edsl import Model, ModelList

        built = []
        for name in models:
            kwargs: dict[str, Any] = {"max_tokens": max_tokens}
            if service:
                kwargs["service_name"] = service
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort
            built.append(Model(name, **kwargs))
        _save_and_verify_ep(ModelList(built), output)
        emit_json({
            "object_type": "ModelList",
            "output": str(output),
            "models": list(models),
            "max_tokens": max_tokens,
            "service": service,
            "reasoning_effort": reasoning_effort,
            "next": f"ep run <jobs.ep> --model_list {output} --output <results.ep>",
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"output": str(output)})


@spotter_app.command("ingest")
def spotter_ingest(
    results_path: Path = typer.Argument(..., exists=True, readable=True),
    state: str = typer.Option("draft", "--state"),
    commit: Optional[str] = typer.Option(None, "--commit"),
    jobs: Optional[Path] = typer.Option(None, "--jobs", exists=True, readable=True),
    allow_partial: bool = typer.Option(False, "--allow-partial"),
) -> None:
    """Parse an EDSL Results package and file manuscript-anchored Katz issues."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        _edsl_imports()
        from edsl import Results

        resolved, dest, _, _, canonical = load_version(commit)
        resolved_jobs = _resolve_audit_jobs(dest, results_path, jobs)
        audit = _audit_spotter_results(results_path, resolved_jobs)
        audit_rows = audit.pop("_rows")
        if not audit["complete"] and not allow_partial:
            record_run(
                dest, "spotter", "invalid",
                results_path=str(results_path.resolve()),
                audit=audit,
            )
            raise KatzError(
                "Refusing to ingest incomplete or unauditable spotter Results",
                "incomplete_results",
                {
                    "audit": audit,
                    "next_actions": [
                        ["katz", "results", "failures", str(results_path)],
                        ["katz", "results", "audit", str(results_path)]
                        + (["--jobs", str(resolved_jobs)] if resolved_jobs else ["--jobs", "<jobs.ep>"]),
                    ],
                },
            )
        results = Results.git.load(results_path)
        manuscript = canonical.read_text(encoding="utf-8")
        existing_keys: set[str] = set()
        issues_dir = dest / "issues"
        if issues_dir.is_dir():
            for issue_path in issues_dir.glob("*/issue.json"):
                record = read_json(issue_path)
                meta = record.get("meta", {}) if isinstance(record.get("meta"), dict) else {}
                result_key = meta.get("edsl_result_key")
                if result_key:
                    existing_keys.add(result_key)
                for member_key in meta.get("edsl_result_keys") or []:
                    if isinstance(member_key, str):
                        existing_keys.add(member_key)

        found = filed = skipped = 0
        issue_ids: list[str] = []
        skipped_details: list[dict[str, Any]] = []
        positives: list[dict[str, Any]] = []
        for result_index, result in enumerate(results):
            if result_index < len(audit_rows) and not audit_rows[result_index]["valid"]:
                skipped += 1
                continue
            answer = _coerce_spotter_answer(_result_value(result, "answer", "spotter_result"))
            scenario = result["scenario"] if isinstance(result["scenario"], dict) else dict(result["scenario"])
            if not isinstance(answer, dict) or not _answer_is_found(answer.get("found")):
                continue
            found += 1
            if scenario.get("katz_commit") != resolved:
                raise KatzError(
                    "Results were generated for a different Katz version",
                    "validation_error",
                    {"expected": resolved, "actual": scenario.get("katz_commit")},
                )
            spotter_name = str(scenario.get("spotter_name", ""))
            if not (dest / "spotters" / f"{spotter_name}.md").exists():
                raise KatzError("Result references a spotter not enabled for this version", "not_found", {"spotter": spotter_name})
            quoted = str(answer.get("quoted_text", "")).strip()
            range_start = int(scenario.get("byte_start", 0))
            range_end = int(scenario.get("byte_end", len(manuscript.encode("utf-8"))))
            region = manuscript.encode("utf-8")[range_start:range_end].decode("utf-8")
            located = _locate_quoted_text(region, quoted) if quoted else None
            if located is None:
                skipped += 1
                # A positive finding whose quotation cannot be located in its
                # section region is dropped rather than anchored to a guessed
                # offset; surface it so the reviewer can recover it by hand.
                skipped_details.append({
                    "reason": "quote_not_located" if quoted else "missing_quote",
                    "spotter": spotter_name,
                    "section_id": scenario.get("section_id"),
                    "title": str(answer.get("title", "")),
                    "quoted_text": quoted[:200],
                })
                continue
            char_start, char_end = located
            byte_start = range_start + len(region[:char_start].encode("utf-8"))
            byte_end = range_start + len(region[:char_end].encode("utf-8"))
            model = _result_value(result, "model", "model") or _result_value(result, "model", "_model_") or "unknown"
            key_payload = json.dumps(
                {"commit": resolved, "spotter": spotter_name, "model": str(model), "answer": answer, "scenario": scenario},
                sort_keys=True,
                default=str,
            )
            result_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
            positives.append({
                "spotter": spotter_name,
                "byte_start": byte_start,
                "byte_end": byte_end,
                "model": str(model),
                "answer": answer,
                "result_key": result_key,
            })

        model_count = max(1, len([name for name in audit.get("models", []) if name]))
        for group in _group_positive_findings(positives):
            member_keys = sorted({member["result_key"] for member in group})
            group_key = hashlib.sha256("\n".join(member_keys).encode("utf-8")).hexdigest()
            if group_key in existing_keys or any(key in existing_keys for key in member_keys):
                skipped += 1
                continue
            primary = group[0]
            models_flagging = sorted({member["model"] for member in group})
            agreement = round(min(1.0, len(models_flagging) / model_count), 6)
            byte_start = min(member["byte_start"] for member in group)
            byte_end = max(member["byte_end"] for member in group)
            issue_id = uuid.uuid4().hex
            timestamp = now_utc()
            issue_dir = _issue_dir(dest, issue_id)
            (issue_dir / "status").mkdir(parents=True, exist_ok=True)
            (issue_dir / "investigations").mkdir(parents=True, exist_ok=True)
            record = {
                "schema_version": 2,
                "id": issue_id,
                "commit": resolved,
                "title": str(primary["answer"].get("title", "Untitled issue")),
                "body": str(primary["answer"].get("description", "")),
                "spotter": primary["spotter"],
                "artifacts": [],
                "location": resolve_location(canonical, byte_start, byte_end),
                "created_at": timestamp,
                "meta": {
                    "edsl_result_key": group_key,
                    "edsl_result_keys": member_keys,
                    "edsl_model": primary["model"],
                    "edsl_models": models_flagging,
                    "agreement": agreement,
                    "edsl_results_path": str(results_path),
                },
            }
            write_json(issue_dir / "issue.json", record)
            write_event_json(issue_dir / "status", {"state": state, "reason": "imported from EDSL Results", "timestamp": timestamp})
            existing_keys.add(group_key)
            existing_keys.update(member_keys)
            issue_ids.append(issue_id)
            filed += 1

        record_run(
            dest, "spotter", "partial" if not audit["complete"] else "ingested",
            results_path=str(results_path.resolve()),
            result_count=len(results),
            issues_found=found,
            issues_filed=filed,
            skipped=skipped,
            audit=audit,
        )
        emit_json({
            "results": str(results_path),
            "commit": resolved,
            "result_count": len(results),
            "issues_found": found,
            "issues_filed": filed,
            "cross_model_merged": max(0, len(positives) - len(_group_positive_findings(positives))),
            "skipped": skipped,
            "skipped_details": skipped_details,
            "issue_ids": issue_ids,
            "audit": audit,
            "run_status": "partial" if not audit["complete"] else "ingested",
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"results": str(results_path)})
