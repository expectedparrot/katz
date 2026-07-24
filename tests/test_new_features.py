"""Tests for new katz features: auto-chunk, eval primitive, catalog collections,
issue show section field, spotter field on issues, and file-based catalogs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import base64
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
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["command"] == list(args)
    return payload["data"]


def katz_fail(repo: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        ["python", "-m", "katz.cli", *args],
        cwd=repo, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == list(args)
    return payload["error"]


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


def test_register_pdf_returns_prepare_action(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    pdf = repo / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    error = katz_fail(repo, "paper", "register", "--canonical", str(pdf))

    assert error["code"] == "binary_manuscript"
    assert error["details"]["next_action"][0:3] == ["katz", "paper", "prepare"]


def test_register_latex_returns_prepare_action(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    latex = repo / "paper.tex"
    latex.write_text("\\documentclass{article}\\begin{document}Paper\\end{document}")

    error = katz_fail(repo, "paper", "register", "--canonical", str(latex))

    assert error["code"] == "source_manuscript_requires_preparation"
    assert error["details"]["next_action"][0:3] == ["katz", "paper", "prepare"]


def test_latex_prepare_inlines_input_table_and_preserves_it(tmp_path: Path) -> None:
    if shutil.which("pandoc") is None:
        return
    repo, _ = setup_rich_repo(tmp_path)
    latex_dir = repo / "latex"
    tables_dir = latex_dir / "tables"
    tables_dir.mkdir(parents=True)
    (tables_dir / "results.tex").write_text(
        "\\begin{table}\n"
        "\\caption{Main results}\n"
        "\\begin{tabular}{lr}\n"
        "Outcome & Estimate \\\\\n"
        "Trust & 0.42 \\\\\n"
        "\\end{tabular}\n"
        "\\end{table}\n",
        encoding="utf-8",
    )
    (latex_dir / "refs.bib").write_text(
        "@article{doe2020,\n"
        "  author = {Doe, Jane},\n"
        "  title = {Prior Work},\n"
        "  journal = {Journal of Tests},\n"
        "  year = {2020}\n"
        "}\n",
        encoding="utf-8",
    )
    main = latex_dir / "main.tex"
    main.write_text(
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage{natbib}\n"
        "\\title{Battery Evaluation}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        "\\begin{abstract}\n"
        "We evaluate batteries following \\citep{doe2020}.\n"
        "\\end{abstract}\n"
        "\\section{Results}\n"
        "\\resizebox{\\textwidth}{!}{\\input{tables/results}}\n"
        "\\bibliography{refs}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    output = repo / "paper" / "manuscript.md"

    prepared = katz(repo, "paper", "prepare", str(main), "--output", str(output))

    assert prepared["source_type"] == "latex"
    assert prepared["dependency_count"] == 2
    assert prepared["source_inventory"]["table_environments"] == 1
    assert prepared["converted_table_artifacts"] >= 1
    assert prepared["normalization"]["resizebox_wrappers_stripped"] == 1
    assert prepared["normalization"]["title_restored"] is True
    assert prepared["normalization"]["abstract_restored"] is True
    markdown = output.read_text(encoding="utf-8")
    assert "# Battery Evaluation" in markdown
    assert "# Abstract" in markdown
    assert "Doe" in markdown
    assert "Trust" in markdown


def test_latex_prepare_fails_for_missing_input(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    main = repo / "main.tex"
    main.write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\input{tables/missing}\n\\end{document}\n",
        encoding="utf-8",
    )

    error = katz_fail(
        repo, "paper", "prepare", str(main),
        "--output", str(repo / "paper.md"),
    )

    assert error["code"] == "missing_source_dependency"


def test_latex_prepare_allows_existing_graphic_outside_repo(tmp_path: Path) -> None:
    if shutil.which("pandoc") is None:
        return
    repo, _ = setup_rich_repo(tmp_path)
    latex_dir = repo / "latex"
    latex_dir.mkdir()
    external_dir = tmp_path / "output" / "figures"
    external_dir.mkdir(parents=True)
    graphic = external_dir / "result.png"
    graphic.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    main = latex_dir / "main.tex"
    main.write_text(
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "\\begin{figure}\n"
        "\\includegraphics{../../output/figures/result.png}\n"
        "\\caption{External result figure}\n"
        "\\end{figure}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    output = repo / "paper" / "manuscript.md"

    prepared = katz(repo, "paper", "prepare", str(main), "--output", str(output))

    external = prepared["external_assets"]
    assert external[0]["code"] == "external_graphic"
    assert external[0]["path"] == str(graphic)
    assert prepared["lossy_conversion_allowed"] is False
    assert output.is_file()


def test_latex_prepare_missing_graphic_requires_allow_lossy(tmp_path: Path) -> None:
    if shutil.which("pandoc") is None:
        return
    repo, _ = setup_rich_repo(tmp_path)
    main = repo / "main.tex"
    main.write_text(
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "\\includegraphics{../output/figures/missing.png}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    output = repo / "paper.md"

    error = katz_fail(repo, "paper", "prepare", str(main), "--output", str(output))

    assert error["code"] == "lossy_conversion"
    assert error["details"]["external_assets"][0]["code"] == "missing_graphic"
    assert not output.exists()

    prepared = katz(
        repo, "paper", "prepare", str(main), "--output", str(output), "--allow-lossy",
    )
    assert prepared["external_assets"][0]["code"] == "missing_graphic"
    assert prepared["lossy_conversion_allowed"] is True


def test_auto_chunk_rejects_if_sections_exist(tmp_path: Path) -> None:
    repo, commit = setup_rich_repo(tmp_path)

    katz(repo, "paper", "auto-chunk")
    err = katz_fail(repo, "paper", "auto-chunk")
    assert "already has" in err["message"]


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


def test_spotter_jobs_builds_edsl_package(tmp_path: Path) -> None:
    from edsl import Jobs

    repo, commit = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "causal_language")
    output = repo / "review-jobs.ep"

    result = katz(repo, "spotter", "jobs", "--output", str(output))

    assert result["object_type"] == "Jobs"
    assert result["commit"] == commit
    assert result["spotters"] == ["causal_language"]
    assert result["scenario_count"] == result["section_scenarios"]
    assert result["scenario_count"] >= 8
    assert result["holistic_scenarios"] == 0
    jobs = Jobs.git.load(output)
    assert jobs.survey.question_names == ["spotter_result"]
    first = dict(jobs.scenarios[0])
    assert first["katz_commit"] == commit
    assert first["spotter_name"] == "causal_language"
    assert first["spotter_scope"] == "section"
    assert first["manuscript_content"]
    assert "Section map:" in first["paper_context"]
    assert result["answer_contract"]["pilot_required_before_large_run"] is False
    # KATZ-2: run guidance must carry an adequate output-token budget so free-text
    # verdicts are not truncated into unparseable answers.
    assert result["answer_contract"]["recommended_max_tokens"] >= 3000
    assert "max_tokens" in result["answer_contract"]["token_budget_note"]
    assert "model_list" in result["next"]


def test_agent_next_offers_run_action_after_packaging(tmp_path: Path) -> None:
    """KATZ-1: once jobs are packaged, `agent next` must surface a run action
    (not loop on read-only inspect), with inspect available as an alternative."""
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "causal_language")
    katz(repo, "spotter", "jobs", "--output", str(repo / "jobs.ep"))

    nxt = katz(repo, "agent", "next")
    assert nxt["action"]["id"] == "run_jobs"
    assert nxt["action"]["command"][0] == "ep"
    assert "run" in nxt["action"]["command"]
    assert nxt["action"]["requires_user_approval"] is True
    assert nxt["action"]["requires_network"] is True
    assert any(alt["id"] == "inspect_jobs" for alt in nxt["alternatives"])


def test_paper_review_jobs_embeds_manuscript_and_figure_attachments(tmp_path: Path) -> None:
    from edsl import FileStore, Jobs

    repo, commit = setup_rich_repo(tmp_path)
    figure = tmp_path / "results.png"
    figure.write_bytes(b"\x89PNG\r\n\x1a\nexample")
    # Re-register at a new commit so the newly added sibling asset is copied.
    (repo / "README.md").write_text("# Paper\n\nFigure added.\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Add figure")
    commit = git(repo, "rev-parse", "HEAD")
    katz(repo, "paper", "register", "--canonical", str(tmp_path / "manuscript.md"))

    output = repo / "whole-paper.jobs.ep"
    result = katz(repo, "paper", "review-jobs", "--output", str(output))

    assert result["object_type"] == "Jobs"
    assert result["commit"] == commit
    assert result["question"] == "economic_review"
    assert result["scenario_count"] == 1
    assert [item["kind"] for item in result["attachments"]] == ["manuscript", "figure"]

    jobs = Jobs.git.load(output)
    scenario = jobs.scenarios[0]
    assert isinstance(scenario["manuscript"], FileStore)
    assert isinstance(scenario["figure_1"], FileStore)
    question_text = jobs.survey.questions[0].question_text
    assert "{{ manuscript }}" in question_text
    assert "{{ figure_1 }}" in question_text
    assert "economics referee" in question_text


def test_human_journal_review_add_and_jobs(tmp_path: Path) -> None:
    from edsl import FileStore, Jobs

    repo, commit = setup_rich_repo(tmp_path)
    review = tmp_path / "referee-report.md"
    review.write_text(
        "# Referee report\n\nPlease explain why the effect is so large.\n",
        encoding="utf-8",
    )

    added = katz(
        repo, "review", "add", str(review),
        "--reviewer", "Reviewer 2", "--venue", "Journal of Things", "--round", "R1",
    )
    review_id = added["id"]
    assert added["commit"] == commit
    assert added["reviewer"] == "Reviewer 2"
    assert katz(repo, "review", "list")[0]["id"] == review_id

    output = repo / "journal-review.jobs.ep"
    built = katz(repo, "review", "jobs", review_id, "--output", str(output))
    assert built["review_id"] == review_id
    assert built["question"] == "journal_review_issues"
    jobs = Jobs.git.load(output)
    scenario = jobs.scenarios[0]
    assert isinstance(scenario["manuscript"], FileStore)
    assert isinstance(scenario["journal_review"], FileStore)
    assert jobs.survey.question_names == ["journal_review_issues"]


def test_human_journal_review_ingest_is_grounded_and_idempotent(tmp_path: Path) -> None:
    from edsl import Agent, Model, Results, Scenario, Survey
    from edsl.results import Result

    repo, commit = setup_rich_repo(tmp_path)
    review = tmp_path / "review.txt"
    review.write_text("The reported effect needs more explanation.", encoding="utf-8")
    review_id = katz(repo, "review", "add", str(review))["id"]
    quoted = "The effect is 0.5 standard deviations."
    parsed = [{
        "title": "Explain the effect magnitude",
        "body": "The magnitude is difficult to interpret without context.",
        "quoted_text": quoted,
        "reviewer_comment": "The reported effect needs more explanation.",
        "severity": "major",
        "suggested_response": "Add a substantive benchmark.",
    }]
    result = Result(
        agent=Agent(),
        scenario=Scenario({"katz_commit": commit, "review_id": review_id}),
        model=Model("test"),
        iteration=0,
        answer={"journal_review_issues": json.dumps(parsed)},
    )
    results_path = repo / "journal-review-results.ep"
    Results(survey=Survey([]), data=[result]).git.save(results_path)

    first = katz(repo, "review", "ingest", str(results_path))
    second = katz(repo, "review", "ingest", str(results_path))
    assert first["candidates"] == 1
    assert first["issues_filed"] == 1
    assert second["issues_filed"] == 0
    assert second["skipped"] == 1
    issue = katz(repo, "issue", "show", first["issue_ids"][0])
    assert issue["location"]["resolved_text"] == quoted
    assert issue["meta"]["source"] == "human_journal_review"
    assert issue["meta"]["review_id"] == review_id
    assert "Reviewer comment:" in issue["body"]


def test_spotter_jobs_can_filter_section_and_spotter(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "causal_language")
    katz(repo, "spotter", "enable", "overclaiming")
    output = repo / "one-job.ep"

    result = katz(
        repo,
        "spotter",
        "jobs",
        "--output",
        str(output),
        "--section",
        "abstract",
        "--spotters",
        "causal_language",
    )

    assert result["spotters"] == ["causal_language"]
    assert result["scenario_count"] == 1
    assert result["section_scenarios"] == 1


def test_spotter_jobs_can_build_five_scenario_pilot(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "--recommended")
    output = repo / "pilot.jobs.ep"

    result = katz(repo, "spotter", "jobs", "--pilot", "5", "--output", str(output))

    assert result["pilot"] is True
    assert result["scenario_count"] == 5
    assert result["estimated_prompt_characters"] > 0


def test_spotter_ingest_files_anchored_issue_idempotently(tmp_path: Path) -> None:
    from edsl import Agent, Model, Results, Scenario, Survey
    from edsl.results import Result

    repo, commit = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "causal_language")
    manuscript = (repo / ".katz" / "versions" / commit / "paper" / "manuscript.md").read_text()
    quoted = "This paper studies something important."
    byte_start = manuscript.encode().find(quoted.encode())
    result = Result(
        agent=Agent(),
        scenario=Scenario({
            "katz_commit": commit,
            "spotter_name": "causal_language",
            "spotter_scope": "section",
            "section_id": "abstract",
            "byte_start": byte_start,
            "byte_end": byte_start + len(quoted.encode()),
        }),
        model=Model("test"),
        iteration=0,
        answer={
            "spotter_result": {
                "found": "true",
                "title": "Causal claim",
                "quoted_text": quoted,
                "description": "The wording needs qualification.",
            }
        },
    )
    null_result = Result(
        agent=Agent(),
        scenario=Scenario({
            "katz_commit": commit,
            "spotter_name": "causal_language",
            "spotter_scope": "section",
            "section_id": "methods",
            "byte_start": 0,
            "byte_end": len(manuscript.encode()),
        }),
        model=Model("test"),
        iteration=0,
        answer={
            "spotter_result": {
                "found": "false",
                "title": "",
                "quoted_text": "",
                "description": "",
            }
        },
    )
    results_path = repo / "results.ep"
    Results(survey=Survey([]), data=[result, null_result]).git.save(results_path)

    first = katz(repo, "spotter", "ingest", str(results_path), "--allow-partial")
    second = katz(repo, "spotter", "ingest", str(results_path), "--allow-partial")

    assert first["issues_found"] == 1
    assert first["issues_filed"] == 1
    assert first["result_count"] == 2
    assert second["issues_filed"] == 0
    assert second["skipped"] == 1
    issue = katz(repo, "issue", "show", first["issue_ids"][0])
    assert issue["spotter"] == "causal_language"
    assert issue["location"]["resolved_text"] == quoted
    assert issue["meta"]["edsl_model"] == "test"


def test_spotter_audit_rejects_null_and_accepts_explicit_negative(tmp_path: Path) -> None:
    from edsl import Agent, Jobs, Model, Results, Scenario, Survey
    from edsl.results import Result

    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "causal_language")
    jobs_path = repo / "audit.jobs.ep"
    katz(
        repo, "spotter", "jobs",
        "--output", str(jobs_path),
        "--section", "abstract",
        "--spotters", "causal_language",
    )
    scenario = Scenario(dict(Jobs.git.load(jobs_path).scenarios[0]))

    null_path = repo / "audit-results.ep"
    null_result = Result(
        agent=Agent(), scenario=scenario, model=Model("test"), iteration=0,
        answer={"spotter_result": None},
    )
    Results(survey=Survey([]), data=[null_result]).git.save(null_path)

    audit = katz(repo, "results", "audit", str(null_path), "--jobs", str(jobs_path))
    assert audit["complete"] is False
    assert audit["coverage"] == 0
    assert audit["null_answers"] == 1
    error = katz_fail(repo, "spotter", "ingest", str(null_path), "--jobs", str(jobs_path))
    assert error["code"] == "incomplete_results"

    negative_path = repo / "negative-results.ep"
    negative_result = Result(
        agent=Agent(), scenario=scenario, model=Model("test"), iteration=0,
        answer={"spotter_result": {
            "found": False,
            "title": "",
            "quoted_text": "",
            "description": "",
        }},
    )
    Results(survey=Survey([]), data=[negative_result]).git.save(negative_path)
    complete = katz(repo, "results", "audit", str(negative_path), "--jobs", str(jobs_path))
    assert complete["complete"] is True
    assert complete["valid_negative_findings"] == 1
    ingested = katz(
        repo, "spotter", "ingest", str(negative_path), "--jobs", str(jobs_path),
    )
    assert ingested["run_status"] == "ingested"
    assert ingested["issues_found"] == 0


def test_spotter_freetext_verdict_audits_and_ingests(tmp_path: Path) -> None:
    """A free-text answer that reasons then emits a fenced JSON verdict must audit
    as a valid finding and ingest as an anchored issue — never drop to null."""
    from edsl import Agent, Jobs, Model, Results, Scenario, Survey
    from edsl.results import Result

    repo, manuscript = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")
    katz(repo, "spotter", "enable", "causal_language")
    jobs_path = repo / "ft.jobs.ep"
    katz(
        repo, "spotter", "jobs",
        "--output", str(jobs_path),
        "--section", "abstract",
        "--spotters", "causal_language",
    )
    scenario = Scenario(dict(Jobs.git.load(jobs_path).scenarios[0]))
    # The scenario carries the exact section region; quote a verbatim sentence
    # from it so the quotation locates cleanly.
    quoted = "This paper studies something important."
    assert quoted in scenario["manuscript_content"]

    freetext = (
        "# Causal Language Analysis\n\n"
        "I weighed several candidate concerns about the wording in this section.\n"
        "The strongest is an unhedged causal claim that the design does not support.\n\n"
        "```json\n"
        + json.dumps({
            "found": True,
            "title": "Unhedged causal claim",
            "quoted_text": quoted,
            "description": "Causal phrasing exceeds what the correlational design supports.",
        })
        + "\n```\n"
    )
    result = Result(
        agent=Agent(), scenario=scenario, model=Model("test"), iteration=0,
        answer={"spotter_result": freetext},
    )
    path = repo / "ft-results.ep"
    Results(survey=Survey([]), data=[result]).git.save(path)

    audit = katz(repo, "results", "audit", str(path), "--jobs", str(jobs_path))
    assert audit["complete"] is True
    assert audit["valid_positive_findings"] == 1
    assert audit["null_answers"] == 0

    ingested = katz(repo, "spotter", "ingest", str(path), "--jobs", str(jobs_path))
    assert ingested["run_status"] == "ingested"
    assert ingested["issues_found"] == 1
    assert ingested["issues_filed"] == 1
    issue = katz(repo, "issue", "show", ingested["issue_ids"][0])
    assert issue["title"] == "Unhedged causal claim"

    # Pure prose with no verdict block must be flagged, never scored as negative.
    prose = Result(
        agent=Agent(), scenario=scenario, model=Model("test"), iteration=0,
        answer={"spotter_result": "This section reads fine; I see no problem."},
    )
    prose_path = repo / "prose-results.ep"
    Results(survey=Survey([]), data=[prose]).git.save(prose_path)
    prose_audit = katz(repo, "results", "audit", str(prose_path), "--jobs", str(jobs_path))
    assert prose_audit["complete"] is False
    assert prose_audit["valid_negative_findings"] == 0
    assert prose_audit["invalid_answers"] == 1


def test_locate_quoted_text_allows_line_break_normalization() -> None:
    from katz.cli import _locate_quoted_text

    region = "TMLE employs a substitution “targeting”\nstep to estimate an effect."
    quote = "TMLE employs a substitution “targeting” step to estimate an effect."

    located = _locate_quoted_text(region, quote)

    assert located is not None
    start, end = located
    assert region[start:end] == region


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

    enabled = katz(repo, "spotter", "enable", "overclaiming")
    assert enabled["already_enabled"] is False
    enabled_again = katz(repo, "spotter", "enable", "overclaiming")
    assert enabled_again["already_enabled"] is True
    katz(repo, "spotter", "enable", "logical_gaps")

    listed = katz(repo, "spotter", "list")
    names = {s["name"] for s in listed}
    assert names == {"overclaiming", "logical_gaps"}


def test_spotter_enable_recommended_activates_default_set(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "spotter", "init-catalog")

    enabled = katz(repo, "spotter", "enable", "--recommended")

    assert enabled["selection"] == "recommended"
    assert enabled["enabled_count"] == 13
    assert len(katz(repo, "spotter", "list")) == 13


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


def test_issue_commands_accept_unambiguous_id_prefixes(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    issue = katz(repo, "issue", "write",
                 "--title", "Prefix issue",
                 "--byte-start", "0",
                 "--byte-end", "10",
                 "--body", "Test body")
    prefix = issue["id"][:8]

    shown = katz(repo, "issue", "show", prefix)
    assert shown["id"] == issue["id"]

    katz(repo, "issue", "update", "--id", prefix, "--state", "open")
    katz(repo, "issue", "investigate", "--id", prefix, "--verdict", "uncertain", "--notes", "Needs more checking.")
    katz(repo, "issue", "suggest", "--id", prefix, "--text", "Clarify this sentence.")

    shown = katz(repo, "issue", "show", prefix)
    assert shown["state"] == "open"
    assert shown["investigations"][0]["verdict"] == "uncertain"
    assert shown["suggestions"][0]["text"] == "Clarify this sentence."


def test_uncertain_investigation_moves_draft_to_open(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    issue = katz(
        repo, "issue", "write",
        "--title", "Needs investigation",
        "--byte-start", "0",
        "--byte-end", "10",
        "--body", "Evidence is ambiguous.",
    )

    investigated = katz(
        repo, "issue", "investigate",
        "--id", issue["id"],
        "--verdict", "uncertain",
        "--notes", "The available artifact cannot resolve this.",
    )

    assert investigated["state_updated"] == "open"
    assert katz(repo, "issue", "show", issue["id"])["state"] == "open"
    assert katz(repo, "issue", "next")["issue"] is None


def test_issue_clusters_suggests_merge_for_overlapping_findings(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    first = katz(
        repo, "issue", "write",
        "--title", "Null result interpreted as no effect",
        "--byte-start", "0", "--byte-end", "20",
        "--body", "The null estimate is described as proof of no effect.",
    )
    second = katz(
        repo, "issue", "write",
        "--title", "No-effect claim overstates null result",
        "--byte-start", "10", "--byte-end", "30",
        "--body", "The null result does not establish absence of an effect.",
    )

    result = katz(repo, "issue", "clusters")

    assert result["cluster_count"] == 1
    assert set(result["clusters"][0]["issue_ids"]) == {first["id"], second["id"]}
    assert result["clusters"][0]["suggested_command"][0:4] == ["katz", "issue", "merge", "--ids"]


def test_issue_show_accepts_batch_ids(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    i1 = katz(repo, "issue", "write", "--title", "A",
              "--byte-start", "0", "--byte-end", "10", "--body", "A")
    i2 = katz(repo, "issue", "write", "--title", "B",
              "--byte-start", "10", "--byte-end", "20", "--body", "B")

    shown = katz(repo, "issue", "show", "--ids", f"{i1['id'][:8]},{i2['id'][:8]}")
    assert [issue["id"] for issue in shown] == [i1["id"], i2["id"]]


def test_issue_list_stdout_is_clean_json_for_pipelines(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "issue", "write", "--title", "Pipeline",
         "--byte-start", "0", "--byte-end", "10", "--body", "A")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        ["python", "-m", "katz.cli", "issue", "list", "--state", "draft"],
        cwd=repo, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"][0]["title"] == "Pipeline"


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


def test_issue_merge_accepts_id_prefixes(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)

    i1 = katz(repo, "issue", "write", "--title", "First",
              "--byte-start", "0", "--byte-end", "10", "--body", "A")
    i2 = katz(repo, "issue", "write", "--title", "Second",
              "--byte-start", "10", "--byte-end", "20", "--body", "B")

    parent = katz(repo, "issue", "merge", "--ids", f"{i1['id'][:8]},{i2['id'][:8]}")
    assert parent["meta"]["merged_from"] == [i1["id"], i2["id"]]


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
# Report command
# ---------------------------------------------------------------------------


def test_report_generate_writes_html(tmp_path: Path) -> None:
    repo, _ = setup_rich_repo(tmp_path)
    katz(repo, "paper", "auto-chunk")
    katz(repo, "issue", "write",
         "--title", "Report issue",
         "--byte-start", "0",
         "--byte-end", "10",
         "--body", "This should appear in the report.")

    output = tmp_path / "review.html"
    result = katz(repo, "report", "generate", "--output", str(output))

    assert result["generated"] is True
    assert result["issues"] == 1
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    assert "Review Report" in html
    assert "Report issue" in html
    assert "Expected Parrot" in html
    assert "Paper explorer" in html
    assert 'src="logo.png"' in html
    assert (tmp_path / "logo.png").read_bytes() == (
        Path(__file__).parents[1] / "src" / "katz" / "assets" / "logo.png"
    ).read_bytes()


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
