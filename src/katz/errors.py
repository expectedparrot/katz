"""Error type and stable JSON envelope helpers for the Katz CLI."""
from __future__ import annotations

import json
import sys
from typing import Any

import typer



class KatzError(Exception):
    def __init__(self, message: str, code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}



def _command_argv() -> str:
    """Return the canonical command path, independent of executable spelling."""
    args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if not args:
        return "katz"
    groups = {
        "agent", "docs", "eval", "guide", "issue", "paper", "report",
        "results", "review", "spotter",
    }
    depth = 2 if args[0] in groups and len(args) > 1 else 1
    return " ".join(["katz", *args[:depth]])



def emit_json(
    value: Any,
    *,
    warnings: list[dict[str, Any] | str] | None = None,
    next_steps: list[str] | None = None,
) -> None:
    """Emit exactly one stable, agent-facing JSON envelope."""
    warning_items = warnings or []
    if next_steps is None and isinstance(value, dict):
        inferred = [value.get("next"), value.get("ingest_next")]
        next_steps = [step for step in inferred if isinstance(step, str) and step]
    payload = {
        "status": "warning" if warning_items else "ok",
        "command": _command_argv(),
        "data": value,
        "warnings": warning_items,
        "errors": [],
        "next_steps": next_steps or [],
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=False))



def fail(
    message: str,
    code: str,
    details: dict[str, Any] | None = None,
    *,
    hint: str | None = None,
    next_steps: list[str] | None = None,
) -> None:
    context = dict(details or {})
    embedded_hint = context.pop("hint", None)
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "context": context,
    }
    resolved_hint = hint or (embedded_hint if isinstance(embedded_hint, str) else None)
    if resolved_hint:
        error["hint"] = resolved_hint
    payload = {
        "status": "error",
        "command": _command_argv(),
        "data": {},
        "warnings": [],
        "errors": [error],
        "next_steps": next_steps or [],
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=False))
    raise typer.Exit(1)
