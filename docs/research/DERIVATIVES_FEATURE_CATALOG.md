# Derivatives Feature Catalog

All features are research-only and causal at `T_decision`.

| Feature | Definition | Economic purpose |
|---|---|---|
| `oi_change_1h/3h/6h` | percent change in aligned OI USD stock | leverage expansion/deleveraging |
| `oi_zscore`, `oi_percentile` | 30-day rolling causal normalization | position size relative to recent history |
| `price_return_1h/6h` | closed-perpetual return | direction and confirmation |
| `price_oi_quadrant` | signs of six-hour price and OI changes | distinguish new positioning from covering/liquidation |
| `funding_rate` | last causally known settled rate | observed crowding price |
| `funding_change` | change in settled rate | crowding acceleration |
| `funding_zscore/percentile` | causal 30-day normalization | exchange/regime-adaptive extremes |
| `basis_pct` | perpetual close / index close - 1 | leverage-demand dislocation |
| `spot_basis_pct` | perpetual close / spot close - 1 | venue-coherent cash/perp dislocation |
| `basis_change/zscore/percentile` | causal changes/normalization | expansion or compression |
| `price_oi_interaction` | six-hour price return times OI change | signed participation interaction |
| archived long/short and taker ratios | source-provided ratios | descriptive diagnostics only in primary cycle |

Forward returns at 1/3/6/12/24 bars, MFE, and MAE are Development labels only. They are never feature columns or runtime inputs.
