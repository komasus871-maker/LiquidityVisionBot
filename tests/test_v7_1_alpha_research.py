from services.data_integrity import DataIntegrityEngine
from services.alpha_research import AlphaResearchEngine


def _plan():
    return {
        "direction": "LONG",
        "entry": 100.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "tp3": 115.0,
    }


def test_plan_rejects_entry_outside_zone():
    plan = _plan()
    plan["entry"] = 80.0
    result = DataIntegrityEngine.validate_plan(plan)
    assert not result.valid
    assert result.code == "INVALID_GEOMETRY" or result.code == "ENTRY_OUTSIDE_ZONE"


def test_activation_rejects_extreme_deviation():
    signal = _plan()
    result = DataIntegrityEngine(max_activation_deviation_pct=3).validate_activation(signal, 198.0)
    assert not result.valid
    assert result.code == "ACTIVATION_PRICE_DEVIATION"


def test_activation_allows_normal_zone_fill():
    signal = _plan()
    result = DataIntegrityEngine().validate_activation(signal, 100.8)
    assert result.valid


def test_alpha_metrics_profit_factor_and_drawdown():
    rows = [
        {"realized_r": 1.0, "mfe_pct": 2.0, "mae_pct": -0.5},
        {"realized_r": -1.0, "mfe_pct": 0.2, "mae_pct": -1.2},
        {"realized_r": 2.0, "mfe_pct": 3.0, "mae_pct": -0.3},
    ]
    metrics = AlphaResearchEngine.metrics(rows)
    assert metrics.trades == 3
    assert round(metrics.expectancy_r, 4) == round(2 / 3, 4)
    assert metrics.profit_factor == 3.0
    assert metrics.max_drawdown_r == 1.0


def test_integrity_rejected_rows_are_not_usable():
    engine = AlphaResearchEngine()
    signals = [
        {"id": 1, "status": "INVALIDATED", "result": "DATA_INTEGRITY_REJECTED", "features_json": "{}"},
        {"id": 2, "status": "STOP", "result": "STOP", "realized_r": -1, "features_json": "{}"},
    ]
    rows = engine.dataset(signals, usable_only=True)
    assert [row["signal_id"] for row in rows] == [2]
