# Liquidity Vision v6.3 — Market Regime Engine

This release adds a regime filter that separates directional bias from whether
current market conditions are suitable for trend execution.

## Regimes
- Trending Long / Trending Short
- Ranging / Choppy
- Volatility Compression
- Volatile Expansion
- Transitional / Mixed

## Execution protection
A setup can only become READY when the regime is TRENDING, aligned with the
scenario direction, and volatility is not extreme. Ranging, compressed,
transitional and late-expansion conditions cap readiness and add explicit
activation requirements.

## New report data
- regime confidence
- trend strength
- price-path efficiency
- volatility percentile
- execution mode
- risk multiplier
