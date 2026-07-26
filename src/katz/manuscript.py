"""Canonical manuscript text utilities: ventilation, segmentation, locations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import KatzError
from .storage import read_json


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


def _provenance_sidecar_path(canonical: Path) -> Path:
    return canonical.with_name(canonical.name + ".provenance.json")


def _load_provenance_sidecar(canonical: Path) -> dict[str, Any] | None:
    """Load `<canonical>.provenance.json` written by `paper prepare`, if present."""
    sidecar = _provenance_sidecar_path(canonical)
    if not sidecar.is_file():
        return None
    data = read_json(sidecar)
    sections = data.get("sections")
    files_collapsed = data.get("files_collapsed")
    return {
        "sections": [
            {"title": item.get("title"), "file": item.get("file")}
            for item in sections
            if isinstance(item, dict) and item.get("title")
        ] if isinstance(sections, list) else [],
        "files_collapsed": [
            str(item) for item in files_collapsed
        ] if isinstance(files_collapsed, list) else [],
    }


def _quote_matches(region: str, quoted: str) -> list[tuple[int, int]]:
    """Return all whitespace-tolerant character spans of a quote in a region."""
    matches: list[tuple[int, int]] = []
    pattern = r"\s+".join(re.escape(part) for part in quoted.split())
    if not pattern:
        return matches
    for match in re.finditer(pattern, region):
        matches.append((match.start(), match.end()))
    return matches


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


_DERIVED_LOCATION_FIELDS = ("resolved_text", "line_start", "line_end", "contains_math")


def _plan_location_repair(
    canonical: Path,
    record_path: Path,
    location: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a repair plan for one location, or None when it is already hydrated.

    Only derived fields are ever rewritten; byte ranges are never invented or
    changed, and an invalid byte range is reported as unrepairable.
    """
    byte_start = location.get("byte_start")
    byte_end = location.get("byte_end")
    if not isinstance(byte_start, int) or not isinstance(byte_end, int):
        return {
            "path": str(record_path),
            "repairable": False,
            "reason": "location byte_start and byte_end must be integers",
        }
    try:
        resolved = resolve_location(canonical, byte_start, byte_end)
    except KatzError as exc:
        return {
            "path": str(record_path),
            "repairable": False,
            "reason": exc.message,
            "code": exc.code,
        }
    stale = [
        field_name for field_name in _DERIVED_LOCATION_FIELDS
        if location.get(field_name) != resolved[field_name]
    ]
    if not stale:
        return None
    return {
        "path": str(record_path),
        "repairable": True,
        "action": "hydrate_location",
        "fields": stale,
        "hydrated": {field_name: resolved[field_name] for field_name in stale},
    }
