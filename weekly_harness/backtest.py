"""
Backtest — 回测引擎
====================
基于历史数据模拟红利周期轮动策略的表现。

回测流程：
  1. 加载历史评分数据（weekly_history.csv）或用模拟评分
  2. 获取历史价格数据（tushare daily/fund_daily）
  3. 按周逐步模拟：计算评分 → 生成调仓指令 → 执行交易 → 记录净值
  4. 计算策略绩效指标（年化收益、最大回撤、夏普比率等）
  5. 与基准对比（沪深300 / 中证红利）

使用方法：
  # 用 weekly_history.csv 回测
  python -m weekly_harness.backtest --start 2024-01-01 --end 2026-05-01

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
        """获取基准指数数据"""
        pro = self._get_tushare_pro()

        # 尝试用 index_daily 接口
        try:
            df = pro.index_daily(
                ts_code=benchmark_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ts_code,trade_date,open,high,low,close,vol",
            )
            if df is not None and not df.empty:
                df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
                df = df.sort_values("trade_date").reset_index(drop=True)
                return df
        except Exception:
            pass

        return pd.DataFrame()

    def get_friday_dates(self, start_date: str, end_date: str) -> List[str]:
        """获取回测期间的所有周五日期（调仓日）"""
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        fridays = pd.date_range(start=start, end=end, freq="W-FRI")
        return [d.strftime("%Y-%m-%d") for d in fridays]


# ─── 回测引擎 ──────────────────────────────────────────────────

class BacktestEngine:
    """
    回测引擎

    按周逐步模拟红利周期轮动策略的执行。
    """

    def __init__(
        self,
        strategy_params: Optional[StrategyParams] = None,
        initial_cash: float = 100_0000,
        commission_rate: float = 0.001,
        slippage: float = 0.001,
    ):
        self.strategy = DividendCycleStrategy(strategy_params)
        self.portfolio = Portfolio(
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage=slippage,
        )
        self.fetcher = BacktestDataFetcher()
        self.metrics = PerformanceMetrics()

        # 股票元数据
        self._stock_meta: Dict[str, Dict] = {}
        # 价格数据缓存
        self._price_data: Dict[str, pd.DataFrame] = {}
        # 基准数据
        self._benchmark: Optional[pd.DataFrame] = None

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

    def _simulate_scores_at_date(self, date_str: str) -> Dict[str, Dict]:
        """
        模拟指定日期的评分数据

        方案1: 使用 weekly_history.csv 中已有的周度评分
        方案2: 基于历史价格 + 股息率模拟评分（简化版）
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
                    }
                if scores:
                    return scores

        # 回退：基于价格模拟简化评分
        return self._simulate_simple_scores(date_str)

    def _simulate_simple_scores(self, date_str: str) -> Dict[str, Dict]:
        """
        基于历史价格的简化评分模拟

        使用当前 FALLBACK_DATA 中的股息率 + 历史价格变动来模拟评分
        """
        import sys
        sys.path.insert(0, str(_PROJECT_ROOT))
        from dividend_evaluator import FALLBACK_DATA, THRESHOLDS

        scores = {}
        for ts_code, meta in self._stock_meta.items():
            price = self._get_price_on_date(ts_code, date_str)
            fallback = FALLBACK_DATA.get(ts_code, {})
            category = meta["category"]

            if price and price > 0 and fallback:
                # 用 fallback 的股息率 + 当前价格模拟
                fallback_close = fallback.get("close", 0)
                fallback_div = fallback.get("div_yield", 0)

                if fallback_close > 0 and fallback_div > 0:
                    # 假设分红不变，股息率随价格反比变动
                    sim_div_yield = fallback_div * (fallback_close / price)
                else:
                    sim_div_yield = fallback_div

                # 简化评分：主要基于股息率和息差
                bond_yield = THRESHOLDS.get("bond_yield_10y", 1.65)
                spread_bp = (sim_div_yield - bond_yield) * 100

                # 简化评分公式（近似 DividendCycleEvaluator 的逻辑）
                s1 = min(30, max(0, sim_div_yield * 5))   # 股息率评分
                s2 = min(25, max(0, spread_bp / 10))       # 息差评分
                s3 = min(20, max(0, sim_div_yield * 3))    # 等效分红
                certainty_score = {"AA": 15, "A": 13, "A-": 11, "B+": 8, "B": 5}.get(
                    meta.get("certainty", "B+"), 8
                )
                s4 = certainty_score
                s5 = 5  # 成长性给默认值

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

                scores[ts_code] = {
                    "name": meta["name"],
                    "category": category,
                    "total_score": round(total, 1),
                    "verdict": verdict,
                    "div_yield": round(sim_div_yield, 2),
                    "close": price,
                    "pe_ttm": fallback.get("pe_ttm", 0),
                    "roe": fallback.get("roe", 0),
                    "bond_spread_bp": round(spread_bp, 0),
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

        # 获取调仓日（每周五）
        rebalance_dates = self.fetcher.get_friday_dates(start_date, end_date)

        if verbose:
            print(f"\n  📅 回测期间: {start_date} ~ {end_date}")
            print(f"  📊 调仓次数: {len(rebalance_dates)} 次（每周五）")
            print(f"  💰 初始资金: {self.portfolio.initial_cash:,.0f} 元")
            print(f"  📋 持仓标的: {len(self._stock_meta)} 只")

        # 获取基准数据
        self._benchmark = self.fetcher.fetch_benchmark_data(benchmark_code, start_date, end_date)

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

            # 记录净值
            self.portfolio.record_nav()

            if verbose and (i + 1) % 10 == 0:
                ret = self.portfolio.total_return
                print(f"  📈 第{i+1}周 ({date_str}): 总收益 {ret:+.2f}%, "
                      f"持仓 {len([p for p in self.portfolio.positions.values() if p.shares > 0])} 只")

        # 计算绩效
        return self._calculate_results(start_date, end_date, verbose)

    def _calculate_results(self, start_date: str, end_date: str, verbose: bool) -> Dict:
        """计算回测结果和绩效指标"""
        nav_df = self.portfolio.get_nav_dataframe()
        trade_df = self.portfolio.get_trade_log()

        if nav_df.empty:
            return {"error": "无净值数据"}

        # 基本指标
        total_return = self.portfolio.total_return
        trading_days = len(nav_df)
        annual_return = self.metrics.annual_return(total_return, trading_days * 5)  # 周数据×5≈交易日

        # 最大回撤
        nav_series = nav_df.set_index("date")["total_value"]
        max_dd = self.metrics.max_drawdown(nav_series)

        # 夏普比率
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

        # 基准对比
        benchmark_return = 0.0
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
            "trading_weeks": trading_days,
        }

        if verbose:
            self._print_results(results)

        return results

    def _print_results(self, results: Dict):
        """打印回测结果"""
        print("\n" + "=" * 70)
        print("  📊 红利周期轮动策略 — 回测结果")
        print("=" * 70)

        print(f"\n  ── 收益指标 ──")
        print(f"  回测期间:     {results['period']}")
        print(f"  初始资金:     {results['initial_cash']:,.0f} 元")
        print(f"  最终资产:     {results['final_value']:,.2f} 元")
        print(f"  总收益率:     {results['total_return']:+.2f}%")
        print(f"  年化收益率:   {results['annual_return']:+.2f}%")

        print(f"\n  ── 风险指标 ──")
        print(f"  最大回撤:     {results['max_drawdown']:.2f}%")
        print(f"  夏普比率:     {results['sharpe_ratio']:.2f}")
        print(f"  卡尔玛比率:   {results['calmar_ratio']:.2f}")

        print(f"\n  ── 交易统计 ──")
        print(f"  交易周数:     {results['trading_weeks']}")
        print(f"  总交易次数:   {results['num_trades']}")
        print(f"  买入次数:     {results['buy_trades']}")
        print(f"  卖出次数:     {results['sell_trades']}")
        print(f"  总手续费:     {results['total_commission']:,.2f} 元")

        print(f"\n  ── 基准对比 ──")
        print(f"  基准收益:     {results['benchmark_return']:+.2f}%")
        print(f"  超额收益:     {results['excess_return']:+.2f}%")

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
            f"> **初始资金**: {results['initial_cash']:,.0f} 元  \n",
            "---\n",
        ]

        # 收益指标
        lines.append("## 📈 收益指标\n")
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 总收益率 | **{results['total_return']:+.2f}%** |")
        lines.append(f"| 年化收益率 | **{results['annual_return']:+.2f}%** |")
        lines.append(f"| 最终资产 | {results['final_value']:,.2f} 元 |")
        lines.append(f"| 最大回撤 | {results['max_drawdown']:.2f}% |")
        lines.append(f"| 夏普比率 | {results['sharpe_ratio']:.2f} |")
        lines.append(f"| 卡尔玛比率 | {results['calmar_ratio']:.2f} |")
        lines.append("")

        # 基准对比
        lines.append("## 📊 基准对比\n")
        lines.append("| 指标 | 策略 | 基准(沪深300) |")
        lines.append("|------|------|--------------|")
        lines.append(f"| 收益率 | {results['total_return']:+.2f}% | {results['benchmark_return']:+.2f}% |")
        lines.append(f"| 超额收益 | {results['excess_return']:+.2f}% | — |")
        lines.append("")

        # 交易统计
        lines.append("## 🔄 交易统计\n")
        lines.append(f"- 交易周数: {results['trading_weeks']}")
        lines.append(f"- 总交易次数: {results['num_trades']} (买入{results['buy_trades']}次, 卖出{results['sell_trades']}次)")
        lines.append(f"- 总手续费: {results['total_commission']:,.2f} 元")
        lines.append("")

        # 净值曲线数据
        if not nav_df.empty:
            lines.append("## 📉 净值曲线\n")
            lines.append("| 日期 | 总资产 | 收益率 | 持仓数 |")
            lines.append("|------|--------|--------|--------|")
            # 每4周取一条
            for i, row in nav_df.iloc[::4].iterrows():
                lines.append(
                    f"| {row['date'][:10]} | {row['total_value']:,.0f} | "
                    f"{row['total_return']:+.2f}% | {row['num_positions']} |"
                )
            lines.append("")

        # 交易明细（最近20笔）
        if not trade_df.empty:
            lines.append("## 📋 交易明细（最近20笔）\n")
            lines.append("| 日期 | 标的 | 动作 | 价格 | 数量 | 金额 | 手续费 |")
            lines.append("|------|------|------|------|------|------|--------|")
            for _, row in trade_df.tail(20).iterrows():
                action_str = "🟢买入" if row["action"] == "buy" else "🔴卖出"
                lines.append(
                    f"| {row['date'][:10]} | {row['name']} | {action_str} | "
                    f"{row['price']:.2f} | {row['shares']} | {row['amount']:,.0f} | "
                    f"{row['commission']:.0f} |"
                )
            lines.append("")

        lines.append(f"\n---\n> ⚠️ **免责声明**: 回测结果不代表未来表现，仅供策略验证参考。\n")
        lines.append(f"*生成时间: {now_str} | 红利周期轮动策略回测*")

        content = "\n".join(lines)
        report_path = output_dir / "backtest_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  💾 回测报告 → {report_path}")

        # 保存净值数据
        if not nav_df.empty:
            nav_path = output_dir / "nav_curve.csv"
            nav_df.to_csv(nav_path, index=False, encoding="utf-8")
            print(f"  💾 净值曲线 → {nav_path}")

        # 保存交易记录
        if not trade_df.empty:
            trade_path = output_dir / "trade_log.csv"
            trade_df.to_csv(trade_path, index=False, encoding="utf-8")
            print(f"  💾 交易记录 → {trade_path}")

        return content
