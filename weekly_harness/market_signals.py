"""
MarketSignals — 市场信号模块
===========================
基于宏观/风格指标的辅助信号，不直接参与评分，作为策略的上下文参考。

1. 板块轮动信号：科技/红利风格相对强弱
2. 牛熊周期信号：宽基股息率分位数 + 周期阶段判断

数据来源：
  - tushare: 指数日线（沪深300/中证红利/创业板指）
  - 可选: 10年期国债利率
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from datetime import datetime

import pandas as pd
import numpy as np


# ── 风格指数定义 ──────────────────────────────────────────────
STYLE_INDICES = {
    "红利": "000015.SH",   # 上证红利指数（000922.SH中证红利无日线数据，改用此替代）
    "成长": "399006.SZ",   # 创业板指
    "大盘": "000300.SH",   # 沪深300
}

# ── 牛熊周期阈值 ──────────────────────────────────────────────
BULL_BEAR_THRESHOLDS = {
    # 沪深300股息率历史分位数
    "div_yield_percentile_bear": 80,   # ≥80%分位 → 极度低估（熊市底部信号）
    "div_yield_percentile_bull": 20,   # ≤20%分位 → 极度高估（牛市顶部信号）
    # 风格强弱阈值
    "style_rotation_threshold": 0.15,  # 风格强弱差>15% → 强轮动信号
}


@dataclass
class RotationSignal:
    """板块轮动信号"""
    style: str               # 当前强势风格："红利"/"成长"/"均衡"
    strength: float          # 轮动强度（0-1）
    reason: str              # 信号原因
    suggestion: str          # 操作建议
    detail: Dict = None      # 详细数据


@dataclass
class BullBearSignal:
    """牛熊周期信号"""
    phase: str               # "牛市"/"熊市"/"震荡"
    confidence: float         # 信心度（0-1）
    reason: str               # 信号原因
    suggestion: str           # 操作建议
    detail: Dict = None      # 详细数据（分位数等）


class MarketSignals:
    """
    市场信号引擎

    使用方法：
        engine = MarketSignals(tushare_token="xxx")
        rotation = engine.get_rotation_signal()
        bullbear = engine.get_bull_bear_signal()
    """

    def __init__(self, tushare_token: str = ""):
        self._token = tushare_token
        self._pro = None

    def _get_pro(self):
        """延迟初始化 tushare"""
        if self._pro is None:
            import tushare as ts
            if self._token:
                ts.set_token(self._token)
            self._pro = ts.pro_api()
        return self._pro

    # ── 板块轮动信号 ─────────────────────────────────────────

    def get_rotation_signal(self, lookback_months: int = 6) -> RotationSignal:
        """
        板块轮动信号：红利 vs 成长风格相对强弱

        逻辑：
          - 计算近N个月红利指数和创业板指的收益率
          - 收益差 > 阈值 → 红利强势 → 红利策略加仓
          - 收益差 < -阈值 → 成长强势 → 红利策略谨慎
          - 中间 → 均衡

        Parameters
        ----------
        lookback_months : int — 回看月数（默认6个月）

        Returns
        -------
        RotationSignal
        """
        try:
            pro = self._get_pro()
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - pd.DateOffset(months=lookback_months)).strftime("%Y%m%d")

            # 获取红利指数和创业板指数据
            dividend_idx = pro.index_daily(
                ts_code=STYLE_INDICES["红利"],
                start_date=start_date, end_date=end_date,
                fields="trade_date,close"
            )
            growth_idx = pro.index_daily(
                ts_code=STYLE_INDICES["成长"],
                start_date=start_date, end_date=end_date,
                fields="trade_date,close"
            )

            if dividend_idx.empty or growth_idx.empty:
                return RotationSignal("均衡", 0.0, "数据不足", "无法判断风格轮动")

            dividend_idx = dividend_idx.sort_values("trade_date")
            growth_idx = growth_idx.sort_values("trade_date")

            # 计算区间收益率
            div_ret = (float(dividend_idx.iloc[-1]["close"]) / float(dividend_idx.iloc[0]["close"]) - 1) * 100
            gro_ret = (float(growth_idx.iloc[-1]["close"]) / float(growth_idx.iloc[0]["close"]) - 1) * 100

            # 风格强弱差
            spread = div_ret - gro_ret
            threshold = BULL_BEAR_THRESHOLDS["style_rotation_threshold"] * 100

            detail = {
                "dividend_return": round(div_ret, 2),
                "growth_return": round(gro_ret, 2),
                "spread": round(spread, 2),
                "lookback_months": lookback_months,
            }

            # 临界提示：spread 绝对值进入阈值 80% 区间内（如 12%~15%），容易翻转
            near_threshold = abs(spread) >= threshold * 0.8 and abs(spread) < threshold
            critical_note = "（⚠️ 接近轮动阈值，风格临界）" if near_threshold else ""

            if spread > threshold:
                strength = min(spread / 50, 1.0)  # 归一化
                return RotationSignal(
                    style="红利",
                    strength=round(strength, 2),
                    reason=f"近{lookback_months}月红利{div_ret:+.1f}% vs 成长{gro_ret:+.1f}%，红利领先{spread:.1f}%",
                    suggestion="红利强势期，红利策略可积极布局",
                    detail=detail,
                )
            elif spread < -threshold:
                strength = min(abs(spread) / 50, 1.0)
                return RotationSignal(
                    style="成长",
                    strength=round(strength, 2),
                    reason=f"近{lookback_months}月成长{gro_ret:+.1f}% vs 红利{div_ret:+.1f}%，成长领先{abs(spread):.1f}%",
                    suggestion="成长强势期，红利策略需谨慎，降低仓位",
                    detail=detail,
                )
            else:
                return RotationSignal(
                    style="均衡",
                    strength=0.0,
                    reason=f"近{lookback_months}月红利{div_ret:+.1f}% vs 成长{gro_ret:+.1f}%，风格均衡{critical_note}",
                    suggestion="风格均衡，维持正常仓位",
                    detail=detail,
                )

        except Exception as e:
            return RotationSignal("均衡", 0.0, f"计算异常: {e}", "无法判断风格轮动")

    # ── 牛熊周期信号 ─────────────────────────────────────────

    def get_bull_bear_signal(self, lookback_years: int = 10) -> BullBearSignal:
        """
        牛熊周期信号：基于宽基股息率历史分位数

        逻辑：
          - 计算沪深300当前股息率在近N年历史中的分位数
          - 分位数 ≥ 80% → 熊市底部区域（极度低估，应加仓）
          - 分位数 ≤ 20% → 牛市顶部区域（极度高估，应减仓）
          - 中间 → 震荡

        附加信号：
          - 超高股息率（沪深300股息率>4%） → 强底部信号
          - GJD回流 → 辅助确认（需外部数据）

        Parameters
        ----------
        lookback_years : int — 历史分位数回看年数（默认10年）

        Returns
        -------
        BullBearSignal
        """
        try:
            pro = self._get_pro()

            # 获取沪深300近N年日线
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - pd.DateOffset(years=lookback_years)).strftime("%Y%m%d")

            hs300 = pro.index_daily(
                ts_code=STYLE_INDICES["大盘"],
                start_date=start_date, end_date=end_date,
                fields="trade_date,close"
            )

            if hs300.empty or len(hs300) < 100:
                return BullBearSignal("震荡", 0.0, "数据不足", "无法判断牛熊周期")

            hs300 = hs300.sort_values("trade_date")

            # 获取沪深300股息率（tushare index_dailybasic）
            try:
                basic = pro.index_dailybasic(
                    ts_code=STYLE_INDICES["大盘"],
                    start_date=start_date, end_date=end_date,
                    fields="trade_date,dv_ratio"
                )
                if not basic.empty:
                    basic = basic.sort_values("trade_date")
                    current_div_yield = float(basic.iloc[-1]["dv_ratio"])
                    hist_div_yields = basic["dv_ratio"].dropna().values
                else:
                    raise ValueError("无股息率数据")
            except Exception:
                # fallback: 用PE倒数估算（粗略）
                # 注意：沪深300的index_dailybasic不返回dv_ratio字段，需用PE倒数×分红率估算
                DIVIDEND_PAYOUT_RATIO = 0.40  # A股宽基指数分红率约40%
                pe_data = pro.index_dailybasic(
                    ts_code=STYLE_INDICES["大盘"],
                    start_date=start_date, end_date=end_date,
                    fields="trade_date,pe_ttm"
                )
                if pe_data.empty or len(pe_data) < 100:
                    return BullBearSignal("震荡", 0.0, "数据不足", "无法判断牛熊周期")
                pe_data = pe_data.sort_values("trade_date")
                hist_earnings_yield = (100 / pe_data["pe_ttm"].replace(0, np.nan)).dropna().values
                hist_div_yields = hist_earnings_yield * DIVIDEND_PAYOUT_RATIO
                current_earnings_yield = 100 / float(pe_data.iloc[-1]["pe_ttm"]) if float(pe_data.iloc[-1]["pe_ttm"]) > 0 else 6.0
                current_div_yield = current_earnings_yield * DIVIDEND_PAYOUT_RATIO

            # 计算分位数
            if len(hist_div_yields) > 0:
                percentile = float(np.percentile(hist_div_yields, 100) - 
                                  (np.percentile(hist_div_yields, 100) - current_div_yield) / 
                                  (np.percentile(hist_div_yields, 100) - np.percentile(hist_div_yields, 0) + 1e-6) * 100)
                # 更准确的分位数计算
                percentile = float((hist_div_yields <= current_div_yield).sum() / len(hist_div_yields) * 100)
            else:
                percentile = 50.0

            bear_threshold = BULL_BEAR_THRESHOLDS["div_yield_percentile_bear"]
            bull_threshold = BULL_BEAR_THRESHOLDS["div_yield_percentile_bull"]

            detail = {
                "current_div_yield": round(current_div_yield, 2),
                "percentile": round(percentile, 1),
                "lookback_years": lookback_years,
                "bear_threshold": bear_threshold,
                "bull_threshold": bull_threshold,
            }

            # 判断周期
            if percentile >= bear_threshold:
                confidence = min((percentile - bear_threshold) / (100 - bear_threshold) * 2, 1.0)
                super_signal = " ⚡超强底部信号" if current_div_yield >= 4.0 else ""
                return BullBearSignal(
                    phase="熊市",
                    confidence=round(confidence, 2),
                    reason=f"沪深300股息率{current_div_yield:.2f}%，历史分位{percentile:.0f}%（≥{bear_threshold}%=熊市区间）{super_signal}",
                    suggestion="熊市底部区域，红利策略应加大仓位，历史证明超高股息时加仓胜率极高",
                    detail=detail,
                )
            elif percentile <= bull_threshold:
                confidence = min((bull_threshold - percentile) / bull_threshold * 2, 1.0)
                return BullBearSignal(
                    phase="牛市",
                    confidence=round(confidence, 2),
                    reason=f"沪深300股息率{current_div_yield:.2f}%，历史分位{percentile:.0f}%（≤{bull_threshold}%=牛市区间）",
                    suggestion="牛市顶部区域，红利策略应降低仓位，锁定利润",
                    detail=detail,
                )
            else:
                return BullBearSignal(
                    phase="震荡",
                    confidence=0.0,
                    reason=f"沪深300股息率{current_div_yield:.2f}%，历史分位{percentile:.0f}%，处于中间区间",
                    suggestion="震荡市，维持正常仓位，网格交易为主",
                    detail=detail,
                )

        except Exception as e:
            return BullBearSignal("震荡", 0.0, f"计算异常: {e}", "无法判断牛熊周期")

    # ── ETF 定投信号 ─────────────────────────────────────────

    def get_etf_dca_signal(self, ts_code: str, etf_name: str, bond_yield: float) -> Dict:
        """
        ETF 定投信号分析（基于文章第七部分策略）

        策略核心指标：
          1. 60周线偏离度：当前价格相对60周均线的偏离百分比
             - 低于60周线      → 开始定投区间
             - 偏离60周线 -10% → 加大定投
             - 高于60周线 +10% → 停止定投
             - 高于60周线 +15% → 减仓
          2. 20月线偏离度：当前价格相对20月均线的偏离百分比
             - 跌破20月线      → 触发定投信号（五年来难得机会）
          3. 股息率绝对值：
             - 股息率 ≥ 5.0% → 开始/加大定投
             - 股息率 4.0-5.0% → 可以定投
             - 股息率 < 4.0%  → 停止定投
             - 股息率 < 3.5%  → 减仓
          4. 股债息差：
             - 息差 > 4% (400BP) → 强定投信号

        Returns
        -------
        dict: {
            "ma60w": 60周均线价格,
            "ma20m": 20月均线价格,
            "dev_60w": 偏离60周线百分比（负=低于，正=高于）,
            "dev_20m": 偏离20月线百分比,
            "div_yield": 当前股息率,
            "bond_spread_bp": 股债息差(BP),
            "dca_action": 定投建议(开始/加大/停止/减仓/持有),
            "dca_reason": 建议原因,
            "signals": [激活的信号列表],
        }
        """
        try:
            pro = self._get_pro()
            end_date = datetime.now().strftime("%Y%m%d")

            # 获取近3年日线数据（足够计算60周线约420个交易日）
            start_date_3y = (datetime.now() - pd.DateOffset(years=3)).strftime("%Y%m%d")

            df = pro.fund_daily(
                ts_code=ts_code,
                start_date=start_date_3y, end_date=end_date,
                fields="trade_date,close,high,low"
            )

            if df is None or df.empty or len(df) < 60:
                return {"dca_action": "数据不足", "dca_reason": f"{etf_name} 日线数据不足", "signals": []}

            df = df.sort_values("trade_date").reset_index(drop=True)
            df["close"] = pd.to_numeric(df["close"], errors="coerce")

            current_price = float(df.iloc[-1]["close"])
            if current_price <= 0:
                return {"dca_action": "数据异常", "dca_reason": "价格数据异常", "signals": []}

            # 1. 计算60周线（约300个交易日，A股每年约250个交易日，60周=约300个交易日）
            MA60W_DAYS = 300
            if len(df) >= MA60W_DAYS:
                ma60w = float(df["close"].tail(MA60W_DAYS).mean())
            else:
                ma60w = float(df["close"].mean())

            dev_60w = (current_price / ma60w - 1) * 100  # 正=高于，负=低于

            # 2. 计算20月线（约420个交易日）
            MA20M_DAYS = 420
            if len(df) >= MA20M_DAYS:
                ma20m = float(df["close"].tail(MA20M_DAYS).mean())
            else:
                ma20m = float(df["close"].mean())

            dev_20m = (current_price / ma20m - 1) * 100

            # 3. 获取当前股息率（从 fund_div 接口估算年化）
            div_yield = 0.0
            try:
                df_div = pro.fund_div(ts_code=ts_code)
                if df_div is not None and not df_div.empty:
                    df_div["div_cash"] = pd.to_numeric(df_div["div_cash"], errors="coerce").fillna(0)
                    df_div = df_div[df_div["div_cash"] > 0]
                    if not df_div.empty:
                        df_div["ex_date"] = df_div["ex_date"].astype(str)
                        df_div = df_div.drop_duplicates(subset=["ex_date"])
                        df_div["year"] = df_div["ex_date"].str[:4]
                        # 取最近完整一年的分红
                        latest_year = df_div["year"].max()
                        annual_div = float(df_div[df_div["year"] == latest_year]["div_cash"].sum())
                        if current_price > 0 and annual_div > 0:
                            div_yield = annual_div / current_price * 100
            except Exception:
                pass

            bond_spread_bp = (div_yield - bond_yield) * 100 if div_yield > 0 else 0

            # 4. 综合判断定投信号
            signals = []
            if dev_60w < -10:
                signals.append(f"📉 价格低于60周线{abs(dev_60w):.1f}%（加大定投区）")
            elif dev_60w < 0:
                signals.append(f"📉 价格低于60周线{abs(dev_60w):.1f}%（开始定投区）")

            if dev_20m < 0:
                signals.append(f"📅 价格低于20月线{abs(dev_20m):.1f}%（难得定投时机）")

            if div_yield >= 5.0:
                signals.append(f"💰 股息率{div_yield:.2f}% ≥ 5%（强烈定投信号）")
            elif div_yield >= 4.0:
                signals.append(f"💰 股息率{div_yield:.2f}% ≥ 4%（可以定投）")

            if bond_spread_bp >= 400:
                signals.append(f"📊 股债息差{bond_spread_bp:.0f}BP ≥ 400BP（极佳买点）")
            elif bond_spread_bp >= 230:
                signals.append(f"📊 股债息差{bond_spread_bp:.0f}BP ≥ 230BP（历史高位）")

            # 综合建议
            if dev_60w >= 15:
                dca_action = "减仓"
                dca_reason = f"价格高于60周线{dev_60w:.1f}%（偏离≥15%减仓区），锁定利润"
            elif dev_60w >= 10:
                dca_action = "停止定投"
                dca_reason = f"价格高于60周线{dev_60w:.1f}%（偏离≥10%停止区），等待回调"
            elif dev_60w < -10 or dev_20m < 0:
                dca_action = "加大定投"
                dca_reason = f"价格低于60周线{abs(dev_60w):.1f}%或跌破20月线，历史极佳买点"
                if div_yield >= 5.0:
                    dca_action = "大力定投"
                    dca_reason += f"，且股息率{div_yield:.2f}%已达5%+"
            elif dev_60w < 0 or div_yield >= 4.0:
                dca_action = "开始定投"
                dca_reason = f"价格低于60周线或股息率{div_yield:.2f}%达定投阈值，可分批买入"
            elif div_yield < 3.5:
                dca_action = "减仓"
                dca_reason = f"股息率{div_yield:.2f}% < 3.5%（减仓线），估值偏高"
            elif div_yield < 4.0:
                dca_action = "停止定投"
                dca_reason = f"股息率{div_yield:.2f}% < 4%（停止定投线），等待回调"
            else:
                dca_action = "持有"
                dca_reason = "维持现有仓位，等待更好机会"

            return {
                "etf_name": etf_name,
                "current_price": round(current_price, 3),
                "ma60w": round(ma60w, 3),
                "ma20m": round(ma20m, 3),
                "dev_60w": round(dev_60w, 1),
                "dev_20m": round(dev_20m, 1),
                "div_yield": round(div_yield, 2),
                "bond_spread_bp": round(bond_spread_bp, 0),
                "dca_action": dca_action,
                "dca_reason": dca_reason,
                "signals": signals,
            }

        except Exception as e:
            return {
                "etf_name": etf_name,
                "dca_action": "计算失败",
                "dca_reason": f"异常: {e}",
                "signals": [],
            }

    # ── 综合信号 ──────────────────────────────────────────────

    def get_all_signals(self, bond_yield: float = 1.65) -> Dict:
        """
        获取所有市场信号

        Parameters
        ----------
        bond_yield : float
            当前10年期国债收益率，用于 ETF 定投信号息差计算
        """
        rotation = self.get_rotation_signal()
        bullbear = self.get_bull_bear_signal()

        # ETF 定投信号（两大主流红利 ETF）
        etf_dca_signals = {}
        try:
            etf_dca_signals["515180.SH"] = self.get_etf_dca_signal(
                "515180.SH", "易方达红利ETF", bond_yield
            )
        except Exception:
            pass
        try:
            etf_dca_signals["563020.SH"] = self.get_etf_dca_signal(
                "563020.SH", "红利低波ETF", bond_yield
            )
        except Exception:
            pass

        # 综合仓位建议
        position_suggestion = "正常"
        if bullbear.phase == "熊市" and rotation.style in ("红利", "均衡"):
            position_suggestion = "加仓"
        elif bullbear.phase == "牛市" and rotation.style == "成长":
            position_suggestion = "减仓"
        elif bullbear.phase == "熊市":
            position_suggestion = "谨慎加仓"
        elif bullbear.phase == "牛市":
            position_suggestion = "谨慎减仓"

        return {
            "rotation": rotation,
            "bullbear": bullbear,
            "position_suggestion": position_suggestion,
            "etf_dca_signals": etf_dca_signals,
        }
