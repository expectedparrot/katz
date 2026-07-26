"""Repository-local ledger storage: paths, versions, JSON records, and runs."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .errors import KatzError


KATZ_DIR = ".katz"


ACTIVE_VERSION = "ACTIVE_VERSION"


@dataclass
class PaperMap:
    header: dict[str, Any]
    sections: list[dict[str, Any]] = field(default_factory=list)
    sentences: list[dict[str, Any]] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KatzError(f"{path} does not exist", "not_found", {"path": str(path)}) from exc
    except json.JSONDecodeError as exc:
        raise KatzError(
            f"{path} is not valid JSON",
            "validation_error",
            {"path": str(path), "line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(data, dict):
        raise KatzError(f"{path} must contain a JSON object", "validation_error", {"path": str(path)})
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise KatzError(f"{path} does not exist", "not_found", {"path": str(path)}) from exc
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise KatzError(
                f"{path} line {lineno} is not valid JSON",
                "validation_error",
                {"path": str(path), "line": lineno, "column": exc.colno},
            ) from exc
        if not isinstance(obj, dict):
            raise KatzError(
                f"{path} line {lineno} must be a JSON object",
                "validation_error",
                {"path": str(path), "line": lineno},
            )
        records.append(obj)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=False) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")


def load_paper_map(path: Path) -> PaperMap:
    records = read_jsonl(path)
    headers = [r for r in records if r.get("type") == "header"]
    if len(headers) != 1:
        raise KatzError(
            f"paper_map.jsonl must contain exactly one header record, found {len(headers)}",
            "validation_error",
            {"path": str(path)},
        )
    return PaperMap(
        header=headers[0],
        sections=[r for r in records if r.get("type") == "section"],
        sentences=[r for r in records if r.get("type") == "sentence"],
        figures=[r for r in records if r.get("type") == "figure"],
    )


def paper_map_from_legacy(map_data: dict[str, Any]) -> PaperMap:
    """Convert old-format paper_map.json dict into a PaperMap."""
    return PaperMap(
        header={
            "type": "header",
            "schema_version": map_data.get("schema_version"),
            "commit": map_data.get("commit"),
            "checksum": map_data.get("checksum"),
            "canonical": map_data.get("canonical"),
            "source": map_data.get("source", {}),
        },
        sections=map_data.get("sections", []),
        sentences=map_data.get("sentences", []),
        figures=map_data.get("figures", []),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise KatzError("katz requires an existing git repository", "not_git_repo")
    return Path(result.stdout.strip())


def current_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise KatzError("git HEAD is not available", "invalid_commit")
    commit = result.stdout.strip()
    if len(commit) != 40:
        raise KatzError("git did not return a full commit SHA", "invalid_commit", {"commit": commit})
    return commit


def katz_root() -> Path:
    return repo_root() / KATZ_DIR


def active_version_path() -> Path:
    return katz_root() / ACTIVE_VERSION


def active_commit() -> str:
    path = active_version_path()
    try:
        commit = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise KatzError("No active katz version", "invalid_commit") from exc
    if len(commit) != 40:
        raise KatzError("ACTIVE_VERSION does not contain a full SHA", "invalid_commit", {"commit": commit})
    return commit


def resolve_commit(commit: Optional[str]) -> str:
    ensure_initialized()
    if commit is None:
        return active_commit()
    if len(commit) == 40 and version_dir(commit).exists():
        return commit
    versions = katz_root() / "versions"
    matches = [path.name for path in versions.iterdir() if path.is_dir() and path.name.startswith(commit)]
    if not matches:
        raise KatzError("SHA is not registered as a katz version", "invalid_commit", {"commit": commit})
    if len(matches) > 1:
        raise KatzError("SHA prefix matches multiple registered versions", "ambiguous_commit", {"commit": commit})
    return matches[0]


def version_dir(commit: str) -> Path:
    return katz_root() / "versions" / commit


def ensure_initialized() -> Path:
    root = katz_root()
    if not root.exists():
        raise KatzError(".katz is not initialized; run `katz init` first", "not_found")
    return root


def source_from_header(
    header: dict[str, Any],
    source_root: Optional[str],
    source_uri: Optional[str],
) -> dict[str, Any]:
    source = header.get("source")
    if not isinstance(source, dict):
        source = {}
    return {
        "format": source.get("format", "unknown"),
        "root": source_root if source_root is not None else source.get("root"),
        "uri": source_uri if source_uri is not None else source.get("uri"),
        "method": source.get("method", "unknown"),
        "files_collapsed": source.get("files_collapsed", []),
    }


def load_version(commit: Optional[str]) -> tuple[str, Path, dict[str, Any], PaperMap, Path]:
    """Load a registered version, returning (commit, dest, version_json, paper_map, canonical_path).

    Supports both the new paper_map.jsonl and legacy paper_map.json.
    """
    resolved = resolve_commit(commit)
    dest = version_dir(resolved)
    version = read_json(dest / "version.json")
    jsonl_path = dest / "paper_map.jsonl"
    json_path = dest / "paper_map.json"
    if jsonl_path.exists():
        pmap = load_paper_map(jsonl_path)
    elif json_path.exists():
        pmap = paper_map_from_legacy(read_json(json_path))
    else:
        raise KatzError("No paper map found", "not_found", {"version_dir": str(dest)})
    canonical = dest / version.get("canonical", "paper/manuscript.md")
    return resolved, dest, version, pmap, canonical


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def event_filename() -> str:
    """Return a filename-safe timestamp with microseconds for uniqueness."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    return f"{ts}.json"


def write_event_json(directory: Path, data: dict[str, Any]) -> Path:
    """Write an event record without overwriting an existing timestamp file."""
    directory.mkdir(parents=True, exist_ok=True)
    filename = event_filename()
    candidate = directory / filename
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    write_json(candidate, data)
    return candidate


def record_run(dest: Path, kind: str, status: str, **details: Any) -> Path:
    """Append a first-class run lifecycle record to the active version."""
    return write_event_json(dest / "runs", {
        "schema_version": 1,
        "kind": kind,
        "status": status,
        "timestamp": now_utc(),
        **details,
    })


def _latest_packaged_run(dest: Path, results_path: Path | None = None) -> dict[str, Any] | None:
    """Find the newest packaged run, optionally matching its expected Results path."""
    runs_dir = dest / "runs"
    if not runs_dir.is_dir():
        return None
    target = results_path.resolve() if results_path is not None else None
    for path in reversed(sorted(runs_dir.glob("*.json"))):
        record = read_json(path)
        if record.get("status") != "packaged":
            continue
        expected = record.get("expected_results_path")
        if target is None or (expected and Path(str(expected)).resolve() == target):
            return record
    return None


def parse_meta(meta: Optional[str]) -> dict[str, Any]:
    if meta is None:
        return {}
    try:
        value = json.loads(meta)
    except json.JSONDecodeError as exc:
        raise KatzError("meta must be valid JSON object", "validation_error", {"line": exc.lineno, "column": exc.colno}) from exc
    if not isinstance(value, dict):
        raise KatzError("meta must be valid JSON object", "validation_error")
    return value
