import unittest

import pandas as pd

from weekly_harness.backtest import PerformanceMetrics


class PerformanceMetricsTests(unittest.TestCase):
    def test_annual_return_uses_calendar_day_duration(self):
        result = PerformanceMetrics.annual_return(total_return=10.0, calendar_days=365)

        self.assertAlmostEqual(result, 10.0)

    def test_max_drawdown_reports_worst_peak_to_trough_loss(self):
        nav = pd.Series([100.0, 120.0, 90.0, 95.0, 130.0])

        self.assertAlmostEqual(PerformanceMetrics.max_drawdown(nav), -25.0)

    def test_sharpe_ratio_handles_flat_returns(self):
        returns = pd.Series([0.0, 0.0, 0.0])

        self.assertEqual(PerformanceMetrics.sharpe_ratio(returns), 0.0)

    def test_profit_factor_handles_no_losses(self):
        trades = [{"profit": 100}, {"profit": 50}]

        self.assertEqual(PerformanceMetrics.profit_factor(trades), float("inf"))


if __name__ == "__main__":
    unittest.main()
