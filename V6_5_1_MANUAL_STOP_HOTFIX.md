# Liquidity Vision v6.5.1 — Manual Stop Hotfix

## Fixed
- `/trade <id> stop` manually stops tracking again.
- Added aliases: `close`, `cancel`, `стоп`, `закрыть`, `отмена`.
- Manual stops are stored as `INVALIDATED` with `result=MANUAL_STOP`.
- Manual stops do not increase Stop Loss statistics.
- Trade replay includes the `MANUAL_STOP` lifecycle event.
- Invalid syntax now shows both replay and stop command examples.

## Validation
- Full test suite: 27 passed.
