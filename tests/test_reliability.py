"""Offline invariants for preservation, execution coverage, and safe reports."""
from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from katz.cli import app
from katz.edsl_bridge import _audit_spotter_results

runner = CliRunner()


def cli(*args):
    result = runner.invoke(app, list(map(str, args)))
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)["data"]


@pytest.fixture
def review(tmp_path, monkeypatch):
    from edsl import Model, ModelList
    monkeypatch.chdir(tmp_path)
    for args in [("init",), ("config", "user.name", "Test"), ("config", "user.email", "test@example.invalid")]:
        subprocess.run(["git", *args], check=True, capture_output=True)
    (tmp_path / "a.md").write_bytes(b"# Title\r\n\r\n## Results\r\n\r\nA causal claim.\r\n")
    (tmp_path / "b.md").write_text("# Another paper\nA different claim.\n")
    subprocess.run(["git", "add", "a.md", "b.md"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Fixture"], check=True, capture_output=True)
    cli("init")
    registration = cli("paper", "register", "--canonical", "a.md")
    cli("paper", "auto-chunk")
    cli("spotter", "init-catalog")
    cli("spotter", "enable", "causal_language")
    ModelList([Model("test")]).git.save(tmp_path / "models.ep")
    return tmp_path, registration["commit"]


def package(review, *, routed=False):
    from edsl import Jobs
    root, _ = review
    path = root / "jobs.ep"
    cli("spotter", "routed-jobs" if routed else "jobs", "--section", "results",
        "--models", root / "models.ep", "--output", path)
    return path, Jobs.git.load(path)


def response(job, path, *, model=None, answer=None, scenario=None):
    from edsl import Agent, Model, Results
    from edsl.results import Result
    row = Result(agent=Agent(), model=model or Model("test"), iteration=0,
                 scenario=scenario or job.scenarios[0], answer=answer or {"spotter_result": {"found": False}})
    Results(survey=job.survey, data=[row]).git.save(path)
    return path


def test_registration_replay_preserves_map_and_rejects_conflicting_file(review):
    root, commit = review
    dest = root / ".katz" / "versions" / commit
    before = (dest / "paper_map.jsonl").read_bytes()
    assert cli("paper", "register", "--canonical", "a.md")["replayed"]
    result = runner.invoke(app, ["paper", "register", "--canonical", "b.md"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["errors"][0]["code"] == "registration_conflict"
    assert (dest / "paper_map.jsonl").read_bytes() == before
    assert (dest / "paper/manuscript.md").read_bytes() == (root / "a.md").read_bytes()


def test_crlf_packaged_section_is_exact_registered_slice(review):
    root, _ = review
    _, job = package(review)
    s = job.scenarios[0]
    assert s["manuscript_content"] == (root / "a.md").read_bytes()[s["byte_start"]:s["byte_end"]].decode()


def test_missing_model_configuration_is_incomplete(review):
    from edsl import Model, ModelList
    root, _ = review
    ModelList([Model("test"), Model("test", temperature=0.7)]).git.save(root / "models.ep")
    path, job = package(review)
    results = response(job, root / "results.ep")
    audit = _audit_spotter_results(results, path)
    assert audit["expected_answers"] == 2
    assert audit["missing_answers"] == 1
    assert not audit["complete"]


def test_unknown_models_never_prove_complete_coverage(review):
    from edsl import Jobs
    root, _ = review
    cli("spotter", "jobs", "--section", "results", "--output", "jobs.ep")
    job = Jobs.git.load(root / "jobs.ep")
    results = response(job, root / "results.ep")
    audit = _audit_spotter_results(results, root / "jobs.ep")
    assert not audit["model_coverage_known"]
    assert not audit["complete"]


def test_wrong_version_negative_cannot_be_ingested_even_with_partial(review):
    from edsl import Scenario
    root, _ = review
    path, job = package(review)
    wrong = Scenario({**dict(job.scenarios[0]), "katz_commit": "f" * 40})
    results = response(job, root / "results.ep", scenario=wrong)
    result = runner.invoke(app, ["spotter", "ingest", str(results), "--jobs", str(path), "--allow-partial"])
    assert result.exit_code != 0
    assert not list((root / ".katz").glob("versions/*/issues/*/issue.json"))


def test_routed_answers_audit_and_ingest_as_explicit_skips(review):
    root, _ = review
    path, job = package(review, routed=True)
    results = response(job, root / "results.ep", answer={"applicable_spotters": [], "spotter_detail_0": None})
    audit = _audit_spotter_results(results, path)
    assert audit["complete"]
    assert audit["intentional_skips"] == 1
    assert audit["valid_negative_findings"] == 0
    applied = cli("ingest", results, "--jobs", path, "--apply")
    assert applied["audit"]["complete"]


def test_routed_selected_detail_files_grounded_draft(review):
    root, _ = review
    path, job = package(review, routed=True)
    results = response(job, root / "results.ep", answer={"applicable_spotters": ["causal_language"],
        "spotter_detail_0": {"found": True, "title": "Causality", "quoted_text": "A causal claim.", "description": "No design supports this claim."}})
    applied = cli("ingest", results, "--jobs", path, "--apply")
    assert applied["issues_filed"] == 1
    assert cli("issue", "list")[0]["state"] == "draft"


def test_audited_full_run_advances_to_ingestion(review):
    root, _ = review
    path, job = package(review)
    results = response(job, root / "results.ep")
    assert cli("results", "audit", results, "--jobs", path)["complete"]
    assert cli("next")["action"]["id"] == "preview_ingestion"


def test_atomic_write_failure_preserves_previous_file(tmp_path, monkeypatch):
    from katz import storage
    path = tmp_path / "record.json"
    storage.write_json(path, {"original": True})
    def fail(*args):
        raise OSError("injected publish failure")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError):
        storage.write_json(path, {"original": False})
    assert json.loads(path.read_text()) == {"original": True}
    assert not list(tmp_path.glob(".katz-write-*"))


def test_report_data_cannot_escape_script_or_link_attribute():
    from katz.commands.report import _inline_markdown, _load_report_module
    module = _load_report_module()
    probe = "</script><script>window.katzProbe=1</script>"
    document = module.build_html(status={"commit": "a"*40, "valid": True, "sections": 0, "sentences": 0},
                                 sections=[], issues=[], manuscript=probe)
    assert probe not in document
    class Parser(HTMLParser):
        tags = []
        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, attrs))
    parser = Parser()
    parser.feed(_inline_markdown('[reference](https://example.invalid/"onmouseover="location=\'https://other.invalid\')'))
    assert all(key != "onmouseover" for _, attrs in parser.tags for key, _ in attrs)


def test_retry_targets_failed_model_and_merge_preserves_sources(review):
    from edsl import Jobs, Model, ModelList, Results
    from katz.edsl_bridge import build_retry_packages, merge_retry_results
    root, _ = review
    ModelList([Model("test"), Model("test", temperature=0.7)]).git.save(root / "models.ep")
    jobs_path, job = package(review)
    original = response(job, root / "original.ep")
    before = original.read_bytes()
    retry = build_retry_packages(original, jobs_path, root / "retry")
    assert len(retry["retries"]) == 1
    exported = retry["retries"][0]
    child = Jobs.git.load(exported["jobs"])
    assert child.models[0].parameters["temperature"] == 0.7
    child_results = Path(exported["command"][-1])
    response(child, child_results, model=child.models[0])
    child_before = child_results.read_bytes()
    merged = merge_retry_results([original, child_results], jobs_path, root / "merged.ep", [Path(exported["provenance"])])
    assert merged["audit"]["complete"]
    assert len(Results.git.load(root / "merged.ep")) == 2
    assert original.read_bytes() == before
    assert child_results.read_bytes() == child_before
    assert len(merged["decisions"]) == 2


def test_changed_plan_and_altered_scenario_are_rejected(review):
    from edsl import Scenario
    from katz.edsl_bridge import register_execution_plan
    from katz.errors import KatzError
    root, _ = review
    path, job = package(review)
    with pytest.raises(KatzError, match="already exists"):
        register_execution_plan(path, root / "models.ep", iterations=2)
    changed = Scenario({**dict(job.scenarios[0]), "spotter_instructions": "Different instructions"})
    results = response(job, root / "results.ep", scenario=changed)
    audit = _audit_spotter_results(results, path)
    assert not audit["complete"]
    assert audit["unexpected_scenarios"] == 1


def test_changed_model_artifact_invalidates_frozen_plan(review):
    from edsl import Model, ModelList
    root, _ = review
    path, job = package(review)
    results = response(job, root / "results.ep")
    ModelList([Model("test", temperature=0.7)]).git.save(root / "models.ep")
    audit = _audit_spotter_results(results, path)
    assert not audit["complete"]
    assert "models_checksum_mismatch" in audit["errors"]


def test_bound_report_reviews_require_exact_report_and_all_models(tmp_path):
    from edsl import Jobs, Model, ModelList
    report = tmp_path / "report.md"
    report.write_text("# Results\nThe estimate is 0.5.\n")
    models = tmp_path / "models.ep"
    ModelList([Model("test"), Model("test", temperature=0.7)]).git.save(models)
    jobs_path = tmp_path / "report.jobs.ep"
    cli("report", "review-jobs", "--report", report, "--models", models, "--output", jobs_path)
    jobs = Jobs.git.load(jobs_path)
    results = response(jobs, tmp_path / "results.ep", answer={"report_review": {"verdict": "pass", "summary": "No defects identified.", "issues": []}})
    normalized = cli("report", "review-ingest", "--report", report, "--results", results, "--jobs", jobs_path,
                     "--output", tmp_path / "normalized.json")
    assert not normalized["review"]["complete"]
    assert normalized["review"]["audit"]["missing_answers"] == 1
    report.write_text("# Different report\n")
    changed = cli("report", "review-ingest", "--report", report, "--results", results, "--jobs", jobs_path,
                  "--output", tmp_path / "changed.json")
    assert {p["reason"] for p in changed["review"]["parse_failures"]} == {"report_identity_mismatch"}


def test_report_empty_contract_is_not_a_completed_review(tmp_path):
    from edsl import Agent, Model, Results, Scenario, Survey
    from edsl.results import Result
    report = tmp_path / "report.md"
    report.write_text("# Report\n")
    path = tmp_path / "results.ep"
    Results(survey=Survey([]), data=[Result(agent=Agent(), scenario=Scenario({}), model=Model("test"),
        iteration=0, answer={"report_review": {"issues": []}})]).git.save(path)
    result = cli("report", "review-ingest", "--report", report, "--results", path, "--output", tmp_path / "review.json")
    assert not result["review"]["complete"]
    assert any(p["reason"] == "invalid_review_verdict" for p in result["review"]["parse_failures"])


def test_repeated_ingestion_does_not_make_finalization_incomplete(review):
    from katz.commands.report import _finalization_snapshot, _load_report_module
    root, commit = review
    path, job = package(review)
    answer = {"spotter_result": {"found": True, "title": "Causality", "quoted_text": "A causal claim.", "description": "Unsupported causal assertion."}}
    results = response(job, root / "results.ep", answer=answer)
    applied = cli("ingest", results, "--jobs", path, "--apply")
    cli("issue", "investigate", "--id", applied["issue_ids"][0], "--verdict", "confirmed", "--notes", "Verified.")
    replay = cli("ingest", results, "--jobs", path, "--apply")
    assert replay["duplicate_replays"] == 1
    assert replay["anchoring_failures"] == 0
    data = _load_report_module().collect_report_data(commit)
    snapshot = _finalization_snapshot(root / ".katz/versions" / commit, data)
    assert snapshot["complete"], snapshot


def test_multiple_findings_are_distinct_grounded_issues(review):
    root, _ = review
    path, job = package(review)
    findings = [{"title": title, "quoted_text": "A causal claim.", "description": title + " matters."}
                for title in ("Unsupported causality", "Unclear magnitude")]
    results = response(job, root / "results.ep", answer={"spotter_result": json.dumps({"schema_version": 2, "findings": findings})})
    audit = _audit_spotter_results(results, path)
    assert audit["complete"]
    assert audit["valid_positive_findings"] == 2
    assert cli("ingest", results, "--jobs", path, "--apply")["issues_filed"] == 2


def test_verification_preserves_proposals_without_confirming_issue(review):
    from edsl import Jobs
    root, _ = review
    path, job = package(review)
    results = response(job, root / "results.ep", answer={"spotter_result": {
        "found": True, "title": "Causality", "quoted_text": "A causal claim.", "description": "Unsupported assertion."}})
    issue_id = cli("ingest", results, "--jobs", path, "--apply")["issue_ids"][0]
    verification = root / "verify.jobs.ep"
    cli("issue", "verification-jobs", "--output", verification, "--models", root / "models.ep")
    verifier = Jobs.git.load(verification)
    evidence = response(verifier, root / "verify-results.ep", answer={"verification": {
        "verdict": "confirmed", "notes": "No causal design is described in this fixture.", "evidence_quotes": ["A causal claim."]}})
    assert cli("issue", "verification-ingest", evidence, "--jobs", verification)["proposals"] == 1
    issue = cli("issue", "show", issue_id)
    assert issue["state"] == "draft"
    assert issue["verification_proposals"][0]["proposed_verdict"] == "confirmed"
    assert cli("issue", "verification-ingest", evidence, "--jobs", verification)["proposals"] == 0


def test_comparison_package_contains_both_passages(review):
    from edsl import Jobs
    root, _ = review
    output = root / "comparison.ep"
    cli("spotter", "compare-jobs", "--left", "title", "--right", "results", "--spotters", "causal_language",
        "--models", root / "models.ep", "--output", output)
    scenario = Jobs.git.load(output).scenarios[0]
    assert "# Title" in scenario["manuscript_content"]
    assert "A causal claim." in scenario["manuscript_content"]
    assert len(scenario["katz_evidence_ranges"]) == 2


def test_comparison_requires_evidence_from_both_passages(review):
    from edsl import Jobs
    root, _ = review
    output = root / "comparison.ep"
    cli("spotter", "compare-jobs", "--left", "title", "--right", "results", "--spotters", "causal_language",
        "--models", root / "models.ep", "--output", output)
    job = Jobs.git.load(output)
    results = response(job, root / "results.ep", answer={"spotter_result": {
        "found": True, "title": "Unproven comparison", "quoted_text": "A causal claim.",
        "description": "The title contradicts the result."}})
    outcome = cli("spotter", "ingest", results, "--jobs", output)
    assert outcome["issues_filed"] == 0
    assert outcome["anchoring_failures"] == 1


def test_image_bundle_preserves_nested_references_and_omits_unrelated_files(tmp_path):
    from katz.assets import copy_referenced_images
    source = tmp_path / "source"
    (source / "figures").mkdir(parents=True)
    (source / "figures/result.png").write_bytes(b"referenced image")
    (source / "unrelated.png").write_bytes(b"unrelated")
    manuscript = source / "paper.md"
    manuscript.write_text("# Paper\n![Result][plot]\n\n[plot]: figures/result.png\n")
    destination = tmp_path / "bundle"
    manifest = copy_referenced_images(manuscript, destination)
    assert len(manifest) == 1
    assert (destination / "figures/result.png").read_bytes() == b"referenced image"
    assert not (destination / "unrelated.png").exists()


def test_benchmark_scores_clean_false_positives_and_withholds_partial_metrics():
    from katz.benchmark import score_benchmark
    truth = {"papers": [{"id": "clean", "defects": []}, {"id": "defective", "defects": ["d1", "d2"]}]}
    judgments = {"reviews": [
        {"paper_id": "clean", "complete": True, "cost_usd": 1, "investigation_seconds": 10,
         "findings": [{"id": "false-positive", "grounded": True, "defect_id": None}]},
        {"paper_id": "defective", "complete": True, "cost_usd": 1, "investigation_seconds": 20,
         "findings": [{"id": "correct", "grounded": True, "defect_id": "d1"}]}]}
    result = score_benchmark(truth, judgments)
    assert result["metrics"]["defect_recall"] == 0.5
    assert result["metrics"]["finding_precision"] == 0.5
    assert result["metrics"]["cost_per_confirmed_defect_usd"] == 2
    judgments["reviews"][0]["complete"] = False
    assert score_benchmark(truth, judgments)["metrics"] is None
