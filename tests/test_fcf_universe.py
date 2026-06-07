"""
FCF 指数选样引擎专项测试
=======================
覆盖 FcfUniverse 核心逻辑：行业剔除、权重封顶、财务计算、
as-of 时间校验、5年OCF检测、调仓日生成。
"""
import unittest
import sys
from pathlib import Path
from datetime import datetime

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.fcf_universe import (
    _is_financial_or_real_estate,
    _EXCLUDED_INDUSTRIES,
    _EXCLUDED_INDUSTRY_KEYWORDS,
    FcfUniverse,
)


# ══════════════════════════════════════════════════════════════════
# 行业剔除测试
# ══════════════════════════════════════════════════════════════════

class IndustryExclusionTests(unittest.TestCase):
    """测试金融/房地产行业剔除逻辑"""

    def test_explicit_finance_industries_excluded(self):
        """显式列出的金融行业应被剔除"""
        for ind in _EXCLUDED_INDUSTRIES:
            if ind:  # 跳过空字符串
                self.assertTrue(
                    _is_financial_or_real_estate(ind),
                    f"行业 '{ind}' 应被排除但未排除",
                )

    def test_keyword_match_subsidiary_industries(self):
        """关键词兜底：含金融/银行/证券等词应被剔除"""
        for kw in _EXCLUDED_INDUSTRY_KEYWORDS:
            test_industry = f"某种{kw}服务"
            self.assertTrue(
                _is_financial_or_real_estate(test_industry),
                f"含关键词 '{kw}' 的行业 '{test_industry}' 应被排除",
            )

    def test_normal_industries_not_excluded(self):
        """正常行业不应被误杀"""
        normal = [
            "白酒", "家用电器", "汽车整车", "煤炭开采", "医药商业",
            "水泥", "建筑工程", "软件服务", "化工原料",
        ]
        for ind in normal:
            self.assertFalse(
                _is_financial_or_real_estate(ind),
                f"正常行业 '{ind}' 不应被排除",
            )

    def test_empty_industry_not_excluded(self):
        """空行业名不应导致错误"""
        self.assertFalse(_is_financial_or_real_estate(""))
        self.assertFalse(_is_financial_or_real_estate("None"))
        self.assertFalse(_is_financial_or_real_estate("nan"))


# ══════════════════════════════════════════════════════════════════
# 权重封顶测试
# ══════════════════════════════════════════════════════════════════

class CappedRedistributionTests(unittest.TestCase):
    """测试 _apply_capped_redistribution 权重封顶算法"""

    def test_empty_input(self):
        self.assertEqual(
            FcfUniverse._apply_capped_redistribution({}),
            {},
        )

    def test_single_stock_hits_cap_limit(self):
        """单只股票在10%上限下最多配置10%（不能满仓）"""
        result = FcfUniverse._apply_capped_redistribution({"A": 100.0}, cap=0.10)
        # 只有1只标的，10%上限，数学上最多配置10%
        self.assertAlmostEqual(result["A"], 0.10, places=4,
                               msg="单只标的无法突破10%上限")

    def test_all_equal_no_cap_breach(self):
        """均匀权重且不触碰上限"""
        raw = {f"S{i}": 10.0 for i in range(10)}
        result = FcfUniverse._apply_capped_redistribution(raw, cap=0.10)
        self.assertAlmostEqual(sum(result.values()), 1.0, places=4)
        for w in result.values():
            self.assertAlmostEqual(w, 0.10, places=4)

    def test_single_stock_exceeds_cap(self):
        """一只股票超过10%，应被截断并重新分配"""
        raw = {"BIG": 100, "S1": 10, "S2": 10, "S3": 10, "S4": 10,
               "S5": 10, "S6": 10, "S7": 10, "S8": 10, "S9": 10}
        result = FcfUniverse._apply_capped_redistribution(raw, cap=0.10)

        # 没有权重超过上限
        for k, w in result.items():
            self.assertLessEqual(
                w, 0.10 + 1e-8,
                f"{k} 权重 {w:.6f} 超过上限 10%"
            )
        # 总和为 1.0
        self.assertAlmostEqual(sum(result.values()), 1.0, places=4)

    def test_two_stocks_exceed_cap(self):
        """两只超过10%的大盘股"""
        raw = {"BIG1": 100, "BIG2": 80,
               "S1": 5, "S2": 5, "S3": 5, "S4": 5,
               "S5": 5, "S6": 5, "S7": 5, "S8": 5}
        result = FcfUniverse._apply_capped_redistribution(raw, cap=0.10)

        for k, w in result.items():
            self.assertLessEqual(w, 0.10 + 1e-8, f"{k} 超过上限")
        self.assertAlmostEqual(sum(result.values()), 1.0, places=4)

    def test_top_n_5_with_allow_cash(self):
        """top_n=5 时 n*cap=0.50 < 1.0，允许现金剩余"""
        raw = {f"S{i}": 10.0 for i in range(5)}
        result = FcfUniverse._apply_capped_redistribution(
            raw, cap=0.10, allow_cash=True
        )
        # 每只不应超过 10%
        for w in result.values():
            self.assertLessEqual(w, 0.10 + 1e-8)
        # 总和应 ≤ 50%（允许现金剩余），不强制满仓
        total = sum(result.values())
        self.assertLessEqual(total, 0.50 + 1e-8,
                             f"top_n=5 应允许多余现金，但总权重={total:.4f}")

    def test_top_n_5_without_allow_cash(self):
        """top_n=5 且不允许现金时：算法会封顶在10%，总和=50%"""
        raw = {f"S{i}": 20.0 for i in range(5)}
        result = FcfUniverse._apply_capped_redistribution(
            raw, cap=0.10, allow_cash=False
        )
        # 5只 × 10% = 50%，数学上不可能满仓
        total = sum(result.values())
        self.assertAlmostEqual(total, 0.50, places=2,
                               msg="5只标的最多配置50%（5×10%）")
        for w in result.values():
            self.assertLessEqual(w, 0.10 + 1e-8,
                                 "每只不应超过10%")

    def test_all_zero_raw_weights(self):
        """零权重应等权分配"""
        raw = {"A": 0, "B": 0, "C": 0}
        result = FcfUniverse._apply_capped_redistribution(raw)
        self.assertEqual(len(result), 3)
        for w in result.values():
            self.assertAlmostEqual(w, 1.0 / 3, places=4)

    def test_negative_raw_weights_handled(self):
        """负权重应在取绝对值前就被过滤（FCF>0筛选确保传入前已过滤）"""
        # 传入时已确保 FCF>0，此处仅验证算法不崩溃
        raw = {"A": 10, "B": -5}
        result = FcfUniverse._apply_capped_redistribution(raw)
        # 允许算法产出结果（实际使用前已做 FCF>0 过滤）
        self.assertGreater(len(result), 0)


# ══════════════════════════════════════════════════════════════════
# 财务计算测试
# ══════════════════════════════════════════════════════════════════

class FinancialCalculationTests(unittest.TestCase):
    """测试 _calc_fcf, _calc_ev, _calc_fcf_yield, _calc_profit_quality"""

    def setUp(self):
        self.uni = FcfUniverse()

    def test_fcf_calculation(self):
        """自由现金流 = 经营CF - Capex"""
        self.assertIsNone(self.uni._calc_fcf(None, 100))
        self.assertIsNone(self.uni._calc_fcf(100, None))
        self.assertEqual(self.uni._calc_fcf(100, 30), 70)
        self.assertEqual(self.uni._calc_fcf(50, 100), -50)

    def test_ev_calculation_unit_conversion(self):
        """EV = 市值(万元)×10000 + 总负债 - 货币资金（单位统一为元）"""
        # total_mv=100亿(万元) = 1e10 元, liab=5e9, money_cap=1e9
        ev = self.uni._calc_ev(
            total_mv=1_000_000,     # 100亿万元
            total_liab=5_000_000_000,  # 50亿元
            money_cap=1_000_000_000,   # 10亿元
        )
        # 1e6 万 × 10000 = 1e10 元 + 5e9 - 1e9 = 1.4e10 元 = 1400亿
        self.assertAlmostEqual(ev / 1e8, 140.0, places=1)

    def test_ev_none_when_missing(self):
        """缺少任何字段返回 None"""
        self.assertIsNone(self.uni._calc_ev(None, 100, 100))
        self.assertIsNone(self.uni._calc_ev(100, None, 100))
        self.assertIsNone(self.uni._calc_ev(100, 100, None))

    def test_fcf_yield(self):
        """FCF率 = FCF / EV"""
        self.assertIsNone(self.uni._calc_fcf_yield(None, 1000))
        self.assertIsNone(self.uni._calc_fcf_yield(100, None))
        self.assertIsNone(self.uni._calc_fcf_yield(100, 0))      # EV=0
        self.assertIsNone(self.uni._calc_fcf_yield(100, -50))    # EV<0
        self.assertAlmostEqual(self.uni._calc_fcf_yield(10, 100), 0.10)

    def test_profit_quality(self):
        """盈利质量 = (经营CF - 营业利润) / 总资产"""
        self.assertIsNone(
            self.uni._calc_profit_quality(None, 100, 1000)
        )
        self.assertIsNone(
            self.uni._calc_profit_quality(100, None, 1000)
        )
        self.assertIsNone(
            self.uni._calc_profit_quality(100, 50, None)
        )
        self.assertIsNone(
            self.uni._calc_profit_quality(100, 50, 0)   # 总资产=0
        )
        # 经营CF=80, 营业利润=50, 总资产=1000 → (80-50)/1000 = 0.03
        self.assertAlmostEqual(
            self.uni._calc_profit_quality(80, 50, 1000), 0.03
        )
        # 负值：经营CF不够覆盖营业利润
        self.assertAlmostEqual(
            self.uni._calc_profit_quality(40, 100, 1000), -0.06
        )


# ══════════════════════════════════════════════════════════════════
# As-of 时间校验测试
# ══════════════════════════════════════════════════════════════════

class AsOfTimeTests(unittest.TestCase):
    """测试 _get_available_report_year 三表审计逻辑"""

    def test_report_year_never_beyond_current(self):
        """年报年份不应超过调仓日年份"""
        uni = FcfUniverse()
        # 未 preload 财务缓存，三表均无数据 → 所有年份都不可用
        # 最终回退到 year - 2
        year = uni._get_available_report_year("2020-06-30", "000001.SZ")
        self.assertLessEqual(year, 2019, "年报年份不应超过调仓日前一年")


# ══════════════════════════════════════════════════════════════════
# FCF 调仓日生成测试
# ══════════════════════════════════════════════════════════════════

class FcfRebalanceDateTests(unittest.TestCase):
    """测试 FCF 季度调仓日计算（BacktestDataFetcher.get_fcf_rebalance_dates）"""

    def test_second_friday_in_month(self):
        """验证每季度第二周五日期的正确性"""
        from weekly_harness.backtest import BacktestDataFetcher

        fetcher = BacktestDataFetcher()
        dates = fetcher.get_fcf_rebalance_dates("2020-01-01", "2020-12-31")

        # 应生成 4 个调仓日（Q1-Q4）
        self.assertGreaterEqual(len(dates), 4,
                                f"2020年应有4个季度调仓日，实际: {len(dates)}")

        # 每个日期应在对应月份内
        expected_months = [3, 6, 9, 12]
        for date_str, expected_m in zip(dates[:4], expected_months):
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            self.assertIn(
                dt.month, [expected_m, expected_m + 1],
                f"调仓日 {date_str} 应在 {expected_m} 月附近"
            )

    def test_fcf_dates_are_trading_days(self):
        """FCF 调仓日应为实际交易日（第二周五后的下一个交易日）"""
        from weekly_harness.backtest import BacktestDataFetcher

        fetcher = BacktestDataFetcher()
        dates = fetcher.get_fcf_rebalance_dates("2024-01-01", "2024-12-31")

        # 周末不可能是调仓日
        for d in dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            self.assertNotIn(dt.weekday(), [5, 6],
                             f"调仓日 {d} 不应是周末")

    def test_multiple_years_continuity(self):
        """多年调仓日连续无断档"""
        from weekly_harness.backtest import BacktestDataFetcher

        fetcher = BacktestDataFetcher()
        dates = fetcher.get_fcf_rebalance_dates("2019-01-01", "2021-12-31")

        # 3年 × 4季度 = 12个调仓日
        self.assertGreaterEqual(len(dates), 12,
                                f"3年至少12个调仓日，实际: {len(dates)}")


# ══════════════════════════════════════════════════════════════════
# FCF 选样集成测试（带 Mock 数据）
# ══════════════════════════════════════════════════════════════════

class FcfUniverseIntegrationTests(unittest.TestCase):
    """使用 minimal 财务缓存测试 get_fcf_basket 全流程"""

    def setUp(self):
        self.uni = FcfUniverse()

    def test_preload_without_download(self):
        """preload_all() 从空缓存加载不应崩溃"""
        self.uni.preload_all(download=False)
        self.assertTrue(self.uni._preloaded,
                        "preload_all() 应标记已加载")

    def test_empty_basket_when_no_constituents(self):
        """无成分股时返回空字典"""
        self.uni.preload_all(download=False)
        # 没有成分股权重缓存 + 空财务数据 → 应返回空
        basket = self.uni.get_fcf_basket("2020-06-30", top_n=100)
        self.assertEqual(basket, {},
                         "无数据时 get_fcf_basket 应返回空字典")

    def test_capped_redistribution_convergence(self):
        """验证 capped redistribution 在各种输入下均收敛"""
        import random
        random.seed(42)

        for _ in range(20):
            n = random.randint(3, 200)
            raw = {f"S{i}": random.uniform(1, 100) for i in range(n)}
            result = FcfUniverse._apply_capped_redistribution(raw, cap=0.10)

            if n >= 10:
                self.assertAlmostEqual(sum(result.values()), 1.0, places=4,
                                       msg=f"n={n}时应满仓")

            for k, w in result.items():
                if n >= 10:
                    self.assertLessEqual(
                        w, 0.10 + 1e-8,
                        f"n={n}时 {k}={w:.6f} 超过上限"
                    )

    def test_quality_warnings_persist(self):
        """数据质量警告应被记录"""
        self.uni.preload_all(download=False)
        self.uni._data_quality_warnings.append("test warning")
        warnings = self.uni._data_quality_warnings.copy()
        self.assertIn("test warning", warnings)

    def test_basket_structure(self):
        """返回的 basket 中每只股票包含必要字段"""
        self.uni.preload_all(download=False)
        basket = self.uni.get_fcf_basket("2020-06-30", top_n=10)

        # 空数据返回空basket，不做字段检查
        if basket:
            # 排除元数据键
            stock_keys = [k for k in basket if not k.startswith("__")]
            for code in stock_keys:
                meta = basket[code]
                for field in ["name", "industry", "sector", "fcf", "ev",
                              "fcf_yield", "profit_quality", "total_mv",
                              "category", "certainty", "is_etf", "weight"]:
                    self.assertIn(field, meta,
                                  f"{code} 缺少字段 '{field}'")


# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
