from __future__ import annotations

import importlib


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_PERSISTENT_DB", "false")
    import database.database as db
    import services.performance_intelligence as pi
    importlib.reload(db)
    importlib.reload(pi)
    db.create_tables()
    return db, pi.PerformanceIntelligence()


def _insert(db, *, status, realized_r, symbol="BTC", timeframe="1h", side="LONG", active=False, current=105):
    now = "2026-07-18T12:00:00+00:00"
    activated = now
    closed = None if active else now
    with db.connect() as conn:
        conn.execute("""INSERT INTO signals(
            owner_telegram_id,notification_chat_id,symbol,timeframe,side,status,
            created_at,updated_at,activated_at,closed_at,entry,stop,tp1,tp2,tp3,
            rr,confidence,bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,
            realized_r,result,current_price
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            1,1,symbol,timeframe,side,status,now,now,activated,closed,
            100,90,110,120,130,3,70,70,30,"TEST",f"{symbol}-{status}-{realized_r}","{}","[]",
            realized_r,status,current,
        ))


def test_performance_computes_expectancy_and_profit_factor(tmp_path, monkeypatch):
    db, engine = _fresh(tmp_path, monkeypatch)
    _insert(db, status="TP3", realized_r=3.0)
    _insert(db, status="STOP", realized_r=-1.0)
    report = engine.performance(1)
    assert report["trades"] == 2
    assert report["win_rate"] == 50.0
    assert report["net_r"] == 2.0
    assert report["expectancy"] == 1.0
    assert report["profit_factor"] == 3.0


def test_portfolio_detects_direction_and_symbol_concentration(tmp_path, monkeypatch):
    db, engine = _fresh(tmp_path, monkeypatch)
    for tf in ("15m", "1h", "4h"):
        _insert(db, status="ACTIVE", realized_r=None, symbol="BTC", timeframe=tf, side="LONG", active=True, current=105)
    report = engine.portfolio(1)
    assert report["count"] == 3
    assert report["dominant"] == "LONG"
    assert report["open_r"] == 1.5
    assert any("Directional concentration" in x for x in report["warnings"])
    assert any("Symbol concentration" in x for x in report["warnings"])


def test_trade_dna_finds_best_segment(tmp_path, monkeypatch):
    db, engine = _fresh(tmp_path, monkeypatch)
    _insert(db, status="TP3", realized_r=2.0, symbol="TAO", timeframe="15m", side="SHORT")
    _insert(db, status="MANUAL_STOP", realized_r=1.0, symbol="TAO", timeframe="15m", side="SHORT")
    _insert(db, status="STOP", realized_r=-1.0, symbol="BTC", timeframe="1h", side="LONG")
    dna = engine.dna(1)
    assert dna["best_symbol"].name == "TAO"
    assert dna["best_timeframe"].name == "15m"
    assert dna["best_side"].name == "SHORT"
