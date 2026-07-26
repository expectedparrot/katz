"""`katz issue` commands: write, triage, investigate, carry forward."""
from __future__ import annotations

import json
import typer
import uuid
from pathlib import Path
from typing import Any, List, Optional

from ..assets import AGENT_API_VERSION
from ..definitions import _parse_spotter
from ..errors import KatzError, emit_json, fail
from ..issues import (
    VALID_STATES,
    _full_issue_record,
    _issue_dir,
    _issue_dir_for_id,
    _issue_duplicate_clusters,
    _load_issue,
)
from ..manuscript import _quote_matches, resolve_location, section_for_range
from ..storage import load_version, now_utc, parse_meta, read_json, write_event_json, write_json
from .agent import _agent_action


issue_app = typer.Typer(help="Write and query issue records.")


@issue_app.command("write")
def issue_write(
    title: str = typer.Option(..., "--title"),
    byte_start: int = typer.Option(..., "--byte-start"),
    byte_end: int = typer.Option(..., "--byte-end"),
    body: str = typer.Option(..., "--body"),
    state: str = typer.Option("draft", "--state"),
    spotter: Optional[str] = typer.Option(None, "--spotter"),
    artifacts: Optional[str] = typer.Option(None, "--artifacts", help="Comma-separated list of related repo files (scripts, data, notebooks)"),
    meta: Optional[str] = typer.Option(None, "--meta"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Write an issue and hydrate its location from the canonical manuscript."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        resolved, dest, _, _, canonical = load_version(commit)
        if spotter is not None and not (dest / "spotters" / f"{spotter}.md").exists():
            raise KatzError(f"Spotter '{spotter}' is not registered", "not_found", {"spotter": spotter})
        artifact_list = [a.strip() for a in artifacts.split(",") if a.strip()] if artifacts else []
        issue_id = uuid.uuid4().hex
        timestamp = now_utc()
        record = {
            "schema_version": 2,
            "id": issue_id,
            "commit": resolved,
            "title": title,
            "body": body,
            "spotter": spotter,
            "artifacts": artifact_list,
            "location": resolve_location(canonical, byte_start, byte_end),
            "created_at": timestamp,
            "meta": parse_meta(meta),
        }
        issue_dir = _issue_dir(dest, issue_id)
        (issue_dir / "status").mkdir(parents=True, exist_ok=True)
        (issue_dir / "investigations").mkdir(parents=True, exist_ok=True)
        write_json(issue_dir / "issue.json", record)
        status_record = {"state": state, "reason": "created", "timestamp": timestamp}
        write_event_json(issue_dir / "status", status_record)
        record["state"] = state
        emit_json(record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("update")
def issue_update(
    issue_id: str = typer.Option(..., "--id"),
    state: Optional[str] = typer.Option(None, "--state"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    title: Optional[str] = typer.Option(None, "--title"),
    body: Optional[str] = typer.Option(None, "--body"),
    meta: Optional[str] = typer.Option(None, "--meta", help="JSON object merged into the issue meta."),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Update an issue by appending status and/or edit records.

    State changes append to status/; title, body, and meta changes append an
    edit event to edits/. The original issue.json is never rewritten, so the
    complete history stays inspectable.
    """
    try:
        if state is None and title is None and body is None and meta is None:
            raise KatzError(
                "Provide at least one of --state, --title, --body, or --meta",
                "validation_error",
            )
        if state is not None and state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        _, dest, _, _, _ = load_version(commit)
        _, issue_dir = _issue_dir_for_id(dest, issue_id)
        timestamp = now_utc()
        applied: dict[str, Any] = {}
        if title is not None or body is not None or meta is not None:
            fields: dict[str, Any] = {}
            if title is not None:
                fields["title"] = title
            if body is not None:
                fields["body"] = body
            if meta is not None:
                fields["meta"] = parse_meta(meta)
            edit_record = {"timestamp": timestamp, "reason": reason, "fields": fields}
            write_event_json(issue_dir / "edits", edit_record)
            applied["edit"] = edit_record
        if state is not None:
            status_record = {"state": state, "reason": reason, "timestamp": timestamp}
            write_event_json(issue_dir / "status", status_record)
            applied.update(status_record)
        record = _load_issue(issue_dir)
        applied["issue"] = {
            "id": record.get("id"),
            "state": record.get("state"),
            "title": record.get("title"),
            "body": record.get("body"),
            "meta": record.get("meta"),
        }
        emit_json(applied)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("patch")
def issue_patch(
    issue_id: str = typer.Argument(..., help="Issue id or unambiguous prefix."),
    field: str = typer.Argument(..., help="Meta field name to set (e.g. severity)."),
    value: str = typer.Argument(..., help="Value; parsed as JSON when valid, else stored as a string."),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Set a single meta field via an append-only edit event."""
    try:
        _, dest, _, _, _ = load_version(commit)
        resolved_id, issue_dir = _issue_dir_for_id(dest, issue_id)
        try:
            parsed_value: Any = json.loads(value)
        except json.JSONDecodeError:
            parsed_value = value
        timestamp = now_utc()
        edit_record = {"timestamp": timestamp, "reason": None, "fields": {"meta": {field: parsed_value}}}
        write_event_json(issue_dir / "edits", edit_record)
        record = _load_issue(issue_dir)
        emit_json({
            "id": resolved_id,
            "field": field,
            "value": parsed_value,
            "meta": record.get("meta"),
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("carry-forward")
def issue_carry_forward(
    to: str = typer.Option(..., "--to", help="Target version: registered commit SHA or unambiguous prefix."),
    from_commit: Optional[str] = typer.Option(None, "--from", help="Source version; defaults to the active version."),
    states: str = typer.Option(
        "confirmed,open",
        "--states",
        help="Comma-separated source issue states to check.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Create draft issues in the target version for persisted findings.",
    ),
) -> None:
    """Check which issues' anchored text survives in another registered version.

    For every selected source issue, the exact quoted passage is searched in the
    target version's canonical manuscript (whitespace-tolerant). The command is
    read-only by default; --apply files draft issues in the target version with
    meta.parent_issue_id linking back to the source finding. Ambiguous and
    missing passages are reported but never guessed.
    """
    try:
        state_filter = {item.strip() for item in states.split(",") if item.strip()}
        invalid_states = state_filter - VALID_STATES
        if invalid_states:
            raise KatzError(
                "Invalid issue state in --states",
                "validation_error",
                {"invalid": sorted(invalid_states), "valid": sorted(VALID_STATES)},
            )
        source_commit, source_dest, _, source_pmap, _ = load_version(from_commit)
        target_commit, target_dest, _, _, target_canonical = load_version(to)
        if source_commit == target_commit:
            raise KatzError(
                "Source and target versions are the same",
                "validation_error",
                {"commit": source_commit},
            )
        target_text = target_canonical.read_text(encoding="utf-8")

        existing_parent_ids: set[str] = set()
        if (target_dest / "issues").is_dir():
            for issue_path in (target_dest / "issues").glob("*/issue.json"):
                parent = read_json(issue_path).get("meta", {}).get("parent_issue_id")
                if isinstance(parent, str):
                    existing_parent_ids.add(parent)

        findings: list[dict[str, Any]] = []
        persisted = missing = ambiguous = applied = skipped_existing = 0
        issues_dir = source_dest / "issues"
        source_paths = sorted(issues_dir.glob("*/issue.json")) if issues_dir.is_dir() else []
        for issue_path in source_paths:
            record = _load_issue(issue_path.parent)
            if record.get("state") not in state_filter:
                continue
            location = record.get("location") if isinstance(record.get("location"), dict) else {}
            quoted = str(location.get("resolved_text") or "").strip()
            finding: dict[str, Any] = {
                "id": record.get("id"),
                "state": record.get("state"),
                "title": record.get("title"),
                "from_location": {
                    "byte_start": location.get("byte_start"),
                    "byte_end": location.get("byte_end"),
                    "line_start": location.get("line_start"),
                    "line_end": location.get("line_end"),
                    "section": section_for_range(
                        source_pmap.sections,
                        location.get("byte_start", -1),
                        location.get("byte_end", -1),
                    ),
                },
            }
            if not quoted:
                finding["status"] = "no_anchor"
                missing += 1
                findings.append(finding)
                continue
            matches = _quote_matches(target_text, quoted)
            if not matches:
                finding["status"] = "missing"
                missing += 1
                findings.append(finding)
                continue
            if len(matches) > 1:
                finding["status"] = "ambiguous"
                finding["occurrences"] = len(matches)
                ambiguous += 1
                findings.append(finding)
                continue
            char_start, char_end = matches[0]
            byte_start = len(target_text[:char_start].encode("utf-8"))
            byte_end = len(target_text[:char_end].encode("utf-8"))
            target_location = resolve_location(target_canonical, byte_start, byte_end)
            finding["status"] = "persisted"
            finding["moved"] = (
                target_location["line_start"] != location.get("line_start")
                or target_location["line_end"] != location.get("line_end")
            )
            finding["to_location"] = {
                "byte_start": target_location["byte_start"],
                "byte_end": target_location["byte_end"],
                "line_start": target_location["line_start"],
                "line_end": target_location["line_end"],
            }
            persisted += 1
            if apply:
                if record.get("id") in existing_parent_ids:
                    skipped_existing += 1
                    finding["applied"] = False
                    finding["already_carried"] = True
                    findings.append(finding)
                    continue
                spotter_name = record.get("spotter")
                spotter_available = (
                    isinstance(spotter_name, str)
                    and (target_dest / "spotters" / f"{spotter_name}.md").exists()
                )
                source_meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
                new_meta = dict(source_meta)
                new_meta.update({
                    "parent_issue_id": record.get("id"),
                    "parent_commit": source_commit,
                })
                if spotter_name and not spotter_available:
                    new_meta["original_spotter"] = spotter_name
                new_id = uuid.uuid4().hex
                timestamp = now_utc()
                new_record = {
                    "schema_version": 2,
                    "id": new_id,
                    "commit": target_commit,
                    "title": record.get("title"),
                    "body": record.get("body"),
                    "spotter": spotter_name if spotter_available else None,
                    "artifacts": list(record.get("artifacts") or []),
                    "location": target_location,
                    "created_at": timestamp,
                    "meta": new_meta,
                }
                new_issue_dir = _issue_dir(target_dest, new_id)
                (new_issue_dir / "status").mkdir(parents=True, exist_ok=True)
                (new_issue_dir / "investigations").mkdir(parents=True, exist_ok=True)
                write_json(new_issue_dir / "issue.json", new_record)
                write_event_json(new_issue_dir / "status", {
                    "state": "draft",
                    "reason": f"carried forward from {source_commit[:12]}",
                    "timestamp": timestamp,
                })
                existing_parent_ids.add(str(record.get("id")))
                finding["applied"] = True
                finding["new_issue_id"] = new_id
                applied += 1
            findings.append(finding)

        emit_json({
            "from": source_commit,
            "to": target_commit,
            "states": sorted(state_filter),
            "checked": len(findings),
            "persisted": persisted,
            "missing": missing,
            "ambiguous": ambiguous,
            "applied": applied,
            "already_carried": skipped_existing,
            "apply": apply,
            "findings": findings,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("merge")
def issue_merge(
    ids: str = typer.Option(..., "--ids", help="Comma-separated issue IDs to merge"),
    title: Optional[str] = typer.Option(None, "--title"),
    body: Optional[str] = typer.Option(None, "--body"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Merge multiple issues into a single parent. Children become wontfix."""
    try:
        child_ids = [i.strip() for i in ids.split(",") if i.strip()]
        if len(child_ids) < 2:
            raise KatzError("Merge requires at least 2 issue IDs", "validation_error")

        resolved, dest, _, _, canonical = load_version(commit)

        # Load and validate all children
        children = []
        resolved_child_ids = []
        for cid in child_ids:
            resolved_child_id, child_dir = _issue_dir_for_id(dest, cid)
            resolved_child_ids.append(resolved_child_id)
            children.append(read_json(child_dir / "issue.json"))

        # Build parent issue
        if title is None:
            title = children[0].get("title", "Merged issue")
        if body is None:
            parts = []
            for child in children:
                child_title = child.get("title", "")
                child_body = child.get("body", "")
                parts.append(f"[{child['id'][:12]}] {child_title}: {child_body}")
            body = "\n\n".join(parts)

        # Union byte range across all children
        byte_starts = [c["location"]["byte_start"] for c in children if isinstance(c.get("location"), dict) and "byte_start" in c["location"]]
        byte_ends = [c["location"]["byte_end"] for c in children if isinstance(c.get("location"), dict) and "byte_end" in c["location"]]
        byte_start = min(byte_starts) if byte_starts else 0
        byte_end = max(byte_ends) if byte_ends else 1

        # Union artifacts across all children
        all_artifacts: list[str] = []
        seen_artifacts: set[str] = set()
        for child in children:
            for a in child.get("artifacts", []):
                if a not in seen_artifacts:
                    all_artifacts.append(a)
                    seen_artifacts.add(a)

        parent_id = uuid.uuid4().hex
        timestamp = now_utc()
        record = {
            "schema_version": 2,
            "id": parent_id,
            "commit": resolved,
            "title": title,
            "body": body[:2000],
            "spotter": None,
            "artifacts": all_artifacts,
            "location": resolve_location(canonical, byte_start, byte_end),
            "created_at": timestamp,
            "meta": {"merged_from": resolved_child_ids},
        }
        parent_dir = _issue_dir(dest, parent_id)
        (parent_dir / "status").mkdir(parents=True, exist_ok=True)
        (parent_dir / "investigations").mkdir(parents=True, exist_ok=True)
        write_json(parent_dir / "issue.json", record)
        status_record = {"state": "draft", "reason": "created via merge", "timestamp": timestamp}
        write_event_json(parent_dir / "status", status_record)

        # Mark children as wontfix
        for cid in resolved_child_ids:
            child_dir = _issue_dir(dest, cid)
            wontfix = {"state": "wontfix", "reason": f"Merged into {parent_id}", "timestamp": timestamp}
            write_event_json(child_dir / "status", wontfix)

        record["state"] = "draft"
        emit_json(record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("investigate")
def issue_investigate(
    issue_id: str = typer.Option(..., "--id"),
    verdict: str = typer.Option(..., "--verdict"),
    evidence: Optional[str] = typer.Option(None, "--evidence"),
    notes: Optional[str] = typer.Option(None, "--notes"),
    state: Optional[str] = typer.Option(None, "--state", help="Also update issue state (e.g. confirmed, rejected, open)"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Append an investigation record to an issue."""
    try:
        if verdict not in {"confirmed", "rejected", "uncertain"}:
            raise KatzError("Invalid verdict", "validation_error", {"verdict": verdict})
        if state is not None and state not in VALID_STATES:
            raise KatzError("Invalid state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        _, dest, _, _, _ = load_version(commit)
        _, issue_dir = _issue_dir_for_id(dest, issue_id)
        timestamp = now_utc()
        inv_record: dict[str, Any] = {"verdict": verdict, "timestamp": timestamp}
        if evidence is not None:
            inv_record["evidence"] = parse_meta(evidence) if evidence.startswith("[") or evidence.startswith("{") else evidence
        if notes is not None:
            inv_record["notes"] = notes
        write_event_json(issue_dir / "investigations", inv_record)

        target_state = state or {
            "confirmed": "confirmed",
            "rejected": "rejected",
            "uncertain": "open",
        }[verdict]
        reason = notes[:200] if notes else verdict
        status_record = {"state": target_state, "reason": reason, "timestamp": timestamp}
        write_event_json(issue_dir / "status", status_record)
        inv_record["state_updated"] = target_state

        emit_json(inv_record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("suggest")
def issue_suggest(
    issue_id: str = typer.Option(..., "--id"),
    text: str = typer.Option(..., "--text"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Append a suggested fix to an issue."""
    try:
        _, dest, _, _, _ = load_version(commit)
        _, issue_dir = _issue_dir_for_id(dest, issue_id)
        suggestions_dir = issue_dir / "suggestions"
        suggestions_dir.mkdir(parents=True, exist_ok=True)
        timestamp = now_utc()
        record = {"text": text, "timestamp": timestamp}
        write_event_json(suggestions_dir, record)
        emit_json(record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("show")
def issue_show(
    issue_id: Optional[str] = typer.Argument(None),
    ids: Optional[str] = typer.Option(None, "--ids", help="Comma-separated issue IDs or unambiguous prefixes"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Return one or more full issue records with current state and history."""
    try:
        _, dest, _, pmap, _ = load_version(commit)
        if issue_id is not None and ids is not None:
            raise KatzError("Provide an issue id or --ids, not both", "validation_error")
        if issue_id is None and ids is None:
            raise KatzError("Provide an issue id or --ids", "validation_error")
        if ids is not None:
            requested_ids = [i.strip() for i in ids.split(",") if i.strip()]
            if not requested_ids:
                raise KatzError("--ids must include at least one issue id", "validation_error")
            emit_json([_full_issue_record(_issue_dir_for_id(dest, requested_id)[1], pmap) for requested_id in requested_ids])
            return
        _, issue_dir = _issue_dir_for_id(dest, issue_id)
        emit_json(_full_issue_record(issue_dir, pmap))
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("list")
def issue_list(
    state: Optional[str] = typer.Option(None, "--state"),
    section: Optional[str] = typer.Option(None, "--section"),
    spotter: Optional[str] = typer.Option(None, "--spotter"),
    meta: Optional[str] = typer.Option(None, "--meta"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """List issue summaries."""
    try:
        resolved, dest, _, pmap, _ = load_version(commit)
        meta_key = None
        meta_value: Any = None
        if meta is not None:
            if "=" not in meta:
                raise KatzError("--meta must be key=value", "validation_error", {"meta": meta})
            meta_key, raw_value = meta.split("=", 1)
            try:
                meta_value = json.loads(raw_value)
            except json.JSONDecodeError:
                meta_value = raw_value
        results = []
        issues_dir = dest / "issues"
        if not issues_dir.is_dir():
            emit_json([])
            return
        for issue_dir in sorted(issues_dir.iterdir()):
            if not issue_dir.is_dir() or not (issue_dir / "issue.json").exists():
                continue
            record = _load_issue(issue_dir)
            if record.get("commit") != resolved:
                continue
            if state is not None and record.get("state") != state:
                continue
            if spotter is not None and record.get("spotter") != spotter:
                continue
            record_meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            if meta_key is not None and record_meta.get(meta_key) != meta_value:
                continue
            location = record.get("location") if isinstance(record.get("location"), dict) else {}
            record_section = None
            if isinstance(location.get("byte_start"), int) and isinstance(location.get("byte_end"), int):
                record_section = section_for_range(pmap.sections, location["byte_start"], location["byte_end"])
            if section is not None and record_section != section:
                continue
            results.append(
                {
                    "id": record.get("id"),
                    "state": record.get("state"),
                    "title": record.get("title"),
                    "spotter": record.get("spotter"),
                    "location": {
                        "line_start": location.get("line_start"),
                        "line_end": location.get("line_end"),
                        "section": record_section,
                    },
                    "meta": record_meta,
                }
            )
        emit_json(results)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("next")
def issue_next(
    state: str = typer.Option("draft", "--state"),
    context_lines: int = typer.Option(3, "--context-lines", min=0, max=20),
    view: str = typer.Option("full", "--view", help="full or compact"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Return one complete, deterministic issue investigation packet."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state})
        if view not in {"full", "compact"}:
            raise KatzError("Invalid view", "validation_error", {"view": view, "valid": ["full", "compact"]})
        resolved, dest, _, pmap, canonical = load_version(commit)
        candidates: list[tuple[str, Path]] = []
        issues_dir = dest / "issues"
        if issues_dir.is_dir():
            for path in sorted(issues_dir.glob("*/issue.json")):
                record = _load_issue(path.parent)
                if record.get("state") == state:
                    candidates.append((str(record.get("created_at", "")), path.parent))
        if not candidates:
            emit_json({
                "schema_version": AGENT_API_VERSION,
                "commit": resolved,
                "state": state,
                "issue": None,
                "remaining": 0,
                "next_actions": [],
            })
            return
        candidates.sort(key=lambda item: (item[0], item[1].name))
        issue_dir = candidates[0][1]
        issue = _full_issue_record(issue_dir, pmap)
        location = issue.get("location", {})
        manuscript_lines = canonical.read_text(encoding="utf-8").splitlines()
        line_start = int(location.get("line_start") or 1)
        line_end = int(location.get("line_end") or line_start)
        context_start = max(1, line_start - context_lines)
        context_end = min(len(manuscript_lines), line_end + context_lines)
        context = "\n".join(
            f"{number}: {manuscript_lines[number - 1]}"
            for number in range(context_start, context_end + 1)
        )
        spotter_instructions = None
        spotter_name = issue.get("spotter")
        if spotter_name:
            spotter_path = dest / "spotters" / f"{spotter_name}.md"
            if spotter_path.is_file():
                spotter_instructions = _parse_spotter(spotter_path.read_text(encoding="utf-8"))
        issue_id = str(issue["id"])
        if view == "compact":
            issue = {
                "id": issue.get("id"),
                "state": issue.get("state"),
                "title": issue.get("title"),
                "body": issue.get("body"),
                "spotter": issue.get("spotter"),
                "location": issue.get("location"),
            }
            spotter_instructions = None
        emit_json({
            "schema_version": AGENT_API_VERSION,
            "commit": resolved,
            "state": state,
            "issue": issue,
            "manuscript_context": {
                "line_start": context_start,
                "line_end": context_end,
                "numbered_text": context,
            },
            "review_procedure": spotter_instructions,
            "remaining": len(candidates),
            "allowed_verdicts": ["confirmed", "rejected", "uncertain"],
            "next_actions": [
                _agent_action(
                    "record_investigation",
                    "Record an evidence-backed verdict after checking the manuscript and related artifacts",
                    [
                        "katz", "issue", "investigate", "--id", issue_id[:12],
                        "--verdict", "<confirmed|rejected|uncertain>",
                        "--notes", "<evidence-backed notes>",
                    ],
                    mutates_state=True,
                ),
                _agent_action(
                    "show_issue",
                    "Re-read the complete issue record",
                    ["katz", "issue", "show", issue_id[:12]],
                    mutates_state=False,
                ),
            ],
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("clusters")
def issue_clusters(commit: Optional[str] = typer.Option(None, "--commit")) -> None:
    """Suggest groups of overlapping or textually similar issue candidates."""
    try:
        resolved, dest, _, _, _ = load_version(commit)
        clusters = _issue_duplicate_clusters(dest)
        emit_json({"commit": resolved, "cluster_count": len(clusters), "clusters": clusters})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("merge-suggest")
def issue_merge_suggest(commit: Optional[str] = typer.Option(None, "--commit")) -> None:
    """Return duplicate clusters and explicit merge commands without mutating issues."""
    issue_clusters(commit=commit)
