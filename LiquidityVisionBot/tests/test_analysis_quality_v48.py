import unittest

from services.analyzer import Analyzer


class AnalysisQualityV48Test(unittest.TestCase):
    def setUp(self):
        self.analyzer = Analyzer()
        self.raw = {
            "price": 100.0,
            "trend": "🟢 Bullish",
            "structure": "🟡 Range",
            "bos": "⚪ No BOS",
            "choch": "🟡 No CHOCH",
            "liquidity": "🟢 Equal Lows (98.0)",
            "sweep": "⚪ Internal Liquidity (bullish)",
            "order_block": "⚪ No Active Order Block",
            "breaker": "🟢 Bullish Breaker (98 - 101)",
            "mitigation": "⚪ No Mitigation Block",
            "fvg": "🟢 Bullish FVG (97 - 99)",
            "premium": {"zone": "🔴 Premium", "premium": 82.0, "low": 90, "equilibrium": 95, "high": 101},
            "volume": "⚪ Low Volume (0.25x)",
            "volume_ratio": 0.25,
            "displacement": "⚪ Weak Bearish Displacement (40%, 0.48x)",
            "atr": {"atr": 2.0},
            "rsi": 62.0,
            "macd_bullish": False,
        }

    def test_premium_and_low_volume_do_not_erase_bullish_direction(self):
        score, _, _, _, _ = self.analyzer._direction_score("LONG", self.raw)
        self.assertGreaterEqual(score, 55)

    def test_bad_location_reduces_entry_not_market_direction(self):
        score, _, _, _, _ = self.analyzer._direction_score("LONG", self.raw)
        execution = self.analyzer._execution_metrics("LONG", self.raw, score, 4.0, 20.0)
        self.assertGreater(score, execution["entry_quality"])
        self.assertNotEqual(execution["execution_bias"], "LONG NOW")

    def test_breakdown_and_verdict_fields_exist_on_full_analysis(self):
        from tests.test_analyzer_regression import make_frame

        result = self.analyzer.analyze(make_frame())
        self.assertIn("direction_breakdown", result)
        self.assertIn("strongest_drivers", result)
        self.assertIn("biggest_blockers", result)
        self.assertIn("execution_bias", result)
        self.assertIn("final_verdict", result)


if __name__ == "__main__":
    unittest.main()
