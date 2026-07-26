"""Tests for the SPEC-salvage features: version group, repair, issue edits,
carry-forward, workspace new, section provenance, and agreement grouping."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from katz import cli


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def _run_katz(repo: Path, *args: str, check: bool) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    return subprocess.run(
        ["python", "-m", "katz.cli", *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def katz(repo: Path, *args: str) -> dict | list:
    result = _run_katz(repo, *args, check=True)
    payload = json.loads(result.stdout)
    assert payload["status"] in {"ok", "warning"}
    assert payload["command"].startswith("katz ")
    assert payload["errors"] == []
    return payload["data"]


def katz_fail(repo: Path, *args: str) -> dict:
    result = _run_katz(repo, *args, check=False)
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["errors"]
    error = payload["errors"][0]
    error["details"] = error["context"]
    return error


def setup_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("# Paper\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    commit = git(repo, "rev-parse", "HEAD")

    canonical = tmp_path / "manuscript.md"
    canonical.write_text("# Title\nOne sentence.\n", encoding="utf-8")

    return repo, canonical, commit


def register_manuscript(repo: Path, canonical: Path) -> str:
    katz(repo, "init")
    result = katz(repo, "paper", "register", "--canonical", str(canonical))
    return result["commit"]


def commit_revision(repo: Path, canonical: Path, text: str, message: str) -> str:
    canonical.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# katz version group
# ---------------------------------------------------------------------------


def test_bare_version_still_reports_build(tmp_path: Path) -> None:
    repo, _, _ = setup_repo(tmp_path)
    result = katz(repo, "version")
    assert result["agent_api_version"]
    assert "version_management" in result["required_capabilities"]


def test_version_list_and_checkout(tmp_path: Path) -> None:
    repo, canonical, first_commit = setup_repo(tmp_path)
    register_manuscript(repo, canonical)

    second_commit = commit_revision(repo, canonical, "# Title\nAnother sentence.\n", "Revise")
    katz(repo, "paper", "register", "--canonical", str(canonical))

    versions = katz(repo, "version", "list")
    # registered_at has second precision, so same-second registrations have no
    # reliable order; assert membership and the active flag instead.
    assert {v["commit"] for v in versions} == {first_commit, second_commit}
    assert {v["commit"]: v["current"] for v in versions} == {
        first_commit: False,
        second_commit: True,
    }
    assert all(v["issue_count"] == 0 for v in versions)

    checked = katz(repo, "version", "checkout", first_commit[:8])
    assert checked["commit"] == first_commit
    assert checked["previous"] == second_commit
    status = katz(repo, "paper", "status")
    assert status["commit"] == first_commit


def test_version_diff_reports_section_changes(tmp_path: Path) -> None:
    repo, canonical, first_commit = setup_repo(tmp_path)
    canonical.write_text(
        "# Intro\nKept sentence.\nChanged sentence.\n# Methods\nStable sentence.\n",
        encoding="utf-8",
    )
    register_manuscript(repo, canonical)
    katz(repo, "paper", "auto-chunk")

    second_commit = commit_revision(
        repo,
        canonical,
        "# Intro\nKept sentence.\nRewritten sentence.\nAdded sentence.\n# Methods\nStable sentence.\n",
        "Revise intro",
    )
    katz(repo, "paper", "register", "--canonical", str(canonical))
    katz(repo, "paper", "auto-chunk")

    diff = katz(repo, "version", "diff", first_commit, second_commit)
    assert diff["from"] == first_commit
    assert diff["to"] == second_commit
    assert diff["identical"] is False
    assert diff["modified_sections"] == ["intro"]
    assert "methods" in diff["unchanged_sections"]
    types = {change["type"] for change in diff["changes"]}
    assert "changed" in types
    assert "added" in types
    changed = next(change for change in diff["changes"] if change["type"] == "changed")
    assert changed["before"] == "Changed sentence."
    assert changed["after"] == "Rewritten sentence."

    same = katz(repo, "version", "diff", second_commit, second_commit)
    assert same["identical"] is True


# ---------------------------------------------------------------------------
# katz repair
# ---------------------------------------------------------------------------


def test_repair_hydrates_locations_and_scaffolding(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    register_manuscript(repo, canonical)

    dest = repo / ".katz" / "versions" / commit
    issue_dir = dest / "issues" / ("a" * 32)
    (issue_dir / "status").mkdir(parents=True)
    (issue_dir / "issue.json").write_text(json.dumps({
        "schema_version": 2,
        "id": "a" * 32,
        "commit": commit,
        "title": "Direct write",
        "body": "Written by an agent without derived fields.",
        "spotter": None,
        "artifacts": [],
        "location": {"byte_start": 8, "byte_end": 21},
        "created_at": "2026-01-01T00:00:00Z",
        "meta": {},
    }), encoding="utf-8")
    (dest / "symbol_table.json").unlink()
    shutil.rmtree(dest / "chunks")

    plan = katz(repo, "repair", "--check")
    assert plan["check"] is True
    assert plan["repaired"] is False
    actions = {item.get("action") for item in plan["planned_repairs"]}
    assert actions == {"hydrate_location", "create_directory", "create_empty_symbol_table"}
    # --check must not write anything
    assert not (dest / "symbol_table.json").exists()
    assert "resolved_text" not in json.loads((issue_dir / "issue.json").read_text())["location"]

    result = katz(repo, "repair")
    assert result["repaired"] is True
    assert (dest / "symbol_table.json").exists()
    assert (dest / "chunks").is_dir()
    hydrated = json.loads((issue_dir / "issue.json").read_text())["location"]
    assert hydrated["resolved_text"] == "One sentence."
    assert hydrated["line_start"] == 2

    # Second run has nothing left to do, and validate is clean.
    assert katz(repo, "repair")["planned_repairs"] == []
    validation = katz(repo, "validate")
    assert validation["valid"] is True
    assert validation["warnings"] == []


def test_repair_reports_unrepairable_ranges(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    register_manuscript(repo, canonical)

    dest = repo / ".katz" / "versions" / commit
    issue_dir = dest / "issues" / ("b" * 32)
    (issue_dir / "status").mkdir(parents=True)
    (issue_dir / "issue.json").write_text(json.dumps({
        "schema_version": 2,
        "id": "b" * 32,
        "commit": commit,
        "title": "Broken",
        "body": "Range out of bounds.",
        "location": {"byte_start": 5, "byte_end": 999999},
        "created_at": "2026-01-01T00:00:00Z",
        "meta": {},
    }), encoding="utf-8")

    result = katz(repo, "repair", "--check")
    assert len(result["unrepairable"]) == 1
    assert result["unrepairable"][0]["code"] == "invalid_range"


# ---------------------------------------------------------------------------
# issue update field merge + issue patch
# ---------------------------------------------------------------------------


def write_issue(repo: Path) -> dict:
    return katz(
        repo, "issue", "write",
        "--title", "Original title",
        "--byte-start", "8",
        "--byte-end", "21",
        "--body", "Original body.",
        "--meta", '{"severity":"minor"}',
    )


def test_issue_update_merges_fields_append_only(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    register_manuscript(repo, canonical)
    issue = write_issue(repo)

    updated = katz(
        repo, "issue", "update",
        "--id", issue["id"],
        "--title", "Better title",
        "--meta", '{"category":"clarity"}',
        "--reason", "typo fix",
    )
    assert updated["issue"]["title"] == "Better title"
    assert updated["issue"]["meta"] == {"severity": "minor", "category": "clarity"}

    shown = katz(repo, "issue", "show", issue["id"])
    assert shown["title"] == "Better title"
    assert shown["body"] == "Original body."
    assert shown["meta"]["category"] == "clarity"
    assert len(shown["edits"]) == 1
    assert shown["edits"][0]["reason"] == "typo fix"

    # The original record is preserved: issue.json is never rewritten.
    issue_json = repo / ".katz" / "versions" / commit / "issues" / issue["id"] / "issue.json"
    assert json.loads(issue_json.read_text())["title"] == "Original title"

    # State-only updates still work and require no edit event.
    state_only = katz(repo, "issue", "update", "--id", issue["id"], "--state", "confirmed")
    assert state_only["state"] == "confirmed"
    assert katz(repo, "issue", "show", issue["id"])["state"] == "confirmed"
    assert len(katz(repo, "issue", "show", issue["id"])["edits"]) == 1


def test_issue_update_requires_a_field(tmp_path: Path) -> None:
    repo, canonical, _ = setup_repo(tmp_path)
    register_manuscript(repo, canonical)
    issue = write_issue(repo)
    error = katz_fail(repo, "issue", "update", "--id", issue["id"])
    assert error["code"] == "validation_error"


def test_issue_patch_sets_single_meta_field(tmp_path: Path) -> None:
    repo, canonical, _ = setup_repo(tmp_path)
    register_manuscript(repo, canonical)
    issue = write_issue(repo)

    patched = katz(repo, "issue", "patch", issue["id"][:8], "severity", "major")
    assert patched["id"] == issue["id"]
    assert patched["meta"]["severity"] == "major"

    # JSON values are parsed; strings stay strings.
    patched = katz(repo, "issue", "patch", issue["id"], "agreement", "0.75")
    assert patched["meta"]["agreement"] == 0.75

    shown = katz(repo, "issue", "show", issue["id"])
    assert shown["meta"]["severity"] == "major"
    assert shown["meta"]["agreement"] == 0.75
    assert len(shown["edits"]) == 2


# ---------------------------------------------------------------------------
# issue carry-forward
# ---------------------------------------------------------------------------


def test_issue_carry_forward_reports_and_applies(tmp_path: Path) -> None:
    repo, canonical, first_commit = setup_repo(tmp_path)
    canonical.write_text(
        "# Intro\nA fragile claim sentence.\nA stable claim sentence.\n",
        encoding="utf-8",
    )
    register_manuscript(repo, canonical)

    stable = katz(
        repo, "issue", "write",
        "--title", "Stable finding",
        "--byte-start", "34",
        "--byte-end", "58",
        "--body", "Anchored to the stable sentence.",
    )
    assert stable["location"]["resolved_text"] == "A stable claim sentence."
    fragile = katz(
        repo, "issue", "write",
        "--title", "Fragile finding",
        "--byte-start", "8",
        "--byte-end", "33",
        "--body", "Anchored to the fragile sentence.",
    )
    assert fragile["location"]["resolved_text"] == "A fragile claim sentence."
    for issue in (stable, fragile):
        katz(repo, "issue", "update", "--id", issue["id"], "--state", "confirmed")

    second_commit = commit_revision(
        repo,
        canonical,
        "# Intro\nA new opening sentence.\nA rewritten fragile line.\nA stable claim sentence.\n",
        "Revise",
    )
    katz(repo, "paper", "register", "--canonical", str(canonical))

    report = katz(repo, "issue", "carry-forward", "--to", second_commit, "--from", first_commit)
    assert report["checked"] == 2
    assert report["persisted"] == 1
    assert report["missing"] == 1
    assert report["applied"] == 0
    by_id = {finding["id"]: finding for finding in report["findings"]}
    assert by_id[stable["id"]]["status"] == "persisted"
    assert by_id[stable["id"]]["moved"] is True
    assert by_id[fragile["id"]]["status"] == "missing"

    applied = katz(
        repo, "issue", "carry-forward",
        "--to", second_commit, "--from", first_commit, "--apply",
    )
    assert applied["applied"] == 1
    new_id = next(f["new_issue_id"] for f in applied["findings"] if f.get("applied"))

    carried = katz(repo, "issue", "show", new_id, "--commit", second_commit)
    assert carried["state"] == "draft"
    assert carried["title"] == "Stable finding"
    assert carried["meta"]["parent_issue_id"] == stable["id"]
    assert carried["meta"]["parent_commit"] == first_commit
    assert carried["location"]["resolved_text"] == "A stable claim sentence."

    # Idempotent: applying again carries nothing new.
    again = katz(
        repo, "issue", "carry-forward",
        "--to", second_commit, "--from", first_commit, "--apply",
    )
    assert again["applied"] == 0
    assert again["already_carried"] == 1


def test_issue_carry_forward_flags_ambiguous_anchors(tmp_path: Path) -> None:
    repo, canonical, first_commit = setup_repo(tmp_path)
    canonical.write_text("# Intro\nA repeated sentence.\n", encoding="utf-8")
    register_manuscript(repo, canonical)
    issue = katz(
        repo, "issue", "write",
        "--title", "Ambiguous later",
        "--byte-start", "8",
        "--byte-end", "28",
        "--body", "Will match twice in the revision.",
        "--state", "confirmed",
    )
    assert issue["location"]["resolved_text"] == "A repeated sentence."

    second_commit = commit_revision(
        repo,
        canonical,
        "# Intro\nA repeated sentence.\nA repeated sentence.\n",
        "Duplicate line",
    )
    katz(repo, "paper", "register", "--canonical", str(canonical))

    report = katz(
        repo, "issue", "carry-forward",
        "--to", second_commit, "--from", first_commit, "--apply",
    )
    assert report["ambiguous"] == 1
    assert report["applied"] == 0
    assert report["findings"][0]["occurrences"] == 2


# ---------------------------------------------------------------------------
# workspace new
# ---------------------------------------------------------------------------


def test_workspace_new_creates_and_registers(tmp_path: Path) -> None:
    canonical = tmp_path / "prepared.md"
    canonical.write_text("# Paper\nA standalone sentence.\n", encoding="utf-8")
    workspace = tmp_path / "ws"

    result = katz(
        tmp_path, "workspace", "new", str(workspace),
        "--canonical", str(canonical),
        "--source", "https://arxiv.org/abs/2301.00001",
    )
    assert result["git_initialized"] is True
    assert result["registration"]["registered"] is True
    assert result["source"]["uri"] == "https://arxiv.org/abs/2301.00001"

    status = katz(workspace, "paper", "status")
    assert status["valid"] is True
    assert status["sentences"] == 1
    assert git(workspace, "rev-parse", "HEAD") == result["registration"]["commit"]


def test_workspace_new_copies_local_source(tmp_path: Path) -> None:
    canonical = tmp_path / "prepared.md"
    canonical.write_text("# Paper\nA sentence.\n", encoding="utf-8")
    source_pdf = tmp_path / "original.pdf"
    source_pdf.write_bytes(b"%PDF-1.4 fake")
    workspace = tmp_path / "ws"

    result = katz(
        tmp_path, "workspace", "new", str(workspace),
        "--canonical", str(canonical),
        "--source", str(source_pdf),
        "--source-format", "pdf",
    )
    assert result["source"]["root"] == "source/original.pdf"
    assert (workspace / "source" / "original.pdf").is_file()


def test_workspace_new_refuses_existing_directory(tmp_path: Path) -> None:
    canonical = tmp_path / "prepared.md"
    canonical.write_text("# Paper\nA sentence.\n", encoding="utf-8")
    (tmp_path / "ws").mkdir()
    error = katz_fail(
        tmp_path, "workspace", "new", str(tmp_path / "ws"),
        "--canonical", str(canonical),
    )
    assert error["code"] == "validation_error"


# ---------------------------------------------------------------------------
# section provenance
# ---------------------------------------------------------------------------


def test_section_provenance_from_expanded_markers() -> None:
    expanded = "\n".join([
        r"\section{Introduction}",
        "Root prose.",
        r"% katz: begin inlined sections/model.tex",
        r"\section{The Model}",
        "Model prose.",
        r"% katz: begin inlined sections/nested.tex",
        r"\subsection{Nested Part}",
        r"% katz: end inlined sections/nested.tex",
        r"% katz: end inlined sections/model.tex",
        r"\section{Conclusion}",
    ])
    provenance = cli._section_provenance_from_expanded(expanded, "main.tex")
    assert provenance == [
        {"title": "Introduction", "file": "main.tex"},
        {"title": "The Model", "file": "sections/model.tex"},
        {"title": "Nested Part", "file": "sections/nested.tex"},
        {"title": "Conclusion", "file": "main.tex"},
    ]


def test_register_picks_up_provenance_sidecar_and_auto_chunk_attaches(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    canonical.write_text("# Introduction\nIntro sentence.\n# The Model\nModel sentence.\n", encoding="utf-8")
    sidecar = canonical.with_name(canonical.name + ".provenance.json")
    sidecar.write_text(json.dumps({
        "schema_version": 1,
        "source_root": "main.tex",
        "files_collapsed": ["main.tex", "sections/model.tex"],
        "sections": [
            {"title": "Introduction", "file": "main.tex"},
            {"title": "The Model", "file": "sections/model.tex"},
        ],
    }), encoding="utf-8")

    katz(repo, "init")
    result = katz(repo, "paper", "register", "--canonical", str(canonical))
    assert result["provenance"] == {"sections": 2, "files_collapsed": 2}

    katz(repo, "paper", "auto-chunk")
    sections = katz(repo, "paper", "sections")
    by_id = {section["id"]: section for section in sections}
    assert by_id["introduction"]["source_file"] == "main.tex"
    assert by_id["the-model"]["source_file"] == "sections/model.tex"

    version = json.loads(
        (repo / ".katz" / "versions" / commit / "version.json").read_text()
    )
    assert version["source"]["files_collapsed"] == ["main.tex", "sections/model.tex"]


def test_ventilate_copies_provenance_sidecar(tmp_path: Path) -> None:
    source = tmp_path / "paper.md"
    source.write_text("# Paper\nFirst sentence. Second sentence.\n", encoding="utf-8")
    sidecar = source.with_name(source.name + ".provenance.json")
    sidecar.write_text(json.dumps({"schema_version": 1, "sections": []}), encoding="utf-8")
    output = tmp_path / "paper_ventilated.md"

    katz(tmp_path, "ventilate", str(source), "--output-path", str(output))
    assert output.with_name(output.name + ".provenance.json").is_file()


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_paper_prepare_latex_writes_provenance_sidecar(tmp_path: Path) -> None:
    repo, _, _ = setup_repo(tmp_path)
    (repo / "sections").mkdir()
    (repo / "sections" / "model.tex").write_text(
        "\\section{The Model}\nModel prose sentence.\n", encoding="utf-8"
    )
    (repo / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\n"
        "Intro prose sentence.\n"
        "\\input{sections/model}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    result = katz(
        repo, "paper", "prepare", str(repo / "main.tex"),
        "--output", str(repo / "prepared" / "paper.md"),
    )
    assert result["section_provenance"] == [
        {"title": "Introduction", "file": "main.tex"},
        {"title": "The Model", "file": "sections/model.tex"},
    ]
    sidecar = json.loads(Path(result["provenance_sidecar"]).read_text())
    assert sidecar["sections"] == result["section_provenance"]
    assert any(dep.endswith("model.tex") for dep in sidecar["files_collapsed"])


# ---------------------------------------------------------------------------
# cross-model agreement grouping
# ---------------------------------------------------------------------------


def _positive(spotter: str, start: int, end: int, model: str, key: str) -> dict:
    return {
        "spotter": spotter,
        "byte_start": start,
        "byte_end": end,
        "model": model,
        "answer": {"found": True, "title": f"{spotter} finding", "description": "d"},
        "result_key": key,
    }


def test_group_positive_findings_merges_overlaps_across_models() -> None:
    positives = [
        _positive("causal_language", 100, 160, "model-a", "k1"),
        _positive("causal_language", 120, 150, "model-b", "k2"),
        _positive("causal_language", 500, 540, "model-b", "k3"),
        _positive("overclaiming", 100, 160, "model-a", "k4"),
    ]
    groups = cli._group_positive_findings(positives)
    assert len(groups) == 3
    merged = next(group for group in groups if len(group) == 2)
    assert {member["model"] for member in merged} == {"model-a", "model-b"}
    assert all(member["spotter"] == "causal_language" for member in merged)


def test_group_positive_findings_chains_transitive_overlaps() -> None:
    positives = [
        _positive("s", 100, 130, "model-a", "k1"),
        _positive("s", 125, 160, "model-b", "k2"),
        _positive("s", 155, 190, "model-c", "k3"),
    ]
    groups = cli._group_positive_findings(positives)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_group_positive_findings_keeps_disjoint_separate() -> None:
    positives = [
        _positive("s", 0, 10, "model-a", "k1"),
        _positive("s", 10, 20, "model-a", "k2"),  # half-open: no overlap at 10
    ]
    groups = cli._group_positive_findings(positives)
    assert len(groups) == 2
