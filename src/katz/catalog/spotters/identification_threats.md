---
scope: holistic
---
# Identification Threats

Look for threats to the causal identification strategy that the paper does not adequately address.

Pay special attention to:
- Omitted variable bias: are there plausible confounders not controlled for?
- Reverse causality: could the outcome cause the treatment rather than vice versa?
- Selection on unobservables: could unobserved factors drive both treatment and outcome?
- Violation of exclusion restrictions in IV designs
- Parallel trends assumption violations in difference-in-differences
- Manipulation or sorting around cutoffs in regression discontinuity
- SUTVA violations: could treatment of one unit affect outcomes of another?
- Generalizability: does the local average treatment effect (LATE) speak to the policy-relevant parameter?

## Investigation

- Read the identification strategy section and any formal assumptions stated
- Check robustness sections and appendices for sensitivity analyses
- Determine if the threat is acknowledged and tested, acknowledged but untested, or unacknowledged
- Assess materiality: would the threat plausibly change the sign or magnitude of the main results?
- **Confirm** if the threat is material and unaddressed
- **Reject** if the paper adequately addresses it or the threat is implausible in context
