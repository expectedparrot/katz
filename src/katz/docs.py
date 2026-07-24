from __future__ import annotations

import re
from pathlib import Path


DOCS_DIR = Path(__file__).parent / "docs_content"

DOCS: dict[str, dict[str, str]] = {
    "overview": {
        "title": "Package Overview",
        "summary": "What katz does, when to use it, core concepts, and storage layout.",
        "file": "overview.md",
    },
    "getting-started": {
        "title": "Getting Started",
        "summary": "Complete worked example from paper registration through issue investigation.",
        "file": "getting-started.md",
    },
    "workflow": {
        "title": "Review Workflow",
        "summary": "Phase-by-phase guide: init, register, chunk, spotters, find, investigate, report.",
        "file": "workflow.md",
    },
    "edsl-jobs": {
        "title": "EDSL Jobs Workflow",
        "summary": "Build jobs.ep from Katz state, run it with ep, audit it, and ingest results.ep.",
        "file": "edsl-jobs.md",
    },
    "cli-reference": {
        "title": "CLI Quick Reference",
        "summary": "All commands with syntax, flags, and examples.",
        "file": "cli-reference.md",
    },
}


def load_doc(topic: str) -> str:
    meta = DOCS[topic]
    return (DOCS_DIR / meta["file"]).read_text(encoding="utf-8")


def search_docs(query: str) -> list[dict[str, object]]:
    terms = re.findall(r"[A-Za-z0-9_-]+", query.lower())
    results: list[dict[str, object]] = []
    for topic, meta in DOCS.items():
        try:
            text = load_doc(topic)
        except OSError:
            continue
        haystack = f"{topic} {meta['title']} {meta['summary']} {text}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score <= 0:
            continue
        snippet = ""
        for term in terms:
            index = haystack.find(term)
            if index >= 0:
                snippet = text[max(0, index - 60):min(len(text), index + 200)].strip()
                break
        results.append({**meta, "topic": topic, "score": score, "snippet": snippet})
    return sorted(results, key=lambda result: int(result["score"]), reverse=True)
