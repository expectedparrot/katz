"""`katz paper` commands: registration, preparation, sections, lookup."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import typer
from pathlib import Path
from typing import Any, List, Optional

from ..edsl_bridge import (
    ECONOMICS_REVIEW_QUESTION_TEXT,
    _expected_results_path,
    _save_and_verify_ep,
)
from ..errors import KatzError, emit_json, fail
from ..latex import _prepare_latex
from ..manuscript import (
    _count_non_ventilated_lines,
    _load_provenance_sidecar,
    line_bounds,
    resolve_location,
    section_for_range,
    segment_sentences,
)
from ..storage import (
    active_commit,
    active_version_path,
    append_jsonl,
    current_commit,
    ensure_initialized,
    load_version,
    now_utc,
    parse_meta,
    read_json,
    record_run,
    repo_root,
    sha256_file,
    version_dir,
    write_json,
    write_jsonl,
)


paper_app = typer.Typer(help="Register and query canonical manuscripts.")


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
        if canonical.suffix.lower() == ".pdf":
            raise KatzError(
                "A PDF cannot be the canonical review text; extract it to Markdown first",
                "binary_manuscript",
                {
                    "canonical": str(canonical),
                    "next_action": [
                        "katz", "paper", "prepare", str(canonical),
                        "--output", str(canonical.with_suffix(".md")),
                    ],
                },
            )
        if canonical.suffix.lower() in {".tex", ".latex"}:
            raise KatzError(
                "LaTeX source must be assembled and structurally audited before registration",
                "source_manuscript_requires_preparation",
                {
                    "canonical": str(canonical),
                    "next_action": [
                        "katz", "paper", "prepare", str(canonical),
                        "--output", str(canonical.with_suffix(".md")),
                    ],
                    "reason": "Direct registration can omit content supplied through input/include files.",
                },
            )
        emit_json(_register_manuscript(
            canonical,
            source_root=source_root,
            source_uri=source_uri,
            source_format=source_format,
            source_method=source_method,
            source_meta=source_meta,
        ))
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


def _register_manuscript(
    canonical: Path,
    *,
    source_root: Optional[str] = None,
    source_uri: Optional[str] = None,
    source_format: str = "unknown",
    source_method: str = "unknown",
    source_meta: Optional[str] = None,
) -> dict[str, Any]:
    """Register a committed canonical manuscript for the current repository HEAD.

    Shared by `paper register` and `workspace new`; raises KatzError and returns
    the registration result instead of emitting it.
    """
    ensure_initialized()
    root = repo_root()
    try:
        relative_canonical = canonical.resolve().relative_to(root)
    except ValueError:
        relative_canonical = None
    if relative_canonical is not None:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(relative_canonical)],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", str(relative_canonical)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if tracked.returncode != 0 or status.stdout.strip():
            raise KatzError(
                "Canonical manuscript must be committed before registration",
                "uncommitted_manuscript",
                {
                    "canonical": str(relative_canonical),
                    "git_status": status.stdout.strip() or "untracked",
                    "next_actions": [
                        ["git", "add", "--", str(relative_canonical)],
                        ["git", "commit", "-m", "Add canonical manuscript for Katz review"],
                    ],
                },
            )
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
    provenance = _load_provenance_sidecar(canonical)
    if provenance is not None:
        if provenance.get("files_collapsed") and not source.get("files_collapsed"):
            source["files_collapsed"] = provenance["files_collapsed"]
        if provenance.get("sections"):
            source["section_provenance"] = provenance["sections"]

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

    parent_commit: Optional[str] = None
    try:
        existing_active = active_commit()
        if existing_active != commit and version_dir(existing_active).is_dir():
            parent_commit = existing_active
    except KatzError:
        parent_commit = None

    version = {
        "schema_version": 1,
        "commit": commit,
        "registered_at": now_utc(),
        "canonical": "paper/manuscript.md",
        "paper_map": "paper_map.jsonl",
        "checksum": checksum,
        "source": source,
        "parent_commit": parent_commit,
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
    if provenance is not None:
        result["provenance"] = {
            "sections": len(provenance.get("sections") or []),
            "files_collapsed": len(provenance.get("files_collapsed") or []),
        }
    if non_ventilated > 0:
        result["warning"] = (
            f"{non_ventilated} line(s) appear to contain multiple sentences. "
            "Katz works best with ventilated prose (one sentence per line). "
            "Consider reformatting the manuscript so each sentence is on its own line."
        )
    return result


@paper_app.command("prepare")
def paper_prepare(
    source: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, readable=True),
    output: Path = typer.Option(..., "--output", "-o"),
    backend: str = typer.Option("auto", "--backend"),
    allow_lossy: bool = typer.Option(False, "--allow-lossy", help="Keep a LaTeX conversion whose structural audit reports possible losses."),
) -> None:
    """Prepare PDF or LaTeX source as canonical Markdown plus figure assets."""
    try:
        source_type = source.suffix.lower()
        if source_type not in {".pdf", ".tex", ".latex"}:
            raise KatzError(
                "paper prepare accepts PDF or LaTeX input",
                "validation_error",
                {"source": str(source), "supported_extensions": [".pdf", ".tex", ".latex"]},
            )
        if output.suffix.lower() not in {".md", ".markdown"}:
            raise KatzError("--output must be a Markdown path", "validation_error", {"output": str(output)})
        if output.exists():
            raise KatzError("Refusing to overwrite the output manuscript", "validation_error", {"output": str(output)})
        if source_type in {".tex", ".latex"}:
            _prepare_latex(source, output, allow_lossy=allow_lossy)
            return
        executable = shutil.which("paper2md")
        if executable is None:
            raise KatzError(
                "paper2md is required to extract PDF text, figures, and tables",
                "dependency_error",
                {
                    "install": ["python", "-m", "pip", "install", "paper2md"],
                    "fallback": ["pdftotext", str(source), str(output.with_suffix(".txt"))],
                },
            )
        if backend not in {"auto", "marker", "pymupdf"}:
            raise KatzError("Invalid paper2md backend", "validation_error", {"backend": backend})
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="katz-paper2md-") as temp_dir:
            completed = subprocess.run(
                [executable, str(source), "--output", temp_dir, "--backend", backend],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if completed.returncode != 0:
                raise KatzError(
                    "paper2md extraction failed",
                    "conversion_error",
                    {"returncode": completed.returncode, "stderr": completed.stderr[-2000:]},
                )
            extracted = sorted(Path(temp_dir).rglob("*.md"))
            if not extracted:
                raise KatzError("paper2md produced no Markdown file", "conversion_error")
            shutil.copyfile(extracted[0], output)
            assets: list[str] = []
            for asset in sorted(Path(temp_dir).rglob("*")):
                if not asset.is_file() or asset == extracted[0] or asset.suffix.lower() not in {
                    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
                }:
                    continue
                destination = output.parent / asset.name
                if destination.exists():
                    destination = output.parent / f"{asset.stem}-{len(assets) + 1}{asset.suffix}"
                shutil.copyfile(asset, destination)
                assets.append(str(destination))
        text = output.read_text(encoding="utf-8")
        headings = sum(bool(re.match(r"^#{1,6}\s+", line)) for line in text.splitlines())
        emit_json({
            "prepared": True,
            "source": str(source),
            "output": str(output),
            "backend": backend,
            "bytes": output.stat().st_size,
            "headings": headings,
            "assets": assets,
            "warnings": [] if headings else [
                "No Markdown headings were detected; section mapping will require cleanup before registration."
            ],
            "next_actions": [
                ["katz", "ventilate", str(output), "--output-path", str(output.with_name(f"{output.stem}_ventilated.md"))],
            ],
        })
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

        # Attach source-file provenance recorded by `paper prepare` when the
        # section titles can be matched against the conversion's heading map.
        section_provenance = (version.get("source") or {}).get("section_provenance")
        if isinstance(section_provenance, list):
            provenance_by_title: dict[str, str] = {}
            for entry in section_provenance:
                if isinstance(entry, dict) and entry.get("title") and entry.get("file"):
                    provenance_by_title.setdefault(str(entry["title"]).strip().lower(), str(entry["file"]))
            for section in sections:
                source_file = provenance_by_title.get(section["title"].strip().lower())
                if source_file:
                    section["source_file"] = source_file

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
                **({"source_file": s["source_file"]} if s.get("source_file") else {}),
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
        saved = _save_and_verify_ep(job, output)
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
            "expected_model_calls": "1 × the number of externally selected models",
            "model_specifications": "Selected explicitly when running ep; none embedded by Katz.",
            "likely_cost": "Unknown until the external model is selected.",
            "inference": "external",
            "attachments": attachment_records,
            "saved": saved,
            "next": f"ep run {output} --model <frontier-model> --output {expected_results}",
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"output": str(output)})
