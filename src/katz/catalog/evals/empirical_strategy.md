---
category: research-design
scope: methods
---
# Empirical Strategy

Is the empirical strategy credible and thoroughly validated? The appropriate checks depend on the design. Evaluate whichever apply:

**Identification:**
- What is the source of identifying variation? Is it clearly stated?

**Panel data / difference-in-differences:**
- Are pre-existing trends examined? Are event-study plots or pre-trend tests shown?

**Regression discontinuity:**
- Are covariates shown to be continuous around the threshold? Is there a McCrary density test or equivalent?

**Matching:**
- Is balance demonstrated on covariates not used in the matching procedure?

**Instrumental variables:**
- Is the first stage discussed? Are Stock and Yogo first-stage F-statistics presented?
- If multiple instruments, is Hansen's J-test for overidentifying restrictions conducted?
- If the exclusion restriction can be explored (e.g., testing whether instruments predict outcomes through channels other than the endogenous variable), is it explored?

**Experiments:**
- Is statistical power discussed? Is the sample large enough to detect effects of the expected magnitude?

**General concerns:**
- Is there any attempt to control for or condition on variables that could be affected by the treatment of interest (bad controls, per Rosenbaum 1984; Elwert and Winship 2014)?
- Are all estimating equations shown in the text before results are presented?
