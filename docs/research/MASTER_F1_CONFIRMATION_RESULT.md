# MASTER F1 Independent Confirmation Result

## Classification

`F1_NOT_CONFIRMED`

The evidence minimum was satisfied: frozen F1 produced 124 simulated fills and
101 same-time/same-direction fill clusters, above the preregistered 60-fill and
30-cluster thresholds. The result is therefore not
`F1_INSUFFICIENT_NEW_EVIDENCE`.

F1 nevertheless failed the immutable qualification contract. Aggregate
expectancy was `-0.018310R`; `CONFIRM_A` and `CONFIRM_C` were catastrophic;
higher-cost expectancy was `-0.054424R`; only one chronological block was
positive; and the dominance, symbol-net-R, cluster, and higher-cost block-floor
checks failed. No requirement was weakened or reinterpreted.

Per the preregistered branch, `BLIND_HOLDOUT` remains locked and
`LEGACY_SEEN_TEST` remains inaccessible for candidate selection. F1 is frozen
as a rejected experiment and is not a production, PAPER, SHADOW, LIVE, or
copy-trading strategy.

## Durable identity and reconciliation

- F1: `F1_COUNTERTREND_REVERSAL_LONG` / `c49803034d94792e`.
- Result artifact: `f1-confirmation-65b1dde9784282f9.json`.
- Result artifact bytes: `154,027,103`.
- Result file SHA-256:
  `eaee9ce810386b98a55feade9b34f73b9ed94df3d4ebdaff59b91703700e106c`.
- Canonical payload digest: `65b1dde9784282f9`, matching the filename.
- Independent validation artifact:
  `f1-confirmation-validation-65b1dde9784282f9.json`.
- Base-cost canonical resimulation parity: BTC `true`, ETH `true`, SOL `true`.
- Validation errors: none.

All three 8,760-candle immutable CSVs re-hash exactly to their preregistered
content hashes:

| Symbol | Dataset | Content SHA-256 |
| --- | --- | --- |
| BTC | `a13e4c39db84cea9` | `a13e4c39db84cea9e0e337fc6092246d81f7dfeb3ef59fa614b8cc3717520edc` |
| ETH | `a7136b6d4ab94d0c` | `a7136b6d4ab94d0cb2b9499630c9400d313bb39ceda180711cef18d9b2a791c8` |
| SOL | `7ca8216bd09d1f29` | `7ca8216bd09d1f294242a912c887026e383eb343d1008df69dbd83f76019757b` |

Checkpoint reconciliation is exact:

| Symbol | Decisions | Baseline outcomes | F1 outcomes | Checkpoint SHA-256 | Evaluation digest |
| --- | ---: | ---: | ---: | --- | --- |
| BTC | 8,106 | 908 | 83 | `54c6029f0faba2fa8c5510ff662069b6fe8a17261220fe51e1a58e9239993352` | `7698eda93fecb0851e84aa5808600d6c7e997319adc4c3d6ecd547b1e5b8e45a` |
| ETH | 8,106 | 533 | 30 | `aa486c481df97422b6bcc3fd1980c599b5e4fd94156b4b3a5bc5ffcf44731e16` | `c162c9ff1df942e749e6ac949dd6e3ea53b5a576277b9d2a63e72a4444a6c827` |
| SOL | 8,106 | 554 | 42 | `6c0f4704c5a8c4efedf7b401be9fcda708e5eebef91ef61b4d4a5ca236c57d1a` | `6dff2386dbf7cd9670ee02a568efde9ea6945d67e0533d93f2d9dfbaae1bc024` |

The preregistration narrative said “8,108 usable decision hours,” but its
immutable boundaries define three blocks of 2,702 hours, totaling 8,106. The
artifact follows the frozen timestamps exactly; the narrative total is an
arithmetic typo, not a boundary change.

## Aggregate results

| Measure | Frozen F1 | Baseline control |
| --- | ---: | ---: |
| Simulated fills | 124 | 1,601 |
| Wins / losses | 59 / 65 | 808 / 793 |
| Win rate | 47.5806% | 50.4685% |
| Expectancy-R | `-0.018310` | `+0.007402` |
| Net-PnL PF | `1.4392` | `1.2840` |
| Modeled net price-unit PnL | `13,530.15` | `92,432.67` |
| Maximum drawdown, price units | `11,238.99` | `26,801.27` |
| Average MFE / MAE | `0.5557R / 0.3824R` | `0.5310R / 0.3468R` |
| Average holding time | 10.75 bars | 8.24 bars |

The positive pooled price-unit PnL and PF do not override negative expectancy-R:
BTC, ETH, and SOL price units are not capital-normalized or interchangeable.
The preregistered contract explicitly requires positive aggregate expectancy-R.

## Chronological stability

| Block | F1 fills | WR | Expectancy-R | Net-PnL PF | Bootstrap 95% interval |
| --- | ---: | ---: | ---: | ---: | --- |
| `CONFIRM_A` | 35 | 48.57% | `-0.025853` | `0.4924` | `[-0.230039, +0.190078]R` |
| `CONFIRM_B` | 55 | 49.09% | `+0.011213` | `4.2989` | `[-0.452152, +0.477306]R` |
| `CONFIRM_C` | 34 | 44.12% | `-0.058303` | `0.8558` | `[-0.346157, +0.228744]R` |

Only `CONFIRM_B` was positive, and every uncertainty interval crosses zero.
`CONFIRM_A` fails both the expectancy and PF catastrophe clauses;
`CONFIRM_C` fails the expectancy catastrophe clause.

## Symbol stability

| Symbol | Fills | WR | Expectancy-R | Net-PnL PF |
| --- | ---: | ---: | ---: | ---: |
| BTC | 61 | 54.10% | `+0.096713` | `1.4539` |
| ETH | 24 | 41.67% | `-0.134585` | `0.8713` |
| SOL | 39 | 41.03% | `-0.126665` | `0.5572` |

BTC supplied 49.19% of fills, below the nominal fill-share cap, but it was the
only positive symbol. With aggregate net R negative, the preregistered
symbol-net-R diversification condition cannot pass.

## Cost sensitivity

| Scenario | Fee | Market slippage | Expectancy-R | Net-PnL PF |
| --- | ---: | ---: | ---: | ---: |
| Base | 0.050% | 0.030% | `-0.018310` | `1.4392` |
| Higher | 0.075% | 0.050% | `-0.054424` | `1.3287` |
| Stress | 0.100% | 0.100% | `-0.090538` | `1.2274` |

Funding remains `NOT_MODELED`. Under higher costs, `CONFIRM_A` has PF `0.4576`,
below the immutable 0.75 block floor.

## Correlation-adjusted evidence

The 124 nominal fills reduce to 101 same-time/same-direction clusters. Eighteen
clusters contain correlated multi-symbol fills, covering 41 fills; maximum
cluster size is three. Aggregate cluster net R is `-2.270480R`, so the positive
single-cluster concentration rule cannot pass.

## Strict failed checks

- `AGGREGATE_EXPECTANCY`
- `NO_CATASTROPHIC_BLOCK`
- `HIGHER_COST_EXPECTANCY`
- `POSITIVE_TEMPORAL_BLOCKS`
- `NO_DOMINANT_BLOCK`
- `SYMBOL_DIVERSIFICATION`
- `CLUSTER_DIVERSIFICATION`
- `COST_BLOCK_FLOOR`

The aggregate PF check passed, as did the new-evidence minimum and canonical
replay-integrity prerequisite. They are insufficient to rescue the failed
contract.
