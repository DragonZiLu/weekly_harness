import unittest

import pandas as pd

from weekly_harness.backtest import BacktestEngine


class BacktestDividendAttributionTests(unittest.TestCase):
    def test_does_not_pay_past_dividend_to_late_buyer(self):
        engine = BacktestEngine(initial_cash=100_000, commission_rate=0.0, slippage=0.0)
        engine._stock_meta = {"600000.SH": {"name": "Test Bank"}}
        engine._dividend_data = {
            "600000.SH": pd.DataFrame([
                {"ts_code": "600000.SH", "ex_date": "20260110", "cash_per_share": 1.0}
            ])
        }

        engine.portfolio.set_date("2026-01-20")
        engine.portfolio.buy(
            "600000.SH",
            "Test Bank",
            "弱周期红利",
            price=10.0,
            target_amount=10_000,
        )
        cash_after_buy = engine.portfolio.cash

        engine._process_dividends("2026-01-31")

        self.assertAlmostEqual(engine.portfolio.cash, cash_after_buy)
        self.assertEqual(engine.portfolio.dividend_records, [])
        self.assertIn(("600000.SH", "20260110"), engine._processed_dividends)

    def test_pays_only_shares_held_before_ex_date(self):
        engine = BacktestEngine(initial_cash=100_000, commission_rate=0.0, slippage=0.0)
        engine._stock_meta = {"600000.SH": {"name": "Test Bank"}}
        engine._dividend_data = {
            "600000.SH": pd.DataFrame([
                {"ts_code": "600000.SH", "ex_date": "20260115", "cash_per_share": 1.0}
            ])
        }

        engine.portfolio.set_date("2026-01-01")
        engine.portfolio.buy("600000.SH", "Test Bank", "弱周期红利", price=10.0, target_amount=10_000)
        engine.portfolio.set_date("2026-01-20")
        engine.portfolio.buy("600000.SH", "Test Bank", "弱周期红利", price=10.0, target_amount=10_000)

        engine._process_dividends("2026-01-31")

        self.assertEqual(len(engine.portfolio.dividend_records), 1)
        self.assertEqual(engine.portfolio.dividend_records[0]["shares"], 1000)
        self.assertAlmostEqual(engine.portfolio.dividend_records[0]["net_dividend"], 1000)


if __name__ == "__main__":
    unittest.main()
