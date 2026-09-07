"""Bundled asset locations and the agent API version."""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


SKILLS_DIR = Path(__file__).parent / "skills"


CATALOG_DIR = Path(__file__).parent / "catalog"


REPORT_SCRIPT = SKILLS_DIR / "find-issues" / "scripts" / "generate_review_report.py"


SCHEMAS_DIR = Path(__file__).parent / "schemas"


TEMPLATES_DIR = Path(__file__).parent / "templates"


AGENT_API_VERSION = "1.0"


def referenced_images(manuscript: Path, explicit: list[Path] | None = None) -> list[dict]:
    """Resolve local inline, reference-style, and HTML images without fetching URLs."""
    import re
    from urllib.parse import unquote, urlsplit
    from .errors import KatzError
    from .storage import sha256_file
    text = manuscript.read_bytes().decode("utf-8")
    refs = re.findall(r'!\[[^\]]*\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)', text)
    refs += re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', text, re.IGNORECASE)
    definitions = {name.casefold(): target for name, target in re.findall(r'^\s*\[([^]]+)\]:\s*<?([^\s>]+)>?', text, re.MULTILINE)}
    for alt, label in re.findall(r'!\[([^]]*)\]\[([^]]*)\]', text):
        target = definitions.get((label or alt).casefold())
        if target is None:
            raise KatzError("Image reference has no definition", "not_found", {"reference": label or alt})
        refs.append(target)
    items = [(ref, (manuscript.parent / unquote(urlsplit(ref).path)).resolve()) for ref in refs
             if not urlsplit(ref).scheme and not urlsplit(ref).netloc]
    items += [(str(path), path.resolve()) for path in explicit or []]
    found = []
    seen = set()
    for reference, path in items:
        if reference in seen:
            continue
        if not path.is_file():
            raise KatzError("Referenced image is missing", "not_found", {"reference": reference})
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
            raise KatzError("Unsupported image format", "validation_error", {"reference": reference})
        seen.add(reference)
        found.append({"reference": reference, "path": path, "sha256": sha256_file(path)})
    return found


def copy_referenced_images(manuscript: Path, destination: Path) -> list[dict]:
    import shutil
    from urllib.parse import unquote, urlsplit
    from .errors import KatzError
    records = []
    for image in referenced_images(manuscript):
        relative = Path(unquote(urlsplit(image["reference"]).path))
        if relative.is_absolute() or ".." in relative.parts:
            raise KatzError("Prepare images inside the canonical manuscript directory before registration",
                            "unsafe_source_reference", {"reference": image["reference"]})
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if image["path"].resolve() != target.resolve():
            shutil.copyfile(image["path"], target)
        records.append({"reference": image["reference"], "path": str(relative), "sha256": image["sha256"]})
    return records


def validate_registered_assets(dest: Path, version: dict) -> list[dict]:
    from .storage import sha256_file
    errors = []
    for asset in version.get("assets", []):
        path = (dest / "paper" / asset["path"]).resolve()
        if not path.is_relative_to((dest / "paper").resolve()):
            errors.append({"code": "unsafe_source_reference", "path": asset["path"]})
        elif not path.is_file() or sha256_file(path) != asset["sha256"]:
            errors.append({"code": "checksum_mismatch", "path": str(path)})
    return errors
