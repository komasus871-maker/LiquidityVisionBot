# Signal Quality V2

Signal Quality V2 answers “how coherent and executable is this setup?” It does not estimate the probability of profit.

## Families

The research score uses Market Quality, Structure, Liquidity, Location, Momentum, Volatility, Microstructure, HTF Context, Relative Strength, Invalidation, Target Realism, Execution Cost and Portfolio Context.

Multiple factors inside one family use shrinkage: 65% of the strongest factor plus 35% of the family mean. This prevents several EMA descriptions or several momentum oscillators from receiving independent full weight. The stored raw components and family aggregate make the score auditable.

## Contradictions

Supporting evidence, contradicting evidence, critical disqualifiers and uncertainties are stored separately. LOW/MEDIUM/HIGH contradictions apply nonlinear penalties. CRITICAL disqualifiers cap overall quality at 35 even when other families are strong.

Missing microstructure, BTC comparison, funding or open interest is uncertainty—not negative fabricated evidence.

## Confidence decomposition

Every snapshot stores:

- data confidence;
- direction confidence;
- setup confidence;
- entry confidence;
- invalidation confidence;
- target confidence;
- execution confidence;
- overall quality.

Invalidation quality evaluates side geometry, ATR-normalized distance, proximity to a meaningful swing and exposure to obvious clustered liquidity. Target research records realistic 1R, 1.5R, 2R, 2.5R and 3R bands and requires later ordered-path evaluation.

## Validation policy

Use `/quality_report` to inspect thresholds 50–90. Each row reports trades, winners, losers, expectancy, missed winners, avoided losses and a drawdown proxy. No threshold may be promoted from the current small sample. Formal evaluation requires decision-time rows, pure-market outcomes, minimum samples, chronological splits and forward testing.

