import json

from typer.testing import CliRunner

from katz.cli import app
from katz.report_review import answer_diagnostic, coverage_summary


def test_coverage_counts_only_usable_rows_and_preserves_missing_panel_members():
    summary = coverage_summary(
        [{"index": 0}, {"index": 1}],
        [{"index": 1, "reviewer": "malformed", "reason": "invalid_issue_schema"}],
        {"expected_answers": 3, "complete": False, "failures": []}, 2,
    )
    assert summary["review_status"] == "partial"
    assert summary["usable_review_count"] == 1
    assert summary["requested_review_count"] == 3
    assert "1 of 3" in summary["suggested_user_disclosure"]
    assert summary["failed_reviewers"] == ["malformed"]


def test_unknown_coverage_is_not_invented_from_returned_rows():
    summary = coverage_summary([{"index": 0}], [], {"expected_answers": None, "complete": False}, 1)
    assert summary["requested_review_count"] is None
    assert summary["review_status"] == "partial"
    assert "unknown" in summary["suggested_user_disclosure"]


def test_identity_invalid_review_is_not_usable():
    summary = coverage_summary([{"index": 0}], [{"index": 0, "reason": "report_identity_mismatch"}],
                               {"expected_answers": 1, "complete": False}, 1)
    assert summary["review_status"] == "failed"
    assert summary["usable_review_count"] == 0


def test_all_empty_output_is_failed_and_original_results_are_preserved(tmp_path):
    from edsl import Agent, Model, Results, Scenario, Survey
    from edsl.results import Result
    from katz.storage import sha256_file
    report = tmp_path / "report.md"
    report.write_text("# Report\nA small exploratory sample.\n")
    results = tmp_path / "results.ep"
    Results(survey=Survey([]), data=[
        Result(agent=Agent(), scenario=Scenario({"report_sha256": sha256_file(report)}),
               model=Model("test"), iteration=i, answer={"report_review": answer})
        for i, answer in enumerate(["", '{"issues":', None])
    ]).git.save(results)
    original = results.read_bytes()
    output = tmp_path / "review.json"
    invocation = CliRunner().invoke(app, ["report", "review-ingest", "--report", str(report),
        "--results", str(results), "--output", str(output)])
    assert invocation.exit_code == 0, invocation.output
    data = json.loads(invocation.stdout)["data"]
    assert data["review_status"] == "failed"
    assert data["usable_review_count"] == 0
    assert data["parse_failure_count"] == 3
    assert "failed" in data["next_action"]
    assert json.loads(output.read_text())["suggested_user_disclosure"] == data["suggested_user_disclosure"]
    assert results.read_bytes() == original


def test_diagnostics_do_not_echo_private_answers_or_infer_truncation():
    assert answer_diagnostic("private report text") == {"answer_state": "unparseable", "answer_characters": 19}
    assert answer_diagnostic("  ")["answer_state"] == "empty"
    assert answer_diagnostic('{"issues":', {"finish_reason": "length", "truncated": True})["answer_state"] == "truncated"
