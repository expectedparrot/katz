---
name: configure-spotters
description: Read the paper and decide which issue spotters to use, removing irrelevant ones and optionally adding custom ones
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Configure Spotters

Reads the manuscript, determines what kind of paper it is, loads default spotters, and then removes any that don't apply. Optionally adds paper-specific custom spotters.

## Usage

```
/configure-spotters
```

## Prerequisites

- The paper must be registered in katz (`katz paper status` should return `"valid": true`).

## Workflow

### 1. Ensure the catalog exists

Run `katz spotter catalog`. If empty, initialize it:

```bash
katz spotter init-catalog 
```

This populates `.katz/spotters/` (the repo-level catalog) with 13 default spotters. This does NOT enable any of them for the current paper.

### 2. Read the paper

Read the abstract and introduction from the canonical manuscript to understand:
- **Paper type**: empirical (experiment, quasi-experiment, observational), theory, descriptive/stylized facts, simulation, or methods paper
- **Methods used**: RCT, IV, diff-in-diff, RD, matching, structural estimation, survey, LLM-based, etc.
- **Claims made**: causal, correlational, descriptive, predictive
- **Scope of contribution**: advancing theory, documenting a new fact, demonstrating a method, policy evaluation

Use `katz paper section <id>` and read the relevant lines from `.katz/versions/<commit>/paper/manuscript.md`.

### 3. Browse the catalog and enable what applies

Run `katz spotter catalog` to see all available spotters. For each one, run `katz spotter catalog-show <name>` to read its description.

Enable only the spotters that apply to this paper:

```bash
katz spotter enable overclaiming
katz spotter enable logical_gaps
katz spotter enable unclear_writing
# ... etc
```

`katz spotter enable <name>` is idempotent: if the spotter is already enabled, the command returns success with `"already_enabled": true`. This makes it safe to run from scripts that defensively enable a set of spotters.

Use judgment. For example:
- A straightforward experiment report doesn't need `literature_positioning` (but DOES benefit from `identification_threats` — SUTVA, spillovers, etc.)
- A pure theory paper doesn't need `statistical_errors`, `methodology_concerns`, or `results_interpretation`
- A descriptive/stylized-facts paper doesn't need `causal_language`
- `overclaiming`, `logical_gaps`, `unclear_writing`, `internal_contradictions`, and `narrative_consistency` apply to nearly all papers
- When in doubt, enable more rather than fewer — the investigation phase filters out false positives. The cost of a missed real issue is higher than the cost of investigating a few extra false positives.

### 4. Add paper-specific custom spotters

Based on what you learned about the paper, add custom spotters for concerns specific to this paper's methods or domain directly to the version. Examples:

- An **online experiment** paper: demand effects, ecological validity
- A paper **using LLMs** as a tool: prompt sensitivity, model version dependence
- A **structural estimation** paper: identification of structural parameters
- A paper with a **novel dataset**: measurement validity

```bash
katz spotter add \
  --name "prompt_sensitivity" \
  --scope section \
  --description "Check whether results could be driven by specific prompt wording. Look for: single untested prompt, no robustness to rephrasing, temperature/sampling not reported." \
  --investigation "Check if alternative prompts are tested. Check if generation parameters are reported. Confirm if a single untested prompt drives results. Reject if robustness is shown."
```

`katz spotter add` writes the new spotter to the catalog and auto-enables it for the active version when a paper is registered. Do not run `katz spotter enable` again for the same custom spotter unless you are intentionally checking idempotence.

### 5. Report

After configuring, report:
- What kind of paper this is (1 sentence)
- Which catalog spotters were enabled and a one-line justification for each
- Which were skipped and why
- Any custom spotters created
- Run `katz spotter list` to show the final configuration
