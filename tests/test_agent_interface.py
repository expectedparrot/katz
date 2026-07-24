from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return result.stdout.strip()


def katz(repo: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        ["python", "-m", "katz.cli", *args],
        cwd=repo, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    return payload["data"]


def setup_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    manuscript = repo / "paper.md"
    manuscript.write_text(
        "# Paper\n\n## Abstract\n\nA claim appears here.\n\n"
        "## Methods\n\nWe estimate a model.\n\n## Results\n\nThe effect is large.\n\n"
        "## References\n\nA. Author (2020).\n",
        encoding="utf-8",
    )
    git(repo, "add", "paper.md")
    git(repo, "commit", "-m", "Add paper")
    return repo, manuscript, git(repo, "rev-parse", "HEAD")


def test_agent_bootstrap_is_read_only_and_returns_actions(tmp_path: Path) -> None:
    repo, manuscript, _ = setup_repo(tmp_path)

    before = git(repo, "status", "--porcelain")
    result = katz(repo, "agent", "bootstrap")
    after = git(repo, "status", "--porcelain")

    assert result["mode"] == "read_only_bootstrap"
    assert result["phase"] == "katz_setup"
    assert result["next_actions"][0]["command"] == ["katz", "init"]
    assert result["next_actions"][0]["mutates_state"] is True
    assert before == after
    assert not (repo / ".katz").exists()


def test_agent_status_advances_and_instruction_templates_are_available(tmp_path: Path) -> None:
    repo, manuscript, commit = setup_repo(tmp_path)
    katz(repo, "init")
    registration = katz(
        repo, "paper", "register", "--canonical", str(manuscript),
        "--source-format", "markdown", "--source-method", "test",
    )
    assert registration["commit"] == commit

    status = katz(repo, "agent", "status")
    assert status["phase"] == "section_mapping"
    assert status["next_actions"][0]["command"] == ["katz", "paper", "auto-chunk"]

    codex = katz(repo, "agent", "instructions", "codex")
    claude = katz(repo, "agent", "instructions", "claude")
    assert codex["suggested_filename"] == "AGENTS.md"
    assert claude["suggested_filename"] == "CLAUDE.md"
    assert "katz agent bootstrap" in codex["markdown"]
    written = katz(repo, "agent", "instructions", "--write")
    assert {item["path"] for item in written["written"]} == {"AGENTS.md", "CLAUDE.md"}
    assert (repo / "AGENTS.md").is_file()
    assert (repo / "CLAUDE.md").is_file()


def test_agent_ignores_agent_files_and_prepares_pdf_candidate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    agent_file = repo / ".claude" / "agents" / "review-report.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text("# Abstract\n\n## Methods\n\n## Results\n" + "agent " * 1000)
    pdf = repo / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"paper " * 1000)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Add upload")
    katz(repo, "init")

    status = katz(repo, "agent", "status")
    candidates = status["review"]["manuscript_candidates"]
    assert [candidate["path"] for candidate in candidates] == ["paper.pdf"]
    assert status["next_actions"][0]["id"] == "prepare_manuscript"
    assert status["next_actions"][0]["command"][0:3] == ["katz", "paper", "prepare"]


def test_agent_prioritizes_and_commits_ventilated_derivative(tmp_path: Path) -> None:
    repo, manuscript, _ = setup_repo(tmp_path)
    katz(repo, "init")
    ventilated = repo / "paper_ventilated.md"
    katz(
        repo, "ventilate", str(manuscript),
        "--output-path", str(ventilated),
    )

    status = katz(repo, "agent", "next")
    assert status["action"]["id"] == "stage_canonical_manuscript"
    assert status["action"]["command"] == ["git", "add", "--", "paper_ventilated.md"]
    assert status["alternatives"] == []

    git(repo, "add", "--", "paper_ventilated.md")
    status = katz(repo, "agent", "next")
    assert status["action"]["id"] == "commit_canonical_manuscript"

    git(repo, "commit", "-m", "Add ventilated manuscript")
    status = katz(repo, "agent", "next")
    assert status["action"]["id"] == "register_manuscript"
    command = status["action"]["command"]
    assert command[command.index("--canonical") + 1] == "paper_ventilated.md"


def test_issue_next_returns_context_and_allowed_mutation(tmp_path: Path) -> None:
    repo, manuscript, _ = setup_repo(tmp_path)
    katz(repo, "init")
    katz(repo, "paper", "register", "--canonical", str(manuscript))
    content = manuscript.read_text(encoding="utf-8")
    quote = "The effect is large."
    byte_start = len(content[: content.index(quote)].encode("utf-8"))
    byte_end = byte_start + len(quote.encode("utf-8"))
    created = katz(
        repo, "issue", "write",
        "--title", "Effect lacks context",
        "--body", "Add a benchmark.",
        "--byte-start", str(byte_start),
        "--byte-end", str(byte_end),
    )

    packet = katz(repo, "issue", "next")
    assert packet["issue"]["id"] == created["id"]
    assert quote in packet["manuscript_context"]["numbered_text"]
    assert packet["allowed_verdicts"] == ["confirmed", "rejected", "uncertain"]
    assert packet["next_actions"][0]["command"][:4] == ["katz", "issue", "investigate", "--id"]
    assert katz(repo, "agent", "status")["phase"] == "section_mapping"

    katz(repo, "paper", "auto-chunk")
    status = katz(repo, "agent", "status")
    assert status["phase"] == "investigation"
    assert status["next_actions"][0]["command"] == ["katz", "issue", "next"]


def test_unified_ingest_detects_jobs_without_mutating(tmp_path: Path) -> None:
    repo, manuscript, _ = setup_repo(tmp_path)
    katz(repo, "init")
    katz(repo, "paper", "register", "--canonical", str(manuscript))
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "causal_language")
    jobs_path = repo / "jobs.ep"
    katz(repo, "spotter", "jobs", "--output", str(jobs_path))

    before = len(list((repo / ".katz").rglob("issue.json")))
    preview = katz(repo, "ingest", str(jobs_path))
    after = len(list((repo / ".katz").rglob("issue.json")))

    assert preview["mode"] == "preview"
    assert preview["detection"]["kind"] == "jobs_package"
    assert preview["detection"]["recommended_command"][0:2] == ["ep", "run"]
    assert before == after == 0

    status = katz(repo, "agent", "status")
    assert status["review"]["runs"]["latest"]["status"] == "packaged"
    action_ids = [action["id"] for action in status["next_actions"]]
    # KATZ-1/#15: the chain drives execution — build a ModelList, then run — rather
    # than looping on read-only inspect (which remains available as an option).
    assert action_ids[0] == "build_spotter_models"
    assert "run_jobs" in action_ids
    assert "inspect_jobs" in action_ids
    profile = status["prerequisites"]["ep"]["profile"]
    assert profile["source"] in {"ep_profiles_current", "environment_or_dotenv_fallback"}
    if profile["api_key_configured"]:
        assert "expected_parrot_login" not in action_ids
        assert "check_expected_parrot" in action_ids
    else:
        assert "expected_parrot_login" in action_ids


def test_capabilities_lists_versioned_schemas(tmp_path: Path) -> None:
    repo, _, _ = setup_repo(tmp_path)
    result = katz(repo, "capabilities")
    assert result["schema_version"] == "1.0"
    assert result["safety"]["unified_ingest_previews_by_default"] is True
    assert "agent-status.schema.json" in result["schemas"]
    schema = katz(repo, "agent", "schema", "action")
    assert schema["name"] == "action.schema.json"
    assert schema["schema"]["required"][0] == "id"


def test_version_identifies_installed_build_and_required_capabilities(tmp_path: Path) -> None:
    repo, _, _ = setup_repo(tmp_path)

    result = katz(repo, "version")

    assert result["version"] == "0.2.3"
    assert result["package_path"].endswith("/src/katz")
    assert "results_audit" in result["required_capabilities"]
    assert "fail_closed_spotter_ingestion" in result["required_capabilities"]
