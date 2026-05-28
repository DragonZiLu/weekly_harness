"""
测试 MarketSignals 的 fallback 逻辑
"""
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd
import numpy as np

from weekly_harness.market_signals import MarketSignals, BullBearSignal, RotationSignal


class MarketSignalsFallbackTests(unittest.TestCase):
    """验证 market_signals 在数据不足时优雅降级"""

    def test_rotation_signal_returns_default_on_empty_data(self):
        """板块轮动数据为空时返回均衡信号"""
        engine = MarketSignals(tushare_token="fake")

        with patch.object(engine, "_get_pro") as mock_pro:
            pro = MagicMock()
            pro.index_daily.return_value = pd.DataFrame()  # 空数据
            mock_pro.return_value = pro

            signal = engine.get_rotation_signal()
            self.assertEqual(signal.style, "均衡")
            self.assertEqual(signal.strength, 0.0)

    def test_bull_bear_signal_returns_default_on_exception(self):
        """牛熊信号异常时返回震荡信号"""
        engine = MarketSignals(tushare_token="fake")

        with patch.object(engine, "_get_pro") as mock_pro:
            mock_pro.side_effect = Exception("网络错误")

            signal = engine.get_bull_bear_signal()
            self.assertEqual(signal.phase, "震荡")
            self.assertEqual(signal.confidence, 0.0)

    def test_bull_bear_fallback_uses_pe_inverse(self):
        """当 dv_ratio 不可用时，fallback 用 PE 倒数 × 分红率估算股息率"""
        engine = MarketSignals(tushare_token="fake")

        with patch.object(engine, "_get_pro") as mock_pro:
            pro = MagicMock()

            # index_daily 返回有效价格数据
            n_days = 200
            dates = pd.date_range("2020-01-01", periods=n_days, freq="B")
            hs300_df = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "close": np.random.uniform(3500, 4500, n_days),
            })
            pro.index_daily.return_value = hs300_df

            # index_dailybasic 第一次抛异常（dv_ratio不可用），第二次返回PE数据
            pe_df = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "pe_ttm": np.random.uniform(10, 15, n_days),
            })
            pro.index_dailybasic.side_effect = [
                Exception("dv_ratio not available"),  # 第一次调用失败
                pe_df,  # 第二次返回PE数据
            ]
            mock_pro.return_value = pro

            signal = engine.get_bull_bear_signal()
            # fallback 路径应该成功返回信号
            self.assertNotEqual(signal.reason, "数据不足")

    def test_bull_bear_returns_insufficient_data_on_short_pe_history(self):
        """PE fallback 数据不足100条时返回数据不足"""
        engine = MarketSignals(tushare_token="fake")

        with patch.object(engine, "_get_pro") as mock_pro:
            pro = MagicMock()

            n_days = 200
            dates = pd.date_range("2020-01-01", periods=n_days, freq="B")
            hs300_df = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "close": np.random.uniform(3500, 4500, n_days),
            })
            pro.index_daily.return_value = hs300_df

            # PE数据只有50条（< 100）
            short_pe = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d")[:50],
                "pe_ttm": np.random.uniform(10, 15, 50),
            })
            pro.index_dailybasic.side_effect = [
                Exception("dv_ratio not available"),
                short_pe,
            ]
            mock_pro.return_value = pro

            signal = engine.get_bull_bear_signal()
            self.assertEqual(signal.phase, "震荡")
            self.assertIn("数据不足", signal.reason)


if __name__ == "__main__":
    unittest.main()
