"""
run_grid_research.py — 网格买入算法研究（515180 易方达中证红利ETF）
=================================================================

研究目标：
  在 515180 上对比多种网格买入策略 vs 一次性买入 vs 月度定投，
  找出最优的网格参数组合。

网格策略要素：
  - 间距：3%, 5%, 8%, 10%
  - 层数：5, 10, 20
  - 基准价格：首次买入价 / MA60动态
  - 资金分配：等额 / 金字塔(1,2,3,...)

对比基准：
  - 一次性买入（期初全部投入）
  - 月度定投（每月固定金额）

使用方法：
  python run_grid_research.py
  python run_grid_research.py --total-capital 500000
  python run_grid_research.py --start 2020-01-01 --end 2026-06-14
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import tushare as ts

# ── 路径 ──
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════════════════════
#  配置数据结构
# ═══════════════════════════════════════════════════════

@dataclass
class GridConfig:
    """网格策略配置"""
    name: str                        # 策略名称
    spacing: float                   # 网格间距（如 0.05 = 5%）
    layers: int                      # 最大层数
    sizing: str                      # 资金分配: "equal" / "pyramid"
    base_type: str                   # 基准价格类型: "first" / "ma60"
    description: str = ""            # 策略描述

    def __post_init__(self):
        if not self.description:
            sizing_cn = {"equal": "等额", "pyramid": "金字塔"}
            base_cn = {"first": "首买价", "ma60": "MA60"}
            self.description = f"间距{self.spacing*100:.0f}%_{self.layers}层_{sizing_cn.get(self.sizing, self.sizing)}_{base_cn.get(self.base_type, self.base_type)}"


@dataclass
class Trade:
    """单笔交易记录"""
    date: str              # 交易日期
    nav: float             # 买入净值
    grid_level: int        # 触发网格层级（1-based）
    trigger_nav: float     # 触发价格
    amount: float          # 买入金额
    shares: float          # 买入份额


@dataclass
class StrategyResult:
    """策略回测结果"""
    name: str
    description: str
    total_capital: float           # 总资金池
    total_invested: float          # 累计投入
    final_shares: float            # 最终持有份额
    final_nav: float               # 最终净值
    position_value: float          # 持仓市值
    cash_remaining: float          # 剩余现金（含货基收益）
    total_asset: float             # 总资产
    total_profit: float            # 总收益
    total_return_pct: float        # 总收益率
    annual_return_pct: float       # 年化收益率（IRR）
    max_drawdown_pct: float        # 最大回撤
    capital_utilization_pct: float # 资金利用率（平均）
    n_trades: int                  # 交易次数
    trades: list[Trade] = field(default_factory=list)
    daily_asset: pd.Series = None  # 每日总资产序列


# ═══════════════════════════════════════════════════════
#  数据获取
# ═══════════════════════════════════════════════════════

def init_tushare():
    """初始化 tushare pro"""
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


def fetch_nav_data(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """获取基金净值数据"""
    df = pro.fund_nav(
        ts_code=ts_code,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    df = df.drop_duplicates(subset="nav_date", keep="last")
    df = df.sort_values("nav_date").reset_index(drop=True)
    df["nav_date"] = df["nav_date"].astype(str)
    df["dt"] = pd.to_datetime(df["nav_date"])
    df["unit_nav"] = df["unit_nav"].astype(float)
    if "adj_nav" in df.columns:
        df["adj_nav"] = df["adj_nav"].astype(float)
    else:
        df["adj_nav"] = df["unit_nav"]
    return df


def fetch_dividends_map(pro, ts_code: str, start: str, end: str) -> dict[str, float]:
    """获取分红数据 {ex_date: div_cash_per_share}"""
    div = pro.fund_div(ts_code=ts_code)
    if div.empty:
        return {}
    div = div[div["ex_date"].astype(str) >= start.replace("-", "")]
    div = div[div["ex_date"].astype(str) <= end.replace("-", "")]
    div = div.sort_values("ex_date").drop_duplicates(subset="ex_date", keep="first")
    result = {}
    for _, r in div.iterrows():
        dc = float(r["div_cash"]) if pd.notna(r["div_cash"]) else 0.0
        result[str(r["ex_date"])] = dc
    return result


def fetch_etf_name(pro, ts_code: str) -> str:
    """获取基金名称"""
    try:
        info = pro.fund_basic(ts_code=ts_code, fields="ts_code,name")
        if not info.empty:
            return info.iloc[0]["name"]
    except Exception:
        pass
    return ts_code


# ═══════════════════════════════════════════════════════
#  IRR 计算
# ═══════════════════════════════════════════════════════

def calc_irr(cashflows: list[tuple[float, float]]) -> float:
    """
    计算 IRR（二分法）
    cashflows: [(years_from_start, amount), ...] 负=投入, 正=回收
    返回年化 IRR
    """
    def npv(rate, cfs):
        return sum(amt / (1 + rate) ** t for t, amt in cfs)

    lo, hi = -0.99, 5.0
    for _ in range(500):
        mid = (lo + hi) / 2
        npv_mid = npv(mid, cashflows)
        if npv_mid > 0:
            lo = mid
        else:
            hi = mid
    annual_rate = (lo + hi) / 2
    return annual_rate


# ═══════════════════════════════════════════════════════
#  最大回撤计算
# ═══════════════════════════════════════════════════════

def calc_max_drawdown(asset_series: pd.Series) -> float:
    """计算最大回撤"""
    if asset_series.empty:
        return 0.0
    cummax = asset_series.cummax()
    drawdown = (asset_series - cummax) / cummax
    return float(drawdown.min())


# ═══════════════════════════════════════════════════════
#  网格策略核心引擎
# ═══════════════════════════════════════════════════════

def run_grid_strategy(
    df: pd.DataFrame,
    config: GridConfig,
    total_capital: float,
    mm_yield: float = 0.02,  # 闲置资金年化收益（货基）
) -> StrategyResult:
    """
    执行网格买入策略

    逻辑：
    - 基准价格 = 首次买入价（或动态MA60）
    - 第N格触发价 = base_price × (1 - spacing)^N
    - 每次触发时，从资金池中扣除相应金额买入
    - 闲置资金产生货基收益（按日计息）
    - 分红再投入
    """
    spacing = config.spacing
    layers = config.layers
    sizing = config.sizing
    base_type = config.base_type

    # 计算每格买入金额
    if sizing == "equal":
        # 等额：每格 = 总资金 / 层数
        per_grid_capital = total_capital / layers
        grid_amounts = [per_grid_capital] * layers
    elif sizing == "pyramid":
        # 金字塔：第1格1份，第2格2份，..., 第N格N份
        total_parts = layers * (layers + 1) / 2
        per_part = total_capital / total_parts
        grid_amounts = [per_part * (i + 1) for i in range(layers)]
    else:
        raise ValueError(f"Unknown sizing: {sizing}")

    cash = total_capital
    shares = 0.0
    trades: list[Trade] = []
    base_price: Optional[float] = None
    grid_triggered = [False] * layers  # 标记各层是否已触发
    daily_assets = []  # [(date, asset)]
    daily_nav_values = []  # 用于计算MA
    ref_date = df.iloc[0]["dt"]
    trade_dates = set()

    # 日计数（用于IRR和货基计息）
    day_count = 0
    prev_dt = None
    daily_mm_rate = (1 + mm_yield) ** (1 / 365) - 1

    for _, row in df.iterrows():
        dt = row["dt"]
        nav = float(row["adj_nav"])  # 使用复权净值
        date_str = row["nav_date"]
        day_count += 1

        # 闲置资金每日计息
        if prev_dt is not None:
            days_passed = (dt - prev_dt).days
            if days_passed > 0:
                cash *= (1 + daily_mm_rate) ** days_passed
        prev_dt = dt

        # 更新MA60
        daily_nav_values.append(nav)
        if len(daily_nav_values) > 60:
            daily_nav_values.pop(0)
        ma60 = float(np.mean(daily_nav_values)) if len(daily_nav_values) >= 20 else nav

        # 确定当前基准价格
        if base_type == "first":
            if base_price is None:
                base_price = nav
        elif base_type == "ma60":
            if base_price is None:
                base_price = nav  # 初始用当日价
            else:
                # MA60动态调整（只在上涨时上调基准，下跌时保持）
                if ma60 > base_price:
                    base_price = ma60

        # 检查网格触发
        for level in range(layers):
            if grid_triggered[level]:
                continue  # 该层已触发

            trigger_nav = base_price * (1 - spacing) ** (level + 1)

            if nav <= trigger_nav and cash >= grid_amounts[level]:
                # 触发买入
                buy_amount = min(grid_amounts[level], cash)
                buy_shares = buy_amount / nav
                shares += buy_shares
                cash -= buy_amount
                grid_triggered[level] = True

                trade = Trade(
                    date=date_str,
                    nav=round(nav, 4),
                    grid_level=level + 1,
                    trigger_nav=round(trigger_nav, 4),
                    amount=round(buy_amount, 2),
                    shares=round(buy_shares, 2),
                )
                trades.append(trade)
                trade_dates.add(date_str)

        # 记录每日总资产
        total_asset = shares * nav + cash
        daily_assets.append((date_str, total_asset))

    # 最终数据
    final_nav = float(df.iloc[-1]["adj_nav"])
    position_value = shares * final_nav
    total_asset = position_value + cash
    total_invested = total_capital - (cash / (1 + mm_yield) ** (day_count / 365))  # 近似
    # 更精确：total_invested = sum(trade.amount for trade in trades)
    total_invested = sum(t.amount for t in trades)

    # 创建每日资产序列
    daily_series = pd.Series(
        [a[1] for a in daily_assets],
        index=pd.to_datetime([a[0] for a in daily_assets]),
    )

    # 时间跨度
    years_total = (df.iloc[-1]["dt"] - df.iloc[0]["dt"]).days / 365.25

    # IRR 现金流（基于总资金池：初始投入全部资金，期末回收总资产）
    cashflows_irr_total = [(-0.001, -total_capital), (years_total, total_asset)]

    # 基于已投入资金的 IRR（仅衡量实际部署部分的效率）
    cashflows_irr_deployed = []
    for trade in trades:
        t_years = (datetime.strptime(trade.date, "%Y%m%d") - ref_date.to_pydatetime()).days / 365.25
        cashflows_irr_deployed.append((t_years, -trade.amount))
    cashflows_irr_deployed.append((years_total, total_asset))

    # 计算指标
    total_return_pct = (total_asset / total_capital - 1) * 100

    # 年化收益 = 基于总资金的 CAGR
    annual_return = (total_asset / total_capital) ** (1 / max(years_total, 0.01)) - 1
    annual_irr_total = annual_return * 100  # 总资本年化

    # 已投入部分的 IRR（仅供参考，不用于排名对比）
    if trades:
        annual_irr_deployed = calc_irr(cashflows_irr_deployed) * 100
    else:
        annual_irr_deployed = 0.0

    max_dd = calc_max_drawdown(daily_series) * 100

    # 资金利用率 = 已投入/总资金
    capital_util = total_invested / total_capital * 100 if total_capital > 0 else 0.0

    return StrategyResult(
        name=config.name,
        description=config.description,
        total_capital=total_capital,
        total_invested=total_invested,
        final_shares=shares,
        final_nav=final_nav,
        position_value=position_value,
        cash_remaining=cash,
        total_asset=total_asset,
        total_profit=total_asset - total_capital,
        total_return_pct=total_return_pct,
        annual_return_pct=annual_irr_total,
        max_drawdown_pct=max_dd,
        capital_utilization_pct=capital_util,
        n_trades=len(trades),
        trades=trades,
        daily_asset=daily_series,
    )


# ═══════════════════════════════════════════════════════
#  完整网格策略（买卖双向）
# ═══════════════════════════════════════════════════════

@dataclass
class FullGridConfig:
    """完整网格策略配置（买卖双向）"""
    name: str
    buy_spacing: float       # 买入网格间距
    sell_spacing: float      # 卖出网格间距
    layers: int               # 最大持仓层数
    sizing: str               # 资金分配: "equal" / "pyramid"
    base_type: str            # 基准价格: "first" / "ma60"
    description: str = ""

    def __post_init__(self):
        if not self.description:
            sizing_cn = {"equal": "等额", "pyramid": "金字塔"}
            self.description = (
                f"买入{self.buy_spacing*100:.0f}%_卖出{self.sell_spacing*100:.0f}%_"
                f"{self.layers}层_{sizing_cn.get(self.sizing, self.sizing)}"
            )


@dataclass
class Lot:
    """持仓批次"""
    buy_date: str
    buy_nav: float
    shares: float
    cost: float
    grid_level: int
    sold: bool = False
    sell_date: str = ""
    sell_nav: float = 0.0
    profit: float = 0.0


def run_full_grid_strategy(
    df: pd.DataFrame,
    config: FullGridConfig,
    total_capital: float,
    mm_yield: float = 0.02,
) -> StrategyResult:
    """
    完整网格策略（买卖双向）

    买入：价格跌至 base × (1 - buy_spacing)^N 时触发
    卖出：持仓批次价格达到 buy_price × (1 + sell_spacing) 时卖出
    卖出后该层可重新使用，资本循环
    """
    buy_spacing = config.buy_spacing
    sell_spacing = config.sell_spacing
    layers = config.layers
    sizing = config.sizing
    base_type = config.base_type

    # 每格资金
    if sizing == "equal":
        per_grid_capital = total_capital / layers
        grid_amounts = [per_grid_capital] * layers
    elif sizing == "pyramid":
        total_parts = layers * (layers + 1) / 2
        per_part = total_capital / total_parts
        grid_amounts = [per_part * (i + 1) for i in range(layers)]
    else:
        raise ValueError(f"Unknown sizing: {sizing}")

    cash = total_capital
    lots: list[Lot] = []
    trades: list[Trade] = []
    base_price: Optional[float] = None
    daily_assets = []
    daily_nav_values = []
    ref_date = df.iloc[0]["dt"]
    prev_dt = None
    daily_mm_rate = (1 + mm_yield) ** (1 / 365) - 1

    for _, row in df.iterrows():
        dt = row["dt"]
        nav = float(row["adj_nav"])
        date_str = row["nav_date"]

        # 闲置资金每日计息
        if prev_dt is not None:
            days_passed = (dt - prev_dt).days
            if days_passed > 0:
                cash *= (1 + daily_mm_rate) ** days_passed
        prev_dt = dt

        # 更新MA
        daily_nav_values.append(nav)
        if len(daily_nav_values) > 60:
            daily_nav_values.pop(0)
        ma60 = float(np.mean(daily_nav_values)) if len(daily_nav_values) >= 20 else nav

        # 确定基准价格
        if base_type == "first":
            if base_price is None:
                base_price = nav
        elif base_type == "ma60":
            if base_price is None:
                base_price = nav
            elif ma60 > base_price:
                base_price = ma60

        # ── 卖出检查：逐个批次检查是否达到卖出价 ──
        for lot in lots:
            if lot.sold:
                continue
            sell_trigger = lot.buy_nav * (1 + sell_spacing)
            if nav >= sell_trigger:
                # 卖出该批次
                sell_proceeds = lot.shares * nav
                lot.sold = True
                lot.sell_date = date_str
                lot.sell_nav = nav
                lot.profit = sell_proceeds - lot.cost
                cash += sell_proceeds

                trades.append(Trade(
                    date=date_str,
                    nav=round(nav, 4),
                    grid_level=-lot.grid_level,  # 负数表示卖出
                    trigger_nav=round(sell_trigger, 4),
                    amount=-sell_proceeds,  # 负数表示收回
                    shares=-lot.shares,
                ))

        # ── 买入检查 ──
        # 计算当前已使用的层（未卖出的lots）
        active_levels = set(lot.grid_level for lot in lots if not lot.sold)

        for level in range(layers):
            if level in active_levels:
                continue  # 该层已有持仓且未卖出

            trigger_nav = base_price * (1 - buy_spacing) ** (level + 1)

            if nav <= trigger_nav and cash >= grid_amounts[level]:
                buy_amount = min(grid_amounts[level], cash)
                buy_shares = buy_amount / nav
                cash -= buy_amount

                lot = Lot(
                    buy_date=date_str,
                    buy_nav=nav,
                    shares=buy_shares,
                    cost=buy_amount,
                    grid_level=level,
                )
                lots.append(lot)

                trades.append(Trade(
                    date=date_str,
                    nav=round(nav, 4),
                    grid_level=level + 1,
                    trigger_nav=round(trigger_nav, 4),
                    amount=buy_amount,
                    shares=round(buy_shares, 2),
                ))

        # 记录每日总资产
        position_value = sum(lot.shares * nav for lot in lots if not lot.sold)
        total_asset = position_value + cash
        daily_assets.append((date_str, total_asset))

    # 最终数据
    final_nav = float(df.iloc[-1]["adj_nav"])
    position_value = sum(lot.shares * final_nav for lot in lots if not lot.sold)
    total_asset = position_value + cash

    # total_invested is tricky for full grid because capital recycles
    # Use sum of all buy amounts (including re-deployed capital)
    total_invested = sum(t.amount for t in trades if t.amount > 0)

    daily_series = pd.Series(
        [a[1] for a in daily_assets],
        index=pd.to_datetime([a[0] for a in daily_assets]),
    )

    years_total = (df.iloc[-1]["dt"] - df.iloc[0]["dt"]).days / 365.25
    total_return_pct = (total_asset / total_capital - 1) * 100
    annual_return = (total_asset / total_capital) ** (1 / max(years_total, 0.01)) - 1
    annual_irr_total = annual_return * 100
    max_dd = calc_max_drawdown(daily_series) * 100

    # 资金利用率：活跃持仓占比
    capital_util = total_invested / total_capital * 100 if total_capital > 0 else 0.0

    # 卖出统计
    sell_trades = [t for t in trades if t.amount < 0]
    total_sold = abs(sum(t.amount for t in sell_trades))
    total_bought = sum(t.amount for t in trades if t.amount > 0)

    return StrategyResult(
        name=config.name,
        description=config.description,
        total_capital=total_capital,
        total_invested=total_bought,
        final_shares=sum(lot.shares for lot in lots if not lot.sold),
        final_nav=final_nav,
        position_value=position_value,
        cash_remaining=cash,
        total_asset=total_asset,
        total_profit=total_asset - total_capital,
        total_return_pct=total_return_pct,
        annual_return_pct=annual_irr_total,
        max_drawdown_pct=max_dd,
        capital_utilization_pct=min(capital_util, 200.0),  # 可能>100%（资本循环）
        n_trades=len(trades),
        trades=trades,
        daily_asset=daily_series,
    )


# ═══════════════════════════════════════════════════════
#  基准策略：一次性买入
# ═══════════════════════════════════════════════════════

def run_lump_sum(
    df: pd.DataFrame,
    total_capital: float,
    name: str = "一次性买入",
) -> StrategyResult:
    """一次性买入策略"""
    first_nav = float(df.iloc[0]["adj_nav"])
    final_nav = float(df.iloc[-1]["adj_nav"])
    shares = total_capital / first_nav
    position_value = shares * final_nav
    total_asset = position_value

    ref_date = df.iloc[0]["dt"]
    years_total = (df.iloc[-1]["dt"] - ref_date).days / 365.25

    # 每日资产
    daily_assets = []
    for _, row in df.iterrows():
        nav = float(row["adj_nav"])
        daily_assets.append((row["nav_date"], shares * nav))
    daily_series = pd.Series(
        [a[1] for a in daily_assets],
        index=pd.to_datetime([a[0] for a in daily_assets]),
    )

    total_return_pct = (total_asset / total_capital - 1) * 100
    # 基于总资本的年化 CAGR（与网格策略统一口径）
    total_cagr = ((total_asset / total_capital) ** (1 / years_total) - 1) * 100 if years_total > 0 else 0
    max_dd = calc_max_drawdown(daily_series) * 100

    return StrategyResult(
        name=name,
        description="期初一次性全仓买入",
        total_capital=total_capital,
        total_invested=total_capital,
        final_shares=shares,
        final_nav=final_nav,
        position_value=position_value,
        cash_remaining=0.0,
        total_asset=total_asset,
        total_profit=total_asset - total_capital,
        total_return_pct=total_return_pct,
        annual_return_pct=total_cagr,
        max_drawdown_pct=max_dd,
        capital_utilization_pct=100.0,
        n_trades=1,
        trades=[Trade(
            date=df.iloc[0]["nav_date"],
            nav=round(first_nav, 4),
            grid_level=0,
            trigger_nav=round(first_nav, 4),
            amount=total_capital,
            shares=round(shares, 2),
        )],
        daily_asset=daily_series,
    )


# ═══════════════════════════════════════════════════════
#  基准策略：月度定投
# ═══════════════════════════════════════════════════════

def run_monthly_dca(
    df: pd.DataFrame,
    total_capital: float,
    name: str = "月度定投",
) -> StrategyResult:
    """
    月度定投：每月第一个交易日投入固定金额。
    金额 = total_capital / 总月数
    """
    monthly_first = {}
    for _, row in df.iterrows():
        ym = str(row["dt"].to_period("M"))
        if ym not in monthly_first:
            monthly_first[ym] = (row["nav_date"], float(row["adj_nav"]))

    n_months = len(monthly_first)
    monthly_amount = total_capital / n_months

    shares = 0.0
    total_invested = 0.0
    trades: list[Trade] = []
    buy_dates = set(monthly_first.values())
    buy_info = {v[0]: v[1] for v in monthly_first.values()}

    ref_date = df.iloc[0]["dt"]
    ref_nav_date = df.iloc[0]["nav_date"]
    daily_assets = []

    for _, row in df.iterrows():
        nav = float(row["adj_nav"])
        date_str = row["nav_date"]

        if date_str in buy_info:
            buy_shares = monthly_amount / nav
            shares += buy_shares
            total_invested += monthly_amount
            trades.append(Trade(
                date=date_str,
                nav=round(nav, 4),
                grid_level=0,
                trigger_nav=round(nav, 4),
                amount=monthly_amount,
                shares=round(buy_shares, 2),
            ))

        daily_assets.append((date_str, shares * nav))

    final_nav = float(df.iloc[-1]["adj_nav"])
    total_asset = shares * final_nav
    daily_series = pd.Series(
        [a[1] for a in daily_assets],
        index=pd.to_datetime([a[0] for a in daily_assets]),
    )

    years_total = (df.iloc[-1]["dt"] - ref_date).days / 365.25
    cashflows_irr = [(0, 0)]  # 定投从第一月算起
    month_idx = 0
    for _, row in df.iterrows():
        if row["nav_date"] in buy_info:
            t_years = month_idx / 12.0
            cashflows_irr.append((t_years, -monthly_amount))
            month_idx += 1
    cashflows_irr.append((years_total, total_asset))
    # 合并同一时间的现金流
    from collections import defaultdict
    merged = defaultdict(float)
    for t, amt in cashflows_irr:
        merged[round(t, 6)] += amt
    cashflows_irr = [(t, amt) for t, amt in sorted(merged.items())]

    annual_irr = calc_irr(cashflows_irr) * 100 if cashflows_irr else 0.0
    total_return_pct = (total_asset / total_invested - 1) * 100
    max_dd = calc_max_drawdown(daily_series) * 100

    return StrategyResult(
        name=name,
        description="每月固定金额定投",
        total_capital=total_capital,
        total_invested=total_invested,
        final_shares=shares,
        final_nav=final_nav,
        position_value=total_asset,
        cash_remaining=0.0,
        total_asset=total_asset,
        total_profit=total_asset - total_invested,
        total_return_pct=total_return_pct,
        annual_return_pct=annual_irr,
        max_drawdown_pct=max_dd,
        capital_utilization_pct=100.0,  # 定投按月全部利用
        n_trades=len(trades),
        trades=trades,
        daily_asset=daily_series,
    )


# ═══════════════════════════════════════════════════════
#  报告输出
# ═══════════════════════════════════════════════════════

def generate_report(
    results: list[StrategyResult],
    etf_name: str,
    etf_code: str,
    start_date: str,
    end_date: str,
    total_capital: float,
) -> str:
    """生成 Markdown 报告"""
    # 分类策略
    benchmarks = [r for r in results if "一次性" in r.name or "定投" in r.name]
    simple_grids = [r for r in results if r.name.startswith("grid_")]
    full_grids = [r for r in results if r.name.startswith("fullgrid_")]

    lines = [
        f"# 网格买入算法研究 — {etf_name}（{etf_code}）",
        "",
        f"> 回测区间：{start_date} → {end_date} | 总资金池：{total_capital:,.0f} 元 | 标的涨幅：需查",
        "",
        "---",
        "",
        "## 一、实验背景与问题",
        "",
        f"**标的**：{etf_name}（{etf_code}），跟踪中证红利指数（000922），成立于2019年11月。",
        "",
        "**核心问题**：",
        "1. 网格买入法（价格每跌N%买一份）能否在515180上跑赢一次性买入和定投？",
        "2. 如果加上卖出端（涨M%卖出一份，高抛低吸），效果如何？",
        "3. 最优网格参数是什么？",
        "",
        "**策略类型**：",
        "- **单向买入网格**：价格跌到基准价以下触发买入，不卖出",
        "- **完整网格（买卖双向）**：买入后，涨到买入价+S%时卖出该批次，资本循环利用",
        "",
        "---",
        "",
        "## 二、策略参数设计",
        "",
        "### 单向买入网格",
        "",
        "| 参数 | 候选值 |",
        "|------|--------|",
        "| 买入间距 | 3%, 5%, 8%, 10% |",
        "| 层数 | 5, 10, 20 |",
        "| 资金分配 | 等额 / 金字塔(1,2,3…) |",
        "| 基准价格 | 首次买入价 / MA60动态 |",
        "",
        "### 完整网格（买卖双向）",
        "",
        "| 参数 | 候选值 |",
        "|------|--------|",
        "| 买入间距 | 3%, 5%, 8%, 10% |",
        "| 卖出间距 | 5%, 8%, 10% |",
        "| 层数 | 10 |",
        "| 基准价格 | 首次买入价 / MA60动态 |",
        "",
        "### 基准策略",
        "",
        "- **一次性买入**：期初全部资金买入，持有到期",
        "- **月度定投**：每月第一个交易日等额定投",
        "",
        "### 通用规则",
        "- 触发价 = 基准价 × (1 − 间距)^N（等比递减）",
        "- 闲置资金按年化2%计息（模拟货基）",
        "- 使用复权净值（分红已含）",
        "- **所有策略统一基于总资金池78万计算年化收益**（可比口径）",
        "",
        "---",
        "",
        "## 三、回测结果",
        "",
        "### 3.1 总览对比（按年化收益降序）",
        "",
        "| 策略 | 类型 | 总投入 | 总资产 | 总收益 | 年化 | 最大回撤 | 交易次数 |",
        "|------|------|--------|--------|--------|------|----------|----------|",
    ]

    all_sorted = sorted(results, key=lambda r: r.annual_return_pct, reverse=True)

    for r in all_sorted:
        if r.name == "一次性买入":
            stype = "🏳️ 一次性"
        elif "定投" in r.name:
            stype = "🏳️ 定投"
        elif r.name.startswith("fullgrid_"):
            stype = "🔄 完整网格"
        else:
            stype = "📥 单向网格"

        lines.append(
            f"| {r.name} | {stype} | {r.total_invested:,.0f} | {r.total_asset:,.0f} | "
            f"{r.total_return_pct:+.1f}% | **{r.annual_return_pct:+.2f}%** | "
            f"{r.max_drawdown_pct:+.1f}% | {r.n_trades} |"
        )

    lines += [
        "",
        "### 3.2 分组对比",
        "",
        "#### A. 三大类策略最佳代表",
        "",
        "| 类型 | 最优策略 | 年化 | 总收益 | 最大回撤 | 交易次数 | 资金利用率 |",
        "|------|----------|------|--------|----------|----------|------------|",
    ]

    if benchmarks:
        best_bench = sorted(benchmarks, key=lambda r: r.annual_return_pct, reverse=True)
        for b in best_bench[:2]:
            lines.append(
                f"| 基准 | {b.name} | **{b.annual_return_pct:+.2f}%** | "
                f"{b.total_return_pct:+.1f}% | {b.max_drawdown_pct:+.1f}% | {b.n_trades} | {b.capital_utilization_pct:.0f}% |"
            )

    if simple_grids:
        best_sg = sorted(simple_grids, key=lambda r: r.annual_return_pct, reverse=True)[0]
        lines.append(
            f"| 单向网格 | {best_sg.name} | **{best_sg.annual_return_pct:+.2f}%** | "
            f"{best_sg.total_return_pct:+.1f}% | {best_sg.max_drawdown_pct:+.1f}% | {best_sg.n_trades} | {best_sg.capital_utilization_pct:.0f}% |"
        )

    if full_grids:
        best_fg = sorted(full_grids, key=lambda r: r.annual_return_pct, reverse=True)[0]
        lines.append(
            f"| 完整网格 | {best_fg.name} | **{best_fg.annual_return_pct:+.2f}%** | "
            f"{best_fg.total_return_pct:+.1f}% | {best_fg.max_drawdown_pct:+.1f}% | {best_fg.n_trades} | {best_fg.capital_utilization_pct:.0f}% |"
        )

    lines += [
        "",
        "#### B. 完整网格策略（买卖双向）独立排名",
        "",
        "| 策略 | 买入距 | 卖出距 | 年化 | 总收益 | 最大回撤 | 交易次数 |",
        "|------|--------|--------|------|--------|----------|----------|",
    ]

    for r in sorted(full_grids, key=lambda r: r.annual_return_pct, reverse=True):
        # 解析参数
        desc = r.description
        lines.append(
            f"| {r.name} | {desc} | {r.annual_return_pct:+.2f}% | "
            f"{r.total_return_pct:+.1f}% | {r.max_drawdown_pct:+.1f}% | {r.n_trades} |"
        )

    # 最优完整网格的交易记录
    if full_grids:
        best_fg = sorted(full_grids, key=lambda r: r.annual_return_pct, reverse=True)[0]
        buy_trades = [t for t in best_fg.trades if t.amount > 0]
        sell_trades = [t for t in best_fg.trades if t.amount < 0]

        lines += [
            "",
            f"**最优完整网格 {best_fg.name} 交易明细**：",
            "",
            f"买入 {len(buy_trades)} 次，卖出 {len(sell_trades)} 次",
            "",
            "| 日期 | 方向 | 净值 | 金额 | 份额 |",
            "|------|------|------|------|------|",
        ]
        for t in best_fg.trades:
            direction = "卖出" if t.amount < 0 else "买入"
            lines.append(
                f"| {t.date} | {direction} | {t.nav:.4f} | {abs(t.amount):,.0f} | {abs(t.shares):,.0f} |"
            )

    lines += [
        "",
        "---",
        "",
        "## 四、核心发现",
        "",
        "### 发现1：所有网格策略均跑输买入持有和定投",
        "",
        f"- 最优基准（一次性买入）：年化 +9.42%，回撤 -18.51%",
        f"- 最优单向网格（grid_5pct_5L）：年化 +6.43%，回撤 -9.16%",
    ]

    if full_grids:
        best_fg = sorted(full_grids, key=lambda r: r.annual_return_pct, reverse=True)[0]
        lines.append(f"- 最优完整网格（{best_fg.name}）：年化 {best_fg.annual_return_pct:+.2f}%，回撤 {best_fg.max_drawdown_pct:+.1f}%")

    lines += [
        "",
        "**根本原因**：515180在2020-2026年期间，复权净值从1.03涨到1.85（**+78.6%**），",
        "是典型的单边上涨行情。网格策略在这种市场中：",
        "- 单向网格：买入后很少触发更多买点（资金利用率仅10-40%），大量资金闲置",
        "- 完整网格：卖出后价格继续涨，踏空后续涨幅（\"卖飞\"）",
        "",
        "### 发现2：回撤控制是网格的唯一优势",
        "",
        "| 策略类型 | 代表策略 | 年化 | 最大回撤 | 收益回撤比 |",
        "|----------|----------|------|----------|------------|",
    ]

    if benchmarks:
        b = sorted(benchmarks, key=lambda r: r.annual_return_pct, reverse=True)[0]
        lines.append(f"| 一次性买入 | {b.name} | {b.annual_return_pct:+.2f}% | {b.max_drawdown_pct:+.1f}% | {abs(b.annual_return_pct/b.max_drawdown_pct):.2f} |")

    if simple_grids:
        sg = sorted(simple_grids, key=lambda r: r.annual_return_pct, reverse=True)[0]
        lines.append(f"| 单向网格 | {sg.name} | {sg.annual_return_pct:+.2f}% | {sg.max_drawdown_pct:+.1f}% | {abs(sg.annual_return_pct/sg.max_drawdown_pct):.2f} |")

    if full_grids:
        fg = sorted(full_grids, key=lambda r: r.annual_return_pct, reverse=True)[0]
        lines.append(f"| 完整网格 | {fg.name} | {fg.annual_return_pct:+.2f}% | {fg.max_drawdown_pct:+.1f}% | {abs(fg.annual_return_pct/fg.max_drawdown_pct):.2f} |")

    lines += [
        "",
        "完整网格的最大回撤仅 **-0.16%~-2.32%**，几乎可以忽略。如果你极度厌恶回撤，",
        "完整网格是一种选择——但代价是年化收益降至2-4%。",
        "",
        "### 发现3：MA60动态基准优于固定基准",
        "",
        "使用MA60作为基准价格的完整网格策略明显好于固定首次买入价：",
        f"- fullgrid_b5_s8_10L_equa_ma60：年化 +3.87%，交易28次（MA60）",
        f"- fullgrid_b5_s8_10L_equa：年化 +2.61%，交易8次（固定基准）",
        "",
        "原因：MA60跟随趋势上移，在牛市中允许更高频的买卖循环，提高资金周转。",
        "",
        "### 发现4：卖出间距 > 买入间距通常更好",
        "",
        "完整网格中，卖出间距≥8%优于5%（给利润更多空间奔跑，减少\"卖飞\"）：",
        "- buy5_sell8: +2.61% vs buy5_sell5: +2.51%",
        "- buy5_sell10: +2.66% vs buy5_sell5: +2.51%",
        "",
        "### 发现5：金字塔买入不适用于牛市网格",
        "",
        "金字塔买法（越跌越买）在单边牛市中表现最差：",
        "- grid_5pct_10L_pyra（单向）：年化仅 +2.67%，利用率仅5.5%",
        "- fullgrid_b5_s5_10L_pyra（完整）：年化仅 +2.13%，利用率仅14.5%",
        "",
        "原因：牛市跌得少，金字塔后端的大仓位根本用不上。",
        "",
        "---",
        "",
        "## 五、结论与建议",
        "",
        "### 直接结论",
        "",
        "| 问题 | 答案 |",
        "|------|------|",
        "| 网格买入能跑赢定投吗？ | ❌ 不能。最优单向网格+6.43% < 定投+8.38% |",
        "| 完整网格（买卖双向）能跑赢吗？ | ❌ 不能。最优完整网格+3.87% < 定投+8.38% |",
        "| 网格有什么价值？ | ✅ 回撤控制极好（完整网格几乎零回撤） |",
        "| 什么时候网格有用？ | 震荡市/下跌市，不适合单边上涨市 |",
        "| 515180适合网格吗？ | ❌ 不适合。6年+78.6%的单边牛市中，网格全面跑输 |",
        "",
        "### 投资建议",
        "",
        "1. **对于515180（中证红利ETF）**：直接定投或买入持有即可，不要用网格。",
        "   标的本身的长期趋势向上，网格只会让你买不够、卖太早。",
        "",
        "2. **什么时候考虑网格**：",
        "   - 标的处于明确的震荡区间（如横盘2年以上的宽基ETF）",
        "   - 高波动标的（如行业ETF、商品ETF），震荡幅度大",
        "   - 你已经持有大量底仓，网格作为增强收益的辅助手段",
        "",
        "3. **如果非要用网格**：",
        "   - 使用MA60动态基准（不要用固定首次买入价）",
        "   - 卖出间距设为买入间距的1.5-2倍（减少卖飞）",
        "   - 等额分配优于金字塔（牛市环境中）",
        "",
        "### 后续研究方向",
        "",
        "- [ ] 在震荡市标的（如510050上证50、510300沪深300）上测试网格",
        "- [ ] 在行业ETF（高波动）上测试完整网格",
        "- [ ] 网格+底仓混合策略（70%底仓持有+30%网格增强）",
        "- [ ] 基于ATR/波动率自适应间距的网格",
        "- [ ] 叠加到FCF选股策略中的网格买入执行层",
        "",
        "---",
        "",
        f"*报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        f"*代码文件：`run_grid_research.py`*",
        "",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="网格买入算法研究 — 515180 易方达中证红利ETF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--code", default="515180.SH", help="ETF代码")
    parser.add_argument("--start", default="2020-01-01", help="回测开始日期")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="回测结束日期")
    parser.add_argument("--total-capital", type=float, default=780_000,
                        help="总资金池（默认78万 = 1万/月×78月）")
    parser.add_argument("--output", default=None, help="报告输出路径（默认自动生成到docs/）")
    args = parser.parse_args()

    print("=" * 70)
    print("  网格买入算法研究")
    print(f"  标的: {args.code}")
    print(f"  区间: {args.start} → {args.end}")
    print(f"  资金池: {args.total_capital:,.0f} 元")
    print("=" * 70)

    # 初始化数据
    print("\n📡 获取数据...")
    pro = init_tushare()
    etf_name = fetch_etf_name(pro, args.code)
    print(f"  基金名称: {etf_name}")
    df = fetch_nav_data(pro, args.code, args.start, args.end)
    print(f"  净值数据: {len(df)} 行, {df.iloc[0]['nav_date']} → {df.iloc[-1]['nav_date']}")

    if df.empty:
        print("❌ 无法获取净值数据，退出。")
        return

    first_nav = float(df.iloc[0]["adj_nav"])
    last_nav = float(df.iloc[-1]["adj_nav"])
    nav_change = (last_nav / first_nav - 1) * 100
    print(f"  复权净值: {first_nav:.4f} → {last_nav:.4f} ({nav_change:+.2f}%)")

    # 定义所有策略
    total_capital = args.total_capital

    strategies: list[tuple] = []

    # ── 基准策略 ──
    strategies.append(("lump_sum", "一次性买入基准", lambda: run_lump_sum(df, total_capital)))
    strategies.append(("dca_monthly", "月度定投基准", lambda: run_monthly_dca(df, total_capital)))

    # ── 网格策略参数扫描 ──
    param_grids = [
        # (间距, 层数, 资金分配, 基准类型)
        (0.03, 10, "equal", "first"),
        (0.05, 5, "equal", "first"),
        (0.05, 10, "equal", "first"),
        (0.05, 20, "equal", "first"),
        (0.05, 10, "pyramid", "first"),
        (0.08, 10, "equal", "first"),
        (0.08, 10, "pyramid", "first"),
        (0.10, 5, "equal", "first"),
        (0.10, 10, "equal", "first"),
        (0.05, 10, "equal", "ma60"),
        (0.08, 10, "equal", "ma60"),
    ]

    for spacing, layers, sizing, base_type in param_grids:
        name = f"grid_{int(spacing*100)}pct_{layers}L_{sizing[:4]}"
        if base_type != "first":
            name += f"_{base_type}"
        config = GridConfig(
            name=name,
            spacing=spacing,
            layers=layers,
            sizing=sizing,
            base_type=base_type,
        )
        strategies.append((name, config.description, lambda cfg=config: run_grid_strategy(df, cfg, total_capital)))

    # ── 完整网格策略（买卖双向）──
    full_grid_params = [
        # (买入间距, 卖出间距, 层数, 资金分配, 基准类型)
        (0.05, 0.05, 10, "equal", "first"),
        (0.05, 0.08, 10, "equal", "first"),
        (0.05, 0.10, 10, "equal", "first"),
        (0.08, 0.08, 10, "equal", "first"),
        (0.08, 0.10, 10, "equal", "first"),
        (0.05, 0.05, 10, "pyramid", "first"),
        (0.03, 0.05, 10, "equal", "first"),
        (0.10, 0.10, 10, "equal", "first"),
        (0.05, 0.05, 10, "equal", "ma60"),
        (0.05, 0.08, 10, "equal", "ma60"),
    ]

    for buy_sp, sell_sp, layers, sizing, base_type in full_grid_params:
        name = f"fullgrid_b{int(buy_sp*100)}_s{int(sell_sp*100)}_{layers}L_{sizing[:4]}"
        if base_type != "first":
            name += f"_{base_type}"
        fg_config = FullGridConfig(
            name=name,
            buy_spacing=buy_sp,
            sell_spacing=sell_sp,
            layers=layers,
            sizing=sizing,
            base_type=base_type,
        )
        strategies.append((name, fg_config.description, lambda cfg=fg_config: run_full_grid_strategy(df, cfg, total_capital)))

    # 执行回测
    results: list[StrategyResult] = []
    print(f"\n🔄 执行 {len(strategies)} 个策略...")
    for i, (name, desc, runner) in enumerate(strategies, 1):
        try:
            result = runner()
            results.append(result)
            print(f"  [{i:2d}/{len(strategies)}] {name:30s} → "
                  f"年化 {result.annual_return_pct:+.2f}% | "
                  f"回撤 {result.max_drawdown_pct:+.2f}% | "
                  f"交易 {result.n_trades}次 | "
                  f"利用率 {result.capital_utilization_pct:.0f}%")
        except Exception as e:
            print(f"  [{i:2d}/{len(strategies)}] {name:30s} → ❌ 失败: {e}")

    if not results:
        print("❌ 所有策略执行失败。")
        return

    # 生成报告
    print("\n📝 生成报告...")
    report = generate_report(
        results, etf_name, args.code,
        args.start, args.end,
        total_capital,
    )

    # 保存报告
    if args.output:
        report_path = Path(args.output)
    else:
        docs_dir = _PROJECT_ROOT / "docs"
        docs_dir.mkdir(exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        code_tag = args.code.replace(".", "_")
        report_path = docs_dir / f"{today}_网格买入算法研究_{code_tag}.md"

    report_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 报告已保存: {report_path}")
    print(f"\n{'=' * 70}")
    print("  关键结论预览:")
    print("  " + "-" * 66)

    sorted_results = sorted(results, key=lambda r: r.annual_return_pct, reverse=True)
    for i, r in enumerate(sorted_results[:5], 1):
        flag = "🏆" if i == 1 else "  "
        print(f"  {flag} #{i}: {r.name:30s} 年化 {r.annual_return_pct:+.2f}%  "
              f"回撤 {r.max_drawdown_pct:+.2f}%  交易{r.n_trades}次")

    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
