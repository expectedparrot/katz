from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import uuid
import warnings
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
docs_app = typer.Typer(help="Read built-in documentation.")
guide_app = typer.Typer(help="Self-documenting guide for agents.")
report_app = typer.Typer(help="Generate review reports.")
review_app = typer.Typer(help="Register and parse human-written peer reviews.")
agent_app = typer.Typer(help="Discover state and next actions for coding agents.")
app.add_typer(paper_app, name="paper")
app.add_typer(issue_app, name="issue")
app.add_typer(spotter_app, name="spotter")
app.add_typer(eval_app, name="eval")
app.add_typer(docs_app, name="docs")
app.add_typer(guide_app, name="guide")
app.add_typer(report_app, name="report")
app.add_typer(review_app, name="review")
app.add_typer(agent_app, name="agent")

SKILLS_DIR = Path(__file__).parent / "skills"
CATALOG_DIR = Path(__file__).parent / "catalog"
REPORT_SCRIPT = SKILLS_DIR / "find-issues" / "scripts" / "generate_review_report.py"
SCHEMAS_DIR = Path(__file__).parent / "schemas"
TEMPLATES_DIR = Path(__file__).parent / "templates"
AGENT_API_VERSION = "1.0"

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


def _command_argv() -> list[str]:
    """Return the invoked Katz arguments for machine-readable provenance."""
    return sys.argv[1:]


def emit_json(value: Any) -> None:
    """Emit the stable, agent-facing success envelope."""
    payload = {
        "ok": True,
        "command": _command_argv(),
        "data": value,
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=False))


def fail(message: str, code: str, details: dict[str, Any] | None = None) -> None:
    error: dict[str, Any] = {"code": code, "message": message, "details": details or {}}
    payload = {
        "ok": False,
        "command": _command_argv(),
        "error": error,
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=False))
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

# TeX environments that contain non-prose content (figures, tables, code, etc.)
_TEX_SKIP_ENVS = frozenset({
    "figure", "figure*", "table", "table*",
    "algorithm", "algorithm*", "algorithmic",
    "tikzpicture", "lstlisting", "verbatim", "Verbatim",
    "thebibliography", "filecontents",
})

# TeX commands that appear on their own line and are purely structural
_TEX_STRUCTURAL_RE = re.compile(
    r"^\\(?:section|subsection|subsubsection|paragraph|subparagraph|"
    r"chapter|part|appendix|"
    r"label|"
    r"bibliographystyle|bibliography|"
    r"documentclass|usepackage|"
    r"newcommand|renewcommand|providecommand|"
    r"setlength|setcounter|addtolength|"
    r"geometry|hypersetup|pgfplotsset|"
    r"title|author|date|affiliation|"
    r"maketitle|tableofcontents|listoffigures|listoftables|"
    r"clearpage|newpage|"
    r"centering|raggedright|raggedleft|"
    r"hline|vline|toprule|midrule|bottomrule|cline|"
    r"includegraphics|graphicspath)\b"
)

# Heuristic: a line with a sentence boundary followed by a capital letter
# suggests multiple sentences on one line (non-ventilated).
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+[A-Z]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _count_non_ventilated_lines(text: str) -> int:
    """Return the count of lines that appear to contain multiple sentences."""
    count = 0
    in_fence = False
    in_display_math = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if stripped == "$$":
            in_display_math = not in_display_math
            continue
        if in_fence or in_display_math:
            continue
        # Only check substantive lines, skip obvious structural ones
        if len(stripped) < 40:
            continue
        if stripped.startswith(("#", "![", "```", "~~~", "%", "\\", "<", ">", "|", "$$")):
            continue
        if re.match(r"^(?:[-+*]|\d+[.)])\s", stripped):
            continue
        if _SENTENCE_BOUNDARY_RE.search(stripped):
            count += 1
    return count


def ventilate_markdown(text: str) -> tuple[str, int]:
    """Split likely multi-sentence Markdown prose lines conservatively.

    Structural Markdown, fenced code, display math, tables, HTML, comments,
    and list items are left unchanged. Returns (ventilated_text, lines_changed).
    """
    output: list[str] = []
    changed = 0
    in_fence = False
    in_display_math = False

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content):]
        stripped = content.strip()

        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            output.append(line)
            continue
        if stripped == "$$":
            in_display_math = not in_display_math
            output.append(line)
            continue

        structural = (
            in_fence
            or in_display_math
            or not stripped
            or stripped.startswith(("#", "![", "%", "\\", "<", ">", "|", "$$"))
            or re.match(r"^(?:[-+*]|\d+[.)])\s", stripped) is not None
            or re.match(r"^ {4}", content) is not None
        )
        if structural or not _SENTENCE_BOUNDARY_RE.search(stripped):
            output.append(line)
            continue

        indent = content[: len(content) - len(content.lstrip())]
        parts = _SENTENCE_SPLIT_RE.split(stripped)
        if len(parts) == 1:
            output.append(line)
            continue
        changed += 1
        for index, part in enumerate(parts):
            suffix = newline if index == len(parts) - 1 else "\n"
            output.append(f"{indent}{part}{suffix}")

    return "".join(output), changed


def segment_sentences(text: str, source_format: str = "markdown") -> list[dict[str, Any]]:
    """Segment ventilated-prose into sentence records.

    source_format: "markdown" (default), "tex", or "latex".
    Assumes one prose sentence per line.  Skips structural elements,
    headings, blank lines, and non-prose environments.
    """
    is_tex = source_format in ("tex", "latex")
    lines = text.split("\n")
    sentences: list[dict[str, Any]] = []
    byte_offset = 0
    in_code_block = False      # markdown only
    in_display_math = False
    in_skip_env = False        # TeX non-prose environments
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

        if is_tex:
            # Skip TeX comment lines
            if stripped.startswith("%"):
                continue
            # Track and skip \begin{...} / \end{...} lines
            if stripped.startswith("\\begin{"):
                env = stripped[7:].split("}")[0] if "}" in stripped[7:] else ""
                if env in _TEX_SKIP_ENVS:
                    in_skip_env = True
                elif env in _MATH_ENVS:
                    in_display_math = True
                continue  # always skip the \begin{...} line itself
            if stripped.startswith("\\end{"):
                env = stripped[5:].split("}")[0] if "}" in stripped[5:] else ""
                if env in _TEX_SKIP_ENVS:
                    in_skip_env = False
                elif env in _MATH_ENVS:
                    in_display_math = False
                continue  # always skip the \end{...} line itself
            if in_skip_env or in_display_math:
                continue
            # Skip empty lines
            if not stripped:
                continue
            # Skip structural TeX commands
            if _TEX_STRUCTURAL_RE.match(stripped):
                continue
        else:
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
        sentence_records = segment_sentences(text, source_format=source_format)
        non_ventilated = _count_non_ventilated_lines(text)

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

        # Copy sibling image files referenced by the manuscript
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        canonical_dir = canonical.parent
        for img_file in canonical_dir.iterdir():
            if img_file.suffix.lower() in image_exts and img_file.is_file():
                shutil.copyfile(img_file, paper_dest / img_file.name)
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

        result: dict[str, Any] = {
            "registered": True,
            "commit": commit,
            "version_dir": str(dest),
            "checksum": checksum,
            "sentences": len(sentence_records),
        }
        if non_ventilated > 0:
            result["warning"] = (
                f"{non_ventilated} line(s) appear to contain multiple sentences. "
                "Katz works best with ventilated prose (one sentence per line). "
                "Consider reformatting the manuscript so each sentence is on its own line."
            )
        emit_json(result)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@paper_app.command("auto-chunk")
def paper_auto_chunk(
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Detect section headings and generate sections automatically."""
    try:
        resolved, dest, version, pmap, canonical = load_version(commit)
        if pmap.sections:
            raise KatzError(
                f"Paper already has {len(pmap.sections)} sections. "
                "Remove them first or use add-sections to append.",
                "validation_error",
            )
        raw = canonical.read_bytes()
        text = raw.decode("utf-8")
        lines = text.split("\n")

        # Determine source format to pick the right heading pattern
        source_format = version.get("source", {}).get("format", "markdown")
        is_tex = source_format in ("tex", "latex")

        # Compute byte offset of each line
        line_offsets: list[int] = []
        offset = 0
        for line in lines:
            line_offsets.append(offset)
            offset += len(line.encode("utf-8")) + 1  # +1 for newline

        headings: list[tuple[int, str, str]] = []  # (line_idx, raw_title, level)
        if is_tex:
            # Detect TeX section commands: \section, \subsection, \subsubsection, \chapter, \part
            tex_heading_re = re.compile(
                r"^\\((?:sub){0,2}section|chapter|part)\*?(?:\[[^\]]*\])?\{(.+?)\}"
            )
            for i, line in enumerate(lines):
                m = tex_heading_re.match(line.strip())
                if m:
                    headings.append((i, m.group(2).strip(), m.group(1)))
            if not headings:
                raise KatzError(
                    "No TeX section commands found in manuscript. "
                    r"Expected \section{...}, \subsection{...}, etc.",
                    "validation_error",
                )
        else:
            # Detect markdown headings
            heading_re = re.compile(r"^(#{1,4})\s+(.+)")
            for i, line in enumerate(lines):
                m = heading_re.match(line)
                if m:
                    headings.append((i, m.group(2).strip(), m.group(1)))
            if not headings:
                raise KatzError("No markdown headings found in manuscript", "validation_error")

        # Build section records
        sections: list[dict[str, Any]] = []
        slug_counts: dict[str, int] = {}
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
            slug_count = slug_counts.get(slug, 0) + 1
            slug_counts[slug] = slug_count
            if slug_count > 1:
                slug = f"{slug}-{slug_count}"

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
            if not isinstance(sec["id"], str) or not sec["id"]:
                raise KatzError("Section id must be a non-empty string", "validation_error", {"section": sec})
            if not isinstance(sec["title"], str):
                raise KatzError("Section title must be a string", "validation_error", {"section": sec})
            if (
                not isinstance(sec["byte_start"], int)
                or isinstance(sec["byte_start"], bool)
                or not isinstance(sec["byte_end"], int)
                or isinstance(sec["byte_end"], bool)
            ):
                raise KatzError(
                    "Section byte_start and byte_end must be integers",
                    "validation_error",
                    {"section": sec},
                )
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


@paper_app.command("sections")
def paper_sections(
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """List all sections from paper_map."""
    try:
        _, _, _, pmap, _ = load_version(commit)
        emit_json([
            {
                "id": s["id"],
                "title": s.get("title", ""),
                "byte_start": s.get("byte_start"),
                "byte_end": s.get("byte_end"),
                "line_start": s.get("line_start"),
                "line_end": s.get("line_end"),
            }
            for s in pmap.sections
            if isinstance(s, dict)
        ])
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
    ignored = {".git", ".katz", ".venv", "node_modules", "dist", "build"}
    preferred_names = {"paper.md", "manuscript.md", "article.md", "paper.tex", "manuscript.tex"}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        if path.suffix.lower() not in {".md", ".tex", ".pdf"}:
            continue
        relative = path.relative_to(root)
        score = 0
        if path.name.lower() in preferred_names:
            score += 100
        lowered_name = path.name.lower()
        if lowered_name.startswith(("paper.", "paper_", "manuscript.", "manuscript_", "article.", "article_")):
            score += 30
        if path.suffix.lower() in {".md", ".tex"}:
            score += 10
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 2_000:
            score += 5
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
        candidates.append({
            "path": str(relative),
            "format": path.suffix.lower().lstrip("."),
            "bytes": size,
            "confidence": score,
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
        actions.extend([
            _agent_action("init_spotter_catalog", "Install reusable review procedures", ["katz", "spotter", "init-catalog"], mutates_state=True),
            _agent_action("list_spotter_catalog", "Inspect available review procedures", ["katz", "spotter", "catalog"], mutates_state=False),
        ])
    else:
        phase = "automated_review"
        if latest_run and latest_run.get("status") == "packaged":
            expected_results = Path(str(latest_run.get("expected_results_path", "")))
            jobs_path = Path(str(latest_run.get("jobs_path", "jobs.ep")))
            if expected_results.is_file():
                actions.append(_agent_action(
                    "preview_ingestion",
                    "Detect and preview the completed EDSL Results before mutating the ledger",
                    ["katz", "ingest", str(expected_results)],
                    mutates_state=False,
                ))
            else:
                actions.append(_agent_action(
                    "inspect_jobs", "Inspect the packaged EDSL job before execution",
                    ["ep", "inspect", str(jobs_path)],
                    mutates_state=False,
                ))
        else:
            actions.append(_agent_action(
                "build_review_jobs", "Package enabled spotters and manuscript sections",
                ["katz", "spotter", "jobs", "--output", "jobs.ep"], mutates_state=True,
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
    target: str = typer.Argument(..., help="codex or claude"),
    output: Optional[Path] = typer.Option(None, "--output"),
    content: bool = typer.Option(True, "--content/--no-content", help="Include template Markdown in the JSON response."),
) -> None:
    """Return or write native repository instructions for a coding agent."""
    try:
        normalized = target.lower()
        filenames = {"codex": "AGENTS.md", "claude": "CLAUDE.md"}
        if normalized not in filenames:
            raise KatzError("Target must be codex or claude", "validation_error", {"target": target})
        template_path = TEMPLATES_DIR / filenames[normalized]
        if not template_path.is_file():
            raise KatzError("Agent instruction template is missing", "not_found", {"target": target})
        markdown = template_path.read_text(encoding="utf-8")
        written = None
        if output is not None:
            if output.exists():
                raise KatzError("Refusing to overwrite an existing instruction file", "validation_error", {"output": str(output)})
            output.write_text(markdown, encoding="utf-8")
            written = str(output)
        response = {
            "schema_version": AGENT_API_VERSION,
            "target": normalized,
            "suggested_filename": filenames[normalized],
            "written": written,
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


@app.command("capabilities")
def capabilities() -> None:
    """Describe Katz's agent API, schemas, integrations, and safety properties."""
    schema_names = sorted(path.name for path in SCHEMAS_DIR.glob("*.json")) if SCHEMAS_DIR.is_dir() else []
    emit_json({
        "schema_version": AGENT_API_VERSION,
        "agent_api": {
            "commands": [
                "katz agent bootstrap", "katz agent status", "katz agent next",
                "katz agent instructions codex", "katz agent instructions claude",
                "katz agent schema NAME", "katz capabilities", "katz ingest PATH", "katz issue next",
            ],
            "action_fields": [
                "id", "purpose", "command", "mutates_state", "requires_network",
                "requires_user_approval", "reason",
            ],
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
    return record


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
    state: str = typer.Option(..., "--state"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Update an issue's state by appending a status record."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        _, dest, _, _, _ = load_version(commit)
        _, issue_dir = _issue_dir_for_id(dest, issue_id)
        timestamp = now_utc()
        status_record = {"state": state, "reason": reason, "timestamp": timestamp}
        write_event_json(issue_dir / "status", status_record)
        emit_json(status_record)
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

        # Optionally update state in the same call
        if state is not None:
            reason = notes[:200] if notes else verdict
            status_record = {"state": state, "reason": reason, "timestamp": timestamp}
            write_event_json(issue_dir / "status", status_record)
            inv_record["state_updated"] = state

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
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Return one complete, deterministic issue investigation packet."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state})
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
                        "--state", "<confirmed|rejected|draft>",
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
    try:
        names = json.loads(preset_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KatzError(
            f"Collection '{preset}' is not valid JSON",
            "validation_error",
            {"path": str(preset_file), "line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise KatzError(
            f"Collection '{preset}' must be a JSON array of strings",
            "validation_error",
            {"path": str(preset_file)},
        )
    return names


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
            emit_json({"enabled": name, "already_enabled": True})
            return
        shutil.copyfile(catalog_path, out_path)
        emit_json({"enabled": name, "already_enabled": False})
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


SPOTTER_QUESTION_TEXT = """\
You are reviewing {{ review_target }} from an academic manuscript.

Issue spotter:
{{ spotter_instructions }}

Manuscript content:
{{ manuscript_content }}

Apply the spotter carefully. Return found=false when there is no genuine,
substantive issue. When found=true, quote the exact shortest passage that
demonstrates the issue and explain why it matters. Do not invent text.
"""

ECONOMICS_REVIEW_QUESTION_TEXT = """\
Act as a demanding but constructive economics referee. Read the complete manuscript
attachment and inspect every attached figure before writing the report.

Attachments:
- Complete manuscript: {{ manuscript }}
{{ figure_attachment_list }}

Evaluate the paper on the dimensions that apply: contribution and relation to the
literature; economic question, mechanism, and interpretation; research design and
identification; estimation and statistical inference; data and measurement; results,
robustness, and heterogeneity; welfare or policy claims; reproducibility; exposition;
and whether each table or figure supports the argument. For a methods or software
paper, adapt these standards rather than pretending it contains an empirical design.

Return a self-contained Markdown referee report with:
1. Summary and contribution
2. Overall assessment and recommendation
3. Major concerns
4. Minor concerns
5. Questions for the authors
6. Figure and table comments

Write each actionable concern under a heading in exactly this form:
### [major] Short title
or:
### [minor] Short title

Under each concern include these labeled fields:
- Evidence: an exact, shortest quotation from the manuscript, or a figure filename
- Location: the manuscript section or figure filename
- Reason: why this matters for the paper's economic argument or evidentiary standard
- Suggested response: a concrete way the authors could address it

Do not invent quotations, results, citations, tables, or figure contents. If a concern
cannot be tied to exact evidence in an attachment, present it as a question rather than
an issue candidate. Distinguish limitations from fatal flaws and acknowledge material
strengths.
"""


def _edsl_imports() -> tuple[Any, Any, Any, Any]:
    try:
        from edsl import Jobs, Scenario, ScenarioList
        from edsl.questions import QuestionDict
    except ImportError as exc:
        raise KatzError(
            "EDSL is required to create or ingest .ep objects",
            "dependency_error",
            {"install": "python -m pip install edsl"},
        ) from exc
    return Jobs, Scenario, ScenarioList, QuestionDict


def _expected_results_path(output: Path) -> Path:
    name = output.name
    if name.endswith(".jobs.ep"):
        return output.with_name(f"{name[:-8]}-results.ep")
    return output.with_name("results.ep")


@paper_app.command("review-jobs")
def paper_review_jobs(
    output: Path = typer.Option(Path("jobs.ep"), "--output", "-o"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Build one EDSL job that reviews the whole paper and its figures."""
    try:
        if output.suffix != ".ep":
            raise KatzError("--output must use the .ep extension", "validation_error", {"output": str(output)})
        if output.exists():
            raise KatzError(f"{output} already exists", "validation_error", {"output": str(output)})

        try:
            from edsl import FileStore, Jobs, Scenario, ScenarioList
            from edsl.questions import QuestionFreeText
        except ImportError as exc:
            raise KatzError(
                "EDSL is required to create .ep objects",
                "dependency_error",
                {"install": "python -m pip install edsl"},
            ) from exc

        resolved, dest, _, _, canonical = load_version(commit)
        paper_dir = dest / "paper"
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        figure_paths = [
            path for path in sorted(paper_dir.iterdir())
            if path.is_file() and path.suffix.lower() in image_exts
        ]

        scenario_data: dict[str, Any] = {
            "katz_commit": resolved,
            "manuscript": FileStore(str(canonical)),
        }
        figure_lines: list[str] = []
        attachment_records: list[dict[str, str]] = [
            {"key": "manuscript", "filename": canonical.name, "kind": "manuscript"}
        ]
        for index, path in enumerate(figure_paths, start=1):
            key = f"figure_{index}"
            scenario_data[key] = FileStore(str(path))
            figure_lines.append(f"- Figure {index} ({path.name}): {{{{ {key} }}}}")
            attachment_records.append({"key": key, "filename": path.name, "kind": "figure"})
        figure_attachment_list = (
            "\n".join(figure_lines) if figure_lines else "- No figure attachments were registered."
        )

        question = QuestionFreeText(
            question_name="economic_review",
            question_text=ECONOMICS_REVIEW_QUESTION_TEXT.replace(
                "{{ figure_attachment_list }}", figure_attachment_list
            ),
        )
        job = Jobs(survey=question.to_survey()).by(ScenarioList([Scenario(scenario_data)]))
        saved = job.git.save(output)
        expected_results = _expected_results_path(output)
        record_run(
            dest, "whole_paper_review", "packaged",
            jobs_path=str(output.resolve()),
            expected_results_path=str(expected_results.resolve()),
            question="economic_review",
            scenario_count=1,
            attachments=attachment_records,
        )
        emit_json({
            "object_type": "Jobs",
            "output": str(output),
            "commit": resolved,
            "question": "economic_review",
            "scenario_count": 1,
            "attachments": attachment_records,
            "saved": saved,
            "next": f"ep run {output} --model <frontier-model> --output {expected_results}",
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"output": str(output)})


JOURNAL_REVIEW_PARSE_PROMPT = """You are converting a human-written journal review into
candidate Katz issues. Read both attached files: the referee review and the registered
manuscript. Preserve the reviewer's meaning and do not add criticisms of your own.

Return ONLY a JSON array. Each element must have these string fields:
- title: a short descriptive title
- body: the reviewer's concern, with enough context to investigate it
- quoted_text: the shortest exact quotation from the manuscript that grounds the concern
- reviewer_comment: the relevant exact quotation from the referee review
- severity: major, minor, question, or unspecified
- suggested_response: the reviewer's requested change, or an empty string

Include only actionable comments that can be grounded in an exact manuscript quotation.
Do not turn praise, editorial logistics, confidential editor-only remarks, or a general
recommendation into manuscript issues. Split distinct concerns, but do not split one
concern merely because it spans several sentences. If no grounded actionable comments
exist, return [].

Registered manuscript: {{ manuscript }}
Human referee review: {{ journal_review }}
"""


def _review_dir(dest: Path, review_id: str) -> Path:
    return dest / "reviews" / review_id


@review_app.command("add")
def review_add(
    source: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    reviewer: Optional[str] = typer.Option(None, "--reviewer"),
    venue: Optional[str] = typer.Option(None, "--venue"),
    round_name: Optional[str] = typer.Option(None, "--round"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Preserve a human-written journal review with the registered paper version."""
    try:
        resolved, dest, _, _, _ = load_version(commit)
        text = source.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        review_id = f"review-{digest[:12]}"
        review_dir = _review_dir(dest, review_id)
        metadata_path = review_dir / "review.json"
        if metadata_path.exists():
            emit_json({**read_json(metadata_path), "already_registered": True})
            return
        review_dir.mkdir(parents=True, exist_ok=False)
        preserved = review_dir / ("review.md" if source.suffix.lower() == ".md" else "review.txt")
        preserved.write_text(text, encoding="utf-8")
        record = {
            "schema_version": 1,
            "id": review_id,
            "commit": resolved,
            "reviewer": reviewer,
            "venue": venue,
            "round": round_name,
            "source_name": source.name,
            "preserved_path": str(preserved),
            "sha256": f"sha256:{digest}",
            "created_at": now_utc(),
        }
        write_json(metadata_path, record)
        emit_json({**record, "already_registered": False, "next": f"katz review jobs {review_id} --output journal-review.jobs.ep"})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@review_app.command("list")
def review_list(commit: Optional[str] = typer.Option(None, "--commit")) -> None:
    """List human-written reviews preserved for a paper version."""
    try:
        _, dest, _, _, _ = load_version(commit)
        reviews_dir = dest / "reviews"
        records = [
            read_json(path)
            for path in sorted(reviews_dir.glob("*/review.json"))
        ] if reviews_dir.is_dir() else []
        emit_json(records)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@review_app.command("jobs")
def review_jobs(
    review_id: str,
    output: Path = typer.Option(Path("journal-review.jobs.ep"), "--output", "-o"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Build an EDSL job that parses a preserved journal review into issue candidates."""
    try:
        if output.suffix != ".ep":
            raise KatzError("--output must use the .ep extension", "validation_error", {"output": str(output)})
        if output.exists():
            raise KatzError(f"{output} already exists", "validation_error", {"output": str(output)})
        try:
            from edsl import FileStore, Jobs, Scenario, ScenarioList
            from edsl.questions import QuestionFreeText
        except ImportError as exc:
            raise KatzError("EDSL is required to create .ep objects", "dependency_error") from exc

        resolved, dest, _, _, canonical = load_version(commit)
        review_dir = _review_dir(dest, review_id)
        metadata_path = review_dir / "review.json"
        if not metadata_path.exists():
            raise KatzError("Human review does not exist", "not_found", {"review_id": review_id})
        metadata = read_json(metadata_path)
        candidates = list(review_dir.glob("review.*"))
        review_path = next((path for path in candidates if path.name != "review.json"), None)
        if review_path is None:
            raise KatzError("Preserved review text is missing", "not_found", {"review_id": review_id})
        scenario = Scenario({
            "katz_commit": resolved,
            "review_id": review_id,
            "manuscript": FileStore(str(canonical)),
            "journal_review": FileStore(str(review_path)),
        })
        question = QuestionFreeText(
            question_name="journal_review_issues",
            question_text=JOURNAL_REVIEW_PARSE_PROMPT,
        )
        job = Jobs(survey=question.to_survey()).by(ScenarioList([scenario]))
        saved = job.git.save(output)
        expected_results = _expected_results_path(output)
        record_run(
            dest, "journal_review", "packaged",
            jobs_path=str(output.resolve()),
            expected_results_path=str(expected_results.resolve()),
            question="journal_review_issues",
            scenario_count=1,
            review_id=review_id,
        )
        emit_json({
            "object_type": "Jobs",
            "output": str(output),
            "commit": resolved,
            "review_id": review_id,
            "question": "journal_review_issues",
            "attachments": [canonical.name, review_path.name],
            "saved": saved,
            "next": f"ep run {output} --model <model-name> --output {expected_results}",
            "ingest_next": f"katz ingest {expected_results}",
            "metadata": metadata,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"output": str(output)})


def _parse_json_array_answer(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        parsed = value
    else:
        text = str(value or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise KatzError(
                "Journal-review result is not a JSON array",
                "validation_error",
                {"line": exc.lineno, "column": exc.colno},
            ) from exc
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise KatzError("Journal-review result must be an array of objects", "validation_error")
    return parsed


@review_app.command("ingest")
def review_ingest(
    results_path: Path = typer.Argument(..., exists=True, readable=True),
    state: str = typer.Option("draft", "--state"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """File grounded issue candidates parsed from a human journal review."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        from edsl import Results

        resolved, dest, _, _, canonical = load_version(commit)
        results = Results.git.load(results_path)
        manuscript = canonical.read_text(encoding="utf-8")
        existing_keys = {
            record.get("meta", {}).get("journal_review_result_key")
            for path in (dest / "issues").glob("*/issue.json")
            for record in [read_json(path)]
            if record.get("meta", {}).get("journal_review_result_key")
        } if (dest / "issues").is_dir() else set()

        candidates = filed = skipped = 0
        issue_ids: list[str] = []
        for result in results:
            scenario = result["scenario"] if isinstance(result["scenario"], dict) else dict(result["scenario"])
            if scenario.get("katz_commit") != resolved:
                raise KatzError("Results were generated for a different Katz version", "validation_error")
            review_id = str(scenario.get("review_id", ""))
            if not (_review_dir(dest, review_id) / "review.json").exists():
                raise KatzError("Result references an unregistered human review", "not_found", {"review_id": review_id})
            answer = _result_value(result, "answer", "journal_review_issues")
            for item in _parse_json_array_answer(answer):
                candidates += 1
                quoted = str(item.get("quoted_text", "")).strip()
                located = _locate_quoted_text(manuscript, quoted) if quoted else None
                if located is None:
                    skipped += 1
                    continue
                char_start, char_end = located
                byte_start = len(manuscript[:char_start].encode("utf-8"))
                byte_end = len(manuscript[:char_end].encode("utf-8"))
                key_payload = json.dumps(
                    {"commit": resolved, "review_id": review_id, "item": item},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                result_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
                if result_key in existing_keys:
                    skipped += 1
                    continue
                issue_id = uuid.uuid4().hex
                timestamp = now_utc()
                issue_dir = _issue_dir(dest, issue_id)
                (issue_dir / "status").mkdir(parents=True, exist_ok=True)
                (issue_dir / "investigations").mkdir(parents=True, exist_ok=True)
                body_parts = [str(item.get("body", "")).strip()]
                if item.get("reviewer_comment"):
                    body_parts.append(f'Reviewer comment: “{str(item["reviewer_comment"]).strip()}”')
                if item.get("suggested_response"):
                    body_parts.append(f'Suggested response: {str(item["suggested_response"]).strip()}')
                record = {
                    "schema_version": 2,
                    "id": issue_id,
                    "commit": resolved,
                    "title": str(item.get("title") or "Journal review comment"),
                    "body": "\n\n".join(part for part in body_parts if part),
                    "spotter": None,
                    "artifacts": [],
                    "location": resolve_location(canonical, byte_start, byte_end),
                    "created_at": timestamp,
                    "meta": {
                        "source": "human_journal_review",
                        "review_id": review_id,
                        "severity": str(item.get("severity", "unspecified")),
                        "journal_review_result_key": result_key,
                        "edsl_results_path": str(results_path),
                    },
                }
                write_json(issue_dir / "issue.json", record)
                write_event_json(issue_dir / "status", {
                    "state": state,
                    "reason": f"parsed from human journal review {review_id}",
                    "timestamp": timestamp,
                })
                existing_keys.add(result_key)
                issue_ids.append(issue_id)
                filed += 1
        record_run(
            dest, "journal_review", "ingested",
            results_path=str(results_path.resolve()),
            result_count=len(results),
            candidates=candidates,
            issues_filed=filed,
            skipped=skipped,
        )
        emit_json({
            "results": str(results_path),
            "commit": resolved,
            "result_count": len(results),
            "candidates": candidates,
            "issues_filed": filed,
            "skipped": skipped,
            "issue_ids": issue_ids,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"results": str(results_path)})


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
    state: str = typer.Option("draft", "--state"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Detect review artifacts safely; preview by default and mutate only with --apply."""
    try:
        detection = _detect_ingest_source(path)
        if not apply:
            command = detection.get("recommended_command")
            apply_action = None
            if detection.get("supported_apply"):
                apply_action = _agent_action(
                    "apply_ingestion",
                    "Apply the detected, version-checked ingestion contract",
                    ["katz", "ingest", str(path), "--apply", "--state", state],
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
            spotter_ingest(path, state=state, commit=commit)
            return
        if detection["kind"] == "journal_review_results":
            review_ingest(path, state=state, commit=commit)
            return
        raise KatzError("No ingestion handler is available", "unsupported_ingestion", {"detection": detection})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@spotter_app.command("jobs")
def spotter_jobs(
    output: Path = typer.Option(Path("jobs.ep"), "--output", "-o"),
    section: Optional[str] = typer.Option(None, "--section"),
    spotters: Optional[str] = typer.Option(None, "--spotters", help="Comma-separated enabled spotter names"),
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
                    "manuscript_content": content,
                }))

        question = QuestionDict(
            question_name="spotter_result",
            question_text=SPOTTER_QUESTION_TEXT,
            answer_keys=["found", "title", "quoted_text", "description"],
            value_types=["bool", "str", "str", "str"],
            value_descriptions=[
                "Whether a genuine issue was found",
                "Short issue title; empty when found is false",
                "Exact manuscript quotation; empty when found is false",
                "Evidence-backed explanation; empty when found is false",
            ],
            include_comment=False,
        )
        job = Jobs(survey=question.to_survey()).by(ScenarioList(scenarios))
        saved = job.git.save(output)
        expected_results = _expected_results_path(output)
        record_run(
            dest, "spotter", "packaged",
            jobs_path=str(output.resolve()),
            expected_results_path=str(expected_results.resolve()),
            question="spotter_result",
            scenario_count=len(scenarios),
            spotters=[item["name"] for item in definitions],
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
            "section_scenarios": section_jobs,
            "holistic_scenarios": holistic_jobs,
            "saved": saved,
            "next": f"ep run {output} --model <model-name> --output {expected_results}",
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"output": str(output)})


def _result_value(result: Any, group: str, key: str) -> Any:
    try:
        value = result[group]
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)
    except (KeyError, TypeError):
        return None


def _answer_is_found(value: Any) -> bool:
    """Interpret structured EDSL booleans without treating the string 'false' as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return value == 1


def _locate_quoted_text(region: str, quoted: str) -> tuple[int, int] | None:
    """Locate an exact quote, allowing runs of whitespace to differ."""
    direct = region.find(quoted)
    if direct >= 0:
        return direct, direct + len(quoted)

    pattern = r"\s+".join(re.escape(part) for part in quoted.split())
    if not pattern:
        return None
    match = re.search(pattern, region)
    if match is None:
        return None
    return match.start(), match.end()


@spotter_app.command("ingest")
def spotter_ingest(
    results_path: Path = typer.Argument(..., exists=True, readable=True),
    state: str = typer.Option("draft", "--state"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Parse an EDSL Results package and file manuscript-anchored Katz issues."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        _edsl_imports()
        from edsl import Results

        resolved, dest, _, _, canonical = load_version(commit)
        results = Results.git.load(results_path)
        manuscript = canonical.read_text(encoding="utf-8")
        existing_keys: set[str] = set()
        issues_dir = dest / "issues"
        if issues_dir.is_dir():
            for issue_path in issues_dir.glob("*/issue.json"):
                record = read_json(issue_path)
                result_key = record.get("meta", {}).get("edsl_result_key")
                if result_key:
                    existing_keys.add(result_key)

        found = filed = skipped = 0
        issue_ids: list[str] = []
        for result in results:
            answer = _result_value(result, "answer", "spotter_result")
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
            if result_key in existing_keys:
                skipped += 1
                continue

            issue_id = uuid.uuid4().hex
            timestamp = now_utc()
            issue_dir = _issue_dir(dest, issue_id)
            (issue_dir / "status").mkdir(parents=True, exist_ok=True)
            (issue_dir / "investigations").mkdir(parents=True, exist_ok=True)
            record = {
                "schema_version": 2,
                "id": issue_id,
                "commit": resolved,
                "title": str(answer.get("title", "Untitled issue")),
                "body": str(answer.get("description", "")),
                "spotter": spotter_name,
                "artifacts": [],
                "location": resolve_location(canonical, byte_start, byte_end),
                "created_at": timestamp,
                "meta": {
                    "edsl_result_key": result_key,
                    "edsl_model": str(model),
                    "edsl_results_path": str(results_path),
                },
            }
            write_json(issue_dir / "issue.json", record)
            write_event_json(issue_dir / "status", {"state": state, "reason": "imported from EDSL Results", "timestamp": timestamp})
            existing_keys.add(result_key)
            issue_ids.append(issue_id)
            filed += 1

        record_run(
            dest, "spotter", "ingested",
            results_path=str(results_path.resolve()),
            result_count=len(results),
            issues_found=found,
            issues_filed=filed,
            skipped=skipped,
        )
        emit_json({
            "results": str(results_path),
            "commit": resolved,
            "result_count": len(results),
            "issues_found": found,
            "issues_filed": filed,
            "skipped": skipped,
            "issue_ids": issue_ids,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"results": str(results_path)})


VALID_GRADES = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"}


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


# ---------------------------------------------------------------------------
# Report commands
# ---------------------------------------------------------------------------


def _load_report_module() -> Any:
    if not REPORT_SCRIPT.exists():
        raise KatzError("Report generator script not found", "not_found", {"path": str(REPORT_SCRIPT)})
    spec = importlib.util.spec_from_file_location("katz_generate_review_report", REPORT_SCRIPT)
    if spec is None or spec.loader is None:
        raise KatzError("Report generator script could not be loaded", "validation_error", {"path": str(REPORT_SCRIPT)})
    module = importlib.util.module_from_spec(spec)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        spec.loader.exec_module(module)
    return module


@report_app.command("generate")
def report_generate(
    output: Path = typer.Option(Path(".katz/review.html"), "--output", "-o"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Generate the HTML review report."""
    try:
        resolved, dest, version, pmap, canonical = load_version(commit)
        report_module = _load_report_module()

        issues = []
        issues_dir = dest / "issues"
        if issues_dir.is_dir():
            for issue_dir in sorted(issues_dir.iterdir()):
                if issue_dir.is_dir() and (issue_dir / "issue.json").exists():
                    issues.append(_full_issue_record(issue_dir, pmap))

        eval_criteria = report_module.load_eval_criteria(resolved)
        eval_results_records = report_module.load_eval_results(resolved)
        referee_report = report_module.load_referee_report(resolved)
        images = report_module.load_images_as_data_uris(resolved)
        source = version.get("source", {})
        if not isinstance(source, dict):
            source = {}
        status = {
            "commit": resolved,
            "source_format": source.get("format"),
            "source_root": source.get("root") or "paper",
            "source_uri": source.get("uri"),
            "canonical": version.get("canonical"),
            "sections": len(pmap.sections),
            "sentences": len(pmap.sentences),
            "figures": len(pmap.figures),
            "valid": canonical.exists() and sha256_file(canonical) == version.get("checksum") == pmap.header.get("checksum"),
        }
        html = report_module.build_html(
            status,
            pmap.sections,
            issues,
            canonical.read_text(encoding="utf-8"),
            eval_criteria,
            eval_results_records,
            referee_report,
            images,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        report_module.write_report_assets(output)
        emit_json(
            {
                "generated": True,
                "path": str(output),
                "commit": resolved,
                "issues": len(issues),
                "sections": len(pmap.sections),
                "evaluations": len(eval_results_records),
            }
        )
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


# ---------------------------------------------------------------------------
# Guide commands
# ---------------------------------------------------------------------------


def available_skills() -> list[str]:
    if not SKILLS_DIR.is_dir():
        return []
    return [d.name for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").exists()]


@guide_app.command("overview")
def guide_overview() -> None:
    """Show how katz works and what it can do."""
    overview = Path(__file__).parent / "OVERVIEW.md"
    if not overview.exists():
        fail("Overview file not found", "not_found")
    emit_json({"markdown": overview.read_text(encoding="utf-8")})


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
    parts = Path(name).parts
    if len(parts) != 1 or parts[0] in {"", ".", ".."}:
        fail(f"Skill '{name}' not found", "not_found", {"name": name, "available": available_skills()})
    skill_file = SKILLS_DIR / name / "SKILL.md"
    if not skill_file.exists():
        fail(f"Skill '{name}' not found", "not_found", {"name": name, "available": available_skills()})
    emit_json({"name": name, "markdown": skill_file.read_text(encoding="utf-8")})


@guide_app.command("script")
def guide_script(path: str) -> None:
    """Show a script file from a skill's scripts/ directory.

    Path format: <skill-name>/scripts/<filename> or just <skill-name>/<filename>
    """
    # Normalize either <skill>/scripts/<file> or <skill>/<file>.
    skills_root = SKILLS_DIR.resolve()

    def safe_skill_file(candidate: Path) -> Path | None:
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(skills_root)
        except (OSError, ValueError):
            return None
        if len(relative.parts) < 3 or relative.parts[1] != "scripts":
            return None
        return resolved

    parts = Path(path).parts
    full_path = None
    if len(parts) >= 3 and parts[1] == "scripts":
        full_path = safe_skill_file(SKILLS_DIR / path)
    if full_path is None or not full_path.exists():
        # Try inserting scripts/
        if len(parts) >= 2 and parts[1] != "scripts":
            full_path = safe_skill_file(SKILLS_DIR / parts[0] / "scripts" / Path(*parts[1:]))
    if full_path is None or not full_path.exists() or not full_path.is_file():
        fail(f"Script not found: {path}", "not_found", {"path": path})
    emit_json({"path": path, "source": full_path.read_text(encoding="utf-8")})


# ---------------------------------------------------------------------------
# Docs commands
# ---------------------------------------------------------------------------


def _load_docs_module() -> Any:
    from katz.docs import DOCS, load_doc, search_docs  # noqa: import here to avoid startup cost
    return DOCS, load_doc, search_docs


@docs_app.command("list")
def docs_list() -> None:
    """List available documentation topics."""
    DOCS, _, _ = _load_docs_module()
    topics = [{"topic": k, "title": v["title"], "summary": v["summary"]} for k, v in DOCS.items()]
    emit_json({"topics": topics})


@docs_app.command("show")
def docs_show(topic: str) -> None:
    """Show a documentation topic as markdown."""
    DOCS, load_doc, _ = _load_docs_module()
    if topic not in DOCS:
        fail(
            f"No doc '{topic}'.",
            "not_found",
            {"available": list(DOCS.keys()), "hint": "Run `katz docs list` to see topics."},
        )
    try:
        text = load_doc(topic)
    except OSError as exc:
        fail(f"Could not load doc '{topic}'.", "internal_error", {"error": str(exc)})
    emit_json({"topic": topic, "title": DOCS[topic]["title"], "markdown": text})


@docs_app.command("search")
def docs_search(query: str) -> None:
    """Search across all documentation topics."""
    _, _, search_docs = _load_docs_module()
    matches = search_docs(query)
    emit_json({"query": query, "matches": matches})


if __name__ == "__main__":
    app()
