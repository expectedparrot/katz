"""LaTeX preparation: include expansion, structural audit, conversion, provenance."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import KatzError, emit_json
from .manuscript import _provenance_sidecar_path
from .storage import repo_root, write_json


_LATEX_INCLUDE_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")


_LATEX_GRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def _tex_code_and_comment(line: str) -> tuple[str, str]:
    """Split at the first unescaped TeX comment marker."""
    for index, character in enumerate(line):
        if character != "%":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return line[:index], line[index:]
    return line, ""


def _expand_latex_source(
    path: Path,
    allowed_root: Path,
    stack: tuple[Path, ...] = (),
) -> tuple[str, list[Path], list[dict[str, str]]]:
    """Recursively inline braced input/include commands without crossing the repository."""
    resolved = path.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise KatzError(
            "LaTeX include resolves outside the allowed source repository",
            "unsafe_source_reference",
            {"path": str(resolved), "allowed_root": str(allowed_root)},
        ) from exc
    if resolved in stack:
        raise KatzError(
            "Cyclic LaTeX include detected",
            "conversion_error",
            {"cycle": [str(item) for item in (*stack, resolved)]},
        )
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise KatzError(
            "Referenced LaTeX input is missing",
            "missing_source_dependency",
            {"path": str(resolved), "included_from": str(stack[-1]) if stack else None},
        ) from exc
    except UnicodeDecodeError as exc:
        raise KatzError(
            "LaTeX source dependency is not UTF-8",
            "validation_error",
            {"path": str(resolved), "start": exc.start},
        ) from exc

    dependencies = [resolved]
    asset_notes: list[dict[str, str]] = []
    expanded_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        code, comment = _tex_code_and_comment(line)

        def replace_include(match: re.Match[str]) -> str:
            raw_target = match.group(1).strip()
            target = (resolved.parent / raw_target)
            if target.suffix == "":
                target = target.with_suffix(".tex")
            nested, nested_dependencies, nested_asset_notes = _expand_latex_source(
                target,
                allowed_root,
                (*stack, resolved),
            )
            dependencies.extend(nested_dependencies)
            asset_notes.extend(nested_asset_notes)
            try:
                marker_target = target.resolve().relative_to(allowed_root).as_posix()
            except ValueError:
                marker_target = raw_target
            return (
                f"\n% katz: begin inlined {marker_target}\n"
                f"{nested.rstrip()}\n"
                f"% katz: end inlined {marker_target}\n"
            )

        code = _LATEX_INCLUDE_RE.sub(replace_include, code)

        def rewrite_graphic(match: re.Match[str]) -> str:
            raw_target = match.group(1).strip()
            target = (resolved.parent / raw_target)
            candidates = [target] if target.suffix else [
                target.with_suffix(extension)
                for extension in (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps")
            ]
            graphic = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
            if graphic is None:
                asset_notes.append({
                    "code": "missing_graphic",
                    "path": str(target),
                    "included_from": str(resolved),
                    "message": "Referenced graphic was not found; its caption and source path remain in the converted text.",
                })
                return match.group(0)
            try:
                graphic.relative_to(allowed_root)
            except ValueError:
                asset_notes.append({
                    "code": "external_graphic",
                    "path": str(graphic),
                    "included_from": str(resolved),
                    "message": "Graphic is outside the manuscript repository; Katz treated it as a binary asset, not source text.",
                })
            dependencies.append(graphic)
            return match.group(0).replace(match.group(1), graphic.as_posix())

        expanded_lines.append(_LATEX_GRAPHICS_RE.sub(rewrite_graphic, code) + comment)
    unique_dependencies = list(dict.fromkeys(dependencies))
    return "".join(expanded_lines), unique_dependencies, asset_notes


def _latex_source_inventory(text: str) -> dict[str, int]:
    table_wrappers = len(re.findall(r"\\begin\{table\*?\}", text))
    data_tables = len(re.findall(r"\\begin\{(?:longtable|tabular\*?)\}", text))
    return {
        "table_environments": max(table_wrappers, data_tables),
        "figure_environments": len(re.findall(r"\\begin\{figure\*?\}", text)),
        "graphics_references": len(_LATEX_GRAPHICS_RE.findall(text)),
        "equation_environments": len(re.findall(r"\\begin\{(?:equation\*?|align\*?|gather\*?)\}", text)),
    }


def _markdown_table_count(text: str) -> int:
    pipe_tables = len(re.findall(r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", text))
    html_tables = len(re.findall(r"(?i)<table(?:\s|>)", text))
    preserved_latex = len(re.findall(r"\\begin\{(?:table\*?|longtable|tabular\*?)\}", text))
    return pipe_tables + html_tables + preserved_latex


def _balanced_brace_group(text: str, start: int) -> tuple[str, int] | None:
    cursor = start
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text) or text[cursor] != "{":
        return None
    depth = 0
    content_start = cursor + 1
    for index in range(cursor, len(text)):
        if text[index] == "{" and (index == 0 or text[index - 1] != "\\"):
            depth += 1
        elif text[index] == "}" and (index == 0 or text[index - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return text[content_start:index], index + 1
    return None


def _strip_resizebox_wrappers(text: str) -> tuple[str, int]:
    """Replace resizebox(width, height, body) with body so Pandoc sees nested tables."""
    stripped = 0
    search_from = 0
    while True:
        match = re.search(r"\\resizebox\*?", text[search_from:])
        if match is None:
            break
        command_start = search_from + match.start()
        cursor = search_from + match.end()
        groups: list[str] = []
        end = cursor
        for _ in range(3):
            parsed = _balanced_brace_group(text, end)
            if parsed is None:
                break
            value, end = parsed
            groups.append(value)
        if len(groups) != 3:
            search_from = cursor
            continue
        text = text[:command_start] + groups[2] + text[end:]
        stripped += 1
        search_from = command_start + len(groups[2])
    return text, stripped


def _restore_latex_front_matter(text: str) -> tuple[str, dict[str, bool]]:
    """Turn title/maketitle and abstract metadata into explicit document sections."""
    title_match = re.search(r"\\title\s*", text)
    title = None
    if title_match is not None:
        parsed = _balanced_brace_group(text, title_match.end())
        if parsed is not None:
            title, end = parsed
            text = text[:title_match.start()] + text[end:]
    title_restored = False
    if title:
        heading = f"\\section*{{{title}}}"
        if re.search(r"\\maketitle\b", text):
            text = re.sub(r"\\maketitle\b", lambda _: heading, text, count=1)
        else:
            text = re.sub(
                r"(\\begin\{document\})",
                lambda match: match.group(1) + "\n" + heading,
                text,
                count=1,
            )
        title_restored = True
    abstract_pattern = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL)
    abstract_restored = bool(abstract_pattern.search(text))
    text = abstract_pattern.sub(
        lambda match: "\\section*{Abstract}\n" + match.group(1).strip() + "\n",
        text,
    )
    return text, {
        "title_restored": title_restored,
        "abstract_restored": abstract_restored,
    }


_LATEX_INLINE_MARKER_RE = re.compile(r"^%\s*katz:\s*(begin|end)\s+inlined\s+(.+?)\s*$")


_LATEX_HEADING_RE = re.compile(
    r"^\\((?:sub){0,2}section|chapter|part)\*?(?:\[[^\]]*\])?\{(.+?)\}"
)


def _section_provenance_from_expanded(expanded: str, root_label: str) -> list[dict[str, str]]:
    """Map each sectioning command in the expanded LaTeX to its source file.

    `_expand_latex_source` brackets every inlined file with
    `% katz: begin inlined <path>` / `% katz: end inlined <path>` comments, so a
    stack walk attributes each heading to the file that supplied it.
    """
    provenance: list[dict[str, str]] = []
    file_stack: list[str] = [root_label]
    for line in expanded.splitlines():
        stripped = line.strip()
        marker = _LATEX_INLINE_MARKER_RE.match(stripped)
        if marker is not None:
            if marker.group(1) == "begin":
                file_stack.append(marker.group(2))
            elif len(file_stack) > 1:
                file_stack.pop()
            continue
        heading = _LATEX_HEADING_RE.match(stripped)
        if heading is not None:
            provenance.append({"title": heading.group(2).strip(), "file": file_stack[-1]})
    return provenance


def _flatten_html_anchors(markdown: str) -> tuple[str, int]:
    """Keep visible cross-reference text while removing raw HTML anchor markup."""
    count = 0

    def paired(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return match.group(1)

    markdown = re.sub(
        r"<a\b[^>]*>(.*?)</a>",
        paired,
        markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return markdown, count


def _prepare_latex(source: Path, output: Path, allow_lossy: bool) -> None:
    executable = shutil.which("pandoc")
    if executable is None:
        raise KatzError(
            "pandoc is required to prepare LaTeX manuscripts",
            "dependency_error",
            {"install": ["brew", "install", "pandoc"], "source": str(source)},
        )
    try:
        repository = repo_root()
        source.resolve().relative_to(repository)
        allowed_root = repository
    except (KatzError, ValueError):
        allowed_root = source.resolve().parent

    expanded, dependencies, asset_notes = _expand_latex_source(source, allowed_root.resolve())
    unresolved = []
    for line_number, line in enumerate(expanded.splitlines(), start=1):
        code, _ = _tex_code_and_comment(line)
        if _LATEX_INCLUDE_RE.search(code):
            unresolved.append(line_number)
    if unresolved:
        raise KatzError(
            "Some LaTeX include commands could not be expanded",
            "conversion_error",
            {"lines": unresolved[:20]},
        )

    inventory = _latex_source_inventory(expanded)
    expanded, resizebox_wrappers_stripped = _strip_resizebox_wrappers(expanded)
    expanded, front_matter = _restore_latex_front_matter(expanded)
    section_provenance = _section_provenance_from_expanded(expanded, source.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    media_name = f"{output.stem}_media"
    destination_media = output.parent / media_name
    if destination_media.exists():
        raise KatzError(
            "Refusing to overwrite an existing LaTeX media directory",
            "validation_error",
            {"media_directory": str(destination_media)},
        )
    with tempfile.TemporaryDirectory(prefix="katz-latex-") as temp_dir:
        temp_root = Path(temp_dir)
        temp_output = temp_root / output.name
        temp_media = temp_root / media_name
        completed = subprocess.run(
            [
                executable,
                "--from", "latex",
                "--to", "gfm",
                "--wrap=none",
                "--citeproc",
                f"--extract-media={temp_media}",
                "--output", str(temp_output),
                "-",
            ],
            cwd=source.resolve().parent,
            input=expanded,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0 or not temp_output.is_file():
            raise KatzError(
                "Pandoc LaTeX conversion failed",
                "conversion_error",
                {"returncode": completed.returncode, "stderr": completed.stderr[-3000:]},
            )
        markdown = temp_output.read_text(encoding="utf-8")
        markdown = markdown.replace(str(temp_media), media_name)
        markdown, anchors_flattened = _flatten_html_anchors(markdown)
        table_artifacts = _markdown_table_count(markdown)
        warnings_list = [note["message"] for note in asset_notes]
        blocking_warnings = [
            note["message"] for note in asset_notes if note["code"] == "missing_graphic"
        ]
        if inventory["table_environments"] and table_artifacts < inventory["table_environments"]:
            warning = (
                f"LaTeX contains {inventory['table_environments']} table environments, "
                f"but only {table_artifacts} table artifacts were detected after conversion."
            )
            warnings_list.append(warning)
            blocking_warnings.append(warning)
        if inventory["graphics_references"] and not temp_media.exists():
            warning = (
                f"LaTeX references {inventory['graphics_references']} graphics, but Pandoc extracted no media."
            )
            warnings_list.append(warning)
            blocking_warnings.append(warning)
        if blocking_warnings and not allow_lossy:
            raise KatzError(
                "LaTeX structural audit detected possible conversion loss",
                "lossy_conversion",
                {
                    "warnings": warnings_list,
                    "blocking_warnings": blocking_warnings,
                    "external_assets": asset_notes,
                    "source_inventory": inventory,
                    "hint": "Repair the source/conversion or rerun with --allow-lossy after inspecting the discrepancy.",
                },
            )
        output.write_text(markdown, encoding="utf-8")
        assets: list[str] = []
        if temp_media.is_dir():
            shutil.copytree(temp_media, destination_media)
            assets = [str(path) for path in destination_media.rglob("*") if path.is_file()]

    headings = sum(bool(re.match(r"^#{1,6}\s+", line)) for line in markdown.splitlines())
    sidecar = _provenance_sidecar_path(output)
    write_json(sidecar, {
        "schema_version": 1,
        "source_root": str(source),
        "method": "katz-paper-prepare",
        "files_collapsed": [str(path) for path in dependencies],
        "sections": section_provenance,
    })
    emit_json({
        "prepared": True,
        "source_type": "latex",
        "source": str(source),
        "output": str(output),
        "converter": "pandoc",
        "dependencies": [str(path) for path in dependencies],
        "dependency_count": len(dependencies),
        "section_provenance": section_provenance,
        "provenance_sidecar": str(sidecar),
        "source_inventory": inventory,
        "normalization": {
            "resizebox_wrappers_stripped": resizebox_wrappers_stripped,
            "html_anchors_flattened": anchors_flattened,
            **front_matter,
            "citeproc": True,
        },
        "converted_table_artifacts": table_artifacts,
        "assets": assets,
        "external_assets": asset_notes,
        "headings": headings,
        "warnings": warnings_list,
        "lossy_conversion_allowed": allow_lossy,
        "next_actions": [
            ["katz", "ventilate", str(output), "--output-path", str(output.with_name(f"{output.stem}_ventilated.md"))],
        ],
    })
