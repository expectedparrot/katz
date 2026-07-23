#!/usr/bin/env python3
"""Review paper figures using vision-capable models via EDSL.

Sends each figure + its caption/notes to multimodal models for feedback
on clarity, labeling, self-explanatory quality, and presentation.

Usage:
    python edsl_review_figures.py                # review all figures
    python edsl_review_figures.py --dry-run      # show what would be sent
    python edsl_review_figures.py --models 1     # use only 1 model
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from edsl import Model, ModelList, QuestionFreeText, Scenario, ScenarioList


def run_katz(*args):
    result = subprocess.run(["katz", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def extract_figure_context(manuscript_text, img_filename):
    """Extract caption/notes surrounding an image reference in the manuscript."""
    lines = manuscript_text.split("\n")
    context_lines = []
    img_ref = f"![" # Look for ![...](...{img_filename}...)

    for i, line in enumerate(lines):
        if img_filename in line:
            # Grab surrounding context: 5 lines before and 10 after
            start = max(0, i - 5)
            end = min(len(lines), i + 11)
            context_lines = lines[start:end]
            break

    return "\n".join(context_lines).strip()


def load_figures(commit):
    """Load figure images and their manuscript context."""
    paper_dir = Path(f".katz/versions/{commit}/paper")
    manuscript = (paper_dir / "manuscript.md").read_text(encoding="utf-8")

    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
    figures = []
    for f in sorted(paper_dir.iterdir()):
        if f.suffix.lower() in image_exts and f.is_file():
            context = extract_figure_context(manuscript, f.name)
            figures.append({
                "path": str(f),
                "name": f.name,
                "context": context,
            })
    return figures


ALL_MODELS = [
    lambda: Model(
        "claude-opus-4-20250514",
        service_name="anthropic",
    ),
    lambda: Model(
        "gpt-5.4",
        service_name="openai",
    ),
]


REVIEW_PROMPT = """\
You are reviewing a figure from an academic paper. The figure image is provided,
along with any surrounding caption or notes from the manuscript.

**Surrounding manuscript context:**
{{ context }}

**Review this figure on the following dimensions. For each, provide a brief
(1-2 sentence) assessment:**

1. **Self-explanatory**: Can the figure be understood without reading the paper?
   Are the axes labeled? Is there a clear legend if needed?

2. **Takeaway**: Is the main message of the figure immediately apparent?
   What does the reader learn from looking at this figure?

3. **Design quality**: Is the figure well-designed? Consider: color choices,
   clutter, data-ink ratio, font sizes, resolution. Could it be simplified?

4. **Caption adequacy**: Does the caption/notes (if present) adequately explain
   what is shown, how to read the figure, and what the key patterns are?

5. **Suggestions**: What specific improvements would make this figure more
   effective? Be concrete.

Return your review as a JSON object:
{
  "self_explanatory": "...",
  "takeaway": "...",
  "design_quality": "...",
  "caption_adequacy": "...",
  "suggestions": "...",
  "overall_grade": "A/A-/B+/B/B-/C+/C/C-/D/F"
}

Return ONLY the JSON object. No other text.
"""


def parse_review(text):
    """Parse a figure review JSON from LLM output."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    # Try to extract JSON object
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
        # Try ast.literal_eval for Python-style dicts
        import ast
        try:
            return ast.literal_eval(match.group())
        except (ValueError, SyntaxError):
            pass
    return None


def slugify(name):
    """Match the katz CLI's eval name slug convention."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        raise ValueError(f"Could not build eval name from {name!r}")
    return slug


def main():
    parser = argparse.ArgumentParser(description="EDSL figure review for katz")
    parser.add_argument("--models", type=int, default=2, help="Number of models (default: 2)")
    parser.add_argument("--dry-run", action="store_true", help="Show figures without running")
    args = parser.parse_args()

    status = run_katz("paper", "status")
    commit = status["commit"]
    figures = load_figures(commit)

    if not figures:
        print("No figures found in paper directory.")
        sys.exit(0)

    models = ModelList([factory() for factory in ALL_MODELS[:args.models]])

    print(f"Paper: {status['source_root']} @ {commit[:8]}")
    print(f"Figures: {len(figures)}, Models: {len(models)}")
    print(f"Total calls: {len(figures) * len(models)}")
    for fig in figures:
        ctx_preview = fig["context"][:80].replace("\n", " ") if fig["context"] else "(no context)"
        print(f"  {fig['name']}: {ctx_preview}...")
    print()

    if args.dry_run:
        print(f"Would run {len(figures) * len(models)} calls.")
        return

    # Build scenarios: each figure gets its image + context
    scenarios = ScenarioList()
    for fig in figures:
        img_scenario = Scenario.from_image(fig["path"], image_name="figure")
        img_scenario["context"] = fig["context"] or "(No caption or notes found in manuscript)"
        img_scenario["figure_name"] = fig["name"]
        scenarios.append(img_scenario)

    q = QuestionFreeText(
        question_name="figure_review",
        question_text=REVIEW_PROMPT,
    )

    print(f"Running {len(scenarios) * len(models)} calls...")
    results = q.by(scenarios).by(models).run()

    # Process results and record as eval responses
    reviews_recorded = 0
    for result in results:
        answer = result["answer"]["figure_review"]
        figure_name = result["scenario"]["figure_name"]
        model_name = result["model"]._model_

        review = parse_review(answer)
        if not review:
            print(f"  WARNING: Could not parse review for {figure_name} from {model_name}")
            continue

        grade = review.get("overall_grade", "")
        parts = []
        for key in ["self_explanatory", "takeaway", "design_quality", "caption_adequacy", "suggestions"]:
            if review.get(key):
                label = key.replace("_", " ").title()
                parts.append(f"**{label}**: {review[key]}")
        narrative = f"[{model_name}] " + " ".join(parts)

        # Record as an eval response — create the eval criterion if needed
        eval_name = slugify(f"figure_{figure_name.rsplit('.', 1)[0]}")
        # Ensure the eval criterion exists
        try:
            run_katz("eval", "show", eval_name)
        except subprocess.CalledProcessError:
            # Create it
            run_katz(
                "eval", "add",
                "--name", eval_name,
                "--question", f"Review of figure {figure_name}",
                "--category", "figures",
            )

        # Record response (will overwrite any previous)
        grade_args = ["--grade", grade] if grade and grade in {"A+","A","A-","B+","B","B-","C+","C","C-","D+","D","D-","F"} else []
        subprocess.run(
            ["katz", "eval", "respond", "--name", eval_name, "--text", narrative] + grade_args,
            capture_output=True, text=True, check=True,
        )
        reviews_recorded += 1
        print(f"  [{figure_name}] [{model_name}] grade={grade}")

    print(f"\nDone: {reviews_recorded} figure reviews recorded as eval responses")


if __name__ == "__main__":
    main()
