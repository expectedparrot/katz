"""Error type and stable JSON envelope helpers for the Katz CLI."""
from __future__ import annotations

import json
import sys
from contextvars import ContextVar
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text


_HUMAN_OUTPUT: ContextVar[bool] = ContextVar("katz_human_output", default=False)


class KatzError(Exception):
    def __init__(self, message: str, code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def configure_output(*, human: bool) -> None:
    """Select the explicit human renderer for the current CLI invocation."""
    _HUMAN_OUTPUT.set(human)


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _record_table(records: list[dict[str, Any]]) -> Table:
    columns: list[str] = []
    for record in records:
        for key, value in record.items():
            if key not in columns and not isinstance(value, (dict, list)):
                columns.append(key)
    columns = columns[:8] or ["value"]
    table = Table(show_header=True, header_style="bold green", expand=False)
    for column in columns:
        table.add_column(column.replace("_", " ").title(), overflow="fold")
    for record in records:
        table.add_row(*[_cell(record.get(column)) for column in columns])
    return table


def _render_human_data(console: Console, value: Any) -> None:
    if isinstance(value, list):
        if not value:
            console.print("[dim]No records.[/dim]")
        elif all(isinstance(item, dict) for item in value):
            console.print(_record_table(value))
        else:
            for item in value:
                console.print(Text(f"• {_cell(item)}"))
        return
    if not isinstance(value, dict):
        console.print(Text(_cell(value)))
        return

    scalars = {
        key: item for key, item in value.items()
        if not isinstance(item, (dict, list))
    }
    if scalars:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Field", style="bold green", no_wrap=True)
        table.add_column("Value", overflow="fold")
        for key, item in scalars.items():
            table.add_row(key.replace("_", " ").title(), _cell(item))
        console.print(table)

    for key, item in value.items():
        if not isinstance(item, (dict, list)):
            continue
        console.print()
        console.print(Text(key.replace("_", " ").title(), style="bold"))
        _render_human_data(console, item)


def _render_human_success(
    command: str,
    value: Any,
    warnings: list[dict[str, Any] | str],
    next_steps: list[str],
) -> None:
    console = Console()
    console.print(Text.assemble(("✓ ", "bold green"), (command, "bold")))
    _render_human_data(console, value)
    if warnings:
        console.print("\n[bold yellow]Warnings[/bold yellow]")
        for warning in warnings:
            console.print(Text(f"• {_cell(warning)}"))
    if next_steps:
        console.print("\n[bold]Next steps[/bold]")
        for index, step in enumerate(next_steps, start=1):
            console.print(Text(f"{index}. {step}"))


def _render_human_error(
    command: str,
    error: dict[str, Any],
    next_steps: list[str],
) -> None:
    console = Console()
    console.print(Text(f"✗ {command}", style="bold red"))
    console.print(
        Text.assemble(
            (str(error["code"]), "bold"),
            f": {error['message']}",
        )
    )
    context = error.get("context") or {}
    if context:
        _render_human_data(console, context)
    if error.get("hint"):
        console.print()
        console.print(Text.assemble(("Hint: ", "bold"), str(error["hint"])))
    if next_steps:
        console.print("\n[bold]Next steps[/bold]")
        for index, step in enumerate(next_steps, start=1):
            console.print(Text(f"{index}. {step}"))

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
    if _HUMAN_OUTPUT.get():
        _render_human_success(
            payload["command"],
            value,
            warning_items,
            payload["next_steps"],
        )
        return
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
    if _HUMAN_OUTPUT.get():
        _render_human_error(payload["command"], error, payload["next_steps"])
        raise typer.Exit(1)
    typer.echo(json.dumps(payload, indent=2, sort_keys=False))
    raise typer.Exit(1)
