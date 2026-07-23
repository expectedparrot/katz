# Review Workflow

katz reviews follow a linear phase sequence. The current phase is inferred from
disk artifacts — run `katz status` or `katz paper status` to check.

---

## Phase 0: Initialization

**Goal:** Set up `.katz/` in the git repo where the paper lives.

```bash
katz init
```

**Artifacts created:** `.katz/versions/`

**What can go wrong:**
- Not in a git repo → `katz requires an existing git repository`
- Already initialized → safe to re-run (idempotent)

---

## Phase 1: Registration

**Goal:** Register the canonical markdown manuscript and build the sentence index.

```bash
katz paper register \
  --canonical paper/manuscript.md \
  --source-format markdown \
  --source-uri https://arxiv.org/abs/2401.00000
```

The manuscript should be in **ventilated prose** (one sentence per line). This is
how issue locations stay precise: each byte range maps to a readable sentence.

**Artifacts created:**
- `.katz/versions/{commit}/version.json`
- `.katz/versions/{commit}/paper/manuscript.md`
- `.katz/versions/{commit}/paper_map.jsonl` (header + sentence records)
- `.katz/ACTIVE_VERSION` (set to current git HEAD)

**Check:** `katz paper status` → `"valid": true`, `"sections": 0`

---

## Phase 2: Chunking

**Goal:** Add section boundaries to the paper map so spotters can run per-section.

**Quick path (auto-detect headings):**
```bash
katz paper auto-chunk
katz paper sections           # verify
katz paper section introduction    # spot-check one
```

**Manual path (for papers with non-standard structure):**
```bash
katz paper find "1. Introduction"   # locate the byte offset
katz paper add-sections --sections '[
  {"id": "introduction", "title": "Introduction", "byte_start": 0, "byte_end": 4200},
  {"id": "data", "title": "Data", "byte_start": 4200, "byte_end": 9100}
]'
```

**Artifacts created:** Section records appended to `paper_map.jsonl`

**Check:** `katz paper status` → `"sections"` > 0

---

## Phase 3: Spotter Configuration

**Goal:** Enable the review criteria relevant to this specific paper.

```bash
katz spotter init-catalog           # populate from built-in catalog
katz spotter catalog                # see what's available
katz spotter catalog-show overclaiming   # read a spotter before enabling
katz spotter enable overclaiming
katz spotter enable logical_gaps
katz spotter enable causal_language
# ...add paper-specific custom spotters if needed
katz spotter add --name "sutva_violations" \
  --scope section \
  --description "Look for SUTVA violations in the experimental design..."
```

**Built-in spotters (from `katz spotter init-catalog`):**
- `overclaiming` — conclusions stronger than evidence
- `logical_gaps` — argument skips a step
- `internal_contradictions` — inconsistencies between sections
- `unclear_writing` — ambiguous or hard-to-parse prose
- `methodology_concerns` — research design problems
- `causal_language` — causal claims without causal evidence
- `identification_threats` — selection, SUTVA, compliance issues
- `statistical_errors` — misuse of p-values, multiple testing, power
- `results_interpretation` — misreading of results
- `literature_positioning` — missing relevant prior work
- `narrative_consistency` — story across sections doesn't cohere (holistic)
- `introduction_flow` — introduction structure problems (holistic)

**Artifacts created:** `.katz/versions/{commit}/spotters/*.md`

**Check:** `katz spotter list` → list of enabled spotters with scopes

---

## Phase 4: Issue Finding (EDSL Codegen)

**Goal:** Sweep the manuscript for issues using parallel LLM calls.

**Do not run `edsl_find_issues.py` directly.** Instead, generate a custom script:

```bash
katz status
# The response includes codegen.script — a complete Python script
# populated with this paper's actual sections and spotters.
```

Save the script, show it to the user, and ask them to run it:

```bash
python find_issues.py --dry-run    # preview: N calls, no LLM costs
python find_issues.py              # run the full sweep
```

**What the script does:**
1. Iterates sections × section_spotters × models (batched in groups of 3 sections)
2. Runs holistic spotters once on the full manuscript
3. Parses LLM responses and files each finding with `katz issue write`

**After the sweep:**
```bash
katz issue list --state draft      # see what was filed
```

**Expected volume:** 150–300 draft issues for a typical paper with 8 spotters.

**Artifacts created:** `.katz/versions/{commit}/issues/*/`

---

## Phase 5: Issue Investigation

**Goal:** Separate signal from noise. Confirm genuine issues, reject false positives.

The false-positive rate is high (~90%). Systematic investigation is the bottleneck.

**Workflow for each issue:**

```bash
katz issue show <id-prefix>
```

Read:
- `title` and `body` — what the spotter flagged
- `location.resolved_text` — the exact manuscript text
- `location.section` — which section it's in

Then decide:
- **Confirmed:** genuine issue worth flagging to authors
- **Rejected:** false positive (PDF artifact, addressed elsewhere, not real)
- **Uncertain:** needs more context; leave as open for now

```bash
katz issue investigate --id <id> \
  --verdict confirmed \
  --notes "Table 3 says N=1200 but section 2 says N=1180 — inconsistency."
katz issue update --id <id> --state confirmed
```

**Merging near-duplicates before investigating:**
```bash
katz issue merge --ids <id1>,<id2>,<id3> \
  --title "Overclaiming in abstract: causal language for correlational results"
```

**Artifacts created:** `status/*.json` and `investigations/*.json` in each issue dir

---

## Phase 6: Evaluation (Optional)

**Goal:** Structured quality assessment using rubric-style criteria.

```bash
katz eval init-catalog
katz eval enable design_matches_claims
katz eval enable findings_clearly_presented
katz eval enable limitations_acknowledged

katz eval respond --name design_matches_claims \
  --text "The IV strategy is credible. Exclusion restriction is well-argued." \
  --grade A-
  
katz eval results                   # see all responses
```

Eval responses appear in the HTML report alongside issue cards.

---

## Phase 7: Reporting

**Goal:** Synthesize findings into a structured output.

**HTML report (issue cards + eval responses):**
```bash
katz report generate --output review.html
# open review.html in a browser
```

**Task-local explorer artifact for research-agent workflows:**
```bash
katz report generate --output writeup/artifacts/paper_explorer.html
```

When the review is being delivered through a research-agent task scaffold, use
the generated Katz HTML as a supporting explorer artifact and write the main
user-facing review in `writeup/report.md`, then compile that report through
Gutenberg.

**Confirm issue list for summary:**
```bash
katz issue list --state confirmed
```

**Filter by section or spotter:**
```bash
katz issue list --state confirmed --section results
katz issue list --state confirmed --spotter overclaiming
```
