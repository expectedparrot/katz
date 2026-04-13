---
name: review-repo
description: Review the paper's repository — code, data, and analysis scripts — against manuscript claims
allowed-tools: Read, Bash, Glob, Grep
user-invocable: true
---

# Review Repo

Explores the paper's repository to verify that code, data, and analysis scripts are consistent with the manuscript's claims. Files katz issues for any discrepancies, linking manuscript locations to the relevant repo artifacts.

## Usage

```
/review-repo
```

## Prerequisites

- The paper must be registered in katz (`katz paper status` should return `"valid": true`).
- The repo should contain analysis code (scripts, notebooks, Makefiles, etc.).

## Workflow

### 1. Map the repo structure

Explore the repository to understand what's there:

```bash
# Find analysis scripts
find . -name "*.R" -o -name "*.py" -o -name "*.do" -o -name "*.jl" -o -name "*.m" | head -50

# Find data files
find . -name "*.csv" -o -name "*.dta" -o -name "*.parquet" -o -name "*.xlsx" | head -30

# Find notebooks
find . -name "*.ipynb" -o -name "*.Rmd" -o -name "*.qmd" | head -20

# Check for build systems
ls Makefile makefile snakefile *.mk 2>/dev/null
```

Report to the user what you find: how many scripts, what languages, whether there's a build system, etc.

### 2. Identify manuscript claims to verify

Read the manuscript and identify verifiable claims:

- **Tables**: For each table, find the script that generates it. Check that the reported numbers match.
- **Figures**: For each figure, find the generating script. Check that the figure description matches what the code produces.
- **Inline statistics**: Numbers reported in the text (sample sizes, coefficients, p-values, percentages). Find where they're computed.
- **Methodology**: Compare the described methodology (estimation approach, variable definitions, exclusion criteria) with the implementation.

Use `katz paper section <id>` and the manuscript to identify what to check.

### 3. Review code quality and consistency

For each analysis script:

- **Read the code** before running it. Understand what it does.
- **Check variable definitions**: Do the variable names and constructions match the paper's descriptions?
- **Check estimation methods**: Does the code use the estimator described in the paper (e.g., OLS vs. WLS, clustered vs. robust SEs)?
- **Check sample restrictions**: Are the same exclusion criteria applied in code as described in the paper?
- **Check hardcoded values**: Are there magic numbers that should be parameters?

### 4. Run code when feasible

If the code can be run (dependencies available, data accessible):

```bash
# Check if dependencies are available
pip list 2>/dev/null | grep -i "pandas\|numpy\|statsmodels"
Rscript -e "installed.packages()[,'Package']" 2>/dev/null | head -20
```

Run scripts and compare output to manuscript claims. When running code:
- Start with small, self-contained scripts
- Check that output matches reported numbers
- Note any discrepancies

If the code cannot be run (missing data, proprietary dependencies), note this and review the code statically.

### 5. File issues for discrepancies

For each discrepancy found, file a katz issue linking the manuscript claim to the relevant code:

```bash
katz issue write \
  --title "Table 2 coefficient differs from code output" \
  --byte-start <start> --byte-end <end> \
  --body "The manuscript reports 0.87 for the hardship coefficient, but running analysis/table2.R produces 0.84. The difference may be due to a different sample restriction in line 45 of the script." \
  --artifacts "analysis/table2.R,data/clean_sample.csv"
```

The `--byte-start` and `--byte-end` point to where the manuscript makes the claim. The `--artifacts` flag lists the repo files involved.

### 6. Common things to check

**Numbers that should match:**
- Sample sizes (N in tables vs. data processing code)
- Coefficients and standard errors
- R-squared and other fit statistics
- Percentages and proportions mentioned in text

**Methodology consistency:**
- Estimator used (OLS, WLS, SUR, IV, etc.)
- Standard error computation (clustered, robust, bootstrap)
- Fixed effects specification
- Weight construction
- Variable transformations (log, normalize, etc.)

**Figure reproduction:**
- Axis ranges and labels
- Data points or distributions
- Regression lines and confidence intervals

**Data pipeline:**
- Raw data → cleaned data: are exclusions documented?
- Are there intermediate files that could be stale?
- Are random seeds set for reproducibility?

### 7. Report findings

After reviewing, summarize:
- How many scripts/files were reviewed
- How many claims were verifiable
- What issues were filed
- What could not be verified and why (missing data, proprietary code, etc.)
