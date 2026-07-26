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
