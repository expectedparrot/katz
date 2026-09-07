"""Score independently adjudicated review results without executing models."""
from __future__ import annotations

import math
from typing import Any

from .errors import KatzError


def score_benchmark(truth: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    papers = truth.get("papers", [])
    reviews = results.get("reviews", [])
    expected = {p["id"]: set(p["defects"]) for p in papers}
    if not expected or len(expected) != len(papers):
        raise KatzError("Benchmark requires unique paper IDs", "validation_error")
    if len({r["paper_id"] for r in reviews}) != len(reviews):
        raise KatzError("Duplicate review paper ID", "validation_error")
    complete = len(reviews) == len(expected)
    found = set()
    candidates = grounding_failures = duplicates = 0
    cost = seconds = 0.0
    for review in reviews:
        paper = review["paper_id"]
        if paper not in expected or not isinstance(review.get("complete"), bool):
            raise KatzError("Unknown paper or missing explicit coverage status", "validation_error")
        complete = complete and review["complete"]
        for key in ("cost_usd", "investigation_seconds"):
            value = review.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise KatzError("Benchmark requires finite nonnegative cost and investigation time", "validation_error")
        cost += review["cost_usd"]
        seconds += review["investigation_seconds"]
        ids = set()
        for finding in review["findings"]:
            if finding["id"] in ids or not isinstance(finding.get("grounded"), bool):
                raise KatzError("Duplicate finding ID or missing grounding judgment", "validation_error")
            ids.add(finding["id"])
            defect = finding.get("defect_id")
            if defect is not None and defect not in expected[paper]:
                raise KatzError("Unknown adjudicated defect ID", "validation_error")
            candidates += 1
            grounding_failures += not finding["grounded"]
            if defect is not None and finding["grounded"]:
                identity = (paper, defect)
                duplicates += identity in found
                found.add(identity)
    total = sum(map(len, expected.values()))
    # Missing/failed reviews must not be counted as negative findings.
    metrics = None if not complete else {
        "defect_recall": len(found) / total if total else None,
        "finding_precision": len(found) / candidates if candidates else None,
        "grounding_failures": grounding_failures,
        "duplicate_findings": duplicates,
        "cost_per_confirmed_defect_usd": cost / len(found) if found else None,
        "investigation_seconds": seconds,
    }
    return {"contract": "katz.benchmark-score.v1", "complete": complete,
            "expected_papers": len(expected), "returned_papers": len(reviews),
            "expected_defects": total, "confirmed_defects": len(found),
            "candidate_findings": candidates, "cost_usd": cost, "metrics": metrics}
