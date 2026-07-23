# LiquidityVisionBot v9.9.1 — BingX Hedge Execution Hotfix

BingX returns error `109400` when the `reduceOnly` field is present in hedge mode, even when set to `false`.

The adapter now omits `reduceOnly` for ordinary opening orders and sends `reduceOnly=true` only for explicitly reduce-only requests.

The multi-user credential boundary introduced in v9.9.0 remains unchanged.
