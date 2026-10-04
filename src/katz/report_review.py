"""Bounded diagnostics and honest coverage for one-shot report reviews."""

from __future__ import annotations

from typing import Any


def answer_diagnostic(raw: Any, metadata: dict | None = None) -> dict[str, Any]:
    """Describe unusable text without leaking report contents or guessing cause."""
    if raw is None:
        diagnostic = {"answer_state": "missing", "answer_characters": 0}
    elif isinstance(raw, str):
        diagnostic = {"answer_state": "empty" if not raw.strip() else "unparseable",
                      "answer_characters": len(raw)}
    else:
        diagnostic = {"answer_state": "invalid_structure", "answer_type": type(raw).__name__}
    metadata = metadata or {}
    for key in ("finish_reason", "incomplete_reason", "truncated", "visible_answer_present"):
        if key in metadata:
            diagnostic[key] = metadata[key]
    if metadata.get("truncated"):
        diagnostic["answer_state"] = "truncated"
    return diagnostic


def coverage_summary(reviews: list[dict], failures: list[dict], audit: dict,
                     received: int) -> dict[str, Any]:
    """Never infer the requested panel size from the number of returned rows."""
    bad_rows = {f["index"] for f in failures if "index" in f}
    bad_rows.update(f["index"] for f in audit.get("failures", []) if "index" in f)
    usable = sum(review["index"] not in bad_rows for review in reviews)
    expected = audit.get("expected_answers")
    complete = bool(usable and not failures and audit.get("complete"))
    state = "complete" if complete else "partial" if usable else "failed"
    if expected is None:
        disclosure = (f"Quality review: {usable} usable review(s) from {received} received; "
                      "requested panel coverage is unknown.")
    else:
        disclosure = f"Quality review: {usable} of {expected} requested reviews were usable."
    if state != "complete":
        disclosure += " The optional review failed." if state == "failed" else " Coverage is partial."
    failed_models = sorted({f["reviewer"] for f in failures if f.get("reviewer")})
    if failed_models:
        disclosure += " Reviewers with failures: " + ", ".join(failed_models) + "."
    next_action = (
        f"{disclosure} Include this coverage statement in the next update and final handoff. "
        "Check supported findings against the report, rerun deterministic checks, and regenerate affected outputs. "
        "Preserve the original Results. Inspect failed-answer diagnostics locally; any paid retry needs a new estimate and scoped approval."
    )
    return {"review_status": state, "usable_review_count": usable,
            "requested_review_count": expected, "received_review_count": received,
            "parse_failure_count": len(failures), "failed_reviewers": failed_models,
            "suggested_user_disclosure": disclosure, "next_action": next_action}
