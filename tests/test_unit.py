from __future__ import annotations

import json

import pytest

from katz import cli
from katz import autokatz


def test_load_collection_rejects_invalid_json(tmp_path, monkeypatch) -> None:
    collection = tmp_path / "spotters" / "collections"
    collection.mkdir(parents=True)
    (collection / "broken.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(cli, "CATALOG_DIR", tmp_path)

    with pytest.raises(cli.KatzError) as excinfo:
        cli._load_collection("spotters", "broken")

    assert excinfo.value.code == "validation_error"
    assert excinfo.value.details["line"] == 1


def test_load_collection_rejects_non_string_arrays(tmp_path, monkeypatch) -> None:
    collection = tmp_path / "evals" / "collections"
    collection.mkdir(parents=True)
    (collection / "bad.json").write_text(json.dumps(["ok", 3]), encoding="utf-8")
    monkeypatch.setattr(cli, "CATALOG_DIR", tmp_path)

    with pytest.raises(cli.KatzError) as excinfo:
        cli._load_collection("evals", "bad")

    assert excinfo.value.code == "validation_error"
    assert "array of strings" in excinfo.value.message


def test_write_event_json_does_not_overwrite_on_filename_collision(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "event_filename", lambda: "20260101T000000_000000.json")

    first = cli.write_event_json(tmp_path, {"state": "draft"})
    second = cli.write_event_json(tmp_path, {"state": "open"})

    assert first != second
    assert first.name == "20260101T000000_000000.json"
    assert second.name == "20260101T000000_000000_1.json"
    assert cli.read_json(first)["state"] == "draft"
    assert cli.read_json(second)["state"] == "open"


def test_autokatz_command_includes_initial_prompt(tmp_path) -> None:
    prompt_path = tmp_path / "prompt.md"

    cmd = autokatz.build_claude_command(prompt_path)

    assert cmd[:3] == ["claude", "--append-system-prompt-file", str(prompt_path)]
    assert "welcome message" in cmd[3]
    assert "current katz state" in cmd[3]
