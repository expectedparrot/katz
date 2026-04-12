from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer

app = typer.Typer(help="Version-aware ledger for paper review artifacts.")
paper_app = typer.Typer(help="Register and query canonical manuscripts.")
issue_app = typer.Typer(help="Write and query issue records.")
spotter_app = typer.Typer(help="Manage issue spotters.")
eval_app = typer.Typer(help="Manage evaluation criteria and responses.")
guide_app = typer.Typer(help="Self-documenting guide for agents.")
app.add_typer(paper_app, name="paper")
app.add_typer(issue_app, name="issue")
app.add_typer(spotter_app, name="spotter")
app.add_typer(eval_app, name="eval")
app.add_typer(guide_app, name="guide")

SKILLS_DIR = Path(__file__).parent / "skills"
CATALOG_DIR = Path(__file__).parent / "catalog"

KATZ_DIR = ".katz"
ACTIVE_VERSION = "ACTIVE_VERSION"


class KatzError(Exception):
    def __init__(self, message: str, code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


@dataclass
class PaperMap:
    header: dict[str, Any]
    sections: list[dict[str, Any]] = field(default_factory=list)
    sentences: list[dict[str, Any]] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)


def emit_json(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=False))


def fail(message: str, code: str, details: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"error": message, "code": code}
    if details:
        payload["details"] = details
    emit_json(payload)
    raise typer.Exit(1)


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


# ---------------------------------------------------------------------------
# JSONL utilities
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Sentence segmentation
# ---------------------------------------------------------------------------

_MATH_ENVS = frozenset({
    "equation", "equation*", "align", "align*",
    "gather", "gather*", "multline", "multline*",
})


def segment_sentences(text: str) -> list[dict[str, Any]]:
    """Segment ventilated-prose markdown into sentence records.

    Assumes one prose sentence per line.  Skips headings, blank lines,
    image references, horizontal rules, fenced code blocks, and display
    math environments.
    """
    lines = text.split("\n")
    sentences: list[dict[str, Any]] = []
    byte_offset = 0
    in_code_block = False
    in_display_math = False
    sentence_index = 0

    for line_number_0, line in enumerate(lines):
        line_byte_length = len(line.encode("utf-8"))
        line_start_byte = byte_offset
        line_end_byte = byte_offset + line_byte_length
        # advance past the newline (if not last line)
        if line_number_0 < len(lines) - 1:
            byte_offset = line_end_byte + 1
        else:
            byte_offset = line_end_byte

        stripped = line.strip()

        # toggle fenced code blocks
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # toggle display math ($$)
        if stripped == "$$":
            in_display_math = not in_display_math
            continue
        if stripped == "\\[":
            in_display_math = True
            continue
        if stripped == "\\]":
            in_display_math = False
            continue
        if stripped.startswith("\\begin{"):
            env = stripped[7:].split("}")[0] if "}" in stripped[7:] else ""
            if env in _MATH_ENVS:
                in_display_math = True
                continue
        if stripped.startswith("\\end{"):
            env = stripped[5:].split("}")[0] if "}" in stripped[5:] else ""
            if env in _MATH_ENVS:
                in_display_math = False
                continue
        if in_display_math:
            continue

        # skip empty lines
        if not stripped:
            continue
        # skip headings
        if stripped.startswith("#"):
            continue
        # skip image references
        if stripped.startswith("!["):
            continue
        # skip horizontal rules
        if re.match(r"^[-*_]{3,}\s*$", stripped):
            continue
        # skip table separator lines (e.g. |---|---|)
        if re.match(r"^\|?[\s\-:|]+\|", stripped):
            continue

        sentences.append({
            "type": "sentence",
            "index": sentence_index,
            "byte_start": line_start_byte,
            "byte_end": line_end_byte,
            "line_start": line_number_0 + 1,
            "line_end": line_number_0 + 1,
        })
        sentence_index += 1

    return sentences


# ---------------------------------------------------------------------------
# Core helpers (unchanged API)
# ---------------------------------------------------------------------------


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


def line_bounds(text: str, byte_start: int, byte_end: int) -> tuple[int, int]:
    starts = [0]
    encoded = text.encode("utf-8")
    for index, byte in enumerate(encoded):
        if byte == 10:
            starts.append(index + 1)
    line_start = 1
    line_end = 1
    for line_number, start in enumerate(starts, start=1):
        if start <= byte_start:
            line_start = line_number
        if start < byte_end:
            line_end = line_number
    return line_start, line_end


def contains_math(text: str) -> bool:
    markers = ["$", "\\(", "\\[", "\\begin{equation", "\\begin{align", "\\frac", "\\sum", "\\int"]
    return any(marker in text for marker in markers)


def resolve_location(canonical: Path, byte_start: int, byte_end: int) -> dict[str, Any]:
    data = canonical.read_bytes()
    if byte_start < 0 or byte_end <= byte_start or byte_end > len(data):
        raise KatzError(
            "Byte range is outside manuscript bounds",
            "invalid_range",
            {"byte_start": byte_start, "byte_end": byte_end, "file_size": len(data)},
        )
    try:
        resolved_text = data[byte_start:byte_end].decode("utf-8")
        full_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KatzError("Byte range is not valid UTF-8", "invalid_range") from exc
    line_start, line_end = line_bounds(full_text, byte_start, byte_end)
    return {
        "byte_start": byte_start,
        "byte_end": byte_end,
        "line_start": line_start,
        "line_end": line_end,
        "resolved_text": resolved_text,
        "contains_math": contains_math(resolved_text),
    }


def section_for_range(sections: list[dict[str, Any]], byte_start: int, byte_end: int) -> str | None:
    for section in sections:
        if not isinstance(section, dict):
            continue
        if section.get("byte_start", -1) <= byte_start and byte_end <= section.get("byte_end", -1):
            return section.get("id")
    return None


def validate_location(canonical: Path, record_path: Path, location: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    byte_start = location.get("byte_start")
    byte_end = location.get("byte_end")
    if not isinstance(byte_start, int) or not isinstance(byte_end, int):
        errors.append(
            {
                "code": "validation_error",
                "path": str(record_path),
                "message": "location byte_start and byte_end must be integers",
            }
        )
        return
    try:
        resolved = resolve_location(canonical, byte_start, byte_end)
    except KatzError as exc:
        errors.append({"code": exc.code, "path": str(record_path), "message": exc.message})
        return
    for field_name in ["resolved_text", "line_start", "line_end", "contains_math"]:
        if field_name in location and location[field_name] != resolved[field_name]:
            errors.append(
                {
                    "code": "stale_resolved_text" if field_name == "resolved_text" else "validation_error",
                    "path": str(record_path),
                    "message": f"location {field_name} does not match manuscript",
                }
            )


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def event_filename() -> str:
    """Return a filename-safe timestamp with microseconds for uniqueness."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    return f"{ts}.json"


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


@paper_app.command("register")
def paper_register(
    canonical: Path = typer.Option(..., "--canonical", exists=True, file_okay=True, dir_okay=False, readable=True),
    source_root: Optional[str] = typer.Option(None, "--source-root"),
    source_uri: Optional[str] = typer.Option(None, "--source-uri"),
    source_format: str = typer.Option("unknown", "--source-format"),
    source_method: str = typer.Option("unknown", "--source-method"),
    source_meta: Optional[str] = typer.Option(None, "--source-meta"),
) -> None:
    """Register a canonical manuscript for the current commit.

    Automatically segments sentences from the markdown.  Sections can be
    added later with ``katz paper add-sections``.
    """
    try:
        ensure_initialized()
        commit = current_commit()
        checksum = sha256_file(canonical)

        text = canonical.read_text(encoding="utf-8")
        sentence_records = segment_sentences(text)

        # Build source metadata
        source: dict[str, Any] = {
            "format": source_format,
            "root": source_root,
            "uri": source_uri,
            "method": source_method,
            "files_collapsed": [],
        }
        if source_meta is not None:
            extra = parse_meta(source_meta)
            source.update(extra)

        header: dict[str, Any] = {
            "type": "header",
            "schema_version": 1,
            "commit": commit,
            "checksum": checksum,
            "canonical": "paper/manuscript.md",
            "source": source,
        }

        records = [header] + sentence_records

        dest = version_dir(commit)
        paper_dest = dest / "paper"
        for directory in [paper_dest, dest / "issues", dest / "chunks"]:
            directory.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(canonical, paper_dest / "manuscript.md")
        write_jsonl(dest / "paper_map.jsonl", records)

        symbol_table = dest / "symbol_table.json"
        if not symbol_table.exists():
            symbol_table.write_text("[]\n", encoding="utf-8")

        version = {
            "schema_version": 1,
            "commit": commit,
            "registered_at": now_utc(),
            "canonical": "paper/manuscript.md",
            "paper_map": "paper_map.jsonl",
            "checksum": checksum,
            "source": source,
            "parent_commit": None,
        }
        write_json(dest / "version.json", version)
        active_version_path().write_text(commit + "\n", encoding="utf-8")

        emit_json(
            {
                "registered": True,
                "commit": commit,
                "version_dir": str(dest),
                "checksum": checksum,
                "sentences": len(sentence_records),
            }
        )
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("auto-chunk")
def paper_auto_chunk(
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Detect markdown headings and generate sections automatically."""
    try:
        resolved, dest, _, pmap, canonical = load_version(commit)
        if pmap.sections:
            raise KatzError(
                f"Paper already has {len(pmap.sections)} sections. "
                "Remove them first or use add-sections to append.",
                "validation_error",
            )
        raw = canonical.read_bytes()
        text = raw.decode("utf-8")
        lines = text.split("\n")

        # Compute byte offset of each line
        line_offsets: list[int] = []
        offset = 0
        for line in lines:
            line_offsets.append(offset)
            offset += len(line.encode("utf-8")) + 1  # +1 for newline

        # Detect headings
        heading_re = re.compile(r"^(#{1,4})\s+(.+)")
        headings: list[tuple[int, str, str]] = []  # (line_idx, raw_title, level)
        for i, line in enumerate(lines):
            m = heading_re.match(line)
            if m:
                headings.append((i, m.group(2).strip(), m.group(1)))

        if not headings:
            raise KatzError("No markdown headings found in manuscript", "validation_error")

        # Build section records
        sections: list[dict[str, Any]] = []
        for idx, (line_idx, raw_title, level) in enumerate(headings):
            # Clean the title: strip span tags, bold markers, numbering
            clean = re.sub(r"<[^>]+>", "", raw_title)
            clean = re.sub(r"\*\*", "", clean)
            clean = clean.strip()
            # Build slug from cleaned title
            slug = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-")
            # Drop leading section numbers like "1-", "a-1-"
            slug = re.sub(r"^[0-9]+-", "", slug)
            slug = re.sub(r"^[a-z]-[0-9]+-", "", slug)
            if not slug:
                slug = f"section-{idx}"

            byte_start = line_offsets[line_idx]
            if idx + 1 < len(headings):
                byte_end = line_offsets[headings[idx + 1][0]]
            else:
                byte_end = len(raw)

            ls, le = line_bounds(text, byte_start, byte_end)
            sections.append({
                "type": "section",
                "id": slug,
                "title": clean,
                "byte_start": byte_start,
                "byte_end": byte_end,
                "line_start": ls,
                "line_end": le,
            })

        # Append to paper_map.jsonl
        jsonl_path = dest / "paper_map.jsonl"
        if not jsonl_path.exists():
            raise KatzError("paper_map.jsonl not found; register the paper first", "not_found")
        append_jsonl(jsonl_path, sections)

        emit_json({"added": len(sections), "total_sections": len(sections)})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("add-sections")
def paper_add_sections(
    sections_json: str = typer.Option(..., "--sections"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Append section records to the paper map."""
    try:
        resolved, dest, _, pmap, canonical = load_version(commit)

        try:
            new_sections = json.loads(sections_json)
        except json.JSONDecodeError as exc:
            raise KatzError("--sections must be valid JSON", "validation_error") from exc
        if not isinstance(new_sections, list):
            raise KatzError("--sections must be a JSON array", "validation_error")

        # Read manuscript size for bounds checking
        manuscript_size = canonical.stat().st_size
        manuscript_text = canonical.read_text(encoding="utf-8")

        existing_ids = {s["id"] for s in pmap.sections if isinstance(s, dict) and "id" in s}

        records: list[dict[str, Any]] = []
        for sec in new_sections:
            if not isinstance(sec, dict):
                raise KatzError("Each section must be a JSON object", "validation_error")
            for req in ("id", "title", "byte_start", "byte_end"):
                if req not in sec:
                    raise KatzError(f"Section missing required field: {req}", "validation_error", {"section": sec})
            if sec["id"] in existing_ids:
                raise KatzError(
                    f"Duplicate section id: {sec['id']}",
                    "validation_error",
                    {"id": sec["id"]},
                )
            if sec["byte_start"] < 0 or sec["byte_end"] > manuscript_size or sec["byte_end"] <= sec["byte_start"]:
                raise KatzError(
                    "Section byte range is out of bounds",
                    "invalid_range",
                    {"id": sec["id"], "byte_start": sec["byte_start"], "byte_end": sec["byte_end"]},
                )
            ls, le = line_bounds(manuscript_text, sec["byte_start"], sec["byte_end"])
            records.append({
                "type": "section",
                "id": sec["id"],
                "title": sec["title"],
                "byte_start": sec["byte_start"],
                "byte_end": sec["byte_end"],
                "line_start": ls,
                "line_end": le,
            })
            existing_ids.add(sec["id"])

        jsonl_path = dest / "paper_map.jsonl"
        if jsonl_path.exists():
            append_jsonl(jsonl_path, records)
        else:
            raise KatzError("paper_map.jsonl not found; register the paper first", "not_found")

        emit_json({"added": len(records), "total_sections": len(pmap.sections) + len(records)})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("status")
def paper_status() -> None:
    """Show status for the active or selected paper version."""
    try:
        ensure_initialized()
        commit = active_commit()
        dest = version_dir(commit)
        version = read_json(dest / "version.json")
        _, _, _, pmap, canonical = load_version(commit)
        source = version.get("source", {})
        if not isinstance(source, dict):
            source = {}
        valid = canonical.exists() and sha256_file(canonical) == version.get("checksum") == pmap.header.get("checksum")
        emit_json(
            {
                "commit": commit,
                "source_format": source.get("format"),
                "source_root": source.get("root"),
                "source_uri": source.get("uri"),
                "canonical": version.get("canonical"),
                "sections": len(pmap.sections),
                "sentences": len(pmap.sentences),
                "figures": len(pmap.figures),
                "valid": valid,
            }
        )
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("section")
def paper_section(
    section_id: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Show one section from paper_map."""
    try:
        _, _, _, pmap, _ = load_version(commit)
        for section in pmap.sections:
            if isinstance(section, dict) and section.get("id") == section_id:
                emit_json(section)
                return
        raise KatzError("Section does not exist", "not_found", {"id": section_id})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("sentences")
def paper_sentences(
    commit: Optional[str] = typer.Option(None, "--commit"),
    section: Optional[str] = typer.Option(None, "--section"),
    from_line: Optional[int] = typer.Option(None, "--from-line"),
    to_line: Optional[int] = typer.Option(None, "--to-line"),
) -> None:
    """Return the sentence index, optionally filtered."""
    try:
        _, _, _, pmap, _ = load_version(commit)
        section_bounds = None
        if section is not None:
            for candidate in pmap.sections:
                if isinstance(candidate, dict) and candidate.get("id") == section:
                    section_bounds = (candidate["byte_start"], candidate["byte_end"])
                    break
            if section_bounds is None:
                raise KatzError("Section does not exist", "not_found", {"id": section})
        filtered = []
        for sentence in pmap.sentences:
            if not isinstance(sentence, dict):
                continue
            if section_bounds and not (
                section_bounds[0] <= sentence.get("byte_start", -1) and sentence.get("byte_end", -1) <= section_bounds[1]
            ):
                continue
            if from_line is not None and sentence.get("line_end", 0) < from_line:
                continue
            if to_line is not None and sentence.get("line_start", 0) > to_line:
                continue
            filtered.append(sentence)
        emit_json(filtered)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("resolve")
def paper_resolve(
    byte_start: int,
    byte_end: int,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Resolve a half-open byte range into text and line numbers."""
    try:
        _, _, _, pmap, canonical = load_version(commit)
        location = resolve_location(canonical, byte_start, byte_end)
        location["section"] = section_for_range(pmap.sections, byte_start, byte_end)
        emit_json(location)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("find")
def paper_find(
    text: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
    mode: str = typer.Option("exact", "--mode"),
    ignore_case: bool = typer.Option(False, "--ignore-case"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Find text in the canonical manuscript."""
    try:
        if mode != "exact":
            raise KatzError("Only exact find mode is implemented", "validation_error", {"mode": mode})
        _, _, _, pmap, canonical = load_version(commit)
        content = canonical.read_text(encoding="utf-8")
        haystack = content.lower() if ignore_case else content
        needle = text.lower() if ignore_case else text
        results = []
        start = 0
        while len(results) < limit:
            char_index = haystack.find(needle, start)
            if char_index == -1:
                break
            byte_start_val = len(content[:char_index].encode("utf-8"))
            byte_end_val = byte_start_val + len(content[char_index : char_index + len(text)].encode("utf-8"))
            location = resolve_location(canonical, byte_start_val, byte_end_val)
            location["section"] = section_for_range(pmap.sections, byte_start_val, byte_end_val)
            results.append(location)
            start = char_index + max(len(text), 1)
        emit_json(results)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


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


VALID_STATES = {"draft", "open", "confirmed", "rejected", "resolved", "wontfix"}


def _issue_dir(dest: Path, issue_id: str) -> Path:
    return dest / "issues" / issue_id


def _latest_status(issue_dir: Path) -> dict[str, Any] | None:
    """Read the most recent status file from an issue's status/ directory."""
    status_dir = issue_dir / "status"
    if not status_dir.is_dir():
        return None
    files = sorted(status_dir.glob("*.json"))
    if not files:
        return None
    return read_json(files[-1])


def _load_issue(issue_dir: Path) -> dict[str, Any]:
    """Load an issue record, merging in current state from status/."""
    record = read_json(issue_dir / "issue.json")
    latest = _latest_status(issue_dir)
    record["state"] = latest["state"] if latest else "draft"
    return record


def _list_investigations(issue_dir: Path) -> list[dict[str, Any]]:
    """Return all investigation records for an issue, oldest first."""
    inv_dir = issue_dir / "investigations"
    if not inv_dir.is_dir():
        return []
    return [read_json(f) for f in sorted(inv_dir.glob("*.json"))]


@issue_app.command("write")
def issue_write(
    title: str = typer.Option(..., "--title"),
    byte_start: int = typer.Option(..., "--byte-start"),
    byte_end: int = typer.Option(..., "--byte-end"),
    body: str = typer.Option(..., "--body"),
    state: str = typer.Option("draft", "--state"),
    spotter: Optional[str] = typer.Option(None, "--spotter"),
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
        issue_id = uuid.uuid4().hex
        timestamp = now_utc()
        record = {
            "schema_version": 2,
            "id": issue_id,
            "commit": resolved,
            "title": title,
            "body": body,
            "spotter": spotter,
            "location": resolve_location(canonical, byte_start, byte_end),
            "created_at": timestamp,
            "meta": parse_meta(meta),
        }
        issue_dir = _issue_dir(dest, issue_id)
        (issue_dir / "status").mkdir(parents=True, exist_ok=True)
        (issue_dir / "investigations").mkdir(parents=True, exist_ok=True)
        write_json(issue_dir / "issue.json", record)
        status_record = {"state": state, "reason": "created", "timestamp": timestamp}
        write_json(issue_dir / "status" / event_filename(), status_record)
        record["state"] = state
        emit_json(record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("update")
def issue_update(
    issue_id: str = typer.Option(..., "--id"),
    state: str = typer.Option(..., "--state"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Update an issue's state by appending a status record."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        _, dest, _, _, _ = load_version(commit)
        issue_dir = _issue_dir(dest, issue_id)
        if not (issue_dir / "issue.json").exists():
            raise KatzError("Issue does not exist", "not_found", {"id": issue_id})
        timestamp = now_utc()
        status_record = {"state": state, "reason": reason, "timestamp": timestamp}
        write_json(issue_dir / "status" / event_filename(), status_record)
        emit_json(status_record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("investigate")
def issue_investigate(
    issue_id: str = typer.Option(..., "--id"),
    verdict: str = typer.Option(..., "--verdict"),
    evidence: Optional[str] = typer.Option(None, "--evidence"),
    notes: Optional[str] = typer.Option(None, "--notes"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Append an investigation record to an issue."""
    try:
        if verdict not in {"confirmed", "rejected", "uncertain"}:
            raise KatzError("Invalid verdict", "validation_error", {"verdict": verdict})
        _, dest, _, _, _ = load_version(commit)
        issue_dir = _issue_dir(dest, issue_id)
        if not (issue_dir / "issue.json").exists():
            raise KatzError("Issue does not exist", "not_found", {"id": issue_id})
        timestamp = now_utc()
        inv_record: dict[str, Any] = {"verdict": verdict, "timestamp": timestamp}
        if evidence is not None:
            inv_record["evidence"] = parse_meta(evidence) if evidence.startswith("[") or evidence.startswith("{") else evidence
        if notes is not None:
            inv_record["notes"] = notes
        write_json(issue_dir / "investigations" / event_filename(), inv_record)
        emit_json(inv_record)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@issue_app.command("show")
def issue_show(issue_id: str, commit: Optional[str] = typer.Option(None, "--commit")) -> None:
    """Return a full issue record with current state and history."""
    try:
        _, dest, _, pmap, _ = load_version(commit)
        issue_dir = _issue_dir(dest, issue_id)
        if not (issue_dir / "issue.json").exists():
            raise KatzError("Issue does not exist", "not_found", {"id": issue_id})
        record = _load_issue(issue_dir)
        # Enrich location with section ID
        location = record.get("location") if isinstance(record.get("location"), dict) else {}
        if isinstance(location.get("byte_start"), int) and isinstance(location.get("byte_end"), int):
            location["section"] = section_for_range(pmap.sections, location["byte_start"], location["byte_end"])
        status_dir = issue_dir / "status"
        record["status_history"] = [read_json(f) for f in sorted(status_dir.glob("*.json"))] if status_dir.is_dir() else []
        record["investigations"] = _list_investigations(issue_dir)
        emit_json(record)
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


VALID_SCOPES = {"section", "holistic"}


def _parse_spotter(content: str) -> dict[str, Any]:
    """Parse a spotter markdown file into frontmatter and body parts.

    Returns {"scope": str, "title": str|None, "description": str, "investigation": str|None, "raw": str}
    """
    raw = content
    frontmatter: dict[str, Any] = {}
    body = content

    # Parse YAML frontmatter between --- fences
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            import yaml  # noqa: delay import — only needed for spotter parsing
            try:
                frontmatter = yaml.safe_load(content[4:end]) or {}
            except Exception:
                frontmatter = {}
            body = content[end + 5:]  # skip past closing ---\n

    scope = frontmatter.get("scope", "section")

    # Extract title from first heading
    title = None
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Split body at ## Investigation heading
    description = body
    investigation = None
    inv_pattern = re.split(r"\n## Investigation\b", body, maxsplit=1, flags=re.IGNORECASE)
    if len(inv_pattern) == 2:
        description = inv_pattern[0].rstrip()
        investigation = inv_pattern[1].lstrip("\n")

    return {
        "scope": scope,
        "title": title,
        "description": description,
        "investigation": investigation,
        "raw": raw,
        "frontmatter": frontmatter,
    }


def _load_collection(catalog_type: str, preset: str) -> list[str]:
    """Load a named collection from catalog/{type}/collections/{preset}.json."""
    collections_dir = CATALOG_DIR / catalog_type / "collections"
    preset_file = collections_dir / f"{preset}.json"
    if not preset_file.exists():
        available = [f.stem for f in collections_dir.glob("*.json")] if collections_dir.is_dir() else []
        raise KatzError(
            f"Unknown preset: '{preset}'",
            "validation_error",
            {"preset": preset, "available": sorted(available)},
        )
    return json.loads(preset_file.read_text(encoding="utf-8"))


def _slugify(name: str) -> str:
    """Turn a name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        raise KatzError("Name must contain at least one alphanumeric character", "validation_error", {"name": name})
    return slug


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


@spotter_app.command("enable")
def spotter_enable(
    name: str,
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Enable a catalog spotter for the active version (copies it from catalog to version)."""
    try:
        ensure_initialized()
        catalog_path = katz_root() / "spotters" / f"{name}.md"
        if not catalog_path.exists():
            raise KatzError(f"Spotter '{name}' not in catalog", "not_found", {"name": name})
        _, dest, _, _, _ = load_version(commit)
        spotters_dir = dest / "spotters"
        spotters_dir.mkdir(parents=True, exist_ok=True)
        out_path = spotters_dir / f"{name}.md"
        if out_path.exists():
            raise KatzError(f"Spotter '{name}' is already enabled", "validation_error", {"name": name})
        shutil.copyfile(catalog_path, out_path)
        emit_json({"enabled": name})
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


# ---------------------------------------------------------------------------
# Eval commands
# ---------------------------------------------------------------------------


def _parse_eval(content: str) -> dict[str, Any]:
    """Parse an eval criterion markdown file into frontmatter and body parts.

    Returns {"scope": str|None, "category": str|None, "title": str|None, "body": str, "raw": str}
    """
    raw = content
    frontmatter: dict[str, Any] = {}
    body = content

    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            import yaml
            try:
                frontmatter = yaml.safe_load(content[4:end]) or {}
            except Exception:
                frontmatter = {}
            body = content[end + 5:]

    scope = frontmatter.get("scope")  # None if not set (paper-level)
    category = frontmatter.get("category")

    title = None
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return {
        "scope": scope,
        "category": category,
        "title": title,
        "body": body,
        "raw": raw,
        "frontmatter": frontmatter,
    }


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


VALID_GRADES = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"}


@eval_app.command("respond")
def eval_respond(
    name: str = typer.Option(..., "--name"),
    text: str = typer.Option(..., "--text"),
    grade: Optional[str] = typer.Option(None, "--grade"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Record a narrative response and optional letter grade for an eval criterion."""
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


# ---------------------------------------------------------------------------
# Guide commands
# ---------------------------------------------------------------------------


@guide_app.command("overview")
def guide_overview() -> None:
    """Show how katz works and what it can do."""
    overview = Path(__file__).parent / "OVERVIEW.md"
    if not overview.exists():
        fail("Overview file not found", "not_found")
    typer.echo(overview.read_text(encoding="utf-8"))


@guide_app.command("skills")
def guide_skills() -> None:
    """List available skills with descriptions."""
    results = []
    if SKILLS_DIR.is_dir():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            content = skill_file.read_text(encoding="utf-8")
            # Extract description from YAML frontmatter
            description = None
            name = skill_dir.name
            if content.startswith("---\n"):
                end = content.find("\n---\n", 4)
                if end != -1:
                    import yaml
                    try:
                        fm = yaml.safe_load(content[4:end]) or {}
                        description = fm.get("description")
                        name = fm.get("name", name)
                    except Exception:
                        pass
            # List scripts in this skill
            scripts_dir = skill_dir / "scripts"
            scripts = [f.name for f in sorted(scripts_dir.glob("*.py"))] if scripts_dir.is_dir() else []
            results.append({"name": name, "description": description, "scripts": scripts})
    emit_json(results)


@guide_app.command("skill")
def guide_skill(name: str) -> None:
    """Show a skill's full SKILL.md instructions."""
    skill_file = SKILLS_DIR / name / "SKILL.md"
    if not skill_file.exists():
        fail(f"Skill '{name}' not found", "not_found", {"name": name, "available": [d.name for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").exists()] if SKILLS_DIR.is_dir() else []})
    typer.echo(skill_file.read_text(encoding="utf-8"))


@guide_app.command("script")
def guide_script(path: str) -> None:
    """Show a script file from a skill's scripts/ directory.

    Path format: <skill-name>/scripts/<filename> or just <skill-name>/<filename>
    """
    # Normalize: allow "edsl-find-issues/scripts/edsl_find_issues.py" or "edsl-find-issues/edsl_find_issues.py"
    full_path = SKILLS_DIR / path
    if not full_path.exists():
        # Try inserting scripts/
        parts = Path(path).parts
        if len(parts) >= 2 and parts[1] != "scripts":
            full_path = SKILLS_DIR / parts[0] / "scripts" / Path(*parts[1:])
    if not full_path.exists() or not full_path.is_file():
        fail(f"Script not found: {path}", "not_found", {"path": path})
    typer.echo(full_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
