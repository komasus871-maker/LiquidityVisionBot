import unittest

import numpy as np
import pandas as pd

from services.analyzer import Analyzer


def make_frame(seed: int = 7, drift: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = 260
    returns = rng.normal(drift, 0.006, rows)
    close = 100 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.001, rows))
    high = np.maximum(open_, close) * (1 + rng.uniform(0.0005, 0.006, rows))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.0005, 0.006, rows))
    volume = rng.lognormal(8, 0.7, rows)
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=rows, freq="h", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "confirm": ["1"] * rows,
    })


class AnalyzerRegressionTest(unittest.TestCase):
    def test_execution_metrics_does_not_reference_undefined_zone_flags(self):
        result = Analyzer().analyze(make_frame())
        self.assertIn("execution_status", result)
        self.assertIn("opportunity_category", result)
        self.assertIn(result["direction"], {"LONG", "SHORT"})


if __name__ == "__main__":
    unittest.main()
