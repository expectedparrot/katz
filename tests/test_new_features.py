"""Tests for new katz features: auto-chunk, eval primitive, catalog collections,
issue show section field, spotter field on issues, and file-based catalogs."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return result.stdout.strip()


def katz(repo: Path, *args: str) -> dict | list:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        ["python", "-m", "katz.cli", *args],
        cwd=repo, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return json.loads(result.stdout)


def katz_fail(repo: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        ["python", "-m", "katz.cli", *args],
        cwd=repo, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode != 0
    return json.loads(result.stdout)


RICH_MANUSCRIPT = """\
# My Paper Title

Authors et al.

## Abstract

This paper studies something important. We find significant results.

## 1. Introduction

Understanding the world is hard. This paper contributes by doing X.

We present three findings. First, A is true. Second, B holds. Third, C follows.

## 2. Methods

We use a randomized experiment with 1000 participants.

### 2.1 Data

Data come from a national survey conducted in 2024.

### 2.2 Estimation

We estimate a linear model with fixed effects.

## 3. Results

Table 1 shows the main results. The effect is 0.5 standard deviations.

## 4. Discussion

Our findings suggest that X matters for Y.

## 5. Conclusion

We have shown that A, B, and C hold. Future work should explore D.

## References

Smith (2020). A paper. Journal of Things.
"""


def setup_rich_repo(tmp_path: Path) -> tuple[Path, str]:
    """Create a repo with a multi-section manuscript, registered and initialized."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    canonical = tmp_path / "manuscript.md"
    canonical.write_text(RICH_MANUSCRIPT, encoding="utf-8")
    (repo / "README.md").write_text("# Paper\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    commit = git(repo, "rev-parse", "HEAD")

    katz(repo, "init")
    katz(repo, "paper", "register", "--canonical", str(canonical))
    return repo, commit


# ---------------------------------------------------------------------------
# Auto-chunk
# ---------------------------------------------------------------------------


def test_auto_chunk_detects_headings(tmp_path: Path) -> None:
    repo, commit = setup_rich_repo(tmp_path)

    result = katz(repo, "paper", "auto-chunk")
    assert result["added"] >= 8  # at least: title, abstract, intro, methods, data, estimation, results, discussion, conclusion, references

    status = katz(repo, "paper", "status")
    assert status["sections"] == result["added"]
    assert status["valid"] is True


def test_auto_chunk_rejects_if_sections_exist(tmp_path: Path) -> None:
    repo, commit = setup_rich_repo(tmp_path)

    katz(repo, "paper", "auto-chunk")
    err = katz_fail(repo, "paper", "auto-chunk")
    assert "already has" in err["error"]


def test_auto_chunk_sections_tile_manuscript(tmp_path: Path) -> None:
    """Verify sections cover the full manuscript with no gaps."""
    repo, commit = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")

    status = katz(repo, "paper", "status")
    # Read all section records
    jsonl_path = repo / ".katz" / "versions" / commit / "paper_map.jsonl"
    records = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    sections = sorted(
        [r for r in records if r["type"] == "section"],
        key=lambda s: s["byte_start"],
    )
    assert len(sections) >= 2

    # First section starts at 0
    assert sections[0]["byte_start"] == 0
    # Each section starts where the previous one ends
    for i in range(1, len(sections)):
        assert sections[i]["byte_start"] == sections[i - 1]["byte_end"], (
            f"Gap between sections {sections[i-1]['id']} and {sections[i]['id']}"
        )


# ---------------------------------------------------------------------------
# Spotter catalog (file-based collections)
# ---------------------------------------------------------------------------


def test_spotter_init_catalog_from_files(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    result = katz(repo, "spotter", "init-catalog")
    assert len(result["added"]) == 13
    assert result["preset"] == "default"

    # All spotters should be in catalog
    catalog = katz(repo, "spotter", "catalog")
    assert len(catalog) == 13
    names = {s["name"] for s in catalog}
    assert "overclaiming" in names
    assert "logical_gaps" in names


def test_spotter_init_catalog_skips_existing(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "spotter", "init-catalog")

    result = katz(repo, "spotter", "init-catalog")
    assert len(result["added"]) == 0
    assert len(result["skipped"]) == 13


def test_spotter_init_catalog_bad_preset(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    err = katz_fail(repo, "spotter", "init-catalog", "--preset", "nonexistent")
    assert err["code"] == "validation_error"
    assert "default" in err["details"]["available"]


def test_spotter_enable_and_list(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "spotter", "init-catalog")

    katz(repo, "spotter", "enable", "overclaiming")
    katz(repo, "spotter", "enable", "logical_gaps")

    listed = katz(repo, "spotter", "list")
    names = {s["name"] for s in listed}
    assert names == {"overclaiming", "logical_gaps"}


def test_spotter_catalog_show(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "spotter", "init-catalog")

    shown = katz(repo, "spotter", "catalog-show", "overclaiming")
    assert shown["name"] == "overclaiming"
    assert shown["scope"] in ("section", "holistic")
    assert shown["title"] is not None
    assert shown["description"] is not None


# ---------------------------------------------------------------------------
# Eval primitive
# ---------------------------------------------------------------------------


def test_eval_init_catalog_from_files(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    result = katz(repo, "eval", "init-catalog")
    assert len(result["added"]) >= 10  # sanity: at least 10 criteria in default collection
    assert result["preset"] == "default"

    catalog = katz(repo, "eval", "catalog")
    assert len(catalog) == len(result["added"])  # catalog matches what was added


def test_eval_catalog_filter_by_category(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "eval", "init-catalog")

    intro_flow = katz(repo, "eval", "catalog", "--category", "introduction-flow")
    assert len(intro_flow) == 8
    assert all(e["category"] == "introduction-flow" for e in intro_flow)


def test_eval_enable_show_remove(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "eval", "init-catalog")

    # Enable
    katz(repo, "eval", "enable", "abstract_conveys_findings")
    listed = katz(repo, "eval", "list")
    assert len(listed) == 1
    assert listed[0]["name"] == "abstract_conveys_findings"

    # Show
    shown = katz(repo, "eval", "show", "abstract_conveys_findings")
    assert shown["title"] == "Abstract Conveys Findings"
    assert shown["category"] == "title-and-abstract"
    assert "content" in shown

    # Remove
    katz(repo, "eval", "remove", "abstract_conveys_findings")
    listed = katz(repo, "eval", "list")
    assert len(listed) == 0


def test_eval_add_custom(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    katz(repo, "eval", "add",
         "--name", "policy-impact",
         "--question", "Does the paper discuss policy implications?",
         "--category", "contribution",
         "--scope", "discussion")

    listed = katz(repo, "eval", "list")
    assert len(listed) == 1
    assert listed[0]["name"] == "policy_impact"
    assert listed[0]["category"] == "contribution"
    assert listed[0]["scope"] == "discussion"


def test_eval_respond_and_results(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "eval", "init-catalog")
    katz(repo, "eval", "enable", "abstract_conveys_findings")

    response_text = "The abstract clearly states findings A, B, and C."
    katz(repo, "eval", "respond",
         "--name", "abstract_conveys_findings",
         "--text", response_text)

    results = katz(repo, "eval", "results")
    assert len(results) == 1
    assert results[0]["criterion"] == "abstract_conveys_findings"
    assert results[0]["response"] == response_text
    assert results[0]["category"] == "title-and-abstract"
    assert "timestamp" in results[0]


def test_eval_respond_overwrites(tmp_path: Path) -> None:
    """Responding to the same criterion again should overwrite the previous response."""
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "eval", "init-catalog")
    katz(repo, "eval", "enable", "abstract_conveys_findings")

    katz(repo, "eval", "respond", "--name", "abstract_conveys_findings", "--text", "First response")
    katz(repo, "eval", "respond", "--name", "abstract_conveys_findings", "--text", "Updated response")

    results = katz(repo, "eval", "results")
    assert len(results) == 1
    assert results[0]["response"] == "Updated response"


def test_eval_respond_requires_enabled(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    err = katz_fail(repo, "eval", "respond",
                    "--name", "nonexistent",
                    "--text", "some text")
    assert err["code"] == "not_found"


def test_eval_results_filter_by_category(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "eval", "init-catalog")
    katz(repo, "eval", "enable", "abstract_conveys_findings")
    katz(repo, "eval", "enable", "design_matches_claims")

    katz(repo, "eval", "respond", "--name", "abstract_conveys_findings", "--text", "Good abstract.")
    katz(repo, "eval", "respond", "--name", "design_matches_claims", "--text", "Good design.")

    title_results = katz(repo, "eval", "results", "--category", "title-and-abstract")
    assert len(title_results) == 1
    assert title_results[0]["criterion"] == "abstract_conveys_findings"

    methods_results = katz(repo, "eval", "results", "--category", "methods")
    assert len(methods_results) == 1
    assert methods_results[0]["criterion"] == "design_matches_claims"


# ---------------------------------------------------------------------------
# Issue show includes section
# ---------------------------------------------------------------------------


def test_issue_show_includes_section(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")

    # Find byte range for something in the "introduction" section
    # The intro starts after "## 1. Introduction\n"
    manuscript = (repo / ".katz" / "versions").iterdir().__next__() / "paper" / "manuscript.md"
    text = manuscript.read_text()
    intro_text = "Understanding the world is hard."
    byte_start = text.encode("utf-8").index(intro_text.encode("utf-8"))
    byte_end = byte_start + len(intro_text.encode("utf-8"))

    issue = katz(repo, "issue", "write",
                 "--title", "Test issue",
                 "--byte-start", str(byte_start),
                 "--byte-end", str(byte_end),
                 "--body", "Test body")

    shown = katz(repo, "issue", "show", issue["id"])
    assert "section" in shown["location"]
    assert shown["location"]["section"] is not None
    # Should be in some section containing "introduction"
    assert "introduction" in shown["location"]["section"].lower() or "1" in shown["location"]["section"]


# ---------------------------------------------------------------------------
# Issue with spotter field
# ---------------------------------------------------------------------------


def test_issue_write_with_spotter(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "overclaiming")

    issue = katz(repo, "issue", "write",
                 "--title", "Overclaimed result",
                 "--byte-start", "0",
                 "--byte-end", "10",
                 "--body", "This claim is too strong.",
                 "--spotter", "overclaiming")

    assert issue["spotter"] == "overclaiming"
    shown = katz(repo, "issue", "show", issue["id"])
    assert shown["spotter"] == "overclaiming"


def test_issue_write_rejects_unregistered_spotter(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    err = katz_fail(repo, "issue", "write",
                    "--title", "Test",
                    "--byte-start", "0",
                    "--byte-end", "10",
                    "--body", "Test",
                    "--spotter", "nonexistent")
    assert err["code"] == "not_found"


# ---------------------------------------------------------------------------
# Issue investigation and state updates
# ---------------------------------------------------------------------------


def test_issue_investigate_and_update(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    issue = katz(repo, "issue", "write",
                 "--title", "Test issue",
                 "--byte-start", "0",
                 "--byte-end", "10",
                 "--body", "Test body")

    # Investigate
    inv = katz(repo, "issue", "investigate",
               "--id", issue["id"],
               "--verdict", "confirmed",
               "--notes", "This is a real problem.")
    assert inv["verdict"] == "confirmed"

    # Update state
    katz(repo, "issue", "update",
         "--id", issue["id"],
         "--state", "confirmed",
         "--reason", "Investigation confirmed")

    shown = katz(repo, "issue", "show", issue["id"])
    assert shown["state"] == "confirmed"
    assert len(shown["investigations"]) == 1
    assert shown["investigations"][0]["verdict"] == "confirmed"
    assert shown["investigations"][0]["notes"] == "This is a real problem."


# ---------------------------------------------------------------------------
# Issue artifacts
# ---------------------------------------------------------------------------


def test_issue_write_with_artifacts(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    issue = katz(repo, "issue", "write",
                 "--title", "SE mismatch",
                 "--byte-start", "0",
                 "--byte-end", "10",
                 "--body", "Code uses HC1 but paper says clustered",
                 "--artifacts", "analysis/table2.R,data/survey.csv")

    assert issue["artifacts"] == ["analysis/table2.R", "data/survey.csv"]

    shown = katz(repo, "issue", "show", issue["id"])
    assert shown["artifacts"] == ["analysis/table2.R", "data/survey.csv"]


def test_issue_write_without_artifacts(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    issue = katz(repo, "issue", "write",
                 "--title", "No artifacts",
                 "--byte-start", "0",
                 "--byte-end", "10",
                 "--body", "Plain issue")

    assert issue["artifacts"] == []


def test_issue_merge_unions_artifacts(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    i1 = katz(repo, "issue", "write", "--title", "A",
              "--byte-start", "0", "--byte-end", "10", "--body", "A",
              "--artifacts", "script.R,data.csv")
    i2 = katz(repo, "issue", "write", "--title", "B",
              "--byte-start", "0", "--byte-end", "10", "--body", "B",
              "--artifacts", "data.csv,notebook.ipynb")

    parent = katz(repo, "issue", "merge", "--ids", f"{i1['id']},{i2['id']}")

    # Union with dedup, preserving order
    assert parent["artifacts"] == ["script.R", "data.csv", "notebook.ipynb"]


# ---------------------------------------------------------------------------
# Issue merge
# ---------------------------------------------------------------------------


def test_issue_merge_basic(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    i1 = katz(repo, "issue", "write", "--title", "Bias claim v1",
              "--byte-start", "0", "--byte-end", "10", "--body", "From model A")
    i2 = katz(repo, "issue", "write", "--title", "Bias claim v2",
              "--byte-start", "0", "--byte-end", "10", "--body", "From model B")
    i3 = katz(repo, "issue", "write", "--title", "Bias claim v3",
              "--byte-start", "0", "--byte-end", "10", "--body", "From model C")

    parent = katz(repo, "issue", "merge",
                  "--ids", f"{i1['id']},{i2['id']},{i3['id']}",
                  "--title", "Bias claim (merged)")

    assert parent["title"] == "Bias claim (merged)"
    assert parent["state"] == "draft"
    assert parent["meta"]["merged_from"] == [i1["id"], i2["id"], i3["id"]]

    # Children should be wontfix
    for child_id in [i1["id"], i2["id"], i3["id"]]:
        shown = katz(repo, "issue", "show", child_id)
        assert shown["state"] == "wontfix"

    # Parent should show up in draft list, children should not
    drafts = katz(repo, "issue", "list", "--state", "draft")
    draft_ids = {d["id"] for d in drafts}
    assert parent["id"] in draft_ids
    assert i1["id"] not in draft_ids


def test_issue_merge_union_byte_range(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    i1 = katz(repo, "issue", "write", "--title", "Issue A",
              "--byte-start", "10", "--byte-end", "20", "--body", "A")
    i2 = katz(repo, "issue", "write", "--title", "Issue B",
              "--byte-start", "50", "--byte-end", "80", "--body", "B")

    parent = katz(repo, "issue", "merge", "--ids", f"{i1['id']},{i2['id']}")

    # Parent byte range should be the union: 10-80
    assert parent["location"]["byte_start"] == 10
    assert parent["location"]["byte_end"] == 80


def test_issue_merge_default_title_from_first(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    i1 = katz(repo, "issue", "write", "--title", "First issue title",
              "--byte-start", "0", "--byte-end", "10", "--body", "A")
    i2 = katz(repo, "issue", "write", "--title", "Second",
              "--byte-start", "0", "--byte-end", "10", "--body", "B")

    parent = katz(repo, "issue", "merge", "--ids", f"{i1['id']},{i2['id']}")
    assert parent["title"] == "First issue title"


def test_issue_merge_rejects_single_id(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    i1 = katz(repo, "issue", "write", "--title", "Solo",
              "--byte-start", "0", "--byte-end", "10", "--body", "A")
    err = katz_fail(repo, "issue", "merge", "--ids", i1["id"])
    assert err["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Issue suggestions
# ---------------------------------------------------------------------------


def test_issue_suggest(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    issue = katz(repo, "issue", "write",
                 "--title", "Bias claim",
                 "--byte-start", "0",
                 "--byte-end", "10",
                 "--body", "Unsupported bias claim")

    sug = katz(repo, "issue", "suggest",
               "--id", issue["id"],
               "--text", "Replace 'less prone to biases' with 'less noisy'.")
    assert sug["text"] == "Replace 'less prone to biases' with 'less noisy'."
    assert "timestamp" in sug

    shown = katz(repo, "issue", "show", issue["id"])
    assert len(shown["suggestions"]) == 1
    assert shown["suggestions"][0]["text"] == sug["text"]


def test_issue_suggest_append_only(tmp_path: Path) -> None:
    """Multiple suggestions append, not overwrite."""
    repo, _ = setup_rich_repo(tmp_path)

    issue = katz(repo, "issue", "write",
                 "--title", "Test",
                 "--byte-start", "0",
                 "--byte-end", "10",
                 "--body", "Test")

    katz(repo, "issue", "suggest", "--id", issue["id"], "--text", "First suggestion")
    katz(repo, "issue", "suggest", "--id", issue["id"], "--text", "Second suggestion")

    shown = katz(repo, "issue", "show", issue["id"])
    assert len(shown["suggestions"]) == 2


def test_issue_suggest_requires_existing_issue(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    err = katz_fail(repo, "issue", "suggest", "--id", "nonexistent", "--text", "test")
    assert err["code"] == "not_found"


# ---------------------------------------------------------------------------
# Eval suggestions
# ---------------------------------------------------------------------------


def test_eval_respond_with_suggestion(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "eval", "init-catalog")
    katz(repo, "eval", "enable", "abstract_conveys_findings")

    katz(repo, "eval", "respond",
         "--name", "abstract_conveys_findings",
         "--text", "Good abstract.",
         "--grade", "B+",
         "--suggestion", "Add a sentence about the decomposition model.")

    results = katz(repo, "eval", "results")
    assert len(results) == 1
    assert results[0]["suggestion"] == "Add a sentence about the decomposition model."
    assert results[0]["grade"] == "B+"


def test_eval_respond_suggestion_optional(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "eval", "init-catalog")
    katz(repo, "eval", "enable", "abstract_conveys_findings")

    katz(repo, "eval", "respond",
         "--name", "abstract_conveys_findings",
         "--text", "Good abstract.",
         "--grade", "A")

    results = katz(repo, "eval", "results")
    assert results[0]["suggestion"] is None


# ---------------------------------------------------------------------------
# Collection file structure
# ---------------------------------------------------------------------------


def test_catalog_collection_files_exist() -> None:
    """Verify the catalog directory has the expected structure."""
    catalog = Path(__file__).parents[1] / "src" / "katz" / "catalog"

    # Spotter collection
    spotter_default = catalog / "spotters" / "collections" / "default.json"
    assert spotter_default.exists()
    spotter_names = json.loads(spotter_default.read_text())
    assert len(spotter_names) == 13
    for name in spotter_names:
        assert (catalog / "spotters" / f"{name}.md").exists(), f"Missing spotter file: {name}.md"

    # Eval collection
    eval_default = catalog / "evals" / "collections" / "default.json"
    assert eval_default.exists()
    eval_names = json.loads(eval_default.read_text())
    assert len(eval_names) >= 10  # sanity: at least 10 evals in default
    for name in eval_names:
        assert (catalog / "evals" / f"{name}.md").exists(), f"Missing eval file: {name}.md"
