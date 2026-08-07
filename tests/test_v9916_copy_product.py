from __future__ import annotations

from datetime import datetime, timezone

import pytest


def signal(signal_id=1701, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    value = {
        "id": signal_id, "symbol": "BTCUSDT", "timeframe": "5m", "side": "LONG",
        "status": "ACTIVE", "created_at": now, "updated_at": now, "activated_at": now,
        "entry": 100.0, "current_price": 100.0, "stop": 95.0,
        "tp1": 110.0, "tp2": 115.0, "tp3": 120.0, "rr": 2.0,
        "confidence": 75.0, "dynamic_confidence": 75.0, "setup_key": "breakout",
        "features_json": "{}",
    }
    value.update(overrides)
    return value


@pytest.fixture()
def copy_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "copy-product.db")
    monkeypatch.setenv("PORTFOLIO_ACCOUNTING_SOURCE", "UNIFIED")
    monkeypatch.setenv("COPY_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PAPER_MIN_NOTIONAL_USDT", "5")
    from database.database import create_tables
    create_tables()


def test_profile_templates_custom_overrides_and_audit(copy_db):
    from database.database import connect
    from services.copy_trading import CopyTradingService
    service = CopyTradingService()
    conservative = service.select_profile(41, "conservative")
    assert conservative["profile_name"] == "CONSERVATIVE"
    assert conservative["risk_pct"] == .25 and conservative["max_positions"] == 2
    custom = service.update_profile(41, risk_pct=.4, max_portfolio_exposure_pct=55)
    assert custom["profile_name"] == "CUSTOM" and custom["risk_pct"] == .4
    with connect() as conn:
        events = conn.execute("SELECT * FROM copy_profile_events WHERE telegram_id=41 ORDER BY id").fetchall()
    assert len(events) == 2 and events[-1]["event_type"] == "PROFILE_UPDATED"


def test_filters_normalize_symbols_and_reject_unapproved_signal(copy_db):
    from services.copy_trading import CopyTradingService
    service = CopyTradingService()
    profile = service.update_profile(
        42, enabled=1, auto_copy=1, symbol_policy="WHITELIST",
        symbol_whitelist_json=["BTC-USDT"], symbol_blacklist_json=["DOGE/USDT"],
        timeframe_filters_json=["5M"], setup_filters_json=["Breakout"],
        direction_filters_json=["long"],
    )
    risk = service._risk_profile(profile)
    allowed = service.validator.validate(signal=signal(symbol="BTC/USDT"), profile=risk,
                                         balance=10_000)
    assert allowed.allowed
    assert service.validator.validate(signal=signal(symbol="DOGE-USDT"), profile=risk,
                                      balance=10_000).code == "SYMBOL_BLACKLISTED"
    assert service.validator.validate(signal=signal(symbol="ETHUSDT"), profile=risk,
                                      balance=10_000).code == "SYMBOL_NOT_WHITELISTED"
    assert service.validator.validate(signal=signal(timeframe="1h"), profile=risk,
                                      balance=10_000).code == "TIMEFRAME_FILTERED"


def test_equity_and_proportional_sizing_fail_closed_without_source(copy_db):
    from services.execution_models import PositionSizingMode, RiskProfile
    from services.execution_validator import ExecutionValidator
    validator = ExecutionValidator()
    equity = validator.validate(signal=signal(), profile=RiskProfile(
        sizing_mode=PositionSizingMode.EQUITY_PERCENT, equity_pct=10,
        max_notional_pct=100, max_portfolio_exposure_pct=100), balance=10_000)
    assert equity.allowed and equity.size.notional == pytest.approx(1000)
    missing = validator.validate(signal=signal(), profile=RiskProfile(
        sizing_mode=PositionSizingMode.COPY_MULTIPLIER, copy_multiplier=.5,
        max_notional_pct=100), balance=10_000)
    assert not missing.allowed and missing.code == "SOURCE_SIZE_MISSING"
    proportional = validator.validate(signal=signal(source_notional=400), profile=RiskProfile(
        sizing_mode=PositionSizingMode.COPY_MULTIPLIER, copy_multiplier=.5,
        max_notional_pct=100), balance=10_000)
    assert proportional.allowed and proportional.size.notional == pytest.approx(200)


def test_daily_loss_and_portfolio_exposure_guards(copy_db):
    from services.execution_models import PortfolioState, RiskProfile
    from services.execution_validator import ExecutionValidator
    validator = ExecutionValidator()
    daily = validator.validate(signal=signal(), profile=RiskProfile(
        paper_balance=10_000, daily_loss_pct=2), balance=9_000,
        portfolio=PortfolioState(daily_realized_pnl=-200))
    assert daily.code == "DAILY_LOSS_LIMIT"
    exposure = validator.validate(signal=signal(), profile=RiskProfile(
        max_notional_pct=100, max_portfolio_exposure_pct=50), balance=10_000,
        portfolio=PortfolioState(unified_gross_notional=5_000))
    assert exposure.code == "MAX_PORTFOLIO_EXPOSURE"


def test_automatic_paper_path_is_idempotent_and_multi_user_isolated(copy_db):
    from database.database import connect
    from services.copy_trading import CopyTradingService
    service = CopyTradingService()
    service.update_profile(51, enabled=1, auto_copy=0)
    service.update_profile(52, enabled=1, auto_copy=1)
    item = signal()
    first = service.sync_signal(item)
    assert first["opened"] == 1 and first["skipped"] == 1
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders WHERE telegram_id=51").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders WHERE telegram_id=52").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_fills WHERE telegram_id=52").fetchone()["n"] == 1
    service.update_profile(51, enabled=1, auto_copy=1)
    second = service.sync_signal(item)
    replay = service.sync_signal(item)
    assert second["opened"] == 1 and replay["opened"] == 0
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 2
        positions = conn.execute("SELECT telegram_id,status FROM paper_execution_positions ORDER BY telegram_id").fetchall()
    assert [(row["telegram_id"], row["status"]) for row in positions] == [(51, "OPEN"), (52, "OPEN")]
    assert service.panic(51) == 1
    with connect() as conn:
        user1 = conn.execute("SELECT status,close_reason FROM paper_execution_positions WHERE telegram_id=51").fetchone()
        user2 = conn.execute("SELECT status FROM paper_execution_positions WHERE telegram_id=52").fetchone()
    assert user1["status"] == "CLOSED" and user1["close_reason"] == "PANIC_CLOSE"
    assert user2["status"] == "OPEN"


def test_private_signal_cannot_open_for_another_user_but_global_signal_can(copy_db):
    from database.database import connect
    from services.copy_trading import CopyTradingService
    service = CopyTradingService()
    service.update_profile(53, enabled=1)
    service.update_profile(54, enabled=1)
    private = signal(signal_id=1710, owner_telegram_id=53)
    result = service.sync_signal(private)
    assert result["opened"] == 1 and result["skipped"] == 1
    global_result = service.sync_signal(signal(signal_id=1711, owner_telegram_id=None, symbol="ETHUSDT"))
    assert global_result["opened"] == 2
    with connect() as conn:
        private_users = conn.execute(
            "SELECT telegram_id FROM paper_execution_positions WHERE signal_id=1710"
        ).fetchall()
    assert [row["telegram_id"] for row in private_users] == [53]


def test_automatic_lifecycle_tp_replay_and_stop_are_exactly_once(copy_db):
    from database.database import connect
    from services.copy_trading import CopyTradingService
    service = CopyTradingService()
    service.update_profile(61, enabled=1, auto_copy=1)
    item = signal(signal_id=1702)
    assert service.sync_signal(item)["opened"] == 1
    service.update_profile(61, enabled=0, auto_copy=0)
    tp1 = signal(signal_id=1702, status="TP1", current_price=110, result=None)
    service.sync_signal(tp1)
    service.sync_signal(tp1)
    stopped = signal(signal_id=1702, status="STOP", current_price=95, result="STOP",
                     closed_at=datetime.now(timezone.utc).isoformat())
    service.sync_signal(stopped)
    service.sync_signal(stopped)
    with connect() as conn:
        position = conn.execute("SELECT * FROM paper_execution_positions WHERE telegram_id=61").fetchone()
        lifecycle = conn.execute("SELECT * FROM paper_position_lifecycle_events WHERE position_id=? ORDER BY id",
                                 (position["id"],)).fetchall()
        ledger = conn.execute("SELECT * FROM paper_portfolio_ledger WHERE position_id=?",
                              (position["id"],)).fetchall()
    assert position["status"] == "CLOSED" and position["remaining_fraction"] == 0
    assert position["realized_r"] == pytest.approx(.5)
    assert position["total_commission"] == pytest.approx(1.0125)
    assert [row["event_type"] for row in lifecycle].count("PARTIAL_CLOSED") == 1
    assert [row["event_type"] for row in lifecycle].count("CLOSED") == 1
    assert [row["entry_type"] for row in ledger].count("COMMISSION") == 3
    assert len({row["source_key"] for row in ledger}) == len(ledger)


def test_global_copy_disable_blocks_new_entries_but_not_existing_lifecycle(copy_db, monkeypatch):
    from database.database import connect
    from services.copy_trading import CopyTradingService
    service = CopyTradingService()
    service.update_profile(71, enabled=1, auto_copy=1)
    monkeypatch.setenv("COPY_EXECUTION_ENABLED", "false")
    assert service.sync_signal(signal(signal_id=1703))["opened"] == 0
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0


def test_sync_all_always_includes_old_signals_with_open_positions(copy_db, monkeypatch):
    from database.database import connect
    from services.copy_trading import CopyTradingService
    service = CopyTradingService()
    service.update_profile(72, enabled=1)
    old = signal(signal_id=10)
    with connect() as conn:
        conn.execute("""INSERT INTO signals(id,symbol,timeframe,side,status,created_at,updated_at,
            entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,recommendation,
            setup_key,features_json,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            10, "BTCUSDT", "5m", "LONG", "ACTIVE", old["created_at"], old["updated_at"],
            100, 95, 110, 115, 120, 2, 75, 75, 25, "READY", "breakout", "{}", "[]",
        ))
    assert service.sync_signal(old)["opened"] == 1
    service.update_profile(72, enabled=0)
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("UPDATE signals SET status='STOP',result='STOP',closed_at=? WHERE id=10", (now,))
        for signal_id in range(1000, 1500):
            conn.execute("""INSERT INTO signals(id,symbol,timeframe,side,status,created_at,updated_at,
                entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,recommendation,
                setup_key,features_json,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                signal_id, "ETHUSDT", "5m", "LONG", "EXPIRED", now, now,
                100, 95, 110, 115, 120, 2, 70, 70, 30, "READY", "test", "{}", "[]",
            ))
    seen = []

    def record(item, *, profiles=None):
        seen.append(int(item["id"]))
        return {"opened": 0, "updated": 0, "closed": 0, "rejected": 0, "skipped": 1}

    monkeypatch.setattr(service, "sync_signal", record)
    service.sync_all()
    assert 10 in seen and len(seen) == 501
