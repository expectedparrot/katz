# Review-quality benchmark seed

These original synthetic papers provide a clean control and two planted defects.
They are smoke fixtures, not a validated benchmark of academic review quality.
Add independently adjudicated public papers and a held-out split before tuning
prompts or comparing routing with exhaustive review.

Register each paper in its own workspace. Preserve source, plan, Jobs, ModelList,
Results, retries, and issue investigations. Execute only externally approved
model runs. Have a reviewer assign each finding a defect ID or null (unsupported),
and a grounding judgment. Record failures explicitly as incomplete reviews.

Score an adjudicated artifact with:

```bash
katz benchmark score --truth benchmarks/review-quality/truth.json \
  --judgments judgments.json --output benchmark-score.json
```

Judgments have a `reviews` array. Each review requires `paper_id`, boolean
`complete`, nonnegative `cost_usd`, `investigation_seconds`, and a `findings` array.
Each finding requires a unique `id`, boolean `grounded`, and `defect_id` (a planted
defect ID or null). Incomplete coverage yields no aggregate precision/recall.
Duplicates remain visible and penalize finding precision. Scores retain the
truth and judgments hashes. Provider runs and adjudication have not been performed
for these seed fixtures.
