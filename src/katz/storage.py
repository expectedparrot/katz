"""Repository-local ledger storage: paths, versions, JSON records, and runs."""
from __future__ import annotations

import hashlib
import json
import subprocess
import os
import tempfile
from contextlib import contextmanager
from functools import wraps
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


def atomic_write(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    """Publish a fully flushed file; failures leave the previous file intact."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".katz-write-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


@contextmanager
def ledger_lock(directory: Path):
    """Serialize ledger mutations; the OS releases the lock after a crash."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def ledger_locked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with ledger_lock(ensure_initialized()):
            return function(*args, **kwargs)
    return wrapped


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
    atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with ledger_lock(path.parent):
        previous = path.read_bytes() if path.exists() else b""
        addition = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        atomic_write(path, previous + addition.encode("utf-8"))


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
    encoded = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    while True:
        try:
            atomic_write(candidate, encoded, exclusive=True)
            return candidate
        except FileExistsError:
            candidate = directory / f"{stem}_{counter}{suffix}"
            counter += 1


def record_run(dest: Path, kind: str, status: str, **details: Any) -> Path:
    """Append a first-class run lifecycle record to the active version."""
    audit = details.get("audit") or {}
    artifacts = []
    for role in ("jobs", "results", "models"):
        value = details.get(f"{role}_path") or audit.get(f"{role}_path")
        if not value:
            continue
        path = Path(value).resolve()
        if not path.is_file():
            raise KatzError("Run artifact is missing", "not_found", {"path": str(path)})
        checksum = sha256_file(path)
        expected = audit.get(f"{role}_sha256")
        if expected and expected != checksum:
            raise KatzError("Artifact changed after audit", "checksum_mismatch", {"path": str(path)})
        directory = dest / "artifacts" / checksum.removeprefix("sha256:")
        directory.mkdir(parents=True, exist_ok=True)
        preserved = directory / path.name
        if not preserved.exists():
            try:
                atomic_write(preserved, path.read_bytes(), exclusive=True)
            except FileExistsError:
                pass
        if sha256_file(preserved) != checksum:
            raise KatzError("Preserved artifact is corrupt", "checksum_mismatch", {"path": str(preserved)})
        details[f"{role}_path"] = str(path)
        details[f"{role}_sha256"] = checksum
        artifacts.append({"role": role, "path": str(preserved), "sha256": checksum})
        for suffix in (".katz.json", ".retry.json", ".merge.json"):
            sidecar = path.with_name(path.name + suffix)
            if not sidecar.is_file():
                continue
            sidecar_hash = sha256_file(sidecar)
            sidecar_dir = dest / "artifacts" / sidecar_hash.removeprefix("sha256:")
            sidecar_dir.mkdir(parents=True, exist_ok=True)
            preserved_sidecar = sidecar_dir / sidecar.name
            if not preserved_sidecar.exists():
                try:
                    atomic_write(preserved_sidecar, sidecar.read_bytes(), exclusive=True)
                except FileExistsError:
                    pass
            if sha256_file(preserved_sidecar) != sidecar_hash:
                raise KatzError("Preserved provenance is corrupt", "checksum_mismatch", {"path": str(preserved_sidecar)})
            artifacts.append({"role": role + suffix, "path": str(preserved_sidecar), "sha256": sidecar_hash})
    details["artifacts"] = artifacts
    details["run_id"] = audit.get("run_id") or details.get("jobs_sha256")
    version = dest / "version.json"
    if version.exists():
        details["manuscript_sha256"] = read_json(version).get("checksum")
    return write_event_json(dest / "runs", {
        "schema_version": 1,
        "kind": kind,
        "status": status,
        "timestamp": now_utc(),
        **details,
    })


def latest_review_run(dest: Path) -> dict[str, Any] | None:
    """Select lifecycle evidence for the most recently packaged review, not an unrelated event."""
    records = [read_json(p) for p in sorted((dest / "runs").glob("*.json"))]
    records = [r for r in records if r.get("kind") != "verification"]
    if not records:
        return None
    packaged = next((r for r in reversed(records) if r.get("status") == "packaged"), None)
    if packaged is None:
        return records[-1]  # Legacy ledger: no packaging record was preserved.
    matched = []
    for record in records[records.index(packaged):]:
        jobs_path = record.get("jobs_path") or (record.get("audit") or {}).get("jobs_path")
        if record is packaged or jobs_path == packaged.get("jobs_path"):
            matched.append(record)
    latest = {**packaged, **matched[-1]}
    if latest.get("status") == "audited":
        ingestion = next((r for r in reversed(matched) if r.get("status") == "ingested"
                          and r.get("results_sha256") == latest.get("results_sha256")), None)
        if ingestion:
            latest = {**latest, **ingestion}
    return latest


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
