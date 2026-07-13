import pandas as pd

from core.backtest_engine import BacktestConfig, BacktestEngine
from services.loss_forensics import LossForensicsEngine
from services.validation_gate import ValidationGate


def candles(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_backtest_executes_tp_lifecycle_and_costs():
    frame = candles(
        [(100, 101, 99, 100)] * 20
        + [
            (100, 100.5, 99.5, 100),
            (100, 102.2, 99.8, 102),
            (102, 104.2, 101.5, 104),
            (104, 106.2, 103.5, 106),
        ]
    )

    def strategy(_):
        return {
            "direction": "LONG",
            "entry": 100,
            "stop": 98,
            "tp1": 102,
            "tp2": 104,
            "tp3": 106,
            "execution_readiness": 82,
            "opportunity_category": "READY_NOW",
            "market_regime": {"code": "TRENDING"},
        }

    report = BacktestEngine(
        BacktestConfig(warmup_bars=20, fee_rate=0.0005, slippage_rate=0.0002)
    ).run(frame, strategy)
    assert report.metrics["trades"] == 1
    trade = report.trades[0]
    assert trade.status == "TP3"
    assert trade.gross_r == 1.85  # 40%*1R + 35%*2R + 25%*3R
    assert trade.net_r < trade.gross_r
    assert report.by_regime["TRENDING"]["profit_factor"] == float("inf")


def test_conservative_intrabar_policy_counts_stop_first():
    frame = candles([(100, 101, 99, 100)] * 20 + [(100, 103, 97, 100)])

    def strategy(_):
        return {
            "direction": "LONG", "entry": 100, "stop": 98,
            "tp1": 102, "tp2": 104, "tp3": 106,
            "execution_readiness": 80, "opportunity_category": "READY_NOW",
            "market_regime": {"code": "TRENDING"},
        }

    trade = BacktestEngine(BacktestConfig(warmup_bars=20, fee_rate=0, slippage_rate=0)).run(frame, strategy).trades[0]
    assert trade.status == "STOP"
    assert trade.gross_r == -1.0


def test_rejects_non_executable_signal():
    frame = candles([(100, 101, 99, 100)] * 25)

    def strategy(_):
        return {
            "direction": "LONG", "entry": 100, "stop": 98,
            "tp1": 102, "tp2": 104, "tp3": 106,
            "execution_readiness": 58, "opportunity_category": "REGIME_BLOCKED",
            "market_regime": {"code": "RANGING"},
        }

    report = BacktestEngine(BacktestConfig(warmup_bars=20)).run(frame, strategy)
    assert report.signals_rejected > 0
    assert report.metrics["trades"] == 0


def test_loss_forensics_detects_chop_and_weak_admission():
    diagnosis = LossForensicsEngine().diagnose(
        {
            "entry_index": 10,
            "net_r": -1.1,
            "regime": "RANGING",
            "readiness": 71,
            "mfe_r": 0.1,
            "mae_r": 1.0,
            "bars_held": 3,
            "metadata": {"entry_quality": 55, "risk_quality": 60, "directional_edge": 9},
        }
    )
    assert diagnosis.classification == "CHOP_FALSE_BREAKOUT"
    assert diagnosis.severity in {"HIGH", "CRITICAL"}


def test_validation_gate_keeps_unproven_system_in_paper_mode():
    gate = ValidationGate(minimum_trades=100)
    decision = gate.evaluate({"trades": 5, "profit_factor": 0, "expectancy_r": -1, "max_drawdown_r": 5})
    assert decision.mode == "PAPER"
    assert not decision.live_allowed


def test_validation_gate_allows_only_validated_metrics():
    decision = ValidationGate().evaluate(
        {"trades": 150, "profit_factor": 1.4, "expectancy_r": 0.12, "max_drawdown_r": 8}
    )
    assert decision.mode == "LIVE_VALIDATED"
    assert decision.live_allowed
