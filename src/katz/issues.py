"""Issue record model: directories, state, edits, history, clusters."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, List, Optional

from .errors import KatzError
from .manuscript import section_for_range
from .storage import PaperMap, now_utc, read_json, write_event_json


def _issue_count(dest: Path) -> int:
    issues_dir = dest / "issues"
    if not issues_dir.is_dir():
        return 0
    return sum(1 for path in issues_dir.glob("*/issue.json"))


VALID_STATES = {"draft", "open", "confirmed", "rejected", "resolved", "wontfix"}
VALID_VERDICTS = {"confirmed", "rejected", "uncertain"}


def _issue_dir(dest: Path, issue_id: str) -> Path:
    return dest / "issues" / issue_id


def _resolve_issue_id(dest: Path, issue_id: str) -> str:
    """Resolve a full issue id or unambiguous prefix to the canonical id."""
    issues_dir = dest / "issues"
    if not issues_dir.is_dir():
        raise KatzError("Issue does not exist", "not_found", {"id": issue_id})
    if len(issue_id) == 32 and _issue_dir(dest, issue_id).is_dir():
        return issue_id
    matches = sorted(
        path.name
        for path in issues_dir.iterdir()
        if path.is_dir() and (path / "issue.json").exists() and path.name.startswith(issue_id)
    )
    if not matches:
        raise KatzError(
            "Issue does not exist; pass a full issue id or an unambiguous prefix",
            "not_found",
            {"id": issue_id},
        )
    if len(matches) > 1:
        raise KatzError(
            "Issue id prefix is ambiguous",
            "ambiguous_issue",
            {"id": issue_id, "matches": matches},
        )
    return matches[0]


def _issue_dir_for_id(dest: Path, issue_id: str) -> tuple[str, Path]:
    resolved = _resolve_issue_id(dest, issue_id)
    return resolved, _issue_dir(dest, resolved)


def _latest_status(issue_dir: Path) -> dict[str, Any] | None:
    """Read the most recent status file from an issue's status/ directory."""
    status_dir = issue_dir / "status"
    if not status_dir.is_dir():
        return None
    files = sorted(status_dir.glob("*.json"))
    if not files:
        return None
    return read_json(files[-1])


def _list_issue_edits(issue_dir: Path) -> list[dict[str, Any]]:
    """Return append-only edit events for an issue, oldest first."""
    edits_dir = issue_dir / "edits"
    if not edits_dir.is_dir():
        return []
    return [read_json(path) for path in sorted(edits_dir.glob("*.json"))]


def _apply_issue_edits(record: dict[str, Any], edits: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply edit events in order: title/body replace, meta keys merge."""
    for edit in edits:
        fields = edit.get("fields")
        if not isinstance(fields, dict):
            continue
        if isinstance(fields.get("title"), str):
            record["title"] = fields["title"]
        if isinstance(fields.get("body"), str):
            record["body"] = fields["body"]
        if isinstance(fields.get("meta"), dict):
            meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            meta.update(fields["meta"])
            record["meta"] = meta
    return record


def _load_issue(issue_dir: Path) -> dict[str, Any]:
    """Load an issue record, merging in current state from status/ and edits/."""
    record = read_json(issue_dir / "issue.json")
    record = _apply_issue_edits(record, _list_issue_edits(issue_dir))
    latest = _latest_status(issue_dir)
    record["state"] = latest["state"] if latest else "draft"
    return record


def _list_investigations(issue_dir: Path) -> list[dict[str, Any]]:
    """Return all investigation records for an issue, oldest first."""
    inv_dir = issue_dir / "investigations"
    if not inv_dir.is_dir():
        return []
    return [read_json(f) for f in sorted(inv_dir.glob("*.json"))]


def _full_issue_record(issue_dir: Path, pmap: PaperMap) -> dict[str, Any]:
    """Return a full issue record with current state, history, suggestions, and section."""
    record = _load_issue(issue_dir)
    location = record.get("location") if isinstance(record.get("location"), dict) else {}
    if isinstance(location.get("byte_start"), int) and isinstance(location.get("byte_end"), int):
        location["section"] = section_for_range(pmap.sections, location["byte_start"], location["byte_end"])
    status_dir = issue_dir / "status"
    record["status_history"] = [read_json(f) for f in sorted(status_dir.glob("*.json"))] if status_dir.is_dir() else []
    record["investigations"] = _list_investigations(issue_dir)
    suggestions_dir = issue_dir / "suggestions"
    record["suggestions"] = [read_json(f) for f in sorted(suggestions_dir.glob("*.json"))] if suggestions_dir.is_dir() else []
    record["edits"] = _list_issue_edits(issue_dir)
    return record


def _issue_revision_token(issue_dir: Path) -> str:
    """Hash all durable issue state that can affect an investigation decision."""
    digest = hashlib.sha256()
    for path in sorted(issue_dir.rglob("*.json")):
        digest.update(str(path.relative_to(issue_dir)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_investigation(
    *,
    verdict: str,
    state: str | None,
) -> str:
    if verdict not in VALID_VERDICTS:
        raise KatzError(
            "Invalid verdict",
            "validation_error",
            {"verdict": verdict, "valid": sorted(VALID_VERDICTS)},
        )
    if state is not None and state not in VALID_STATES:
        raise KatzError(
            "Invalid state",
            "validation_error",
            {"state": state, "valid": sorted(VALID_STATES)},
        )
    return state or {
        "confirmed": "confirmed",
        "rejected": "rejected",
        "uncertain": "open",
    }[verdict]


def _append_investigation(
    issue_dir: Path,
    *,
    verdict: str,
    evidence: Any = None,
    notes: str | None = None,
    state: str | None = None,
    timestamp: str | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    """Append the canonical investigation and status events for one issue."""
    target_state = _validate_investigation(verdict=verdict, state=state)
    event_timestamp = timestamp or now_utc()
    inv_record: dict[str, Any] = {"verdict": verdict, "timestamp": event_timestamp}
    if evidence is not None:
        inv_record["evidence"] = evidence
    if notes is not None:
        inv_record["notes"] = notes
    created = [write_event_json(issue_dir / "investigations", inv_record)]
    reason = notes[:200] if notes else verdict
    status_record = {"state": target_state, "reason": reason, "timestamp": event_timestamp}
    try:
        created.append(write_event_json(issue_dir / "status", status_record))
    except Exception:
        created[0].unlink(missing_ok=True)
        raise
    result = dict(inv_record)
    result["state_updated"] = target_state
    return result, created


def _issue_duplicate_clusters(dest: Path) -> list[dict[str, Any]]:
    records = [
        _load_issue(path.parent)
        for path in sorted((dest / "issues").glob("*/issue.json"))
    ] if (dest / "issues").is_dir() else []
    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    def words(record: dict[str, Any]) -> set[str]:
        text = f"{record.get('title', '')} {record.get('body', '')}".lower()
        return {
            token for token in re.findall(r"[a-z0-9]+", text)
            if len(token) > 3 and token not in {"this", "that", "with", "from", "paper", "issue"}
        }

    token_sets = [words(record) for record in records]
    for left in range(len(records)):
        for right in range(left + 1, len(records)):
            left_location = records[left].get("location", {})
            right_location = records[right].get("location", {})
            overlap = (
                left_location.get("byte_start", -1) < right_location.get("byte_end", -1)
                and right_location.get("byte_start", -1) < left_location.get("byte_end", -1)
            )
            union_tokens = token_sets[left] | token_sets[right]
            similarity = len(token_sets[left] & token_sets[right]) / len(union_tokens) if union_tokens else 0.0
            if overlap or similarity >= 0.45:
                union(left, right)

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        groups.setdefault(find(index), []).append(record)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        primary = members[0]
        clusters.append({
            "primary_issue_id": primary["id"],
            "issue_ids": [record["id"] for record in members],
            "titles": [record.get("title") for record in members],
            "spotters": sorted({str(record.get("spotter")) for record in members if record.get("spotter")}),
            "suggested_command": [
                "katz", "issue", "merge", "--ids",
                ",".join(record["id"] for record in members),
            ],
        })
    return clusters
