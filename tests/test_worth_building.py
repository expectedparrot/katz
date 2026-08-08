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
from katz.commands.report import _write_outputs_transactionally


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


def setup_finalizable_review(tmp_path: Path) -> tuple[Path, Path, str]:
    repo, canonical, _ = setup_repo(tmp_path)
    commit = register_manuscript(repo, canonical)
    katz(repo, "paper", "auto-chunk")
    issue = katz(
        repo, "issue", "write",
        "--title", "Clarify the estimate",
        "--body", "The estimate needs context.",
        "--byte-start", "8", "--byte-end", "20",
    )
    katz(
        repo, "issue", "investigate",
        "--id", issue["id"], "--verdict", "confirmed",
        "--notes", "The concern remains after checking the manuscript.",
    )
    runs = repo / ".katz" / "versions" / commit / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "complete.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "spotter",
        "status": "ingested",
        "timestamp": "2026-08-08T00:00:00Z",
        "skipped": 0,
        "audit": {
            "expected_answers": 1,
            "valid_answers": 1,
            "null_answers": 0,
            "invalid_answers": 0,
            "model_exceptions": 0,
            "missing_answers": 0,
            "coverage": 1.0,
            "complete": True,
        },
    }, indent=2), encoding="utf-8")
    report = repo / "writeup" / "report.md"
    report.parent.mkdir()
    report.write_text(
        "---\ntitle: Referee Report\n---\n\n"
        "## Summary\n\nThe paper makes a useful contribution.\n\n"
        "## Major concerns\n\n- Clarify the reported estimate.\n",
        encoding="utf-8",
    )
    return repo, report, commit


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


def test_workspace_new_from_source_packages_review(tmp_path: Path) -> None:
    source = tmp_path / "draft.md"
    source.write_text(
        "# Summary\nA claim sentence. Another claim sentence.\n"
        "# Methods\nA method sentence.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "ws"

    result = katz(
        tmp_path, "workspace", "new", str(workspace),
        "--from", str(source),
        "--model", "gpt-4.1-mini",
    )
    assert result["registration"]["registered"] is True
    assert result["jobs_ready"] is True
    assert result["canonical"] == "paper/draft_ventilated.md"
    assert result["source"]["root"] == "source/draft.md"

    steps = result["steps"]
    assert steps["ventilate"]["data"]["lines_changed"] == 1
    assert steps["auto_chunk"]["status"] == "ok"
    assert steps["spotter_catalog"]["status"] == "ok"
    assert steps["spotter_enable"]["status"] == "ok"
    assert steps["spotter_jobs"]["status"] == "ok"
    assert steps["spotter_jobs"]["data"]["scenario_count"] > 0
    assert steps["spotter_models"]["data"]["models"] == ["gpt-4.1-mini"]
    assert (workspace / "jobs.ep").is_file()
    assert (workspace / "models.ep").is_file()

    # Source provenance was inferred from the --from suffix.
    status = katz(workspace, "paper", "status")
    assert status["source_format"] == "markdown"
    assert status["sections"] == 2

    # The two human checkpoints are explicit: inspect, then authorize ep run.
    payload = json.loads(_run_katz(
        tmp_path, "workspace", "new", str(tmp_path / "ws2"), "--from", str(source),
        check=True,
    ).stdout)
    assert any("Inspect" in step for step in payload["next_steps"])
    assert any("ep run jobs.ep" in step for step in payload["next_steps"])
    assert "never runs models" in payload["data"]["execution_boundary"]


def test_workspace_new_requires_exactly_one_input(tmp_path: Path) -> None:
    canonical = tmp_path / "prepared.md"
    canonical.write_text("# Paper\nA sentence.\n", encoding="utf-8")
    error = katz_fail(
        tmp_path, "workspace", "new", str(tmp_path / "ws"),
        "--canonical", str(canonical), "--from", str(canonical),
    )
    assert error["code"] == "validation_error"
    error = katz_fail(tmp_path, "workspace", "new", str(tmp_path / "ws"))
    assert error["code"] == "validation_error"


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
# bounded report finalization
# ---------------------------------------------------------------------------


def test_report_finalize_preview_apply_and_replay(tmp_path: Path) -> None:
    repo, report, _ = setup_finalizable_review(tmp_path)
    narrative = repo / "writeup" / "report.html"
    explorer = repo / "writeup" / "issues.html"

    preview = katz(
        repo, "report", "finalize", "--report", str(report),
        "--html", str(narrative), "--explorer", str(explorer),
    )
    assert preview["mode"] == "preview"
    assert preview["complete"] is True
    assert preview["coverage"] == {
        "requested": 1,
        "valid": 1,
        "incomplete": 0,
        "fraction": 1.0,
        "parse_failures": 0,
        "missing_answers": 0,
        "ingestion_skips": 0,
        "anchoring_failures": 0,
        "anchoring_failures_are_upper_bound": False,
    }
    assert not narrative.exists()
    command = preview["next_actions"][0]["command"]
    assert command[-1] == "--apply"

    applied = katz(repo, *command[1:])
    assert applied["mode"] == "applied"
    assert applied["plan_hash"] == preview["plan_hash"]
    assert applied["complete"] is True
    assert narrative.is_file() and explorer.is_file()
    narrative_text = narrative.read_text(encoding="utf-8")
    assert "Complete reviewed coverage" in narrative_text
    assert "The paper makes a useful contribution." in narrative_text
    mtimes = (narrative.stat().st_mtime_ns, explorer.stat().st_mtime_ns)

    replayed = katz(repo, *command[1:])
    assert replayed["plan_hash"] == preview["plan_hash"], replayed
    assert replayed["mode"] == "replayed", replayed
    assert replayed["replayed"] is True
    assert (narrative.stat().st_mtime_ns, explorer.stat().st_mtime_ns) == mtimes


def test_report_finalize_rejects_yaml_title_with_h1(tmp_path: Path) -> None:
    repo, report, _ = setup_finalizable_review(tmp_path)
    report.write_text(
        "---\ntitle: Referee Report\n---\n\n# Referee Report\n\n## Summary\n\nText.\n",
        encoding="utf-8",
    )
    narrative = repo / "writeup" / "report.html"
    error = katz_fail(
        repo, "report", "finalize", "--report", str(report),
        "--html", str(narrative), "--apply",
    )
    assert error["code"] == "report_check_failed"
    assert error["details"]["lines"] == [5]
    assert "change body H1 headings to H2" in error["details"]["suggestion"]
    assert not narrative.exists()


def test_report_finalize_marks_partial_evidence_incomplete(tmp_path: Path) -> None:
    repo, report, commit = setup_finalizable_review(tmp_path)
    run_path = repo / ".katz" / "versions" / commit / "runs" / "complete.json"
    run = json.loads(run_path.read_text())
    run["status"] = "partial"
    run["skipped"] = 2
    run["audit"].update({"complete": False, "valid_answers": 0, "coverage": 0.0, "invalid_answers": 1})
    run_path.write_text(json.dumps(run), encoding="utf-8")

    preview_result = _run_katz(
        repo, "report", "finalize", "--report", str(report), check=True,
    )
    envelope = json.loads(preview_result.stdout)
    assert envelope["status"] == "warning"
    preview = envelope["data"]
    assert preview["complete"] is False
    assert set(preview["coverage"].keys()) >= {"parse_failures", "ingestion_skips", "anchoring_failures"}
    assert set(envelope["warnings"][0]["reasons"]) >= {"incomplete_model_coverage", "partial_ingestion", "ingestion_skips_present"}

    applied_result = _run_katz(
        repo, "report", "finalize", "--report", str(report), "--apply", check=True,
    )
    applied = json.loads(applied_result.stdout)["data"]
    assert applied["complete"] is False
    assert "Incomplete review evidence" in report.with_suffix(".html").read_text(encoding="utf-8")


def test_report_finalize_rejects_stale_preview_plan(tmp_path: Path) -> None:
    repo, report, _ = setup_finalizable_review(tmp_path)
    preview = katz(repo, "report", "finalize", "--report", str(report))
    katz(
        repo, "issue", "write",
        "--title", "New draft", "--body", "Created after preview.",
        "--byte-start", "0", "--byte-end", "5",
    )
    error = katz_fail(
        repo, "report", "finalize", "--report", str(report),
        "--expect-plan", preview["plan_hash"], "--apply",
    )
    assert error["code"] == "stale_finalization_plan"
    assert not report.with_suffix(".html").exists()


def test_report_output_transaction_rolls_back_on_publish_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    first.write_bytes(b"original")
    real_replace = os.replace
    calls = 0

    def fail_second(source: str | Path, destination: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publish failure")
        real_replace(source, destination)

    monkeypatch.setattr("katz.commands.report.os.replace", fail_second)
    with pytest.raises(OSError, match="simulated publish failure"):
        _write_outputs_transactionally({first: b"new first", second: b"new second"})

    assert first.read_bytes() == b"original"
    assert not second.exists()


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
