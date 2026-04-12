---
name: review-figures
description: Send paper figures to vision-capable models for feedback on clarity, design, and presentation
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Review Figures

Sends each figure from the paper to vision-capable models via EDSL for feedback on clarity, labeling, design quality, and caption adequacy. Results are recorded as eval responses under the "figures" category.

## Usage

```
/review-figures
```

## Prerequisites

- The paper must be registered in katz with images copied to `.katz/versions/<commit>/paper/`.
  (This happens automatically when registering with `katz paper register` — sibling image files
  are copied alongside the manuscript.)
- `edsl` must be installed.

## Workflow

### 1. Validate

Run `katz paper status` to confirm registration. Check that images exist:

```bash
ls .katz/versions/$(katz paper status | python3 -c "import sys,json; print(json.load(sys.stdin)['commit'])")/paper/*.png
```

### 2. Run the figure review

```bash
# All figures, 2 models (Claude Opus + GPT-5.4)
python <katz-skills-path>/review-figures/scripts/edsl_review_figures.py

# Dry run to see what would be sent
python <katz-skills-path>/review-figures/scripts/edsl_review_figures.py --dry-run

# Single model
python <katz-skills-path>/review-figures/scripts/edsl_review_figures.py --models 1
```

The script:
- Loads all images from the paper directory
- Extracts surrounding caption/notes from the manuscript for context
- Sends each figure + context to vision models
- Records results as eval responses with grades under the "figures" category

### 3. View results

Results appear in the HTML report under the Evaluations section (category: "figures"), or via:

```bash
katz eval results --category figures
```

### 4. What the models evaluate

Each figure is assessed on five dimensions:
- **Self-explanatory**: Can it be understood standalone? Axes labeled? Legend present?
- **Takeaway**: Is the main message immediately apparent?
- **Design quality**: Color choices, clutter, data-ink ratio, font sizes
- **Caption adequacy**: Does the caption explain what's shown and how to read it?
- **Suggestions**: Specific improvements
