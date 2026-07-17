# LiquidityVisionBot v7.9.1 — Intelligence Stabilization

- Hard lifecycle exits are evaluated before live intelligence.
- Closed/invalidated trades cannot emit health or confidence updates.
- Dynamic confidence uses 70/30 smoothing with a 10-point cycle cap.
- Confidence alerts require a meaningful delta or a 40/60/80 threshold crossing.
- Direction and readiness remain independent throughout Analyzer and Scanner.
- Scanner displays Unified Decision as the canonical action.
- Explain Pro deduplicates reasons and uses Direction / Execution / Unified Decision.
- Unreliable historical TP percentages are hidden.
- Similarity cases below 50% are not presented as meaningful closest analogues.
