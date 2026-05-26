import unittest

from weekly_harness.strategy import DividendCycleStrategy, StrategyParams


class StrategyConstraintTests(unittest.TestCase):
    def test_applies_category_and_cash_reserve_limits(self):
        params = StrategyParams(
            max_weight=0.15,
            mid_weight=0.10,
            min_weight=0.05,
            category_limits={"弱周期红利": 0.30},
            cash_reserve=0.10,
            rebalance_threshold=0.0,
            max_positions=10,
        )
        strategy = DividendCycleStrategy(params)
        scores = {
            "A": {"name": "A", "category": "弱周期红利", "total_score": 85, "verdict": "strong"},
            "B": {"name": "B", "category": "弱周期红利", "total_score": 82, "verdict": "strong"},
            "C": {"name": "C", "category": "弱周期红利", "total_score": 81, "verdict": "strong"},
        }

        actions = strategy.generate_rebalance_actions(scores, {})
        targets = {a.ts_code: a.target_weight for a in actions}

        self.assertAlmostEqual(sum(targets.values()), 0.30)
        self.assertTrue(all(weight <= params.max_single_stock for weight in targets.values()))
        self.assertTrue(all(a.action == "buy" for a in actions))

    def test_limits_number_of_positions_to_highest_targets(self):
        params = StrategyParams(max_positions=2, rebalance_threshold=0.0)
        strategy = DividendCycleStrategy(params)
        scores = {
            "strong": {"name": "strong", "category": "其他", "total_score": 90, "verdict": "strong"},
            "buy": {"name": "buy", "category": "其他", "total_score": 70, "verdict": "buy"},
            "watch": {"name": "watch", "category": "其他", "total_score": 55, "verdict": "watch"},
        }

        actions = strategy.generate_rebalance_actions(scores, {})
        targets = {a.ts_code: a.target_weight for a in actions}

        self.assertEqual(set(targets), {"strong", "buy"})
        self.assertAlmostEqual(targets["strong"], 0.15)
        self.assertAlmostEqual(targets["buy"], 0.10)


if __name__ == "__main__":
    unittest.main()
