# Liquidity Vision v4.0.2 — Stability Fix

- Fixed `NameError: ob_ok is not defined` in execution/reversal evaluation.
- Added the missing OB, Breaker and FVG alignment flags inside `_execution_metrics`.
- Added an analyzer regression test that exercises the previously failing branch.
- Removed an accidental nested duplicate copy of the project from the release archive.
- Removed local Git/runtime database artifacts from the distributable archive.
- Added Observation ID to the analysis report for easier lifecycle debugging.
- Ran static undefined-name checks, compilation and synthetic analyzer tests.
- Removed two unused legacy modules that imported a non-existent `services.brain` and could fail tooling/import scans.
