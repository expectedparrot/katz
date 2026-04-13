from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


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


def katz(repo: Path, *args: str) -> dict | list:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        ["python", "-m", "katz.cli", *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(result.stdout)


def katz_fail(repo: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        ["python", "-m", "katz.cli", *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    return json.loads(result.stdout)


def setup_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a git repo with a simple canonical manuscript and return (repo, canonical, commit)."""
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


def test_init_register_and_status(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)

    # Init
    assert katz(repo, "init")["initialized"] is True

    # Register -- no paper_map needed, sentences auto-generated
    result = katz(
        repo, "paper", "register",
        "--canonical", str(canonical),
        "--source-format", "md",
        "--source-method", "test",
    )
    assert result["commit"] == commit
    assert result["sentences"] == 1

    # Status
    status = katz(repo, "paper", "status")
    assert status["commit"] == commit
    assert status["sentences"] == 1
    assert status["sections"] == 0  # no sections added yet
    assert status["valid"] is True

    # paper_map.jsonl exists and has correct structure
    jsonl_path = repo / ".katz" / "versions" / commit / "paper_map.jsonl"
    assert jsonl_path.exists()
    records = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    header = [r for r in records if r["type"] == "header"]
    sentences = [r for r in records if r["type"] == "sentence"]
    assert len(header) == 1
    assert len(sentences) == 1
    assert header[0]["commit"] == commit
    assert sentences[0]["index"] == 0
    assert sentences[0]["byte_start"] == 8  # after "# Title\n"
    assert sentences[0]["byte_end"] == 21   # "One sentence." = 13 bytes, 8+13=21

    # Add sections
    sections_payload = json.dumps([
        {"id": "intro", "title": "Introduction", "byte_start": 8, "byte_end": 21},
    ])
    add_result = katz(repo, "paper", "add-sections", "--sections", sections_payload)
    assert add_result["added"] == 1
    assert add_result["total_sections"] == 1

    # Query section
    section = katz(repo, "paper", "section", "intro")
    assert section["title"] == "Introduction"
    assert section["type"] == "section"

    # Sentences filtered by section
    filtered = katz(repo, "paper", "sentences", "--section", "intro")
    assert filtered[0]["index"] == 0

    # Resolve
    resolved = katz(repo, "paper", "resolve", "8", "21")
    assert resolved["resolved_text"] == "One sentence."
    assert resolved["section"] == "intro"

    # Find
    found = katz(repo, "paper", "find", "sentence")
    assert found[0]["resolved_text"] == "sentence"

    # Validate
    assert katz(repo, "validate")["valid"] is True

    # Issue write
    issue = katz(
        repo, "issue", "write",
        "--title", "Needs attention",
        "--byte-start", "8",
        "--byte-end", "21",
        "--body", "This sentence needs attention.",
        "--meta", '{"severity":"minor"}',
    )
    assert issue["location"]["resolved_text"] == "One sentence."

    # Issue show
    shown = katz(repo, "issue", "show", issue["id"])
    assert shown["title"] == "Needs attention"

    # Issue list filtered by section and meta
    listed = katz(repo, "issue", "list", "--section", "intro", "--meta", "severity=minor")
    assert listed[0]["id"] == issue["id"]
    assert listed[0]["location"]["section"] == "intro"

    # Validate after issue
    assert katz(repo, "validate")["valid"] is True


def test_add_sections_rejects_duplicates(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    katz(repo, "init")
    katz(repo, "paper", "register", "--canonical", str(canonical))

    sections = json.dumps([{"id": "s1", "title": "S1", "byte_start": 8, "byte_end": 21}])
    katz(repo, "paper", "add-sections", "--sections", sections)

    # Adding the same id again should fail
    err = katz_fail(repo, "paper", "add-sections", "--sections", sections)
    assert err["code"] == "validation_error"
    assert "Duplicate" in err["error"]


def test_add_sections_rejects_non_integer_byte_ranges(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    katz(repo, "init")
    katz(repo, "paper", "register", "--canonical", str(canonical))

    sections = json.dumps([{"id": "s1", "title": "S1", "byte_start": "8", "byte_end": 21}])
    err = katz_fail(repo, "paper", "add-sections", "--sections", sections)
    assert err["code"] == "validation_error"
    assert "must be integers" in err["error"]


def test_auto_chunk_uniquifies_duplicate_headings(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    canonical.write_text("# Title\nFirst sentence.\n## Results\nSecond sentence.\n## Results\nThird sentence.\n", encoding="utf-8")

    katz(repo, "init")
    katz(repo, "paper", "register", "--canonical", str(canonical))
    katz(repo, "paper", "auto-chunk")

    sections = katz(repo, "paper", "sections")
    ids = [s["id"] for s in sections]
    assert len(ids) == len(set(ids))
    assert "results" in ids
    assert "results-2" in ids


def test_guide_script_rejects_paths_outside_skill_scripts(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    err = katz_fail(repo, "guide", "script", str(repo / "README.md"))
    assert err["code"] == "not_found"

    err = katz_fail(repo, "guide", "script", "find-issues/scripts/../SKILL.md")
    assert err["code"] == "not_found"


def test_guide_skill_rejects_path_traversal(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    err = katz_fail(repo, "guide", "skill", "../README.md")
    assert err["code"] == "not_found"


def test_unicode_byte_offsets_round_trip(tmp_path: Path) -> None:
    repo, canonical, commit = setup_repo(tmp_path)
    text = "# Café\nRésumé sentence with naïve wording.\n## Methods\nEmoji 😀 sentence.\n"
    canonical.write_text(text, encoding="utf-8")

    katz(repo, "init")
    result = katz(repo, "paper", "register", "--canonical", str(canonical))
    assert result["sentences"] == 2
    katz(repo, "paper", "auto-chunk")

    target = "Emoji 😀 sentence."
    byte_start = text.encode("utf-8").index(target.encode("utf-8"))
    byte_end = byte_start + len(target.encode("utf-8"))
    resolved = katz(repo, "paper", "resolve", str(byte_start), str(byte_end))
    assert resolved["resolved_text"] == target
    assert resolved["section"] == "methods"

    found = katz(repo, "paper", "find", "😀")
    assert found[0]["resolved_text"] == "😀"


def test_sentence_segmentation_skips_non_prose(tmp_path: Path) -> None:
    """Verify that headings, code blocks, math, images, and rules are skipped."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("# Paper\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")

    canonical = tmp_path / "doc.md"
    canonical.write_text(
        "# Heading\n"
        "First sentence.\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "Second sentence.\n"
        "$$\n"
        "E = mc^2\n"
        "$$\n"
        "Third sentence.\n"
        "![figure](img.png)\n"
        "---\n"
        "Fourth sentence.\n",
        encoding="utf-8",
    )

    katz(repo, "init")
    result = katz(repo, "paper", "register", "--canonical", str(canonical))
    assert result["sentences"] == 4  # First, Second, Third, Fourth


def test_legacy_paper_map_json_still_works(tmp_path: Path) -> None:
    """Versions registered with the old paper_map.json format should still be queryable."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("# Paper\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    commit = git(repo, "rev-parse", "HEAD")

    canonical_text = "# Title\nOne sentence.\n"
    checksum = f"sha256:{hashlib.sha256(canonical_text.encode()).hexdigest()}"

    katz(repo, "init")

    # Manually create an old-format version directory
    dest = repo / ".katz" / "versions" / commit
    (dest / "paper").mkdir(parents=True)
    (dest / "issues").mkdir()
    (dest / "chunks").mkdir()
    (dest / "investigations").mkdir()
    (dest / "paper" / "manuscript.md").write_text(canonical_text, encoding="utf-8")
    (dest / "symbol_table.json").write_text("[]\n", encoding="utf-8")

    # Write old-format paper_map.json
    old_map = {
        "schema_version": 1,
        "commit": commit,
        "canonical": "paper/manuscript.md",
        "checksum": checksum,
        "source": {"format": "md", "root": "README.md", "uri": None, "method": "test", "files_collapsed": ["README.md"]},
        "sections": [{"id": "intro", "title": "Introduction", "byte_start": 8, "byte_end": 21, "line_start": 2, "line_end": 2}],
        "sentences": [{"index": 0, "byte_start": 8, "byte_end": 21, "line_start": 2, "line_end": 2}],
    }
    (dest / "paper_map.json").write_text(json.dumps(old_map), encoding="utf-8")

    version = {
        "schema_version": 1,
        "commit": commit,
        "registered_at": "2025-01-01T00:00:00Z",
        "canonical": "paper/manuscript.md",
        "paper_map": "paper_map.json",
        "checksum": checksum,
        "source": old_map["source"],
        "parent_commit": None,
    }
    (dest / "version.json").write_text(json.dumps(version, indent=2) + "\n", encoding="utf-8")
    (repo / ".katz" / "ACTIVE_VERSION").write_text(commit + "\n", encoding="utf-8")

    # Old-format queries should still work
    status = katz(repo, "paper", "status")
    assert status["valid"] is True
    assert status["sections"] == 1
    assert status["sentences"] == 1

    section = katz(repo, "paper", "section", "intro")
    assert section["title"] == "Introduction"

    resolved = katz(repo, "paper", "resolve", "8", "21")
    assert resolved["resolved_text"] == "One sentence."
    assert resolved["section"] == "intro"
