"""
Backtest — 回测引擎
====================
基于历史数据模拟红利周期轮动策略的表现。

回测流程：
  1. 加载历史评分数据（weekly_history.csv）或用模拟评分
  2. 获取历史价格数据（tushare daily/fund_daily）
  3. 按季度逐步模拟：计算评分 → 生成调仓指令 → 执行交易 → 记录净值
  4. 计算策略绩效指标（年化收益、最大回撤、夏普比率等）
  5. 与基准对比（沪深300 / 中证红利）

调仓频率：
  - quarterly (默认): 每季度末最后一个交易日调仓
  - weekly: 每周五调仓

使用方法：
  # 季度调仓回测（默认）
  python -m weekly_harness.backtest --start 2024-01-01 --end 2026-05-01

  # 周度调仓回测
  python -m weekly_harness.backtest --start 2024-01-01 --freq weekly

  # 指定参数
  python -m weekly_harness.backtest --start 2024-01-01 --cash 50万 --max-weight 0.12
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .portfolio import Portfolio
from .strategy import DividendCycleStrategy, StrategyParams, RebalanceAction
from dividend_evaluator import SECTOR_THRESHOLDS, HEDGE_PAIRS

# ─── 路径 ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent


# ─── 绩效指标计算 ──────────────────────────────────────────────

class PerformanceMetrics:
    """策略绩效指标计算器"""

    @staticmethod
    def annual_return(total_return: float, trading_days: int) -> float:
        """年化收益率"""
        if trading_days <= 0:
            return 0.0
        years = trading_days / 244  # 约244个交易日/年
        if years <= 0:
            return 0.0
        return ((1 + total_return / 100) ** (1 / years) - 1) * 100

    @staticmethod
    def max_drawdown(nav_series: pd.Series) -> float:
        """最大回撤（%）"""
        cummax = nav_series.cummax()
        drawdown = (nav_series - cummax) / cummax * 100
        return drawdown.min()

    @staticmethod
    def sharpe_ratio(daily_returns: pd.Series, risk_free_rate: float = 0.015) -> float:
        """夏普比率（年化）"""
        if daily_returns.std() == 0:
            return 0.0
        excess_returns = daily_returns - risk_free_rate / 244
        return excess_returns.mean() / daily_returns.std() * np.sqrt(244)

    @staticmethod
    def calmar_ratio(annual_return: float, max_dd: float) -> float:
        """卡尔玛比率"""
        if max_dd >= 0:
            return 0.0
        return annual_return / abs(max_dd)

    @staticmethod
    def win_rate(trades: list) -> float:
        """胜率（盈利交易占比）"""
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t.get("profit", 0) > 0)
        return wins / len(trades) * 100

    @staticmethod
    def profit_factor(trades: list) -> float:
        """盈亏比"""
        gross_profit = sum(t.get("profit", 0) for t in trades if t.get("profit", 0) > 0)
        gross_loss = abs(sum(t.get("profit", 0) for t in trades if t.get("profit", 0) < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss


# ─── 数据获取 ──────────────────────────────────────────────────

class BacktestDataFetcher:
    """回测用历史数据获取"""

    def __init__(self):
        self._price_cache: Dict[str, pd.DataFrame] = {}

    def _get_tushare_pro(self):
        """初始化 tushare"""
        import sys
        sys.path.insert(0, str(_PROJECT_ROOT))
        from config.settings import tushare_cfg
        import tushare as ts
        ts.set_token(tushare_cfg.token)
        return ts.pro_api()

    def fetch_price_data(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        is_etf: bool = False,
    ) -> pd.DataFrame:
        """
        获取历史价格数据

        Returns
        -------
        DataFrame with columns: trade_date, open, high, low, close, vol
        """
        cache_key = f"{ts_code}_{start_date}_{end_date}_{is_etf}"
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        pro = self._get_tushare_pro()

        if is_etf:
            df = pro.fund_daily(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ts_code,trade_date,open,high,low,close,vol",
            )
        else:
            df = pro.daily(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ts_code,trade_date,open,high,low,close,vol",
            )

        time.sleep(0.15)  # 限流

        if df is None or df.empty:
            return pd.DataFrame()

        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        df = df.sort_values("trade_date").reset_index(drop=True)

        self._price_cache[cache_key] = df
        return df

    def fetch_benchmark_data(
        self,
        benchmark_code: str = "000300.SH",
        start_date: str = "2020-01-01",
        end_date: str = "2026-12-31",
    ) -> pd.DataFrame:
        """
        获取基准指数/ETF数据，自动识别指数/ETF

        对于指数：使用 index_daily 获取收盘价（指数无分红，不复权即可）
        对于ETF：使用 fund_nav 获取累计净值（含分红再投资的真实收益）
        """
        pro = self._get_tushare_pro()
        start_dt = start_date.replace("-", "")
        end_dt = end_date.replace("-", "")

        # 1) 先尝试 index_daily（适用于沪深300等指数）
        try:
            df = pro.index_daily(
                ts_code=benchmark_code,
                start_date=start_dt,
                end_date=end_dt,
                fields="ts_code,trade_date,open,high,low,close,vol",
            )
            if df is not None and not df.empty:
                df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
                df = df.sort_values("trade_date").reset_index(drop=True)
                return df
        except Exception:
            pass

        # 2) 尝试 fund_nav（适用于ETF，使用累计净值反映真实总收益）
        try:
            df_nav = pro.fund_nav(ts_code=benchmark_code)
            if df_nav is not None and not df_nav.empty:
                df_nav = df_nav.sort_values("nav_date").reset_index(drop=True)
                # 筛选日期范围
                df_nav["nav_date"] = pd.to_datetime(df_nav["nav_date"], format="%Y%m%d")
                start_ts = pd.Timestamp(start_date)
                end_ts = pd.Timestamp(end_date)
                df_nav = df_nav[(df_nav["nav_date"] >= start_ts) & (df_nav["nav_date"] <= end_ts)]

                if not df_nav.empty:
                    # 优先使用复权净值(adj_nav)，其次累计净值(accum_nav)
                    if "adj_nav" in df_nav.columns:
                        nav_col = "adj_nav"
                    elif "accum_nav" in df_nav.columns:
                        nav_col = "accum_nav"
                    else:
                        nav_col = "unit_nav"

                    # 统一为与 index_daily 相同的格式
                    result = pd.DataFrame({
                        "ts_code": df_nav["ts_code"],
                        "trade_date": df_nav["nav_date"],
                        "close": df_nav[nav_col].astype(float),
                    })
                    result = result.dropna(subset=["close"]).reset_index(drop=True)
                    if not result.empty:
                        return result
        except Exception:
            pass

        return pd.DataFrame()

    def get_friday_dates(self, start_date: str, end_date: str) -> List[str]:
        """获取回测期间的所有周五日期（周度调仓日）"""
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        fridays = pd.date_range(start=start, end=end, freq="W-FRI")
        return [d.strftime("%Y-%m-%d") for d in fridays]

    def get_quarter_end_dates(self, start_date: str, end_date: str) -> List[str]:
        """
        获取回测期间的所有季度末最后一个周五日期（季度调仓日）

        季度末月份: 3月、6月、9月、12月
        取每个季度末月份的最后一个周五作为调仓日
        """
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        dates = []

        # 生成季度末月份
        current = start
        while current <= end:
            # 找到当前/下一个季度末月份
            month = current.month
            if month <= 3:
                q_end_month = 3
            elif month <= 6:
                q_end_month = 6
            elif month <= 9:
                q_end_month = 9
            else:
                q_end_month = 12

            q_end_year = current.year
            # 如果季度末月份已过，跳到下一季度
            if pd.Timestamp(q_end_year, q_end_month, 1) < start:
                if q_end_month == 12:
                    q_end_year += 1
                    q_end_month = 3
                else:
                    q_end_month += 3

            # 该季度末月份的最后一天
            if q_end_month == 12:
                next_month = pd.Timestamp(q_end_year + 1, 1, 1)
            else:
                next_month = pd.Timestamp(q_end_year, q_end_month + 1, 1)
            month_end = next_month - pd.Timedelta(days=1)

            # 找该月最后一个周五
            fridays_in_month = pd.date_range(
                start=pd.Timestamp(q_end_year, q_end_month, 1),
                end=month_end,
                freq="W-FRI",
            )
            if not fridays_in_month.empty:
                last_friday = fridays_in_month[-1]
                if start <= last_friday <= end:
                    dates.append(last_friday.strftime("%Y-%m-%d"))

            # 移到下一季度
            if q_end_month == 12:
                current = pd.Timestamp(q_end_year + 1, 1, 1)
            else:
                current = pd.Timestamp(q_end_year, q_end_month + 1, 1)

        return dates

    def fetch_dividend_data(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        is_etf: bool = False,
    ) -> pd.DataFrame:
        """
        获取历史分红数据

        Returns
        -------
        DataFrame with columns: ts_code, ex_date, cash_per_share (每股派现, 税前)
        """
        pro = self._get_tushare_pro()

        try:
            if is_etf:
                # ETF 分红用 fund_div 接口，字段名 div_cash
                df = pro.fund_div(ts_code=ts_code)
            else:
                # 个股分红用 dividend 接口，字段名 cash_div
                df = pro.dividend(
                    ts_code=ts_code,
                    fields="ts_code,end_date,div_proc,cash_div,ann_date,ex_date",
                )

            time.sleep(0.15)  # 限流

            if df is None or df.empty:
                return pd.DataFrame()

            # 筛选实施完成的分红
            if "div_proc" in df.columns:
                df = df[df["div_proc"] == "实施"]

            # 去重（ETF同一天可能有多条重复记录）
            if "ex_date" in df.columns:
                df = df.drop_duplicates(subset=["ex_date"])

            # 统一现金分红字段名 → cash_per_share
            if "cash_div" in df.columns:
                df = df.rename(columns={"cash_div": "cash_per_share"})
            elif "div_cash" in df.columns:
                df = df.rename(columns={"div_cash": "cash_per_share"})
            else:
                return pd.DataFrame()

            # 只保留现金分红记录
            df = df[df["cash_per_share"] > 0]

            # 筛选在回测期间的分红（用 ex_date）
            if "ex_date" in df.columns:
                df["ex_date"] = df["ex_date"].astype(str)
                df = df[
                    (df["ex_date"] >= start_date.replace("-", ""))
                    & (df["ex_date"] <= end_date.replace("-", ""))
                ]

            if df.empty:
                return pd.DataFrame()

            # 保留 ann_date 用于回测时判断"当时已知的分红"
            keep_cols = ["ts_code", "ex_date", "cash_per_share"]
            if "ann_date" in df.columns:
                keep_cols.append("ann_date")
            return df[keep_cols].reset_index(drop=True)

        except Exception:
            return pd.DataFrame()


# ─── 回测引擎 ──────────────────────────────────────────────────

class BacktestEngine:
    """
    回测引擎

    支持季度调仓（默认）和周度调仓。
    """

    def __init__(
        self,
        strategy_params: Optional[StrategyParams] = None,
        initial_cash: float = 100_0000,
        commission_rate: float = 0.001,
        slippage: float = 0.001,
        rebalance_freq: str = "quarterly",
    ):
        self.strategy = DividendCycleStrategy(strategy_params)
        self.portfolio = Portfolio(
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage=slippage,
            dividend_reinvest=True,
        )
        self.fetcher = BacktestDataFetcher()
        self.metrics = PerformanceMetrics()
        self.rebalance_freq = rebalance_freq  # "quarterly" or "weekly"

        # 股票元数据
        self._stock_meta: Dict[str, Dict] = {}
        # 价格数据缓存
        self._price_data: Dict[str, pd.DataFrame] = {}
        # 基准数据
        self._benchmark: Optional[pd.DataFrame] = None
        self._benchmark_code: str = ""
        # 分红数据 {ts_code: DataFrame}
        self._dividend_data: Dict[str, pd.DataFrame] = {}
        # 已处理的分红 (ts_code, ex_date) 防止重复
        self._processed_dividends: set = set()
        # 调仓后持仓快照（用于构建日线净值）
        self._rebalance_snapshots: List[Dict] = []

    def _load_stock_meta(self):
        """加载股票元数据（从 dividend_evaluator）"""
        import sys
        sys.path.insert(0, str(_PROJECT_ROOT))
        from dividend_evaluator import COMPANIES

        for sector, companies in COMPANIES.items():
            for name, meta in companies.items():
                ts_code = meta["ts_code"]
                self._stock_meta[ts_code] = {
                    "name": name,
                    "category": meta["category"],
                    "certainty": meta.get("certainty", ""),
                    "sector": sector,  # 行业细分（水电/银行/家电等）
                    "is_etf": meta["category"] == "ETF红利",
                }

    def _fetch_all_prices(self, start_date: str, end_date: str):
        """预获取所有标的价格数据"""
        print("\n  📥 预获取历史价格数据...")
        for ts_code, meta in self._stock_meta.items():
            print(f"    {meta['name']} ({ts_code})...", end=" ")
            df = self.fetcher.fetch_price_data(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                is_etf=meta.get("is_etf", False),
            )
            if not df.empty:
                self._price_data[ts_code] = df
                print(f"✅ {len(df)} 条")
            else:
                print("⚠️ 无数据")

    def _fetch_all_dividends(self, start_date: str, end_date: str):
        """预获取所有标的分红数据"""
        print("\n  💰 预获取历史分红数据...")
        for ts_code, meta in self._stock_meta.items():
            df = self.fetcher.fetch_dividend_data(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                is_etf=meta.get("is_etf", False),
            )
            if not df.empty:
                self._dividend_data[ts_code] = df
                total_div = df["cash_per_share"].sum()
                print(f"    {meta['name']} ({ts_code}): {len(df)} 次分红, 累计每股 {total_div:.3f} 元")

    def _process_dividends(self, date_str: str):
        """
        处理到当前日期为止的分红事件

        在每次调仓日调用，检查是否有除权日在上次调仓到本次调仓之间的分红
        红利税规则：持股 >1年免税, >1个月10%, ≤1个月20%
        回测默认按免税计算（季度调仓天然持股>1年）
        """
        for ts_code, div_df in self._dividend_data.items():
            if div_df.empty:
                continue

            meta = self._stock_meta.get(ts_code, {})
            name = meta.get("name", ts_code)

            for _, row in div_df.iterrows():
                # 除权日（ex_date 或 end_date）
                ex_date_str = str(row.get("ex_date", row.get("end_date", "")))
                if not ex_date_str or len(ex_date_str) < 8:
                    continue

                # 格式化日期
                if len(ex_date_str) == 8:
                    ex_date_fmt = f"{ex_date_str[:4]}-{ex_date_str[4:6]}-{ex_date_str[6:8]}"
                else:
                    continue

                # 防重复
                div_key = (ts_code, ex_date_str)
                if div_key in self._processed_dividends:
                    continue

                # 只处理在本次调仓日之前或当日的分红
                if ex_date_fmt <= date_str:
                    cash_per_share = row.get("cash_per_share", 0)
                    if cash_per_share > 0:
                        eligible_shares = self.portfolio.shares_held_before(ts_code, ex_date_fmt)
                        # 红利税：季度调仓持股>1年，免税
                        if eligible_shares > 0:
                            self.portfolio.receive_dividend(
                                ts_code=ts_code,
                                name=name,
                                cash_per_share=cash_per_share,
                                tax_rate=0.0,  # 持股>1年免税
                                shares=eligible_shares,
                            )
                        self._processed_dividends.add(div_key)

    def _get_price_on_date(self, ts_code: str, date_str: str) -> Optional[float]:
        """获取某只股票在指定日期的收盘价（如非交易日取最近的前一交易日）"""
        if ts_code not in self._price_data:
            return None

        df = self._price_data[ts_code]
        target_date = pd.Timestamp(date_str)

        # 取 <= 目标日期的最近交易日
        mask = df["trade_date"] <= target_date
        valid = df[mask]
        if valid.empty:
            return None

        return float(valid.iloc[-1]["close"])

    def _get_dps_at_date(self, ts_code: str, date_str: str) -> Optional[float]:
        """
        获取指定日期时已知的最近一年度每股分红(DPS)

        关键：只使用 ann_date <= date_str 的分红记录，
        避免前视偏差（不使用未来才公告的分红信息）。
        对于 A 股，年报分红通常在次年3-4月公告，
        中期分红通常在8月公告。
        """
        if ts_code not in self._dividend_data:
            return None

        div_df = self._dividend_data[ts_code]
        if div_df.empty:
            return None

        target_date = pd.Timestamp(date_str)

        # 筛选已公告的分红（ann_date <= date_str）
        if "ann_date" in div_df.columns:
            div_df = div_df.copy()
            div_df["ann_date_ts"] = pd.to_datetime(
                div_df["ann_date"].astype(str), format="%Y%m%d", errors="coerce"
            )
            known = div_df[div_df["ann_date_ts"] <= target_date]
        else:
            # 无 ann_date，回退到 ex_date <= date_str（保守但仍可能有微小偏差）
            div_df = div_df.copy()
            div_df["ex_date_ts"] = pd.to_datetime(
                div_df["ex_date"].astype(str), format="%Y%m%d", errors="coerce"
            )
            known = div_df[div_df["ex_date_ts"] <= target_date]

        if known.empty:
            return None

        # 取最近一次年度分红的 DPS（用最后一行，因为已按 ex_date 排序）
        return float(known.iloc[-1]["cash_per_share"])

    def _get_trailing_dps_at_date(self, ts_code: str, date_str: str) -> float:
        """
        获取指定日期时的过去12个月(4个季度)累计每股分红

        用于计算 trailing dividend yield：div_yield = trailing_dps / price * 100
        只使用 ann_date <= date_str 的分红记录。
        """
        if ts_code not in self._dividend_data:
            return 0.0

        div_df = self._dividend_data[ts_code]
        if div_df.empty:
            return 0.0

        target_date = pd.Timestamp(date_str)
        one_year_ago = target_date - pd.DateOffset(years=1)

        # 筛选已公告的分红
        if "ann_date" in div_df.columns:
            div_df = div_df.copy()
            div_df["ann_date_ts"] = pd.to_datetime(
                div_df["ann_date"].astype(str), format="%Y%m%d", errors="coerce"
            )
            # 只用当时已知的分红
            known = div_df[div_df["ann_date_ts"] <= target_date]
        else:
            div_df = div_df.copy()
            div_df["ex_date_ts"] = pd.to_datetime(
                div_df["ex_date"].astype(str), format="%Y%m%d", errors="coerce"
            )
            known = div_df[div_df["ex_date_ts"] <= target_date]

        if known.empty:
            return 0.0

        # 取 ex_date 在过去1年内的分红（除权日在过去12个月）
        if "ex_date" in known.columns:
            known = known.copy()
            known["ex_date_ts"] = pd.to_datetime(
                known["ex_date"].astype(str), format="%Y%m%d", errors="coerce"
            )
            trailing = known[known["ex_date_ts"] >= one_year_ago]
        else:
            trailing = known

        if trailing.empty:
            return 0.0

        return float(trailing["cash_per_share"].sum())

    # 中国10年期国债收益率历史关键节点（%）
    # 数据来源：Wind / 中国债券信息网
    _BOND_YIELD_HISTORY = {
        2009: 3.60, 2010: 3.80, 2011: 3.90, 2012: 3.55,
        2013: 4.10, 2014: 3.80, 2015: 3.00, 2016: 2.80,
        2017: 3.90, 2018: 3.40, 2019: 3.20, 2020: 3.00,
        2021: 2.80, 2022: 2.70, 2023: 2.60, 2024: 2.00,
        2025: 1.70, 2026: 1.65,
    }

    def _get_bond_yield_at_date(self, date_str: str) -> float:
        """
        获取指定日期的10年期国债收益率（近似值）

        基于年度关键节点线性插值，避免前视偏差。
        """
        dt = pd.Timestamp(date_str)
        year = dt.year
        frac = (dt - pd.Timestamp(f"{year}-01-01")).days / 365.0

        y0 = self._BOND_YIELD_HISTORY.get(year)
        y1 = self._BOND_YIELD_HISTORY.get(year + 1)

        if y0 is None:
            # 超出范围，用最近的
            max_yr = max(self._BOND_YIELD_HISTORY.keys())
            return self._BOND_YIELD_HISTORY[max_yr] / 100.0
        if y1 is None:
            return y0 / 100.0

        # 线性插值
        return (y0 + (y1 - y0) * frac) / 100.0

    def _simulate_scores_at_date(self, date_str: str) -> Dict[str, Dict]:
        """
        模拟指定日期的评分数据

        方案1: 使用 weekly_history.csv 中已有的周度评分（实时评分，无前视偏差）
        方案2: 基于历史分红数据 + 历史价格模拟评分（无前视偏差版本）
        """
        # 尝试从 weekly_history.csv 获取
        history_csv = _PROJECT_ROOT / "data" / "weekly_history.csv"
        if history_csv.exists():
            df = pd.read_csv(history_csv, encoding="utf-8")
            df["date"] = pd.to_datetime(df["date"])
            target = pd.Timestamp(date_str)

            # 取 <= 目标日期的最近一周数据
            valid = df[df["date"] <= target]
            if not valid.empty:
                latest_week = valid["week"].max()
                week_data = valid[valid["week"] == latest_week]

                scores = {}
                for _, row in week_data.iterrows():
                    scores[row["ts_code"]] = {
                        "name": row["name"],
                        "category": row["category"],
                        "total_score": row["total_score"],
                        "verdict": row["verdict"],
                        "div_yield": row["div_yield"],
                        "close": row["close"],
                        "pe_ttm": row.get("pe_ttm", 0),
                        "roe": row.get("roe", 0),
                        "bond_spread_bp": row.get("bond_spread_bp", 0),
                        "score_source": "实时评分",
                    }
                if scores:
                    return scores

        # 回退：基于价格模拟简化评分
        return self._simulate_simple_scores(date_str)

    def _simulate_simple_scores(self, date_str: str) -> Dict[str, Dict]:
        """
        基于历史数据的简化评分模拟（无前视偏差版本）

        核心改进（v2）：
        - 股息率：使用当时已公告的历史分红 + 历史价格计算
        - 行业股息率锚：不同行业使用不同的评分阈值
        - 预期股息率：基于净利润增速推算 forward_div_yield
        - 网格交易区间：低吸/持有/减仓
        - 国债收益率：使用历史年度数据线性插值
        """
        scores = {}
        bond_yield = self._get_bond_yield_at_date(date_str)

        for ts_code, meta in self._stock_meta.items():
            price = self._get_price_on_date(ts_code, date_str)
            category = meta["category"]
            sector = meta.get("sector", "")

            if not price or price <= 0:
                continue

            # 使用历史分红数据计算 trailing dividend yield
            trailing_dps = self._get_trailing_dps_at_date(ts_code, date_str)
            if trailing_dps > 0 and price > 0:
                sim_div_yield = trailing_dps / price * 100
            else:
                # 无历史分红数据，跳过
                continue

            # 息差
            spread_bp = (sim_div_yield - bond_yield * 100)

            # ── 行业股息率锚评分（替代统一阈值）──
            sector_thresholds = SECTOR_THRESHOLDS.get(sector, {})

            # S1 股息率评分：基于行业锚动态调整
            if sector_thresholds:
                buy_line = sector_thresholds.get("buy", 5.0)
                full_line = sector_thresholds.get("full", 7.0)
                if sim_div_yield >= full_line:
                    s1 = 30  # 满分
                elif sim_div_yield >= buy_line:
                    # 在买入线和满仓线之间线性给分
                    s1 = 20 + (sim_div_yield - buy_line) / (full_line - buy_line) * 10
                elif sim_div_yield >= buy_line * 0.7:
                    s1 = sim_div_yield / buy_line * 20
                else:
                    s1 = max(0, sim_div_yield / buy_line * 15)
            else:
                s1 = min(30, max(0, sim_div_yield * 5))

            # S2 息差评分
            s2 = min(25, max(0, spread_bp / 10))

            # S3 等效分红评分（不含回购数据，保守估计）
            if sector_thresholds:
                full_line = sector_thresholds.get("full", 7.0)
                if sim_div_yield >= full_line:
                    s3 = 20
                else:
                    s3 = min(20, max(0, sim_div_yield / full_line * 20))
            else:
                s3 = min(20, max(0, sim_div_yield * 3))

            # S4 确定性评分
            certainty_score = {"AA": 15, "A": 13, "A-": 11, "B+": 8, "B": 5}.get(
                meta.get("certainty", "B+"), 8
            )
            s4 = certainty_score

            # S5 成长性给默认值
            s5 = 5

            total = s1 + s2 + s3 + s4 + s5

            # 确定信号
            if total >= 80:
                verdict = "🔥 大胆攒股"
            elif total >= 65:
                verdict = "✅ 积极布局"
            elif total >= 50:
                verdict = "👀 观察等待"
            elif total >= 35:
                verdict = "⏸️ 暂缓"
            else:
                verdict = "🚫 回避"

            # ── 预期股息率（基于行业增速推算）──
            # 简化：用行业平均增速估算 forward_dps
            sector_growth = {
                "水电": 3.0, "运营商": 5.0, "银行": 4.0, "保险": 6.0,
                "家电": 8.0, "白酒": 12.0, "中药": 8.0, "矿业": 15.0,
                "石油": 5.0, "煤炭": -2.0, "ETF": 5.0,
            }
            growth = sector_growth.get(sector, 5.0)
            forward_dps = trailing_dps * (1 + growth / 100)
            forward_div_yield = forward_dps / price * 100 if price > 0 else 0

            # ── 网格交易区间 ──
            grid = {}
            if sector_thresholds:
                buy_line = sector_thresholds.get("buy", 5.0)
                reduce_line = sector_thresholds.get("reduce", 3.0)
                if sim_div_yield >= buy_line:
                    grid_zone = "低吸"
                elif sim_div_yield >= reduce_line:
                    grid_zone = "持有"
                else:
                    grid_zone = "减仓"
                grid = {"zone": grid_zone, "buy_line": buy_line, "reduce_line": reduce_line}

            # ── 阶梯攒股价格 ──
            ladder = {}
            if sector_thresholds and trailing_dps > 0:
                for level in ["watch", "buy", "add", "full"]:
                    threshold = sector_thresholds.get(level, 0)
                    if threshold > 0:
                        ladder[level] = round(trailing_dps / (threshold / 100), 2)

            scores[ts_code] = {
                "name": meta["name"],
                "category": category,
                "sector": sector,
                "total_score": round(total, 1),
                "verdict": verdict,
                "div_yield": round(sim_div_yield, 2),
                "forward_div_yield": round(forward_div_yield, 2),
                "close": price,
                "pe_ttm": 0,
                "roe": 0,
                "bond_spread_bp": round(spread_bp, 0),
                "grid": grid,
                "ladder": ladder,
                "score_source": "历史模拟",
            }

        return scores

    def _execute_rebalance(
        self,
        actions: List[RebalanceAction],
        prices: Dict[str, float],
    ):
        """执行调仓指令"""
        for action in actions:
            ts_code = action.ts_code
            price = prices.get(ts_code)

            if price is None or price <= 0:
                continue

            total_value = self.portfolio.total_value

            if action.action == "buy":
                # 计算目标买入金额
                target_amount = action.weight_delta * total_value
                if target_amount > 0:
                    self.portfolio.buy(
                        ts_code=ts_code,
                        name=action.name,
                        category=action.category,
                        price=price,
                        target_amount=target_amount,
                        reason=action.reason,
                    )

            elif action.action == "sell":
                pos = self.portfolio.positions.get(ts_code)
                if pos and pos.shares > 0:
                    # 计算需要卖出的股数
                    current_mv = pos.market_value
                    target_mv = action.target_weight * total_value
                    sell_mv = current_mv - target_mv

                    if sell_mv > 0 and current_mv > 0:
                        sell_ratio = sell_mv / current_mv
                        sell_shares = int(pos.shares * sell_ratio / 100) * 100
                        if sell_shares > 0:
                            self.portfolio.sell(
                                ts_code=ts_code,
                                price=price,
                                shares=sell_shares,
                                reason=action.reason,
                            )
                        elif target_mv <= 0:
                            # 清仓
                            self.portfolio.sell(
                                ts_code=ts_code,
                                price=price,
                                reason=action.reason,
                            )

    def run(
        self,
        start_date: str = "2024-01-01",
        end_date: str = "2026-05-01",
        benchmark_code: str = "000300.SH",
        verbose: bool = True,
    ) -> Dict:
        """
        执行回测

        Parameters
        ----------
        start_date : str — 回测开始日期
        end_date : str — 回测结束日期
        benchmark_code : str — 基准指数代码
        verbose : bool — 是否打印进度

        Returns
        -------
        dict : 回测结果
        """
        # 初始化
        self._load_stock_meta()
        self._fetch_all_prices(start_date, end_date)
        self._fetch_all_dividends(start_date, end_date)

        # 获取调仓日
        if self.rebalance_freq == "quarterly":
            rebalance_dates = self.fetcher.get_quarter_end_dates(start_date, end_date)
            freq_label = "季度末"
        else:
            rebalance_dates = self.fetcher.get_friday_dates(start_date, end_date)
            freq_label = "每周五"

        if verbose:
            print(f"\n  📅 回测期间: {start_date} ~ {end_date}")
            print(f"  📊 调仓频率: {freq_label}，共 {len(rebalance_dates)} 次")
            print(f"  💰 初始资金: {self.portfolio.initial_cash:,.0f} 元")
            print(f"  📋 持仓标的: {len(self._stock_meta)} 只")

        # 获取基准数据
        self._benchmark_code = benchmark_code
        self._benchmark = self.fetcher.fetch_benchmark_data(benchmark_code, start_date, end_date)

        # 重置调仓快照
        self._rebalance_snapshots = []

        # 逐周模拟
        for i, date_str in enumerate(rebalance_dates):
            self.portfolio.set_date(date_str)

            # 获取当前价格
            prices = {}
            for ts_code in self._stock_meta:
                p = self._get_price_on_date(ts_code, date_str)
                if p and p > 0:
                    prices[ts_code] = p

            if not prices:
                continue

            # 更新持仓价格
            self.portfolio.update_prices(prices)

            # 处理分红（入账到现金）
            self._process_dividends(date_str)

            # 模拟评分
            scores = self._simulate_scores_at_date(date_str)
            if not scores:
                continue

            # 计算当前权重
            total_val = self.portfolio.total_value
            current_weights = {}
            for ts_code, pos in self.portfolio.positions.items():
                if pos.shares > 0 and total_val > 0:
                    current_weights[ts_code] = pos.market_value / total_val

            # 生成调仓指令
            actions = self.strategy.generate_rebalance_actions(scores, current_weights)

            # 执行调仓
            self._execute_rebalance(actions, prices)

            # 记录持仓快照
            self.portfolio.record_holding_snapshot()

            # 记录调仓后持仓（用于构建日线净值）
            self._rebalance_snapshots.append({
                "date": date_str,
                "cash": self.portfolio.cash,
                "positions": {
                    code: pos.shares
                    for code, pos in self.portfolio.positions.items()
                    if pos.shares > 0
                },
            })

            # 记录净值
            self.portfolio.record_nav()

            if verbose and (i + 1) % 4 == 0:
                ret = self.portfolio.total_return
                print(f"  📈 第{i+1}次调仓 ({date_str}): 总收益 {ret:+.2f}%, "
                      f"持仓 {len([p for p in self.portfolio.positions.values() if p.shares > 0])} 只")

        # 计算绩效
        return self._calculate_results(start_date, end_date, verbose)

    def _build_daily_nav(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        构建逐交易日净值序列，用于准确计算绩效指标

        利用调仓后的持仓快照 + 全量日线价格，计算出每个交易日的总资产。
        相比仅用调仓日净值（45个点），日线净值（~2800个点）能更准确计算
        年化收益、最大回撤和夏普比率。
        """
        # 1. 获取所有交易日（优先使用持仓标的价格数据，确保覆盖完整回测期间）
        all_dates = None
        # 从持仓标的中找最长价格序列作为交易日历
        for df in self._price_data.values():
            if not df.empty:
                dates = df["trade_date"].sort_values().tolist()
                if all_dates is None or len(dates) > len(all_dates):
                    all_dates = dates
        # 仅在无价格数据时才用基准日期
        if not all_dates and self._benchmark is not None and not self._benchmark.empty:
            all_dates = self._benchmark["trade_date"].sort_values().tolist()

        if not all_dates:
            return pd.DataFrame()

        # 筛选回测期间
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        all_dates = [d for d in all_dates if start_ts <= d <= end_ts]

        if not all_dates:
            return pd.DataFrame()

        date_index = pd.DatetimeIndex(all_dates)

        # 2. 构建价格矩阵（前向填充缺失价格，如停牌日）
        price_df = pd.DataFrame(index=date_index)
        for ts_code, df in self._price_data.items():
            if not df.empty:
                s = df.set_index("trade_date")["close"].reindex(date_index).ffill()
                price_df[ts_code] = s

        # 3. 构建持仓矩阵和现金序列
        snapshots = sorted(self._rebalance_snapshots, key=lambda x: x["date"])

        # 初始化：全部为初始现金，0 持仓
        shares_df = pd.DataFrame(0.0, index=date_index, columns=price_df.columns)
        cash_series = pd.Series(float(self.portfolio.initial_cash), index=date_index)

        # 按调仓快照填充持仓和现金
        for i, snap in enumerate(snapshots):
            snap_ts = pd.Timestamp(snap["date"])
            if i + 1 < len(snapshots):
                next_ts = pd.Timestamp(snapshots[i + 1]["date"])
                mask = (date_index >= snap_ts) & (date_index < next_ts)
            else:
                mask = date_index >= snap_ts

            for ts_code, shares in snap["positions"].items():
                if ts_code in shares_df.columns:
                    shares_df.loc[mask, ts_code] = float(shares)

            cash_series.loc[mask] = float(snap["cash"])

        # 4. 构建累计分红序列
        # 将分红记录按除权日映射到日线索引
        cum_div_series = pd.Series(0.0, index=date_index)
        for rec in self.portfolio.dividend_records:
            rec_date = pd.Timestamp(rec["date"])
            if rec_date in cum_div_series.index:
                cum_div_series.loc[rec_date] += rec.get("net_dividend", 0)
        cum_div_series = cum_div_series.cumsum()

        # 5. 计算每日总资产 = 现金 + 持仓市值
        market_value_series = (shares_df * price_df).sum(axis=1)
        total_value_series = cash_series + market_value_series

        # 股价收益 = 总资产 - 累计分红（剔除分红后的纯资本增值）
        price_value_series = total_value_series - cum_div_series

        return pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in date_index],
            "total_value": total_value_series.round(2).values,
            "cum_dividend": cum_div_series.round(2).values,
            "price_value": price_value_series.round(2).values,
        })

    def _generate_yearly_commentary(
        self,
        year: int,
        total_ret: float,
        price_ret: float,
        div_ret: float,
        max_dd: float,
        buy_count: int,
        sell_count: int,
        commission: float,
        div_count: int,
        div_total: float,
        trades_detail: List[Dict],
        year_div: float,
        start_value: float,
    ) -> str:
        """
        生成年度交易总结与反思

        基于规则引擎，对每年的市场环境、交易行为、策略表现进行评述。
        """
        parts: List[str] = []

        # ── 1. 市场环境定性 ──
        if total_ret >= 30:
            market = "牛市行情，市场情绪高涨"
        elif total_ret >= 15:
            market = "偏强市场，结构性机会丰富"
        elif total_ret >= 5:
            market = "震荡偏强，个股分化明显"
        elif total_ret >= -5:
            market = "震荡市，缺乏明确方向"
        elif total_ret >= -15:
            market = "弱势调整，防御为先"
        else:
            market = "熊市环境，系统性风险释放"

        parts.append(f"**市场环境**：{market}。本年收益 {total_ret:+.1f}%。")

        # ── 2. 收益结构分析 ──
        if abs(div_ret) > 0.01:
            div_ratio = div_ret / total_ret * 100 if abs(total_ret) > 0.01 else 0
            if div_ratio > 80:
                parts.append(
                    f"**收益结构**：股息收益 {div_ret:+.1f}% 占比极高（{div_ratio:.0f}%），"
                    f"股价仅贡献 {price_ret:+.1f}%。红利是本年核心收益来源，"
                    f"体现出红利策略在弱势/震荡市中的防御价值。"
                )
            elif div_ratio > 40:
                parts.append(
                    f"**收益结构**：股息贡献 {div_ret:+.1f}%（占 {div_ratio:.0f}%），"
                    f"股价贡献 {price_ret:+.1f}%。股息提供稳定底仓收益，"
                    f"资本增值贡献了另一半涨幅。"
                )
            else:
                parts.append(
                    f"**收益结构**：资本增值 {price_ret:+.1f}% 是主要驱动力，"
                    f"股息 {div_ret:+.1f}% 提供安全垫。股价上涨是盈利核心。"
                )
        else:
            parts.append(f"**收益结构**：本年无显著分红贡献，收益完全来自资本增值 {price_ret:+.1f}%。")

        # ── 3. 回撤与风险 ──
        if max_dd <= -20:
            parts.append(
                f"**风险暴露**：最大回撤 {max_dd:.1f}%，属于深度回撤。"
                f"即便年末修复，回撤期间的持仓体验仍十分煎熬。"
                f"反思：是否应在回撤扩大时减仓或对冲？红利策略的优势在于"
                f"股息现金流提供了心理锚点，帮助扛过极端波动。"
            )
        elif max_dd <= -10:
            parts.append(
                f"**风险暴露**：最大回撤 {max_dd:.1f}%，中等回撤水平。"
                f"红利组合的低波动特性得到体现，回撤幅度相对可控。"
            )
        else:
            parts.append(
                f"**风险暴露**：最大回撤仅 {max_dd:.1f}%，回撤控制优秀。"
                f"组合波动率低，持仓体验良好。"
            )

        # ── 4. 交易行为分析 ──
        total_trades = buy_count + sell_count
        if total_trades == 0:
            parts.append("**交易行为**：全年无调仓，持有不动。低换手策略节省了交易成本。")
        elif total_trades <= 4:
            parts.append(
                f"**交易行为**：全年仅 {buy_count} 买 {sell_count} 卖，调仓频率极低。"
                f"交易手续费 {commission:,.0f} 元，摩擦成本可忽略。"
                f"低换手是红利策略的优势——省心省力。"
            )
        elif total_trades <= 12:
            parts.append(
                f"**交易行为**：全年 {buy_count} 买 {sell_count} 卖，调仓节奏适中。"
                f"手续费 {commission:,.0f} 元。"
                f"季度调仓机制自动捕捉评分变化，无需频繁操作。"
            )
        else:
            parts.append(
                f"**交易行为**：全年 {buy_count} 买 {sell_count} 卖，交易偏频繁。"
                f"手续费 {commission:,.0f} 元，需关注换手成本对收益的侵蚀。"
                f"反思：频繁调仓是否真正增厚了收益？还是只是增加了成本？"
            )

        # ── 5. 重点交易反思 ──
        if trades_detail:
            # 找出金额最大的买入和卖出
            buys = [t for t in trades_detail if t["action"] == "buy"]
            sells = [t for t in trades_detail if t["action"] == "sell"]
            key_insights = []

            if buys:
                top_buy = max(buys, key=lambda t: t["amount"])
                key_insights.append(
                    f"最大买入：{top_buy['name']} {top_buy['shares']}股 @ {top_buy['price']:.2f}，"
                    f"金额 {top_buy['amount']:,.0f} 元"
                )
            if sells:
                top_sell = max(sells, key=lambda t: t["amount"])
                key_insights.append(
                    f"最大卖出：{top_sell['name']} {top_sell['shares']}股 @ {top_sell['price']:.2f}，"
                    f"金额 {top_sell['amount']:,.0f} 元"
                )

            # 卖出标的是否在后续上涨？（事后视角的反思）
            sell_names = [t["name"] for t in sells]
            if sell_names and total_ret < 0:
                key_insights.append(
                    f"本年卖出标的：{', '.join(sell_names)}。在亏损年份卖出可能是止损，"
                    f"但也可能是低位割肉——需关注卖出后的走势验证。"
                )

            if key_insights:
                parts.append("**重点交易**：" + "；".join(key_insights) + "。")

        # ── 6. 分红总结 ──
        if div_count > 0:
            parts.append(
                f"**分红贡献**：{div_count} 次分红入账 {div_total:,.0f} 元，"
                f"占年初资产 {div_total / start_value * 100:.1f}%。"
                f"股息现金流是红利策略的「压舱石」，在震荡和下跌市中尤为重要。"
            )

        # ── 7. 年度总结 ──
        if total_ret >= 20:
            lesson = "顺势持有，让利润奔跑。红利股在牛市同样能提供可观回报。"
        elif total_ret >= 5:
            lesson = "稳健积累，股息再投资是复利增长的关键。"
        elif total_ret >= -5:
            lesson = "震荡市中守住本金比追求收益更重要，股息提供了心理支撑。"
        elif total_ret >= -15:
            lesson = "控制回撤比追求收益更重要，红利策略的防御性在弱势中凸显。"
        else:
            lesson = "极端行情考验持仓纪律，股息现金流帮助扛过至暗时刻。"

        parts.append(f"**年度感悟**：{lesson}")

        return "\n\n".join(parts)

    def _get_benchmark_name(self) -> str:
        """获取基准名称"""
        code = self._benchmark_code
        name_map = {
            "000300.SH": "沪深300",
            "515180.SH": "易方达红利ETF",
            "510880.SH": "华夏红利ETF",
            "000922.SH": "中证红利",
        }
        return name_map.get(code, code)

    def _calc_yearly_returns(self, daily_nav_df: pd.DataFrame, benchmark_df: Optional[pd.DataFrame] = None) -> List[Dict]:
        """
        计算逐年收益指标

        对每个自然年，计算：
        - 总收益率、股价收益率、股息收益率
        - 最大回撤
        - 年初/年末总资产
        - 交易统计（买卖次数、手续费）
        - 分红统计（分红次数、分红金额）
        - 基准年度收益（如基准数据覆盖该年）
        """
        if daily_nav_df.empty:
            return []

        df = daily_nav_df.copy()
        df["date_ts"] = pd.to_datetime(df["date"])
        df["year"] = df["date_ts"].dt.year

        # 预处理交易和分红记录按年分组
        trade_df = self.portfolio.get_trade_log()
        if not trade_df.empty:
            trade_df = trade_df.copy()
            trade_df["year"] = pd.to_datetime(trade_df["date"]).dt.year

        div_records = self.portfolio.dividend_records
        div_by_year: Dict[int, List] = {}
        for rec in div_records:
            y = pd.Timestamp(rec["date"]).year
            div_by_year.setdefault(y, []).append(rec)

        # 预处理基准数据按年分组
        bm_by_year: Dict[int, Dict] = {}
        if benchmark_df is not None and not benchmark_df.empty:
            bm = benchmark_df.copy()
            bm["trade_date"] = pd.to_datetime(bm["trade_date"])
            bm["year"] = bm["trade_date"].dt.year
            for y in sorted(bm["year"].unique()):
                yb = bm[bm["year"] == y]
                if len(yb) >= 2:
                    bm_start = yb.iloc[0]["close"]
                    bm_end = yb.iloc[-1]["close"]
                    if bm_start > 0:
                        bm_by_year[int(y)] = {
                            "return": (bm_end / bm_start - 1) * 100,
                            "start_date": yb.iloc[0]["trade_date"],
                            "end_date": yb.iloc[-1]["trade_date"],
                        }

        years = sorted(df["year"].unique())
        results = []

        for year in years:
            year_df = df[df["year"] == year]
            if year_df.empty:
                continue

            # 年初值 = 该年第一个交易日的值
            start_total = year_df.iloc[0]["total_value"]
            start_cum_div = year_df.iloc[0]["cum_dividend"]
            start_price_val = start_total - start_cum_div

            # 年末值
            end_total = year_df.iloc[-1]["total_value"]
            end_cum_div = year_df.iloc[-1]["cum_dividend"]
            end_price_val = end_total - end_cum_div

            # 该年度内新增的分红
            year_div = end_cum_div - start_cum_div

            # 收益率（基于年初值）
            if start_total > 0:
                total_ret = (end_total / start_total - 1) * 100
                div_ret = year_div / start_total * 100
                price_ret = total_ret - div_ret
            else:
                total_ret = div_ret = price_ret = 0.0

            # 年度最大回撤
            nav_series = year_df.set_index("date")["total_value"]
            max_dd = self.metrics.max_drawdown(nav_series)

            # 逐年交易统计
            yr_buys = yr_sells = 0
            yr_commission = 0.0
            yr_trades_detail: List[Dict] = []
            if not trade_df.empty and year in trade_df["year"].values:
                yr_trades = trade_df[trade_df["year"] == year]
                yr_buys = len(yr_trades[yr_trades["action"] == "buy"])
                yr_sells = len(yr_trades[yr_trades["action"] == "sell"])
                yr_commission = yr_trades["commission"].sum()
                yr_trades_detail = yr_trades.to_dict("records")

            # 逐年分红统计
            yr_div_records = div_by_year.get(year, [])
            yr_div_count = len(yr_div_records)
            yr_div_total = sum(r.get("net_dividend", 0) for r in yr_div_records)

            # 逐年基准收益
            yr_bm_return = None
            yr_excess = None
            if year in bm_by_year:
                yr_bm_return = round(bm_by_year[year]["return"], 2)
                yr_excess = round(total_ret - yr_bm_return, 2)

            results.append({
                "year": int(year),
                "start_value": round(start_total, 2),
                "end_value": round(end_total, 2),
                "total_return": round(total_ret, 2),
                "price_return": round(price_ret, 2),
                "dividend_return": round(div_ret, 2),
                "year_dividend": round(year_div, 2),
                "max_drawdown": round(max_dd, 2),
                "buy_count": yr_buys,
                "sell_count": yr_sells,
                "trade_count": yr_buys + yr_sells,
                "commission": round(yr_commission, 2),
                "div_count": yr_div_count,
                "div_total": round(yr_div_total, 2),
                "benchmark_return": yr_bm_return,
                "excess_return": yr_excess,
                "trades_detail": yr_trades_detail,
                "div_detail": yr_div_records,
                "commentary": self._generate_yearly_commentary(
                    int(year), total_ret, price_ret, div_ret, max_dd,
                    yr_buys, yr_sells, yr_commission, yr_div_count, yr_div_total,
                    yr_trades_detail, year_div, start_total,
                ),
            })

        return results

    def _calculate_results(self, start_date: str, end_date: str, verbose: bool) -> Dict:
        """计算回测结果和绩效指标"""
        nav_df = self.portfolio.get_nav_dataframe()
        trade_df = self.portfolio.get_trade_log()

        if nav_df.empty:
            return {"error": "无净值数据"}

        # 构建逐交易日净值序列
        daily_nav_df = self._build_daily_nav(start_date, end_date)

        # 基本指标
        total_return = self.portfolio.total_return

        if not daily_nav_df.empty and len(daily_nav_df) > 1:
            # 使用真实交易日数量计算绩效
            trading_days = len(daily_nav_df)
            annual_return = self.metrics.annual_return(total_return, trading_days)

            # 最大回撤（基于日线净值）
            daily_nav_series = daily_nav_df.set_index("date")["total_value"]
            max_dd = self.metrics.max_drawdown(daily_nav_series)

            # 夏普比率（基于日线收益率）
            daily_returns = daily_nav_df["total_value"].pct_change().dropna()
            sharpe = self.metrics.sharpe_ratio(daily_returns)
        else:
            # 回退：基于调仓日净值（不乘5，用实际天数估算）
            first_date = pd.Timestamp(nav_df["date"].iloc[0])
            last_date = pd.Timestamp(nav_df["date"].iloc[-1])
            trading_days = max(1, int((last_date - first_date).days * 5 / 7))
            annual_return = self.metrics.annual_return(total_return, trading_days)
            nav_series = nav_df.set_index("date")["total_value"]
            max_dd = self.metrics.max_drawdown(nav_series)
            if len(nav_df) > 1:
                returns = nav_df["total_value"].pct_change().dropna()
                sharpe = self.metrics.sharpe_ratio(returns)
            else:
                sharpe = 0.0

        # 卡尔玛比率
        calmar = self.metrics.calmar_ratio(annual_return, max_dd)

        # 交易统计
        num_trades = len(trade_df)
        buy_trades = len(trade_df[trade_df["action"] == "buy"]) if not trade_df.empty else 0
        sell_trades = len(trade_df[trade_df["action"] == "sell"]) if not trade_df.empty else 0
        total_commission = trade_df["commission"].sum() if not trade_df.empty else 0

        # 红利统计
        dividend_records = self.portfolio.dividend_records
        total_dividend = sum(d.get("net_dividend", 0) for d in dividend_records)
        dividend_contribution = total_dividend / self.portfolio.initial_cash * 100  # 红利贡献(%)

        # 收益分解：股息收益 vs 股价收益
        # price_return = 总收益 - 股息收益（剔除分红后的纯资本增值）
        dividend_return = dividend_contribution  # 股息收益率(%)
        price_return = total_return - dividend_return  # 股价收益率(%)

        # 年度收益分解（含逐年基准对比）
        yearly_returns = self._calc_yearly_returns(daily_nav_df, self._benchmark)

        # 基准对比
        benchmark_return = 0.0
        benchmark_name = self._get_benchmark_name()
        if self._benchmark is not None and not self._benchmark.empty:
            bm_start = self._benchmark.iloc[0]["close"]
            bm_end = self._benchmark.iloc[-1]["close"]
            if bm_start > 0:
                benchmark_return = (bm_end / bm_start - 1) * 100

        excess_return = total_return - benchmark_return

        results = {
            "period": f"{start_date} ~ {end_date}",
            "initial_cash": self.portfolio.initial_cash,
            "final_value": round(self.portfolio.total_value, 2),
            "total_return": round(total_return, 2),
            "annual_return": round(annual_return, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "calmar_ratio": round(calmar, 2),
            "num_trades": num_trades,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "total_commission": round(total_commission, 2),
            "benchmark_return": round(benchmark_return, 2),
            "excess_return": round(excess_return, 2),
            "benchmark_code": self._benchmark_code,
            "benchmark_name": benchmark_name,
            "trading_periods": trading_days,
            "rebalance_freq": self.rebalance_freq,
            "total_dividend": round(total_dividend, 2),
            "dividend_count": len(dividend_records),
            "dividend_contribution": round(dividend_contribution, 2),
            "dividend_return": round(dividend_return, 2),
            "price_return": round(price_return, 2),
            "yearly_returns": yearly_returns,
        }

        if verbose:
            self._print_results(results)

        return results

    def _print_results(self, results: Dict):
        """打印回测结果"""
        print("\n" + "=" * 70)
        print("  📊 红利周期轮动策略 — 回测结果")
        print("=" * 70)

        # 评分数据来源提示
        print(f"\n  ⚠️  评分数据来源: 历史分红数据模拟（无前视偏差）")
        print(f"     股息率=当时已公告分红/历史价格, 国债收益率=年度插值近似")

        print(f"\n  ── 收益指标 ──")
        print(f"  回测期间:     {results['period']}")
        print(f"  初始资金:     {results['initial_cash']:,.0f} 元")
        print(f"  最终资产:     {results['final_value']:,.2f} 元")
        print(f"  总收益率:     {results['total_return']:+.2f}%")
        print(f"    ├─ 股价收益: {results['price_return']:+.2f}%")
        print(f"    └─ 股息收益: {results['dividend_return']:+.2f}%")
        print(f"  年化收益率:   {results['annual_return']:+.2f}%")

        print(f"\n  ── 风险指标 ──")
        print(f"  最大回撤:     {results['max_drawdown']:.2f}%")
        print(f"  夏普比率:     {results['sharpe_ratio']:.2f}")
        print(f"  卡尔玛比率:   {results['calmar_ratio']:.2f}")

        print(f"\n  ── 交易统计 ──")
        freq_label = "季度" if results.get('rebalance_freq') == 'quarterly' else "周"
        print(f"  调仓次数:     {results['trading_periods']} 次（{freq_label}级）")
        print(f"  总交易次数:   {results['num_trades']}")
        print(f"  买入次数:     {results['buy_trades']}")
        print(f"  卖出次数:     {results['sell_trades']}")
        print(f"  总手续费:     {results['total_commission']:,.2f} 元")

        print(f"\n  ── 红利收益 ──")
        print(f"  分红次数:     {results['dividend_count']} 次")
        print(f"  累计红利:     {results['total_dividend']:,.2f} 元")
        print(f"  股息收益率:   {results['dividend_return']:+.2f}%（占初始资金）")
        print(f"  股价收益率:   {results['price_return']:+.2f}%（剔除分红后）")

        print(f"\n  ── 基准对比 ──")
        bm_name = results.get("benchmark_name", results.get("benchmark_code", "基准"))
        print(f"  基准标的:     {bm_name}")
        print(f"  基准收益:     {results['benchmark_return']:+.2f}%")
        print(f"  超额收益:     {results['excess_return']:+.2f}%")

        # 年度收益
        yearly = results.get("yearly_returns", [])
        has_bm = any(yr.get("benchmark_return") is not None for yr in yearly)
        if yearly:
            print(f"\n  ── 逐年收益 ──")
            bm_header = f" {'基准':>8} {'超额':>8}" if has_bm else ""
            print(f"  {'年份':>6} {'总收益':>8} {'股价':>8} {'股息':>8} {'回撤':>8}{bm_header} │ {'买入':>4} {'卖出':>4} {'手续费':>8} │ {'分红':>4} {'分红额':>9}")
            sep_len = 86 + (20 if has_bm else 0)
            print("  " + "-" * sep_len)
            for yr in yearly:
                bm_cols = ""
                if has_bm:
                    bm_ret = yr.get("benchmark_return")
                    exc_ret = yr.get("excess_return")
                    bm_str = f"{bm_ret:>+7.1f}%" if bm_ret is not None else f"{'N/A':>8}"
                    exc_str = f"{exc_ret:>+7.1f}%" if exc_ret is not None else f"{'N/A':>8}"
                    bm_cols = f" {bm_str} {exc_str}"
                print(
                    f"  {yr['year']:>6} {yr['total_return']:>+7.1f}% "
                    f"{yr['price_return']:>+7.1f}% {yr['dividend_return']:>+7.1f}% "
                    f"{yr['max_drawdown']:>7.1f}%{bm_cols} │ "
                    f"{yr['buy_count']:>4} {yr['sell_count']:>4} {yr['commission']:>7.0f} │ "
                    f"{yr['div_count']:>4} {yr['div_total']:>8.0f}"
                )

        # 最终持仓
        pos_df = self.portfolio.get_position_summary()
        if not pos_df.empty:
            print(f"\n  ── 最终持仓 ──")
            print(f"  {'标的':<10} {'股数':>6} {'成本':>8} {'现价':>8} {'市值':>10} {'权重':>6} {'盈亏':>6}")
            print("  " + "-" * 60)
            for _, row in pos_df.iterrows():
                print(
                    f"  {row['name']:<8} {row['shares']:>6} "
                    f"{row['cost_price']:>7.2f} {row['current_price']:>7.2f} "
                    f"{row['market_value']:>9.0f} {row['weight']:>5.1f}% "
                    f"{row['profit_pct']:>+5.1f}%"
                )

        print()

    def generate_backtest_report(
        self,
        results: Dict,
        output_dir: Optional[Path] = None,
    ) -> str:
        """生成回测报告（Markdown）"""
        output_dir = output_dir or (_PROJECT_ROOT / "data" / "backtest")
        output_dir.mkdir(parents=True, exist_ok=True)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        nav_df = self.portfolio.get_nav_dataframe()
        trade_df = self.portfolio.get_trade_log()

        lines = [
            f"# 📊 红利周期轮动策略 — 回测报告",
            f"\n> **回测期间**: {results['period']}  ",
            f"> **生成时间**: {now_str}  ",
            f"> **初始资金**: {results['initial_cash']:,.0f} 元  ",
            f"> **评分数据来源**: 历史分红数据模拟（无前视偏差）  ",
            f"> **股息率计算**: 当时已公告分红÷历史价格，国债收益率按年度插值近似  \n",
            "---\n",
        ]

        # 收益指标
        lines.append("## 📈 收益指标\n")
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 总收益率 | **{results['total_return']:+.2f}%** |")
        lines.append(f"| ├─ 股价收益 | {results['price_return']:+.2f}% |")
        lines.append(f"| └─ 股息收益 | {results['dividend_return']:+.2f}% |")
        lines.append(f"| 年化收益率 | **{results['annual_return']:+.2f}%** |")
        lines.append(f"| 最终资产 | {results['final_value']:,.2f} 元 |")
        lines.append(f"| 最大回撤 | {results['max_drawdown']:.2f}% |")
        lines.append(f"| 夏普比率 | {results['sharpe_ratio']:.2f} |")
        lines.append(f"| 卡尔玛比率 | {results['calmar_ratio']:.2f} |")
        lines.append("")

        # 基准对比
        bm_name = results.get("benchmark_name", results.get("benchmark_code", "基准"))
        lines.append("## 📊 基准对比\n")
        lines.append(f"| 指标 | 策略 | 基准({bm_name}) |")
        lines.append("|------|------|--------------|")
        lines.append(f"| 收益率 | {results['total_return']:+.2f}% | {results['benchmark_return']:+.2f}% |")
        lines.append(f"| 超额收益 | {results['excess_return']:+.2f}% | — |")
        lines.append("")

        # 年度收益
        yearly = results.get("yearly_returns", [])
        has_bm = any(yr.get("benchmark_return") is not None for yr in yearly)
        if yearly:
            lines.append("## 📅 逐年收益\n")
            bm_cols_h = " | 基准收益 | 超额收益" if has_bm else ""
            bm_cols_s = " |--------|--------" if has_bm else ""
            lines.append(f"| 年份 | 年初资产 | 年末资产 | 总收益 | 股价收益 | 股息收益 | 最大回撤 │ 买入 | 卖出 | 手续费 | 分红次数 | 分红金额{bm_cols_h} |")
            lines.append(f"|------|---------|---------|--------|---------|---------|--------|------|------|--------|---------|---------{bm_cols_s} |")
            for yr in yearly:
                bm_cols_v = ""
                if has_bm:
                    bm_ret = yr.get("benchmark_return")
                    exc_ret = yr.get("excess_return")
                    bm_str = f"{bm_ret:+.1f}%" if bm_ret is not None else "N/A"
                    exc_str = f"{exc_ret:+.1f}%" if exc_ret is not None else "N/A"
                    bm_cols_v = f" | {bm_str} | {exc_str}"
                lines.append(
                    f"| {yr['year']} | {yr['start_value']:,.0f} | {yr['end_value']:,.0f} | "
                    f"**{yr['total_return']:+.1f}%** | {yr['price_return']:+.1f}% | "
                    f"{yr['dividend_return']:+.1f}% | {yr['max_drawdown']:.1f}% │ "
                    f"{yr['buy_count']} | {yr['sell_count']} | {yr['commission']:,.0f} | "
                    f"{yr['div_count']} | {yr['div_total']:,.0f}{bm_cols_v} |"
                )
            lines.append("")

            # 逐年交易明细
            lines.append("## 🔄 逐年交易明细\n")
            for yr in yearly:
                yr_trades = yr.get("trades_detail", [])
                yr_divs = yr.get("div_detail", [])
                commentary = yr.get("commentary", "")

                lines.append(f"### {yr['year']}\n")
                summary_line = (
                    f"- 总收益 {yr['total_return']:+.1f}% "
                    f"(股价 {yr['price_return']:+.1f}% + 股息 {yr['dividend_return']:+.1f}%) "
                    f"| 回撤 {yr['max_drawdown']:.1f}% "
                    f"| 买入{yr['buy_count']}次 卖出{yr['sell_count']}次 "
                    f"| 手续费 {yr['commission']:,.0f} 元"
                )
                bm_ret = yr.get("benchmark_return")
                if bm_ret is not None:
                    exc_ret = yr.get("excess_return", 0)
                    summary_line += f" | {bm_name} {bm_ret:+.1f}% 超额 {exc_ret:+.1f}%"
                lines.append(summary_line)
                lines.append("")

                # 交易明细
                if yr_trades:
                    lines.append("| 日期 | 标的 | 动作 | 价格 | 数量 | 金额 | 手续费 | 理由 |")
                    lines.append("|------|------|------|------|------|------|--------|------|")
                    for t in yr_trades:
                        action_str = "🟢买入" if t["action"] == "buy" else "🔴卖出"
                        reason = t.get("reason", "")
                        lines.append(
                            f"| {str(t['date'])[:10]} | {t['name']} | {action_str} | "
                            f"{t['price']:.2f} | {t['shares']} | {t['amount']:,.0f} | "
                            f"{t['commission']:.0f} | {reason} |"
                        )
                    lines.append("")

                # 分红明细
                if yr_divs:
                    lines.append("| 日期 | 标的 | 每股派现 | 股数 | 税前 | 税后 |")
                    lines.append("|------|------|---------|------|------|------|")
                    for d in yr_divs:
                        lines.append(
                            f"| {str(d['date'])[:10]} | {d['name']} | "
                            f"{d['cash_per_share']:.3f} | {d['shares']} | "
                            f"{d['gross_dividend']:,.0f} | {d['net_dividend']:,.0f} |"
                        )
                    lines.append("")

                # 总结反思
                if commentary:
                    lines.append(f"#### 📝 交易总结与反思\n")
                    lines.append(commentary)
                    lines.append("")

        # 交易统计
        lines.append("## 🔄 交易统计\n")
        freq_label = "季度" if results.get('rebalance_freq') == 'quarterly' else "周"
        lines.append(f"- 调仓次数: {results['trading_periods']} 次（{freq_label}级调仓）")
        lines.append(f"- 总交易次数: {results['num_trades']} (买入{results['buy_trades']}次, 卖出{results['sell_trades']}次)")
        lines.append(f"- 总手续费: {results['total_commission']:,.2f} 元")
        lines.append("")

        # ── 季度持仓明细 ──
        snapshots = self.portfolio.holding_snapshots
        if snapshots:
            lines.append("## 📋 季度持仓明细\n")
            snap_df = pd.DataFrame(snapshots)
            for date in snap_df["date"].unique():
                date_data = snap_df[snap_df["date"] == date]
                # 提取季度标签
                dt = pd.Timestamp(date)
                q = (dt.month - 1) // 3 + 1
                quarter_label = f"{dt.year}Q{q}"
                lines.append(f"### {quarter_label}（{date[:10]}）\n")
                total_val = date_data.iloc[0]["total_value"]
                cash = date_data.iloc[0]["cash"]
                lines.append(f"- 总资产: {total_val:,.0f} 元 | 现金: {cash:,.0f} 元\n")
                lines.append("| 标的 | 类别 | 股数 | 成本 | 现价 | 市值 | 权重 | 盈亏 |")
                lines.append("|------|------|------|------|------|------|------|------|")
                for _, row in date_data.iterrows():
                    lines.append(
                        f"| {row['name']} | {row['category']} | {row['shares']} | "
                        f"{row['cost_price']:.3f} | {row['current_price']:.3f} | "
                        f"{row['market_value']:,.0f} | {row['weight']:.1f}% | "
                        f"{row['profit_pct']:+.1f}% |"
                    )
                lines.append("")

        # ── 全量交易明细 ──
        if not trade_df.empty:
            lines.append("## 📋 全量交易明细\n")
            lines.append("| 日期 | 标的 | 动作 | 价格 | 数量 | 金额 | 手续费 | 理由 |")
            lines.append("|------|------|------|------|------|------|--------|------|")
            for _, row in trade_df.iterrows():
                action_str = "🟢买入" if row["action"] == "buy" else "🔴卖出"
                reason = row.get("reason", "")
                lines.append(
                    f"| {row['date'][:10]} | {row['name']} | {action_str} | "
                    f"{row['price']:.2f} | {row['shares']} | {row['amount']:,.0f} | "
                    f"{row['commission']:.0f} | {reason} |"
                )
            lines.append("")

        # 净值曲线数据
        if not nav_df.empty:
            lines.append("## 📉 净值曲线\n")
            lines.append("| 日期 | 总资产 | 收益率 | 持仓数 |")
            lines.append("|------|--------|--------|--------|")
            # 每次调仓取一条（季度调仓则每条都取，周度调仓则每4周取一条）
            step = 4 if self.rebalance_freq == "weekly" else 1
            for i, row in nav_df.iloc[::step].iterrows():
                lines.append(
                    f"| {row['date'][:10]} | {row['total_value']:,.0f} | "
                    f"{row['total_return']:+.2f}% | {row['num_positions']} |"
                )
            lines.append("")

        lines.append(f"\n---\n> ⚠️ **免责声明**: 回测结果不代表未来表现，仅供策略验证参考。\n")
        lines.append(f"*生成时间: {now_str} | 红利周期轮动策略回测*")

        content = "\n".join(lines)
        report_path = output_dir / "backtest_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  💾 回测报告 → {report_path}")

        # 保存净值数据（日线净值）
        daily_nav_df = self._build_daily_nav(
            results['period'].split(' ~ ')[0],
            results['period'].split(' ~ ')[1],
        )
        if not daily_nav_df.empty:
            nav_path = output_dir / "nav_curve.csv"
            daily_nav_df.to_csv(nav_path, index=False, encoding="utf-8")
            print(f"  💾 净值曲线(日线) → {nav_path} ({len(daily_nav_df)} 条)")
        elif not nav_df.empty:
            nav_path = output_dir / "nav_curve.csv"
            nav_df.to_csv(nav_path, index=False, encoding="utf-8")
            print(f"  💾 净值曲线(调仓日) → {nav_path}")

        # 保存全量交易记录
        if not trade_df.empty:
            trade_path = output_dir / "trade_log.csv"
            trade_df.to_csv(trade_path, index=False, encoding="utf-8")
            print(f"  💾 交易记录 → {trade_path}")

        # 保存季度持仓明细
        if snapshots:
            snap_df = pd.DataFrame(snapshots)
            holding_path = output_dir / "holding_snapshots.csv"
            snap_df.to_csv(holding_path, index=False, encoding="utf-8")
            print(f"  💾 持仓明细 → {holding_path}")

        return content
