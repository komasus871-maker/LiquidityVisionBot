# v9.8.7 — Authenticated Safety Core

This release combines the planned v9.8.6 authenticated read-only milestone with the v9.8.7 execution-safety foundation.

## Authenticated account snapshots

`ExchangeManager` now performs a fail-closed private-account health check and then captures balances, positions, and open orders as one normalized snapshot. Credentials remain environment-only, adapters are always closed, and partial/public-only access is never presented as an authenticated account.

Telegram command:

```text
/exchange_account okx
/exchange_account bingx
```

API keys must have read permissions only. Withdrawal permission must remain disabled.

## Execution safety preflight

The new pure `ExecutionSafetyValidator` validates a proposed order without sending it. It enforces:

- demo-only operation by default;
- explicit global LIVE lock;
- symbol whitelist;
- maximum notional;
- maximum leverage;
- maximum open positions;
- minimum quantity and minimum notional;
- price-tick and quantity-step alignment;
- duplicate open-order rejection.

Commands:

```text
/exchange_safety
/exchange_preflight bingx BTCUSDT BUY 0.001 60000 3
```

## Safety boundary

No adapter exposes create, amend, cancel, leverage-change, or position-close methods. A successful preflight means only that an intent satisfies configured limits; it does not execute anything.
