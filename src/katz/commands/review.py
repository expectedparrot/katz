"""`katz review` commands: human referee reports and their parsing jobs."""
from __future__ import annotations

import hashlib
import json
import re
import typer
import uuid
from pathlib import Path
from typing import Any, List, Optional

from ..edsl_bridge import (
    JOURNAL_REVIEW_PARSE_PROMPT,
    _expected_results_path,
    _result_value,
    _save_and_verify_ep,
)
from ..errors import KatzError, emit_json, fail
from ..issues import VALID_STATES, _issue_dir
from ..manuscript import _locate_quoted_text, resolve_location
from ..storage import load_version, now_utc, read_json, record_run, write_event_json, write_json


review_app = typer.Typer(help="Register and parse human-written peer reviews.")


def _review_dir(dest: Path, review_id: str) -> Path:
    return dest / "reviews" / review_id


@review_app.command("add")
def review_add(
    source: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    reviewer: Optional[str] = typer.Option(None, "--reviewer"),
    venue: Optional[str] = typer.Option(None, "--venue"),
    round_name: Optional[str] = typer.Option(None, "--round"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Preserve a human-written journal review with the registered paper version."""
    try:
        resolved, dest, _, _, _ = load_version(commit)
        text = source.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        review_id = f"review-{digest[:12]}"
        review_dir = _review_dir(dest, review_id)
        metadata_path = review_dir / "review.json"
        if metadata_path.exists():
            emit_json({**read_json(metadata_path), "already_registered": True})
            return
        review_dir.mkdir(parents=True, exist_ok=False)
        preserved = review_dir / ("review.md" if source.suffix.lower() == ".md" else "review.txt")
        preserved.write_text(text, encoding="utf-8")
        record = {
            "schema_version": 1,
            "id": review_id,
            "commit": resolved,
            "reviewer": reviewer,
            "venue": venue,
            "round": round_name,
            "source_name": source.name,
            "preserved_path": str(preserved),
            "sha256": f"sha256:{digest}",
            "created_at": now_utc(),
        }
        write_json(metadata_path, record)
        emit_json({**record, "already_registered": False, "next": f"katz review jobs {review_id} --output journal-review.jobs.ep"})
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@review_app.command("list")
def review_list(commit: Optional[str] = typer.Option(None, "--commit")) -> None:
    """List human-written reviews preserved for a paper version."""
    try:
        _, dest, _, _, _ = load_version(commit)
        reviews_dir = dest / "reviews"
        records = [
            read_json(path)
            for path in sorted(reviews_dir.glob("*/review.json"))
        ] if reviews_dir.is_dir() else []
        emit_json(records)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)


@review_app.command("jobs")
def review_jobs(
    review_id: str,
    output: Path = typer.Option(Path("journal-review.jobs.ep"), "--output", "-o"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """Build an EDSL job that parses a preserved journal review into issue candidates."""
    try:
        if output.suffix != ".ep":
            raise KatzError("--output must use the .ep extension", "validation_error", {"output": str(output)})
        if output.exists():
            raise KatzError(f"{output} already exists", "validation_error", {"output": str(output)})
        try:
            from edsl import FileStore, Jobs, Scenario, ScenarioList
            from edsl.questions import QuestionFreeText
        except ImportError as exc:
            raise KatzError("EDSL is required to create .ep objects", "dependency_error") from exc

        resolved, dest, _, _, canonical = load_version(commit)
        review_dir = _review_dir(dest, review_id)
        metadata_path = review_dir / "review.json"
        if not metadata_path.exists():
            raise KatzError("Human review does not exist", "not_found", {"review_id": review_id})
        metadata = read_json(metadata_path)
        candidates = list(review_dir.glob("review.*"))
        review_path = next((path for path in candidates if path.name != "review.json"), None)
        if review_path is None:
            raise KatzError("Preserved review text is missing", "not_found", {"review_id": review_id})
        scenario = Scenario({
            "katz_commit": resolved,
            "review_id": review_id,
            "manuscript": FileStore(str(canonical)),
            "journal_review": FileStore(str(review_path)),
        })
        question = QuestionFreeText(
            question_name="journal_review_issues",
            question_text=JOURNAL_REVIEW_PARSE_PROMPT,
        )
        job = Jobs(survey=question.to_survey()).by(ScenarioList([scenario]))
        saved = _save_and_verify_ep(job, output)
        expected_results = _expected_results_path(output)
        record_run(
            dest, "journal_review", "packaged",
            jobs_path=str(output.resolve()),
            expected_results_path=str(expected_results.resolve()),
            question="journal_review_issues",
            scenario_count=1,
            review_id=review_id,
        )
        emit_json({
            "object_type": "Jobs",
            "output": str(output),
            "commit": resolved,
            "review_id": review_id,
            "question": "journal_review_issues",
            "expected_model_calls": "1 × the number of externally selected models",
            "model_specifications": "Selected explicitly when running ep; none embedded by Katz.",
            "likely_cost": "Unknown until the external model is selected.",
            "inference": "external",
            "attachments": [canonical.name, review_path.name],
            "saved": saved,
            "next": f"ep run {output} --model <model-name> --output {expected_results}",
            "ingest_next": f"katz ingest {expected_results}",
            "metadata": metadata,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"output": str(output)})


def _parse_json_array_answer(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        parsed = value
    else:
        text = str(value or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise KatzError(
                "Journal-review result is not a JSON array",
                "validation_error",
                {"line": exc.lineno, "column": exc.colno},
            ) from exc
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise KatzError("Journal-review result must be an array of objects", "validation_error")
    return parsed


@review_app.command("ingest")
def review_ingest(
    results_path: Path = typer.Argument(..., exists=True, readable=True),
    state: str = typer.Option("draft", "--state"),
    commit: Optional[str] = typer.Option(None, "--commit"),
) -> None:
    """File grounded issue candidates parsed from a human journal review."""
    try:
        if state not in VALID_STATES:
            raise KatzError("Invalid issue state", "validation_error", {"state": state, "valid": sorted(VALID_STATES)})
        from edsl import Results

        resolved, dest, _, _, canonical = load_version(commit)
        results = Results.git.load(results_path)
        manuscript = canonical.read_text(encoding="utf-8")
        existing_keys = {
            record.get("meta", {}).get("journal_review_result_key")
            for path in (dest / "issues").glob("*/issue.json")
            for record in [read_json(path)]
            if record.get("meta", {}).get("journal_review_result_key")
        } if (dest / "issues").is_dir() else set()

        candidates = filed = skipped = 0
        issue_ids: list[str] = []
        for result in results:
            scenario = result["scenario"] if isinstance(result["scenario"], dict) else dict(result["scenario"])
            if scenario.get("katz_commit") != resolved:
                raise KatzError("Results were generated for a different Katz version", "validation_error")
            review_id = str(scenario.get("review_id", ""))
            if not (_review_dir(dest, review_id) / "review.json").exists():
                raise KatzError("Result references an unregistered human review", "not_found", {"review_id": review_id})
            answer = _result_value(result, "answer", "journal_review_issues")
            for item in _parse_json_array_answer(answer):
                candidates += 1
                quoted = str(item.get("quoted_text", "")).strip()
                located = _locate_quoted_text(manuscript, quoted) if quoted else None
                if located is None:
                    skipped += 1
                    continue
                char_start, char_end = located
                byte_start = len(manuscript[:char_start].encode("utf-8"))
                byte_end = len(manuscript[:char_end].encode("utf-8"))
                key_payload = json.dumps(
                    {"commit": resolved, "review_id": review_id, "item": item},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                result_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
                if result_key in existing_keys:
                    skipped += 1
                    continue
                issue_id = uuid.uuid4().hex
                timestamp = now_utc()
                issue_dir = _issue_dir(dest, issue_id)
                (issue_dir / "status").mkdir(parents=True, exist_ok=True)
                (issue_dir / "investigations").mkdir(parents=True, exist_ok=True)
                body_parts = [str(item.get("body", "")).strip()]
                if item.get("reviewer_comment"):
                    body_parts.append(f'Reviewer comment: “{str(item["reviewer_comment"]).strip()}”')
                if item.get("suggested_response"):
                    body_parts.append(f'Suggested response: {str(item["suggested_response"]).strip()}')
                record = {
                    "schema_version": 2,
                    "id": issue_id,
                    "commit": resolved,
                    "title": str(item.get("title") or "Journal review comment"),
                    "body": "\n\n".join(part for part in body_parts if part),
                    "spotter": None,
                    "artifacts": [],
                    "location": resolve_location(canonical, byte_start, byte_end),
                    "created_at": timestamp,
                    "meta": {
                        "source": "human_journal_review",
                        "review_id": review_id,
                        "severity": str(item.get("severity", "unspecified")),
                        "journal_review_result_key": result_key,
                        "edsl_results_path": str(results_path),
                    },
                }
                write_json(issue_dir / "issue.json", record)
                write_event_json(issue_dir / "status", {
                    "state": state,
                    "reason": f"parsed from human journal review {review_id}",
                    "timestamp": timestamp,
                })
                existing_keys.add(result_key)
                issue_ids.append(issue_id)
                filed += 1
        record_run(
            dest, "journal_review", "ingested",
            results_path=str(results_path.resolve()),
            result_count=len(results),
            candidates=candidates,
            issues_filed=filed,
            skipped=skipped,
        )
        emit_json({
            "results": str(results_path),
            "commit": resolved,
            "result_count": len(results),
            "candidates": candidates,
            "issues_filed": filed,
            "skipped": skipped,
            "issue_ids": issue_ids,
        })
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except Exception as exc:
        fail(str(exc), "edsl_error", {"results": str(results_path)})
