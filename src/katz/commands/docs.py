"""`katz docs` commands: packaged documentation topics."""
from __future__ import annotations

import typer
from typing import Any, List, Optional

from ..errors import emit_json, fail


docs_app = typer.Typer(help="Read built-in documentation.")


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
