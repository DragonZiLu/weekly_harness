"""
DCA Planner — 红利周期定投计划引擎
====================================

基于每周评分数据，自动生成定投计划，核心功能：

  1. 计划生成 — 根据当前评分信号，推荐定投标的与金额
  2. 动态调额 — 评分越高投越多（大胆攒股×1.5 / 积极布局×1.2 / 观察×1.0）
  3. 阶梯优化 — 参考行业股息率锚，在低位加大投入
  4. 收益预测 — 10年分红复利预测 + 持有成本股息率
  5. 组合跟踪 — 记录每次定投，计算累计份额/成本/市值/收益率

设计原则：
  - 数据来源：weekly_history.csv（最新周评分），无额外 API 调用
  - 定投频率：每周/每双周/每月，可配置
  - 金额规则：基础金额 + 信号系数 + 阶梯系数

使用方式：
  # 生成定投计划
  python -m weekly_harness.dca_planner --monthly 10000

  # 模拟历史定投
  python -m weekly_harness.dca_planner --simulate --start 2020-01-01 --monthly 10000

  # 更新持仓跟踪
  python -m weekly_harness.dca_planner --track --buy 600900.SH:5000 --date 2026-05-30
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

# ─── 项目路径 ──────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dividend_evaluator import COMPANIES, SECTOR_THRESHOLDS


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class DCAPlanConfig:
    """定投计划配置"""
    frequency: str = "monthly"          # weekly | biweekly | monthly
    base_amount: float = 10000.0        # 每次基础投入金额
    signal_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "🔥 大胆攒股": 1.5,   # ≥80 分
        "✅ 积极布局": 1.2,   # 65-79 分
        "👀 观察等待": 1.0,   # 50-64 分
        "⏸️ 暂缓": 0.5,      # 35-49 分
        "🚫 回避": 0.0,      # <35 分
    })
    max_per_stock_pct: float = 0.30     # 单标的最多占每期资金比例
    min_per_stock_amount: float = 500   # 单标的每期最低金额
    max_stocks_per_period: int = 8      # 每期最多定投标的数量


@dataclass
class DCAStockPlan:
    """单只股票的定投建议"""
    ts_code: str
    name: str
    category: str
    sector: str
    score: float
    verdict: str
    div_yield: float
    current_price: float
    suggested_amount: float          # 本轮建议投入
    signal_multiplier: float         # 信号系数
    ladder_zone: str                 # 阶梯区间（低吸/持有/减仓）
    buy_price_anchor: float          # 买入锚价（股息率=buy线的价格）
    current_dps: float               # 当前每股分红
    reason: str = ""


@dataclass
class DCAPlan:
    """定投计划"""
    config: DCAPlanConfig
    week: str
    timestamp: str
    bond_yield: float
    total_amount: float              # 本轮总投入
    stocks: List[DCAStockPlan]       # 定投标的
    projected_yearly_dividend: float # 预计年分红增量
    summary: str = ""


# ══════════════════════════════════════════════════════════════
# 定投跟踪器（记录实际定投执行情况）
# ══════════════════════════════════════════════════════════════

class DCATracker:
    """
    定投持仓跟踪器

    数据持久化到 data/dca_portfolio.json，记录：
      - 每次定投的执行记录
      - 当前持仓（份额、成本、市值）
      - 分红入账记录
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or (_PROJECT_ROOT / "data" / "dca")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.portfolio_file = self.data_dir / "dca_portfolio.json"
        self._load()

    def _load(self):
        """加载持仓数据"""
        if self.portfolio_file.exists():
            with open(self.portfolio_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"positions": {}, "transactions": [], "dividends": []}

        self.positions: Dict[str, Dict] = data.get("positions", {})
        self.transactions: List[Dict] = data.get("transactions", [])
        self.dividends: List[Dict] = data.get("dividends", [])

    def _save(self):
        """保存持仓数据"""
        data = {
            "positions": self.positions,
            "transactions": self.transactions,
            "dividends": self.dividends,
        }
        with open(self.portfolio_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def record_buy(
        self,
        ts_code: str,
        name: str,
        amount: float,
        price: float,
        date: str,
        reason: str = "",
    ):
        """记录一次定投买入"""
        shares = amount / price if price > 0 else 0
        fee = amount * 0.00025  # 万2.5佣金估算

        transaction = {
            "date": date,
            "ts_code": ts_code,
            "name": name,
            "action": "buy",
            "amount": round(amount, 2),
            "price": round(price, 4),
            "shares": round(shares, 2),
            "fee": round(fee, 2),
            "reason": reason,
        }
        self.transactions.append(transaction)

        # 更新持仓
        if ts_code not in self.positions:
            self.positions[ts_code] = {
                "name": name,
                "shares": 0.0,
                "cost": 0.0,
                "avg_price": 0.0,
                "cum_amount": 0.0,
                "first_buy": date,
                "last_buy": date,
            }

        pos = self.positions[ts_code]
        pos["shares"] += shares
        pos["cum_amount"] += amount
        pos["avg_price"] = pos["cum_amount"] / pos["shares"] if pos["shares"] > 0 else 0
        pos["last_buy"] = date

        self._save()

    def record_dividend(
        self,
        ts_code: str,
        name: str,
        cash_per_share: float,
        date: str,
    ):
        """记录分红入账"""
        pos = self.positions.get(ts_code)
        if not pos or pos["shares"] <= 0:
            return

        total_div = pos["shares"] * cash_per_share
        div_record = {
            "date": date,
            "ts_code": ts_code,
            "name": name,
            "cash_per_share": round(cash_per_share, 4),
            "shares": round(pos["shares"], 2),
            "total_dividend": round(total_div, 2),
        }
        self.dividends.append(div_record)
        self._save()

    def get_summary(self, current_prices: Optional[Dict[str, float]] = None) -> Dict:
        """
        获取持仓摘要

        Parameters
        ----------
        current_prices : dict, optional
            当前各标的实时价格 {ts_code: price}
        """
        if current_prices is None:
            current_prices = {}

        total_cost = 0.0
        total_market_value = 0.0
        total_dividend = sum(d.get("total_dividend", 0) for d in self.dividends)
        positions_detail = []

        for ts_code, pos in self.positions.items():
            price = current_prices.get(ts_code, pos["avg_price"])
            market_value = pos["shares"] * price
            profit = market_value - pos["cum_amount"]
            yield_on_cost = 0.0
            # 估算成本股息率：用最近分红/成本价
            recent_divs = [d for d in self.dividends if d["ts_code"] == ts_code]
            if recent_divs and pos["avg_price"] > 0:
                latest_dps = recent_divs[-1]["cash_per_share"]
                yield_on_cost = latest_dps / pos["avg_price"] * 100

            positions_detail.append({
                "ts_code": ts_code,
                "name": pos["name"],
                "shares": round(pos["shares"], 2),
                "avg_price": round(pos["avg_price"], 4),
                "current_price": round(price, 4),
                "cost": round(pos["cum_amount"], 2),
                "market_value": round(market_value, 2),
                "profit": round(profit, 2),
                "profit_pct": round(profit / pos["cum_amount"] * 100, 2) if pos["cum_amount"] > 0 else 0,
                "yield_on_cost": round(yield_on_cost, 2),
                "first_buy": pos["first_buy"],
                "last_buy": pos["last_buy"],
            })
            total_cost += pos["cum_amount"]
            total_market_value += market_value

        return {
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market_value, 2),
            "total_dividend": round(total_dividend, 2),
            "total_return_pct": round((total_market_value + total_dividend) / total_cost * 100 - 100, 2) if total_cost > 0 else 0,
            "stock_count": len(positions_detail),
            "transaction_count": len(self.transactions),
            "dividend_count": len(self.dividends),
            "positions": positions_detail,
        }


# ══════════════════════════════════════════════════════════════
# DCA 计划引擎
# ══════════════════════════════════════════════════════════════

class DCAPlanner:
    """
    定投计划生成器

    基于最新周评数据，生成个性化的攒股定投计划。
    """

    def __init__(self, config: Optional[DCAPlanConfig] = None):
        self.config = config or DCAPlanConfig()
        self._stock_meta: Dict[str, Dict] = {}
        self._load_stock_meta()

    def _load_stock_meta(self):
        """加载股票元数据"""
        for sector, companies in COMPANIES.items():
            for name, meta in companies.items():
                self._stock_meta[meta["ts_code"]] = {
                    "name": name,
                    "category": meta["category"],
                    "certainty": meta.get("certainty", ""),
                    "sector": sector,
                    "comment": meta.get("comment", ""),
                }

    def _load_latest_scores(self) -> Optional[pd.DataFrame]:
        """
        从 weekly_history.csv 加载最新一周的评分数据

        Returns
        -------
        DataFrame | None
        """
        history_csv = _PROJECT_ROOT / "data" / "weekly_history.csv"
        if not history_csv.exists():
            return None

        df = pd.read_csv(history_csv, encoding="utf-8")
        latest_week = df["week"].max()
        latest_df = df[df["week"] == latest_week].copy()
        return latest_df if not latest_df.empty else None

    def generate_plan(
        self,
        scores_df: Optional[pd.DataFrame] = None,
        bond_yield: Optional[float] = None,
    ) -> DCAPlan:
        """
        生成定投计划

        Parameters
        ----------
        scores_df : DataFrame, optional
            评分数据，默认从 weekly_history.csv 加载
        bond_yield : float, optional
            10年国债收益率

        Returns
        -------
        DCAPlan
        """
        if scores_df is None:
            scores_df = self._load_latest_scores()

        if scores_df is None or scores_df.empty:
            raise ValueError("无法加载评分数据，请先运行 run_weekly.py")

        now = datetime.now()
        iso_week = now.strftime("%G-W%V")

        # 获取国债收益率
        if bond_yield is None:
            bond_yield = self._get_recent_bond_yield()

        # 逐只股票生成定投建议（仅计算权重，金额稍后统一分配）
        stock_candidates: List[Tuple[float, float, str, str, float, float, float, str, str, str, float]] = []
        for _, row in scores_df.iterrows():
            ts_code = row["ts_code"]
            meta = self._stock_meta.get(ts_code, {})
            if not meta:
                continue

            score = row["total_score"]
            verdict = row["verdict"]
            div_yield = row.get("div_yield", 0) or 0
            price = row.get("close", 0) or 0
            sector = meta.get("sector", "")

            # 跳过回避/暂缓信号
            signal_mult = self.config.signal_multipliers.get(verdict, 0)
            if signal_mult <= 0:
                continue

            # 阶梯优化：判断当前处于哪个阶梯区间
            ladder_zone, _ = self._calc_ladder_zone(div_yield, sector)

            # 阶梯系数（低吸区额外加成）
            if ladder_zone == "低吸":
                ladder_mult = 1.3  # 低位多买30%
                zone_reason = f"处于低吸区(股息率≥买入线)，额外加码30%"
            elif ladder_zone == "减仓":
                ladder_mult = 0.5  # 高位减半
                zone_reason = f"处于减仓区(股息率<减仓线)，减半投入"
            else:
                ladder_mult = 1.0
                zone_reason = "持有区，正常定投"

            # 原始权重 = 信号系数 × 阶梯系数
            raw_weight = signal_mult * ladder_mult

            # 计算买入锚价 & DPS
            anchor_price = self._calc_anchor_price(div_yield, price, sector)
            dps = price * div_yield / 100 if price > 0 else 0

            stock_candidates.append((
                raw_weight, score, ts_code, meta["name"], meta["category"],
                sector, div_yield, price, verdict, zone_reason, anchor_price, dps,
                signal_mult, ladder_zone,
            ))

        # 按评分排序，取前 N 只
        stock_candidates.sort(key=lambda x: x[1], reverse=True)
        selected = stock_candidates[:self.config.max_stocks_per_period]

        # ── 按权重分配总额 ──
        total_weight = sum(w for w, *_ in selected)
        stock_plans = []
        for raw_weight, score, ts_code, name, category, sector, div_yield, price, verdict, zone_reason, anchor_price, dps, signal_mult, ladder_zone in selected:
            # 按权重比例分配总预算
            weight_ratio = raw_weight / total_weight if total_weight > 0 else 0
            suggested = self.config.base_amount * weight_ratio

            # 单上限约束（不超过总预算的配置比例）
            max_per = self.config.base_amount * self.config.max_per_stock_pct
            if suggested > max_per:
                suggested = max_per

            # 最低金额约束
            if suggested < self.config.min_per_stock_amount:
                suggested = self.config.min_per_stock_amount

            sp = DCAStockPlan(
                ts_code=ts_code,
                name=name,
                category=category,
                sector=sector,
                score=score,
                verdict=verdict,
                div_yield=div_yield,
                current_price=price,
                suggested_amount=round(suggested, 2),
                signal_multiplier=round(signal_mult, 2),
                ladder_zone=ladder_zone,
                buy_price_anchor=round(anchor_price, 2) if anchor_price else 0,
                current_dps=round(dps, 4),
                reason=f"评分{score:.0f}/{verdict} | {zone_reason}",
            )
            stock_plans.append(sp)

        # 计算总额和分红预测
        total_amount = sum(s.suggested_amount for s in stock_plans)
        projected_div = sum(
            s.suggested_amount / s.current_price * s.current_dps
            if s.current_price > 0 else 0
            for s in stock_plans
        )

        # 生成摘要
        strong_buy_count = sum(1 for s in stock_plans if s.score >= 80)
        buy_count = sum(1 for s in stock_plans if 65 <= s.score < 80)
        watch_count = sum(1 for s in stock_plans if s.score < 65)

        summary = (
            f"本轮定投 {len(stock_plans)} 只，总投入 {total_amount:,.0f} 元\n"
            f"├─ 大胆攒股(≥80分): {strong_buy_count} 只，建议加大投入\n"
            f"├─ 积极布局(65-79): {buy_count} 只，正常定投\n"
            f"├─ 观察等待(<65): {watch_count} 只，控制节奏\n"
            f"└─ 预计年分红增量: {projected_div:,.0f} 元/年"
        )

        return DCAPlan(
            config=self.config,
            week=iso_week,
            timestamp=now.isoformat(timespec="seconds"),
            bond_yield=bond_yield,
            total_amount=round(total_amount, 2),
            stocks=stock_plans,
            projected_yearly_dividend=round(projected_div, 2),
            summary=summary,
        )

    def _calc_ladder_zone(self, div_yield: float, sector: str) -> Tuple[str, float]:
        """
        判断当前股息率处于阶梯攒股的哪个区间

        Returns
        -------
        (zone, anchor_price) : (str, float)
            zone: "低吸" / "持有" / "减仓"
            anchor_price: 买入线对应的价格
        """
        thresholds = SECTOR_THRESHOLDS.get(sector, {})
        if not thresholds:
            return "持有", 0.0

        buy_line = thresholds.get("buy", 5.0)
        reduce_line = thresholds.get("reduce", 3.0)

        if div_yield >= buy_line:
            return "低吸", buy_line
        elif div_yield >= reduce_line:
            return "持有", buy_line
        else:
            return "减仓", reduce_line

    def _calc_anchor_price(self, div_yield: float, price: float, sector: str) -> float:
        """
        计算买入锚价（股息率=buy线的目标价格）

        anchor_price = dps / (buy_threshold / 100)
        """
        thresholds = SECTOR_THRESHOLDS.get(sector, {})
        if not thresholds or price <= 0 or div_yield <= 0:
            return 0.0

        buy_line = thresholds.get("buy", 5.0)
        dps = price * div_yield / 100
        return dps / (buy_line / 100) if buy_line > 0 else 0

    def _get_recent_bond_yield(self) -> float:
        """获取最近使用的国债收益率"""
        # 从最新 artifact 中读取
        history_csv = _PROJECT_ROOT / "data" / "weekly_history.csv"
        if history_csv.exists():
            df = pd.read_csv(history_csv, encoding="utf-8")
            if "bond_yield_10y" in df.columns:
                latest = df["bond_yield_10y"].dropna()
                if not latest.empty:
                    return float(latest.iloc[-1])

        # 从 dividend_evaluator 的默认值
        from dividend_evaluator import THRESHOLDS
        return THRESHOLDS.get("bond_yield_10y", 1.65)

    def print_plan(self, plan: DCAPlan):
        """打印定投计划（富文本）"""
        print("\n" + "=" * 65)
        print("  🌊 红利周期定投计划")
        print(f"  📅 {plan.week} | {plan.timestamp[:16]}")
        print(f"  📡 10年国债: {plan.bond_yield:.2f}%")
        print(f"  💰 每期基础投入: {plan.config.base_amount:,.0f} 元")
        print(f"  📊 频率: {plan.config.frequency}")
        print("=" * 65)

        print(f"\n  ── 本轮定投标的 (共 {len(plan.stocks)} 只) ──\n")
        print(f"  {'标的':<10} {'评分':>4} {'信号':<10} {'股息率':>6} {'现价':>7} {'建议金额':>8} {'阶梯':<6} {'买入锚价':>7} {'理由'}")
        print("  " + "-" * 80)

        for s in plan.stocks:
            ladder_tag = {"低吸": "🔥低吸", "持有": "📊持有", "减仓": "❄️减仓"}.get(s.ladder_zone, s.ladder_zone)
            print(
                f"  {s.name:<8} {s.score:>4.0f} {s.verdict:<8} "
                f"{s.div_yield:>5.1f}% {s.current_price:>6.2f} "
                f"{s.suggested_amount:>7,.0f} {ladder_tag:<6} "
                f"{s.buy_price_anchor if s.buy_price_anchor > 0 else '-':>7} "
                f"{s.reason[:25]}"
            )

        print(f"\n  ── 摘要 ──")
        for line in plan.summary.split("\n"):
            print(f"  {line}")

    def simulate_dca(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        ts_codes: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        模拟历史定投表现

        基于 weekly_history.csv 中的历史评分，模拟如果从 start_date 开始定投的效果。

        Parameters
        ----------
        start_date : str
        end_date : str, optional
        ts_codes : list, optional
            指定标的，默认所有标的

        Returns
        -------
        DataFrame with columns: week, ts_code, name, score, verdict, price,
                                 buy_amount, cum_shares, cum_cost, market_value, return_pct
        """
        history_csv = _PROJECT_ROOT / "data" / "weekly_history.csv"
        if not history_csv.exists():
            raise FileNotFoundError("weekly_history.csv 不存在")

        df = pd.read_csv(history_csv, encoding="utf-8")
        df["date"] = pd.to_datetime(df["date"])

        if end_date:
            df = df[df["date"] <= pd.Timestamp(end_date)]
        df = df[df["date"] >= pd.Timestamp(start_date)]

        if ts_codes:
            df = df[df["ts_code"].isin(ts_codes)]

        if df.empty:
            raise ValueError("无符合条件的记录")

        # 按周分组
        weeks = sorted(df["week"].unique())

        # 模拟持仓
        holdings: Dict[str, Dict] = {}  # {ts_code: {"shares": float, "cost": float}}
        weekly_snapshots = []

        for week in weeks:
            week_data = df[df["week"] == week]
            if week_data.empty:
                continue

            for _, row in week_data.iterrows():
                ts_code = row["ts_code"]
                verdict = row["verdict"]
                price = row.get("close", 0) or 0
                score = row["total_score"]

                if price <= 0:
                    continue

                # 信号系数
                multiplier = self.config.signal_multipliers.get(verdict, 0)
                if multiplier <= 0:
                    continue

                buy_amount = self.config.base_amount * multiplier

                # 初始化持仓
                if ts_code not in holdings:
                    holdings[ts_code] = {"shares": 0.0, "cost": 0.0, "name": row["name"]}

                shares = buy_amount / price
                holdings[ts_code]["shares"] += shares
                holdings[ts_code]["cost"] += buy_amount

                weekly_snapshots.append({
                    "week": week,
                    "ts_code": ts_code,
                    "name": row["name"],
                    "score": score,
                    "verdict": verdict,
                    "price": price,
                    "buy_amount": buy_amount,
                    "shares_bought": shares,
                    "cum_shares": holdings[ts_code]["shares"],
                    "cum_cost": holdings[ts_code]["cost"],
                    "market_value": holdings[ts_code]["shares"] * price,
                    "return_pct": (holdings[ts_code]["shares"] * price / holdings[ts_code]["cost"] - 1) * 100,
                })

        return pd.DataFrame(weekly_snapshots)

    def export_plan(self, plan: DCAPlan, output_path: Optional[Path] = None) -> Path:
        """导出定投计划为 JSON"""
        if output_path is None:
            output_dir = _PROJECT_ROOT / "data" / "dca"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"dca_plan_{plan.week}.json"

        data = {
            "week": plan.week,
            "timestamp": plan.timestamp,
            "bond_yield": plan.bond_yield,
            "config": {
                "frequency": plan.config.frequency,
                "base_amount": plan.config.base_amount,
            },
            "total_amount": plan.total_amount,
            "projected_yearly_dividend": plan.projected_yearly_dividend,
            "stocks": [
                {
                    "ts_code": s.ts_code,
                    "name": s.name,
                    "category": s.category,
                    "sector": s.sector,
                    "score": s.score,
                    "verdict": s.verdict,
                    "div_yield": s.div_yield,
                    "current_price": s.current_price,
                    "suggested_amount": s.suggested_amount,
                    "signal_multiplier": s.signal_multiplier,
                    "ladder_zone": s.ladder_zone,
                    "buy_price_anchor": s.buy_price_anchor,
                    "current_dps": s.current_dps,
                    "reason": s.reason,
                }
                for s in plan.stocks
            ],
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"  💾 定投计划 → {output_path}")
        return output_path


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="红利周期定投计划 — DCA Planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m weekly_harness.dca_planner                          # 生成本周定投计划
  python -m weekly_harness.dca_planner --monthly 20000          # 自定义每期金额
  python -m weekly_harness.dca_planner --frequency weekly       # 周定投模式
  python -m weekly_harness.dca_planner --simulate               # 模拟历史定投
  python -m weekly_harness.dca_planner --track --summary        # 查看定投持仓
  python -m weekly_harness.dca_planner --track --buy 600900.SH:5000  # 记录定投
        """,
    )

    # ── 计划生成 ──
    parser.add_argument("--monthly", type=float, default=10000.0,
                        help="每期基础投入金额（元），默认 10000")
    parser.add_argument("--frequency", default="monthly",
                        choices=["weekly", "biweekly", "monthly"],
                        help="定投频率，默认 monthly")
    parser.add_argument("--max-stocks", type=int, default=8,
                        help="每期最多定投标的，默认 8")

    # ── 模拟 ──
    parser.add_argument("--simulate", action="store_true",
                        help="模拟历史定投表现")
    parser.add_argument("--start", default="2024-01-01",
                        help="模拟开始日期，默认 2024-01-01")
    parser.add_argument("--end", default=None,
                        help="模拟结束日期，默认今天")

    # ── 跟踪 ──
    parser.add_argument("--track", action="store_true",
                        help="定投持仓跟踪模式")
    parser.add_argument("--buy", default=None,
                        help="记录买入, 格式: ts_code:金额, 如 600900.SH:5000")
    parser.add_argument("--date", default=None,
                        help="交易日期, 格式 YYYY-MM-DD")
    parser.add_argument("--price", type=float, default=None,
                        help="买入价格（默认从 tushare 拉取）")
    parser.add_argument("--summary", action="store_true",
                        help="显示持仓摘要")

    args = parser.parse_args()

    # ── 模式 1: 持仓跟踪 ──
    if args.track:
        tracker = DCATracker()

        if args.summary:
            summary = tracker.get_summary()
            print("\n" + "=" * 50)
            print("  📊 定投持仓摘要")
            print("=" * 50)
            print(f"  总成本:       {summary['total_cost']:,.2f} 元")
            print(f"  总市值:       {summary['total_market_value']:,.2f} 元")
            print(f"  累计分红:     {summary['total_dividend']:,.2f} 元")
            print(f"  总收益率:     {summary['total_return_pct']:+.2f}%")
            print(f"  持仓标的数:   {summary['stock_count']}")
            print(f"  交易次数:     {summary['transaction_count']}")
            print(f"  分红次数:     {summary['dividend_count']}")
            if summary["positions"]:
                print(f"\n  {'标的':<10} {'份额':>8} {'成本':>8} {'市值':>8} {'收益率':>7} {'股息率':>7}")
                print("  " + "-" * 60)
                for p in summary["positions"]:
                    print(
                        f"  {p['name']:<8} {p['shares']:>8.0f} "
                        f"{p['cost']:>8,.0f} {p['market_value']:>8,.0f} "
                        f"{p['profit_pct']:>+6.2f}% {p['yield_on_cost']:>6.2f}%"
                    )
            print()
            return

        if args.buy:
            parts = args.buy.split(":")
            if len(parts) != 2:
                print("❌ 格式错误，应为: ts_code:金额，如 600900.SH:5000")
                return
            ts_code = parts[0]
            amount = float(parts[1])

            # 获取价格
            price = args.price
            if price is None:
                try:
                    from dividend_evaluator import TushareDataFetcher
                    fetcher = TushareDataFetcher()
                    basic = fetcher.get_daily_basic(ts_code)
                    if basic:
                        price = basic["close"]
                        print(f"  ✅ 获取到 {ts_code} 最新价: {price:.2f}")
                except Exception:
                    pass

            if price is None:
                print("❌ 无法获取价格，请通过 --price 指定")
                return

            date = args.date or datetime.now().strftime("%Y-%m-%d")
            name = ts_code  # 简化
            for _, meta in COMPANIES.items():
                for n, m in meta.items():
                    if m["ts_code"] == ts_code:
                        name = n
                        break

            tracker.record_buy(ts_code, name, amount, price, date)
            print(f"  ✅ 已记录: {name} {amount:,.0f}元 @ {price:.4f}，{date}")
            return

        parser.print_help()
        return

    # ── 模式 2: 历史模拟 ──
    if args.simulate:
        config = DCAPlanConfig(
            frequency=args.frequency,
            base_amount=args.monthly,
        )
        planner = DCAPlanner(config)
        print(f"\n  📊 模拟历史定投: {args.start} ~ {args.end or '今天'}")
        print(f"  💰 每期投入: {args.monthly:,.0f} 元\n")

        sim_df = planner.simulate_dca(args.start, args.end)

        if sim_df.empty:
            print("  ⚠️ 无符合条件的模拟记录（可能评分数据不足）")
            return

        # 按标的汇总
        for name, group in sim_df.groupby("name"):
            latest = group.iloc[-1]
            n_buys = len(group)
            print(
                f"  {name:<8}: 定投{n_buys}次, "
                f"成本{latest['cum_cost']:,.0f}元, "
                f"市值{latest['market_value']:,.0f}元, "
                f"收益率{latest['return_pct']:+.1f}%"
            )

        # 总计（每个标的取最后一期累计值）
        latest_by_stock = sim_df.groupby("ts_code").last()
        total_cost = latest_by_stock["cum_cost"].sum()
        total_market = latest_by_stock["market_value"].sum()
        total_buys = len(sim_df)
        print(f"\n  总计: {total_buys}次定投, 累计投入{total_cost:,.0f}元, 当前市值{total_market:,.0f}元, "
              f"收益率{(total_market/total_cost-1)*100:+.1f}%" if total_cost > 0 else "")
        return

    # ── 模式 3: 生成计划（默认） ──
    config = DCAPlanConfig(
        frequency=args.frequency,
        base_amount=args.monthly,
        max_stocks_per_period=args.max_stocks,
    )
    planner = DCAPlanner(config)

    try:
        plan = planner.generate_plan()
    except ValueError as e:
        print(f"  ❌ 生成失败: {e}")
        print(f"  💡 请先运行 python run_weekly.py 获取最新评分数据")
        return

    planner.print_plan(plan)
    planner.export_plan(plan)


if __name__ == "__main__":
    main()
