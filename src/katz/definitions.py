"""Spotter and eval definition parsing, catalogs, and validation sets."""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from .assets import CATALOG_DIR
from .errors import KatzError


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


VALID_GRADES = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"}
