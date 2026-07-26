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
SPOTTER_RECOMMENDED_MAX_TOKENS = 4000


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
        return answer if "found" in answer else None
    if not isinstance(answer, str):
        return None
    text = answer.strip()
    if not text:
        return None
    candidates: list[str] = []
    candidates += re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates += re.findall(r"(\{[^{}]*\"found\"[^{}]*\})", text, re.DOTALL)
    greedy = re.search(r"\{.*\"found\".*\}", text, re.DOTALL)
    if greedy:
        candidates.append(greedy.group(0))
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "found" in parsed:
            return parsed
    return None


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


def _audit_spotter_results(results_path: Path, jobs_path: Path | None = None) -> dict[str, Any]:
    """Audit structured spotter Results against the originating Jobs when available."""
    _edsl_imports()
    from edsl import Jobs, Results

    results = Results.git.load(results_path)
    expected_keys: list[str] = []
    if jobs_path is not None:
        jobs = Jobs.git.load(jobs_path)
        expected_keys = [_scenario_key(scenario) for scenario in jobs.scenarios]

    rows: list[dict[str, Any]] = []
    returned_keys: list[str] = []
    returned_pairs: list[tuple[str, str]] = []
    valid_positive = valid_negative = 0
    null_answers = invalid_answers = model_exceptions = 0
    failure_examples: list[dict[str, Any]] = []
    models: set[str] = set()
    for index, result in enumerate(results):
        scenario = result["scenario"] if isinstance(result["scenario"], dict) else dict(result["scenario"])
        key = _scenario_key(scenario)
        returned_keys.append(key)
        answer = _result_value(result, "answer", "spotter_result")
        verdict = _coerce_spotter_answer(answer)
        model = _result_value(result, "model", "model") or _result_value(result, "model", "_model_")
        model_str = str(model) if model else ""
        returned_pairs.append((key, model_str))
        if model:
            models.add(str(model))
        exception = _result_value(result, "exceptions", "spotter_result")
        error = _spotter_answer_error(answer)
        if exception:
            model_exceptions += 1
            error = "model_exception"
        elif error == "null_answer":
            null_answers += 1
        elif error:
            invalid_answers += 1
        elif verdict is not None and _answer_is_found(verdict.get("found")):
            valid_positive += 1
        else:
            valid_negative += 1
        row = {
            "index": index,
            "scenario": {
                "spotter_name": scenario.get("spotter_name"),
                "section_id": scenario.get("section_id"),
                "section_title": scenario.get("section_title"),
            },
            "valid": error is None,
            "found": _answer_is_found(verdict.get("found")) if verdict is not None and error is None else None,
            "error": error,
            "answer": verdict if verdict is not None else answer,
        }
        rows.append(row)
        if error and len(failure_examples) < 10:
            failure_examples.append({key: value for key, value in row.items() if key != "answer"})

    expected_set = set(expected_keys)
    returned_set = set(returned_keys)
    # A duplicate is the SAME scenario answered twice by the SAME model; distinct
    # models answering one scenario are separate observations, not duplicates.
    returned_pair_set = set(returned_pairs)
    duplicate_rows = len(returned_pairs) - len(returned_pair_set)
    missing_scenarios = len(expected_set - returned_set) if expected_keys else None
    unexpected_scenarios = len(returned_set - expected_set) if expected_keys else None
    expected_count = len(expected_keys) if expected_keys else None

    # Coverage is model-aware: every jobs scenario must be answered by every model
    # present in the Results. With one model this reduces to one answer/scenario.
    model_names = models or {""}
    if expected_keys:
        expected_pairs = {(k, m) for k in expected_keys for m in model_names}
        expected_answer_count: int | None = len(expected_pairs)
        missing_answers: int | None = len(expected_pairs - returned_pair_set)
    else:
        expected_answer_count = None
        missing_answers = None

    valid_count = valid_positive + valid_negative
    denominator = expected_answer_count if expected_answer_count is not None else len(results)
    coverage = (valid_count / denominator) if denominator else 0.0
    complete = bool(
        expected_answer_count is not None
        and expected_answer_count > 0
        and valid_count == expected_answer_count
        and not duplicate_rows
        and not missing_scenarios
        and not unexpected_scenarios
        and not missing_answers
        and not null_answers
        and not invalid_answers
        and not model_exceptions
    )
    return {
        "contract": "katz.spotter-results-audit.v1",
        "results_path": str(results_path.resolve()),
        "jobs_path": str(jobs_path.resolve()) if jobs_path is not None else None,
        "expected_scenarios": expected_count,
        "expected_answers": expected_answer_count,
        "returned_rows": len(results),
        "valid_answers": valid_count,
        "valid_positive_findings": valid_positive,
        "valid_negative_findings": valid_negative,
        "null_answers": null_answers,
        "invalid_answers": invalid_answers,
        "model_exceptions": model_exceptions,
        "missing_scenarios": missing_scenarios,
        "missing_answers": missing_answers,
        "unexpected_scenarios": unexpected_scenarios,
        "duplicate_rows": duplicate_rows,
        "coverage": round(coverage, 6),
        "complete": complete,
        "models": sorted(models),
        "failure_examples": failure_examples,
        "_rows": rows,
    }


def _resolve_audit_jobs(dest: Path, results_path: Path, jobs_path: Path | None) -> Path | None:
    if jobs_path is not None:
        return jobs_path
    run = _latest_packaged_run(dest, results_path)
    candidate = Path(str(run.get("jobs_path"))) if run and run.get("jobs_path") else None
    return candidate if candidate is not None and candidate.is_file() else None


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
