# Derivatives Alpha Master

This cycle begins after the completed OHLCV reconstruction concluded `NO VERIFIED EDGE`. Candidate `f2262e97c9019f72` remains rejected and is not tuned.

The research architecture is deliberately separated:

`public immutable derivatives data -> integrity -> causal alignment -> PositioningStateEngine -> research family -> canonical replay -> DecisionQuality evidence gate`

`PositioningStateEngine` is descriptive and has no order, execution, scheduler, production-default, LIVE, PAPER, or copy-trading authority. A historical candidate cannot bypass DecisionQuality or portfolio/risk gates. Required derivatives data fail closed.

The primary cycle is bounded to three symbols, one-hour cadence, four mechanism families, twelve frozen variants, five Development folds, one possible Validation candidate, and one possible Blind access. Liquidation, CVD, and order-book ideas are deferred unless a separately certified history exists.

Current phase status and all results are maintained in `DERIVATIVES_ALPHA_RESULTS.md`. Production behavior remains unchanged.
