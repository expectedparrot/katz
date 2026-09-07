"""EDSL boundary: prompts, package save/verify, results audit, and answer coercion."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import KatzError
from .storage import _latest_packaged_run


SPOTTER_QUESTION_TEXT = """\
You are reviewing {{ review_target }} from an academic manuscript.

Issue spotter:
{{ spotter_instructions }}

Paper context:
{{ paper_context }}

Manuscript content:
{{ manuscript_content }}

Apply the spotter carefully. Return found=false when there is no genuine,
substantive issue. When found=true, quote the exact shortest passage that
demonstrates the issue and explain why it matters. Before claiming something is
missing, use the paper context to distinguish “missing from this section” from
“missing from the paper.” Do not invent text.
"""


# Appended to the spotter prompt so the model reasons freely first and never has
# its deliberation discarded by strict schema validation, then commits to a
# machine-readable verdict Katz can parse. Free-text reasoning avoids the
# false-negative failure mode where a forced JSON binary suppresses a genuine
# concern the model was still weighing.
SPOTTER_VERDICT_SUFFIX = """

Work in two steps.
First, reason in prose: enumerate candidate issues and, for each, decide whether
it is genuine and substantive or whether it is addressed, acknowledged, or out of
scope elsewhere in the paper.
Then, on the final lines and with nothing after it, output your verdict as a
single fenced JSON object:

```json
{"found": true, "title": "short title", "quoted_text": "exact manuscript quotation", "description": "evidence-backed explanation"}
```

Use found=false with empty strings for title, quoted_text, and description when
there is no genuine, substantive issue. Emit exactly one such JSON object.
"""


# Free-text spotter answers reason in prose before the JSON verdict, so
# issue-finding answers are long. Runs need enough output budget to reach the
# verdict; the provider default (often ~1000) truncates them into
# unparseable_answer rows. A ModelList is how EDSL sets this at run time.
# Full-paper reasoning often consumes several thousand hidden reasoning tokens
# before emitting the fenced verdict. 16K is the safe default for GPT-5-class
# high-reasoning runs; callers can still request a smaller budget explicitly.
SPOTTER_RECOMMENDED_MAX_TOKENS = 16000


ROUTED_SPOTTER_QUESTION_TEXT = """\
You are triaging {{ review_target }} from an academic manuscript before focused
issue review.

Available issue spotters:
{{ spotter_catalog }}

Paper context:
{{ paper_context }}

Manuscript content:
{{ manuscript_content }}

Select every spotter for which this section contains a concrete signal worth a
detailed pass. Be inclusive when there is specific evidence, but do not select a
spotter merely because its topic could apply to any paper. Cross-section checks may
be selected when this passage contains one side of a possible inconsistency.
"""


ECONOMICS_REVIEW_QUESTION_TEXT = """\
Act as a demanding but constructive economics referee. Read the complete manuscript
attachment and inspect every attached figure before writing the report.

Attachments:
- Complete manuscript: {{ manuscript }}
{{ figure_attachment_list }}

Evaluate the paper on the dimensions that apply: contribution and relation to the
literature; economic question, mechanism, and interpretation; research design and
identification; estimation and statistical inference; data and measurement; results,
robustness, and heterogeneity; welfare or policy claims; reproducibility; exposition;
and whether each table or figure supports the argument. For a methods or software
paper, adapt these standards rather than pretending it contains an empirical design.

Return a self-contained Markdown referee report with:
1. Summary and contribution
2. Overall assessment and recommendation
3. Major concerns
4. Minor concerns
5. Questions for the authors
6. Figure and table comments

Write each actionable concern under a heading in exactly this form:
### [major] Short title
or:
### [minor] Short title

Under each concern include these labeled fields:
- Evidence: an exact, shortest quotation from the manuscript, or a figure filename
- Location: the manuscript section or figure filename
- Reason: why this matters for the paper's economic argument or evidentiary standard
- Suggested response: a concrete way the authors could address it

Do not invent quotations, results, citations, tables, or figure contents. If a concern
cannot be tied to exact evidence in an attachment, present it as a question rather than
an issue candidate. Distinguish limitations from fatal flaws and acknowledge material
strengths.
"""


def _edsl_imports() -> tuple[Any, Any, Any, Any]:
    try:
        from edsl import Jobs, Scenario, ScenarioList
        from edsl.questions import QuestionDict
    except ImportError as exc:
        raise KatzError(
            "EDSL is required to create or ingest .ep objects",
            "dependency_error",
            {"install": "python -m pip install edsl"},
        ) from exc
    return Jobs, Scenario, ScenarioList, QuestionDict


def _expected_results_path(output: Path) -> Path:
    name = output.name
    if name.endswith(".jobs.ep"):
        return output.with_name(f"{name[:-8]}-results.ep")
    return output.with_name("results.ep")


def _save_and_verify_ep(value: Any, output: Path) -> dict[str, Any]:
    """Save a native EDSL object and prove it can be loaded before success."""
    saved = value.git.save(output)
    type(value).git.load(saved["path"])
    return saved


JOURNAL_REVIEW_PARSE_PROMPT = """You are converting a human-written journal review into
candidate Katz issues. Read both attached files: the referee review and the registered
manuscript. Preserve the reviewer's meaning and do not add criticisms of your own.

Return ONLY a JSON array. Each element must have these string fields:
- title: a short descriptive title
- body: the reviewer's concern, with enough context to investigate it
- quoted_text: the shortest exact quotation from the manuscript that grounds the concern
- reviewer_comment: the relevant exact quotation from the referee review
- severity: major, minor, question, or unspecified
- suggested_response: the reviewer's requested change, or an empty string

Include only actionable comments that can be grounded in an exact manuscript quotation.
Do not turn praise, editorial logistics, confidential editor-only remarks, or a general
recommendation into manuscript issues. Split distinct concerns, but do not split one
concern merely because it spans several sentences. If no grounded actionable comments
exist, return [].

Registered manuscript: {{ manuscript }}
Human referee review: {{ journal_review }}
"""


def _result_value(result: Any, group: str, key: str) -> Any:
    try:
        value = result[group]
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)
    except (KeyError, TypeError):
        return None


def _answer_is_found(value: Any) -> bool:
    """Interpret structured EDSL booleans without treating the string 'false' as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return value == 1


def _scenario_key(value: Any) -> str:
    """Return a stable identity for one Katz review scenario."""
    scenario = value if isinstance(value, dict) else dict(value)
    identity = {
        "katz_commit": scenario.get("katz_commit"),
        "spotter_name": scenario.get("spotter_name"),
        "spotter_scope": scenario.get("spotter_scope"),
        "section_id": scenario.get("section_id"),
        "byte_start": scenario.get("byte_start"),
        "byte_end": scenario.get("byte_end"),
    }
    return json.dumps(identity, sort_keys=True, default=str)


def _coerce_spotter_answer(answer: Any) -> dict[str, Any] | None:
    """Normalise a spotter answer into a {found,title,quoted_text,description} dict.

    Accepts either a structured dict (legacy QuestionDict path) or a free-text
    string that reasons in prose and ends with a JSON verdict object (current
    QuestionFreeText path). Returns None only when no verdict can be recovered,
    so free-text reasoning is never silently scored as a negative finding.
    """
    if isinstance(answer, dict):
        return answer if "found" in answer or "findings" in answer else None
    if not isinstance(answer, str):
        return None
    decoder = json.JSONDecoder()
    candidates = []
    for match in re.finditer(r"\{", answer):
        try:
            value, length = decoder.raw_decode(answer[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("found" in value or "findings" in value):
            candidates.append((match.start() + length, -match.start(), value))
    return max(candidates, key=lambda item: item[:2])[2] if candidates else None


def _spotter_answer_error(answer: Any) -> str | None:
    if answer is None:
        return "null_answer"
    if isinstance(answer, str):
        coerced = _coerce_spotter_answer(answer)
        if coerced is None:
            return "unparseable_answer" if answer.strip() else "null_answer"
        answer = coerced
    if not isinstance(answer, dict):
        return "answer_not_object"
    if "findings" in answer:
        if answer.get("schema_version") != 2 or not isinstance(answer["findings"], list):
            return "invalid_findings_contract"
        for finding in answer["findings"]:
            if not isinstance(finding, dict) or "findings" in finding:
                return "invalid_finding"
            error = _spotter_answer_error({**finding, "found": True})
            if error:
                return error
        return None
    found = answer.get("found")
    valid_found = (
        isinstance(found, bool)
        or (isinstance(found, int) and not isinstance(found, bool) and found in {0, 1})
        or (isinstance(found, str) and found.strip().lower() in {"true", "false"})
    )
    if not valid_found:
        return "invalid_found"
    if _answer_is_found(found):
        missing = [
            key for key in ("title", "quoted_text", "description")
            if not isinstance(answer.get(key), str) or not answer.get(key, "").strip()
        ]
        if missing:
            return "missing_positive_fields:" + ",".join(missing)
    return None


def _plain(value: Any) -> Any:
    """Canonical EDSL metadata without serialization/version decoration."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()
                if k not in {"edsl_version", "edsl_class_name"}}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _fingerprint(value: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _model_spec(value: Any) -> dict[str, Any]:
    value = _plain(value)
    return {key: value.get(key) for key in ("model", "inference_service", "parameters")}


def _observation_id(scenario: Any, model: Any, agent: Any, iteration: int) -> str:
    return _fingerprint({"scenario": _plain(scenario), "model": _model_spec(model),
                         "agent": _plain(agent), "iteration": iteration})


def _plan_path(jobs_path: Path) -> Path:
    return jobs_path.with_name(jobs_path.name + ".katz.json")


def register_execution_plan(jobs_path: Path, models_path: Path | None = None,
                            iterations: int = 1) -> dict[str, Any]:
    """Freeze requested models and repetitions without executing a Jobs object."""
    from edsl import Jobs, ModelList
    from .storage import read_json, atomic_write, sha256_file
    jobs = Jobs.git.load(jobs_path)
    selected = list(ModelList.git.load(models_path)) if models_path else list(jobs.models)
    if iterations < 1 or not selected:
        raise KatzError("An execution plan requires explicit models and positive iterations",
                        "execution_plan_required")
    model_specs = [_model_spec(m) for m in selected]
    if len({_fingerprint(m) for m in model_specs}) != len(model_specs):
        raise KatzError("Duplicate model configurations; use --iterations for repetitions", "validation_error")
    plan = {"contract": "katz.execution-plan.v1", "jobs_sha256": sha256_file(jobs_path),
            "models": model_specs, "iterations": iterations,
            "models_path": str(models_path.resolve()) if models_path else None,
            "models_sha256": sha256_file(models_path) if models_path else None,
            "survey_sha256": _fingerprint(jobs.survey)}
    plan["run_id"] = _fingerprint({k: plan[k] for k in ("jobs_sha256", "models", "iterations")})
    path = _plan_path(jobs_path)
    if path.exists():
        existing = read_json(path)
        if existing != plan:
            raise KatzError("Execution plan already exists; export new Jobs for a changed plan",
                            "execution_plan_conflict", {"path": str(path)})
        return existing
    try:
        atomic_write(path, (json.dumps(plan, indent=2) + "\n").encode("utf-8"), exclusive=True)
    except FileExistsError:
        if read_json(path) != plan:
            raise KatzError("Execution plan already exists; export new Jobs for a changed plan",
                            "execution_plan_conflict", {"path": str(path)})
    return plan


def _execution_audit(results: Any, jobs_path: Path | None, models_path: Path | None = None,
                     iterations: int | None = None, expected_commit: str | None = None) -> dict[str, Any]:
    from edsl import Agent, Jobs, ModelList
    from .storage import read_json, sha256_file
    jobs = Jobs.git.load(jobs_path) if jobs_path else None
    plan = read_json(_plan_path(jobs_path)) if jobs_path and _plan_path(jobs_path).exists() else None
    errors = []
    if plan and plan["jobs_sha256"] != sha256_file(jobs_path):
        errors.append("jobs_checksum_mismatch")
    models = [_model_spec(m) for m in ModelList.git.load(models_path)] if models_path else (
        plan["models"] if plan else [_model_spec(m) for m in jobs.models] if jobs else [])
    if plan and models != plan["models"]:
        errors.append("model_plan_mismatch")
    preserved_models_path = models_path
    if plan and plan.get("models_path"):
        planned_path = Path(plan["models_path"])
        if planned_path.is_file() and sha256_file(planned_path) == plan["models_sha256"]:
            preserved_models_path = preserved_models_path or planned_path
        else:
            errors.append("models_checksum_mismatch")
    repetitions = iterations if iterations is not None else plan.get("iterations", 1) if plan else 1
    if plan and repetitions != plan["iterations"]:
        errors.append("iteration_plan_mismatch")
    expected = {}
    if jobs is not None and models:
        agents = list(jobs.agents) or [Agent()]
        for scenario in jobs.scenarios:
            for model in models:
                for agent in agents:
                    for iteration in range(repetitions):
                        identity = _observation_id(scenario, model, agent, iteration)
                        if identity in expected:
                            errors.append("duplicate_expected_observation")
                        expected[identity] = {"scenario": _plain(scenario), "model": model,
                                              "agent": _plain(agent), "iteration": iteration}
    seen = {}
    row_errors = {}
    scenario_keys = {_fingerprint(s) for s in jobs.scenarios} if jobs else set()
    for index, result in enumerate(results):
        identity = _observation_id(result["scenario"], result["model"], result["agent"], result["iteration"])
        seen.setdefault(identity, []).append(index)
        if expected and identity not in expected:
            row_errors[index] = "unexpected_observation"
        elif jobs and _fingerprint(result["scenario"]) not in scenario_keys:
            row_errors[index] = "unexpected_scenario"
        if expected_commit and result["scenario"].get("katz_commit") != expected_commit:
            row_errors[index] = "manuscript_version_mismatch"
    for indices in seen.values():
        if len(indices) > 1:
            for index in indices:
                row_errors[index] = "duplicate_observation"
    if jobs and expected_commit and any(s.get("katz_commit") != expected_commit for s in jobs.scenarios):
        errors.append("manuscript_version_mismatch")
    if jobs and getattr(results, "survey", None) and list(results.survey.question_names):
        if _fingerprint(results.survey) != _fingerprint(jobs.survey):
            errors.append("survey_mismatch")
    missing = sorted(set(expected) - set(seen))
    return {"jobs": jobs, "plan": plan, "expected": expected, "seen": seen,
            "row_errors": row_errors, "errors": sorted(set(errors)), "missing": missing,
            "models_known": bool(models), "models": models, "models_path": preserved_models_path,
            "iterations": repetitions,
            "run_id": plan["run_id"] if plan else _fingerprint({
                "jobs_sha256": sha256_file(jobs_path) if jobs_path else None,
                "models": models, "iterations": repetitions})}


def _audit_spotter_results(results_path: Path, jobs_path: Path | None = None,
                           models_path: Path | None = None, *, iterations: int | None = None,
                           expected_commit: str | None = None) -> dict[str, Any]:
    """Audit exact requested observations. Unknown model coverage never means complete."""
    from edsl import Results
    from .storage import sha256_file
    results = Results.git.load(results_path)
    execution = _execution_audit(results, jobs_path, models_path, iterations, expected_commit)
    jobs = execution["jobs"]
    rows = []
    invalid = nulls = exceptions = positives = negatives = skipped = 0
    valid_interviews = 0
    failed_ids = set(execution["missing"])
    for index, result in enumerate(results):
        scenario = _plain(result["scenario"])
        identity = _observation_id(result["scenario"], result["model"], result["agent"], result["iteration"])
        answers = result["answer"]
        routed = jobs is not None and "applicable_spotters" in jobs.survey.question_names
        details = scenario.get("katz_detail_questions") or {}
        if routed and not details:
            route = jobs.survey.questions[0]
            details = {name: f"spotter_detail_{i}" for i, name in enumerate(route.question_options)}
        questions = {"spotter_result": scenario.get("spotter_name")} if not routed else {v: k for k, v in details.items()}
        route_answer = answers.get("applicable_spotters") if routed else None
        route_error = None
        if routed and (not isinstance(route_answer, list) or any(s not in details for s in route_answer)
                       or len(set(route_answer)) != len(route_answer)):
            route_error = "invalid_route"
        if routed and _result_value(result, "exceptions", "applicable_spotters"):
            route_error = "model_exception"
        interview_valid = True
        for question, spotter in questions.items():
            intentional_skip = routed and route_error is None and spotter not in route_answer
            answer = answers.get(question)
            error = execution["row_errors"].get(index) or route_error
            if error is None and not intentional_skip:
                error = _spotter_answer_error(answer)
            if _result_value(result, "exceptions", question):
                error = "model_exception"
            if intentional_skip and answer is not None:
                error = "unexpected_route_answer"
            verdict = _coerce_spotter_answer(answer) if not intentional_skip else None
            if error:
                interview_valid = False
                failed_ids.add(identity)
                if error == "null_answer": nulls += 1
                elif error == "model_exception": exceptions += 1
                else: invalid += 1
            elif intentional_skip:
                skipped += 1
            elif verdict and (verdict.get("findings") or _answer_is_found(verdict.get("found"))):
                positives += len(verdict["findings"]) if "findings" in verdict else 1
            else:
                negatives += 1
            findings = ([{**item, "found": True} for item in verdict["findings"]]
                        if verdict and "findings" in verdict and not error else [])
            if verdict is not None and "findings" in verdict and not verdict["findings"] and not error:
                verdict = {"found": False}
            for finding_index, normalized in enumerate(findings or [verdict]):
                rows.append({"index": index, "observation_id": identity, "question": question,
                             "finding_index": finding_index,
                             "scenario": {**scenario, "spotter_name": spotter},
                             "model": _model_spec(result["model"]), "valid": error is None,
                             "found": _answer_is_found(normalized.get("found")) if normalized and not error else None,
                             "error": error, "skipped": intentional_skip,
                             "answer": normalized if normalized is not None else answer})
        valid_interviews += int(interview_valid)
    expected_count = len(execution["expected"]) if execution["models_known"] and jobs else None
    complete = bool(expected_count and valid_interviews == expected_count and not failed_ids
                    and not execution["row_errors"] and not execution["errors"]
                    and len(results) == expected_count)
    models_path = execution["models_path"]
    expected_scenarios = {_fingerprint(s) for s in jobs.scenarios} if jobs else set()
    returned_scenarios = {_fingerprint(r["scenario"]) for r in results}
    return {"contract": "katz.spotter-results-audit.v2", "run_id": execution["run_id"],
            "results_path": str(results_path.resolve()), "results_sha256": sha256_file(results_path),
            "jobs_path": str(jobs_path.resolve()) if jobs_path else None,
            "jobs_sha256": sha256_file(jobs_path) if jobs_path else None,
            "models_path": str(models_path.resolve()) if models_path else None,
            "models_sha256": sha256_file(models_path) if models_path else None,
            "expected_scenarios": len(jobs.scenarios) if jobs else None,
            "expected_answers": expected_count, "returned_rows": len(results),
            "valid_answers": valid_interviews, "valid_positive_findings": positives,
            "valid_negative_findings": negatives, "null_answers": nulls,
            "invalid_answers": invalid, "model_exceptions": exceptions,
            "intentional_skips": skipped, "model_coverage_known": execution["models_known"],
            "missing_answers": len(execution["missing"]) if expected_count is not None else None,
            "missing_scenarios": len(expected_scenarios - returned_scenarios) if jobs else None,
            "unexpected_scenarios": len(returned_scenarios - expected_scenarios) if jobs else None,
            "duplicate_rows": sum(len(v)-1 for v in execution["seen"].values()),
            "coverage": round(min(1.0, valid_interviews / expected_count), 6) if expected_count else 0.0,
            "complete": complete, "models": sorted({m["model"] for m in execution["models"]}),
            "model_configurations": execution["models"],
            "errors": execution["errors"], "failed_observations": sorted(failed_ids),
            "failure_examples": [{k:v for k,v in row.items() if k not in {"answer", "model", "scenario"}}
                                 for row in rows if row["error"]][:10], "_rows": rows}

def _resolve_audit_jobs(dest: Path, results_path: Path, jobs_path: Path | None) -> Path | None:
    from .storage import sha256_file
    run = _latest_packaged_run(dest, results_path)
    candidate = jobs_path or (Path(str(run.get("jobs_path"))) if run and run.get("jobs_path") else None)
    if candidate is not None and candidate.is_file():
        if run and run.get("jobs_sha256") and str(candidate.resolve()) == run.get("jobs_path"):
            if sha256_file(candidate) != run["jobs_sha256"]:
                raise KatzError("Originating Jobs changed after registration", "checksum_mismatch")
        return candidate
    return None


def _group_positive_findings(positives: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group positive spotter findings whose anchors overlap for the same spotter.

    Findings from different models that flag the same passage under the same
    spotter collapse into one group, so ingestion can file one issue with a
    cross-model agreement score instead of N near-duplicates.
    """
    groups: list[list[dict[str, Any]]] = []
    ordered = sorted(
        positives,
        key=lambda finding: (
            str(finding["spotter"]),
            finding["byte_start"],
            finding["byte_end"],
            str(finding["model"]),
        ),
    )
    for finding in ordered:
        target = None
        for group in groups:
            if group[0]["spotter"] != finding["spotter"]:
                continue
            if group[0]["answer"].get("title", "").casefold() != finding["answer"].get("title", "").casefold():
                continue
            overlaps = any(
                member["byte_start"] < finding["byte_end"]
                and finding["byte_start"] < member["byte_end"]
                for member in group
            )
            if overlaps:
                target = group
                break
        if target is None:
            groups.append([finding])
        else:
            target.append(finding)
    return groups


def build_retry_packages(results_path: Path, jobs_path: Path, output_dir: Path,
                         models_path: Path | None = None, *, single_output: Path | None = None) -> dict[str, Any]:
    """Export one exact failed observation per native Jobs package, with parent mapping."""
    from edsl import Agent, Jobs, Results, Scenario
    from edsl.language_models import LanguageModel
    from .storage import sha256_file, write_json
    if output_dir.exists():
        raise KatzError("Retry directory already exists", "validation_error", {"path": str(output_dir)})
    results = Results.git.load(results_path)
    execution = _execution_audit(results, jobs_path, models_path)
    audit = _audit_spotter_results(results_path, jobs_path, models_path)
    if not execution["expected"] or execution["errors"]:
        raise KatzError("An intact execution plan is required for retries", "execution_plan_required")
    failed = audit["failed_observations"]
    if not failed:
        raise KatzError("No failed or missing observations", "validation_error")
    if any(key not in execution["expected"] for key in failed):
        raise KatzError("Unexpected observations require investigation before retry", "validation_error")
    if single_output is not None and len(failed) != 1:
        raise KatzError("Multiple failed observations require katz results retry --output-dir", "validation_error")
    output_dir.mkdir(parents=True)
    exports = []
    for identity in failed:
        item = execution["expected"][identity]
        scenario = next(s for s in execution["jobs"].scenarios if _fingerprint(s) == _fingerprint(item["scenario"]))
        job = Jobs(survey=execution["jobs"].survey, scenarios=[scenario],
                   models=[LanguageModel.from_dict(item["model"])], agents=[Agent.from_dict(item["agent"])])
        path = single_output or output_dir / f"retry-{identity[:16]}.jobs.ep"
        _save_and_verify_ep(job, path)
        register_execution_plan(path)
        metadata = {"contract": "katz.retry.v1", "parent_jobs_path": str(jobs_path.resolve()),
                    "parent_jobs_sha256": sha256_file(jobs_path), "source_results_path": str(results_path.resolve()),
                    "source_results_sha256": sha256_file(results_path), "jobs_path": str(path.resolve()),
                    "jobs_sha256": sha256_file(path), "parent_observation_id": identity,
                    "child_observation_id": _observation_id(scenario, item["model"], item["agent"], 0),
                    "parent_iteration": item["iteration"],
                    "expected_results_path": str(_expected_results_path(path).resolve())}
        metadata_path = path.with_name(path.name + ".retry.json")
        write_json(metadata_path, metadata)
        exports.append({"jobs": str(path), "provenance": str(metadata_path),
                        "observation_id": identity,
                        "command": ["ep", "run", str(path), "--output", str(_expected_results_path(path))]})
    manifest = {"contract": "katz.retry-set.v1", "parent_run_id": execution["run_id"], "retries": exports,
                "inference": "external", "expected_model_calls": len(exports)}
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def merge_retry_results(inputs: list[Path], jobs_path: Path, output: Path,
                        retry_plans: list[Path], models_path: Path | None = None) -> dict[str, Any]:
    """Create a new derived Results object with a decision for every observation."""
    from edsl import Jobs, Results
    from edsl.results import Result
    from .storage import read_json, sha256_file, write_json
    if output.exists():
        raise KatzError("Merge output already exists", "validation_error")
    parent = Jobs.git.load(jobs_path)
    mappings = {}
    for path in retry_plans:
        plan = read_json(path)
        if plan.get("contract") != "katz.retry.v1" or plan["parent_jobs_sha256"] != sha256_file(jobs_path):
            raise KatzError("Retry provenance does not match originating Jobs", "validation_error")
        if sha256_file(Path(plan["jobs_path"])) != plan["jobs_sha256"]:
            raise KatzError("Retry Jobs changed", "checksum_mismatch")
        mappings.setdefault(plan["child_observation_id"], []).append(plan)
    candidates = {}
    sources = []
    for source in inputs:
        loaded = Results.git.load(source)
        if loaded.survey and list(loaded.survey.question_names) and _fingerprint(loaded.survey) != _fingerprint(parent.survey):
            raise KatzError("Merge input uses a different survey", "validation_error")
        sources.append({"path": str(source.resolve()), "sha256": sha256_file(source)})
        for index, row in enumerate(loaded):
            identity = _observation_id(row["scenario"], row["model"], row["agent"], row["iteration"])
            options = [p for p in mappings.get(identity, [])
                       if p.get("expected_results_path") == str(source.resolve())]
            if len(options) > 1:
                raise KatzError("Ambiguous retry mapping; merge each iteration separately", "merge_conflict")
            plan = options[0] if options else None
            cloned = row.to_dict()
            if plan:
                cloned["iteration"] = plan["parent_iteration"]
            mapped = Result.from_dict(cloned)
            key = _observation_id(mapped["scenario"], mapped["model"], mapped["agent"], mapped["iteration"])
            candidates.setdefault(key, []).append((mapped, source, index, plan))
    expected = _execution_audit(Results(survey=parent.survey, data=[]), jobs_path, models_path)
    if not expected["models_known"] or expected["errors"]:
        raise KatzError("Merge requires an intact execution plan", "execution_plan_required")
    selected, decisions = [], []
    for identity, choices in candidates.items():
        if identity not in expected["expected"]:
            raise KatzError("Merge input contains an unexpected observation", "validation_error")
        def valid(row):
            answers = row["answer"]
            if "applicable_spotters" in parent.survey.question_names:
                names = parent.survey.questions[0].question_options
                route = answers.get("applicable_spotters")
                if not isinstance(route, list) or any(n not in names for n in route): return False
                return all(_spotter_answer_error(answers.get(f"spotter_detail_{names.index(n)}")) is None for n in route)
            return _spotter_answer_error(answers.get("spotter_result")) is None
        good = [item for item in choices if valid(item[0])]
        if len({_fingerprint(item[0]["answer"]) for item in good}) > 1:
            raise KatzError("Conflicting valid answers require explicit adjudication", "merge_conflict", {"observation_id": identity})
        chosen = good[0] if good else choices[0]
        selected.append(chosen[0])
        decisions.append({"observation_id": identity, "selected_source": str(chosen[1]), "selected_row": chosen[2],
                          "valid": bool(good), "iteration_mapping": chosen[3],
                          "candidates": [{"source": str(c[1]), "row": c[2]} for c in choices]})
    output.parent.mkdir(parents=True, exist_ok=True)
    _save_and_verify_ep(Results(survey=parent.survey, data=selected), output)
    audit = _audit_spotter_results(output, jobs_path, models_path)
    audit.pop("_rows")
    provenance = {"contract": "katz.results-merge.v1", "jobs_sha256": sha256_file(jobs_path),
                  "sources": sources, "decisions": decisions, "output_sha256": sha256_file(output), "audit": audit}
    write_json(output.with_name(output.name + ".merge.json"), provenance)
    return provenance


def audit_result_contract(results_path: Path, jobs_path: Path | None, models_path: Path | None,
                          question: str, expected_commit: str | None = None) -> dict[str, Any]:
    """Shared transport/identity audit for non-spotter review answer contracts."""
    from edsl import Results
    from .storage import sha256_file
    results = Results.git.load(results_path)
    audit = _execution_audit(results, jobs_path, models_path, expected_commit=expected_commit)
    failures = [{"index": i, "error": error} for i, error in audit["row_errors"].items()]
    for index, row in enumerate(results):
        if _result_value(row, "answer", question) is None or _result_value(row, "exceptions", question):
            failures.append({"index": index, "error": "missing_or_exceptional_answer"})
    expected = len(audit["expected"])
    models_path = audit["models_path"]
    return {"contract": "katz.result-contract-audit.v1", "run_id": audit["run_id"],
            "complete": bool(expected and len(results) == expected and not failures
                             and not audit["missing"] and not audit["errors"]),
            "expected_answers": expected if audit["models_known"] else None,
            "valid_answers": len(results) - len({r["index"] for r in failures}),
            "missing_answers": len(audit["missing"]) if audit["models_known"] else None,
            "model_coverage_known": audit["models_known"], "failures": failures, "errors": audit["errors"],
            "results_path": str(results_path.resolve()), "results_sha256": sha256_file(results_path),
            "jobs_path": str(jobs_path.resolve()) if jobs_path else None,
            "jobs_sha256": sha256_file(jobs_path) if jobs_path else None,
            "models_path": str(models_path.resolve()) if models_path else None,
            "models_sha256": sha256_file(models_path) if models_path else None}


SPOTTER_FINDINGS_SUFFIX = """
Reason carefully, then output one final fenced JSON object with schema_version 2
and a findings array. Include each distinct, substantive defect once. Every
finding requires title, quoted_text (an exact manuscript quotation), and
description. Use an empty findings array only when no supported defects remain.
For contradictions include both sides in an evidence_quotes array. Example:
```json
{"schema_version": 2, "findings": [{"title": "Claim exceeds design", "quoted_text": "exact quotation", "description": "Evidence and consequence"}]}
```
"""


def build_comparison_jobs(commit: str, content: str, left: dict, right: dict, definitions: list[dict]):
    """Build paired evidence scenarios with exact source ranges and multiple findings."""
    from edsl import Jobs, Scenario
    from edsl.questions import QuestionFreeText
    encoded = content.encode("utf-8")
    passages = "\n\n".join(f"Section {item['id']}:\n" + encoded[item['byte_start']:item['byte_end']].decode()
                              for item in (left, right))
    scenarios = [Scenario({"katz_commit": commit, "spotter_name": definition["name"],
                          "spotter_scope": "cross_section", "section_id": f"{left['id']}::{right['id']}",
                          "section_title": f"{left['title']} ↔ {right['title']}",
                          "byte_start": 0, "byte_end": len(encoded),
                          "review_target": "two manuscript sections compared against each other",
                          "spotter_instructions": definition["content"],
                          "paper_context": "Only the two named passages are provided. Require evidence from both sides of a contradiction; do not infer absence elsewhere.",
                          "manuscript_content": passages,
                          "katz_evidence_ranges": [{"byte_start": s['byte_start'], "byte_end": s['byte_end']} for s in (left, right)]})
                 for definition in definitions]
    survey = QuestionFreeText(question_name="spotter_result", question_text=SPOTTER_QUESTION_TEXT + SPOTTER_FINDINGS_SUFFIX).to_survey()
    return Jobs(survey=survey, scenarios=scenarios)


def build_verification_jobs(commit: str, manuscript: str, issues: list[dict]):
    from edsl import Jobs, Scenario
    from edsl.questions import QuestionFreeText
    prompt = """Investigate this candidate using the complete manuscript. Seek counterevidence
and acknowledge uncertainty. This is a proposed decision for human adjudication.
Candidate: {{ candidate }}
Manuscript: {{ manuscript }}
Return one fenced JSON object with verdict (confirmed, rejected, or uncertain),
notes (explain evidence, counterevidence, and consequence), and evidence_quotes
(an array of exact manuscript quotations). A confirmed proposal requires at
least one quotation; a contradiction requires both sides. Do not invent evidence.
"""
    survey = QuestionFreeText(question_name="verification", question_text=prompt).to_survey()
    return Jobs(survey=survey, scenarios=[Scenario({"katz_commit": commit, "issue_id": issue["id"],
        "issue_revision": issue["revision"], "candidate": issue["candidate"], "manuscript": manuscript}) for issue in issues])


def build_model_list(names: list[str], *, service: str | None = None,
                     max_tokens: int = SPOTTER_RECOMMENDED_MAX_TOKENS,
                     reasoning_effort: str | None = None, temperature: float = 1.0):
    """Construct model specifications with ordinary Python defaults; never runs models."""
    from edsl import Model, ModelList
    if max_tokens < 1:
        raise KatzError("max_tokens must be positive", "validation_error")
    kwargs = {"max_tokens": max_tokens, "temperature": temperature}
    if service:
        kwargs["service_name"] = service
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return ModelList([Model(name, **kwargs) for name in names])
