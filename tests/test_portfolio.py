import unittest

from weekly_harness.portfolio import Portfolio


class PortfolioCashFlowTests(unittest.TestCase):
    def test_buy_sell_and_dividend_cash_flows(self):
        portfolio = Portfolio(
            initial_cash=100_000,
            commission_rate=0.001,
            min_commission=5.0,
            stamp_tax_rate=0.001,
            slippage=0.0,
            lot_size=100,
        )
        portfolio.set_date("2026-01-02")

        buy = portfolio.buy(
            ts_code="600000.SH",
            name="Test Bank",
            category="弱周期红利",
            price=10.0,
            target_amount=20_000,
            reason="test buy",
        )

        self.assertIsNotNone(buy)
        self.assertEqual(buy.shares, 2000)
        self.assertAlmostEqual(buy.amount, 20_000)
        self.assertAlmostEqual(buy.commission, 20)
        self.assertAlmostEqual(portfolio.cash, 79_980)
        self.assertAlmostEqual(portfolio.total_value, 99_980)

        dividend = portfolio.receive_dividend(
            ts_code="600000.SH",
            name="Test Bank",
            cash_per_share=0.5,
            tax_rate=0.10,
        )

        self.assertIsNotNone(dividend)
        self.assertAlmostEqual(dividend["gross_dividend"], 1_000)
        self.assertAlmostEqual(dividend["tax"], 100)
        self.assertAlmostEqual(dividend["net_dividend"], 900)
        self.assertAlmostEqual(portfolio.cash, 80_880)

        sell = portfolio.sell("600000.SH", price=12.0, shares=1000, reason="test sell")

        self.assertIsNotNone(sell)
        self.assertEqual(sell.shares, 1000)
        self.assertAlmostEqual(sell.amount, 12_000)
        self.assertAlmostEqual(sell.commission, 24)
        self.assertEqual(portfolio.positions["600000.SH"].shares, 1000)
        self.assertAlmostEqual(portfolio.cash, 92_856)
        self.assertAlmostEqual(portfolio.total_value, 104_856)

    def test_buy_rounds_down_to_lot_size(self):
        portfolio = Portfolio(initial_cash=5_000, slippage=0.0, lot_size=100)
        portfolio.set_date("2026-01-02")

        trade = portfolio.buy("000001.SZ", "Test", "弱周期红利", price=12.3, target_amount=2_000)

        self.assertIsNotNone(trade)
        self.assertEqual(trade.shares, 100)

    def test_dividend_can_be_limited_to_eligible_shares(self):
        portfolio = Portfolio(initial_cash=100_000, slippage=0.0, lot_size=100)
        portfolio.set_date("2026-01-01")
        portfolio.buy("600000.SH", "Test Bank", "弱周期红利", price=10.0, target_amount=10_000)
        portfolio.set_date("2026-02-01")
        portfolio.buy("600000.SH", "Test Bank", "弱周期红利", price=10.0, target_amount=10_000)

        self.assertEqual(portfolio.positions["600000.SH"].shares, 2000)
        self.assertEqual(portfolio.shares_held_before("600000.SH", "2026-01-15"), 1000)

        dividend = portfolio.receive_dividend(
            "600000.SH",
            "Test Bank",
            cash_per_share=1.0,
            shares=portfolio.shares_held_before("600000.SH", "2026-01-15"),
        )

        self.assertIsNotNone(dividend)
        self.assertEqual(dividend["shares"], 1000)
        self.assertAlmostEqual(dividend["net_dividend"], 1000)


if __name__ == "__main__":
    unittest.main()
