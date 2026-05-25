"""
Portfolio — 持仓与仓位管理
==========================
管理投资组合的持仓状态、资金和交易记录。

核心概念：
  - Portfolio: 组合状态（持仓、现金、净值曲线）
  - Position:  单只股票持仓（数量、成本、市值）
  - Trade:     交易记录（买卖、价格、数量、费用）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np


@dataclass
class Trade:
    """交易记录"""
    date: str              # 交易日期 YYYY-MM-DD
    ts_code: str           # 股票代码
    name: str              # 股票名称
    action: str            # "buy" / "sell"
    price: float           # 成交价格
    shares: int            # 成交股数（手×100）
    amount: float           # 成交金额
    commission: float      # 手续费
    reason: str            # 交易原因（评分变化等）
    category: str = ""     # 股票类别


@dataclass
class Position:
    """单只股票持仓"""
    ts_code: str           # 股票代码
    name: str              # 股票名称
    category: str          # 类别
    shares: int = 0        # 持有股数
    cost_price: float = 0.0  # 持仓成本价
    current_price: float = 0.0  # 当前价格
    target_weight: float = 0.0  # 目标权重 (0-1)
    current_weight: float = 0.0  # 当前权重 (0-1)

    @property
    def market_value(self) -> float:
        """持仓市值"""
        return self.shares * self.current_price

    @property
    def cost_value(self) -> float:
        """持仓成本"""
        return self.shares * self.cost_price

    @property
    def profit_pct(self) -> float:
        """持仓盈亏比例"""
        if self.cost_price <= 0:
            return 0.0
        return (self.current_price / self.cost_price - 1) * 100


class Portfolio:
    """
    投资组合管理器

    管理现金、持仓、交易记录和净值曲线。
    """

    def __init__(
        self,
        initial_cash: float = 100_0000,  # 100万
        commission_rate: float = 0.001,    # 交易费率 0.1%
        min_commission: float = 5.0,       # 最低手续费
        stamp_tax_rate: float = 0.001,     # 印花税 0.1%（仅卖出）
        slippage: float = 0.001,           # 滑点 0.1%
        lot_size: int = 100,               # 1手=100股
    ):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_tax_rate = stamp_tax_rate
        self.slippage = slippage
        self.lot_size = lot_size

        # 持仓
        self.positions: Dict[str, Position] = {}
        # 交易记录
        self.trades: List[Trade] = []
        # 净值曲线
        self.nav_curve: List[Dict] = []
        # 当前日期
        self.current_date: str = ""

    @property
    def total_market_value(self) -> float:
        """持仓总市值"""
        return sum(p.market_value for p in self.positions.values())

    @property
    def total_value(self) -> float:
        """总资产 = 现金 + 持仓市值"""
        return self.cash + self.total_market_value

    @property
    def total_return(self) -> float:
        """总收益率"""
        return (self.total_value / self.initial_cash - 1) * 100

    def update_prices(self, prices: Dict[str, float]):
        """更新持仓价格"""
        for ts_code, price in prices.items():
            if ts_code in self.positions:
                self.positions[ts_code].current_price = price

    def set_date(self, date: str):
        """设置当前日期"""
        self.current_date = date

    def record_nav(self):
        """记录当前净值"""
        total_mv = self.total_market_value
        total_val = self.total_value
        pos_weights = {
            code: pos.market_value / total_val if total_val > 0 else 0
            for code, pos in self.positions.items()
        }

        self.nav_curve.append({
            "date": self.current_date,
            "cash": round(self.cash, 2),
            "market_value": round(total_mv, 2),
            "total_value": round(total_val, 2),
            "total_return": round(self.total_return, 4),
            "positions": {
                code: {
                    "name": pos.name,
                    "shares": pos.shares,
                    "market_value": round(pos.market_value, 2),
                    "weight": round(pos_weights.get(code, 0), 4),
                    "profit_pct": round(pos.profit_pct, 2),
                }
                for code, pos in self.positions.items()
                if pos.shares > 0
            },
        })

    def buy(
        self,
        ts_code: str,
        name: str,
        category: str,
        price: float,
        target_amount: float,
        reason: str = "",
    ) -> Optional[Trade]:
        """
        买入股票

        Parameters
        ----------
        ts_code : str
        name : str
        category : str
        price : float — 目标价格（实际成交含滑点）
        target_amount : float — 目标买入金额
        reason : str

        Returns
        -------
        Trade or None
        """
        # 含滑点的实际买入价
        actual_price = price * (1 + self.slippage)
        # 计算可买股数（向下取整到手的整数倍）
        max_shares_by_cash = int(self.cash / (actual_price * self.lot_size)) * self.lot_size
        max_shares_by_amount = int(target_amount / (actual_price * self.lot_size)) * self.lot_size
        shares = min(max_shares_by_cash, max_shares_by_amount)

        if shares <= 0:
            return None

        amount = shares * actual_price
        commission = max(amount * self.commission_rate, self.min_commission)

        # 检查现金
        total_cost = amount + commission
        if total_cost > self.cash:
            # 缩减到可承受范围
            shares = int((self.cash - self.min_commission) / (actual_price * self.lot_size)) * self.lot_size
            if shares <= 0:
                return None
            amount = shares * actual_price
            commission = max(amount * self.commission_rate, self.min_commission)
            total_cost = amount + commission

        # 扣除现金
        self.cash -= total_cost

        # 更新持仓
        if ts_code in self.positions and self.positions[ts_code].shares > 0:
            pos = self.positions[ts_code]
            total_cost_basis = pos.cost_price * pos.shares + amount
            pos.shares += shares
            pos.cost_price = total_cost_basis / pos.shares
            pos.current_price = price
            pos.category = category
        else:
            self.positions[ts_code] = Position(
                ts_code=ts_code,
                name=name,
                category=category,
                shares=shares,
                cost_price=actual_price,
                current_price=price,
            )

        trade = Trade(
            date=self.current_date,
            ts_code=ts_code,
            name=name,
            action="buy",
            price=actual_price,
            shares=shares,
            amount=amount,
            commission=commission,
            reason=reason,
            category=category,
        )
        self.trades.append(trade)
        return trade

    def sell(
        self,
        ts_code: str,
        price: float,
        shares: Optional[int] = None,
        reason: str = "",
    ) -> Optional[Trade]:
        """
        卖出股票

        Parameters
        ----------
        ts_code : str
        price : float — 目标价格
        shares : int or None — 卖出数量，None表示全部卖出
        reason : str

        Returns
        -------
        Trade or None
        """
        if ts_code not in self.positions:
            return None

        pos = self.positions[ts_code]
        if pos.shares <= 0:
            return None

        sell_shares = shares if shares is not None else pos.shares
        sell_shares = min(sell_shares, pos.shares)

        if sell_shares <= 0:
            return None

        # 含滑点的实际卖出价
        actual_price = price * (1 - self.slippage)
        amount = sell_shares * actual_price
        commission = max(amount * self.commission_rate, self.min_commission)
        stamp_tax = amount * self.stamp_tax_rate

        # 回收现金
        self.cash += amount - commission - stamp_tax

        # 更新持仓
        pos.shares -= sell_shares
        pos.current_price = price
        if pos.shares <= 0:
            pos.shares = 0
            pos.cost_price = 0.0

        trade = Trade(
            date=self.current_date,
            ts_code=ts_code,
            name=pos.name,
            action="sell",
            price=actual_price,
            shares=sell_shares,
            amount=amount,
            commission=commission + stamp_tax,
            reason=reason,
            category=pos.category,
        )
        self.trades.append(trade)
        return trade

    def get_position_summary(self) -> pd.DataFrame:
        """获取持仓摘要"""
        rows = []
        total_val = self.total_value
        for code, pos in self.positions.items():
            if pos.shares > 0:
                rows.append({
                    "ts_code": code,
                    "name": pos.name,
                    "category": pos.category,
                    "shares": pos.shares,
                    "cost_price": round(pos.cost_price, 3),
                    "current_price": round(pos.current_price, 3),
                    "market_value": round(pos.market_value, 2),
                    "weight": round(pos.market_value / total_val * 100, 2) if total_val > 0 else 0,
                    "profit_pct": round(pos.profit_pct, 2),
                })
        return pd.DataFrame(rows)

    def get_trade_log(self) -> pd.DataFrame:
        """获取交易日志"""
        if not self.trades:
            return pd.DataFrame()
        rows = [
            {
                "date": t.date,
                "ts_code": t.ts_code,
                "name": t.name,
                "action": t.action,
                "price": round(t.price, 3),
                "shares": t.shares,
                "amount": round(t.amount, 2),
                "commission": round(t.commission, 2),
                "reason": t.reason,
                "category": t.category,
            }
            for t in self.trades
        ]
        return pd.DataFrame(rows)

    def get_nav_dataframe(self) -> pd.DataFrame:
        """获取净值曲线 DataFrame"""
        if not self.nav_curve:
            return pd.DataFrame()
        rows = [
            {
                "date": n["date"],
                "cash": n["cash"],
                "market_value": n["market_value"],
                "total_value": n["total_value"],
                "total_return": n["total_return"],
                "num_positions": len(n["positions"]),
            }
            for n in self.nav_curve
        ]
        return pd.DataFrame(rows)

    def save_state(self, path: str):
        """保存组合状态到 JSON"""
        state = {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "current_date": self.current_date,
            "positions": {
                code: {
                    "ts_code": pos.ts_code,
                    "name": pos.name,
                    "category": pos.category,
                    "shares": pos.shares,
                    "cost_price": pos.cost_price,
                    "current_price": pos.current_price,
                }
                for code, pos in self.positions.items()
            },
            "trades": [
                {
                    "date": t.date, "ts_code": t.ts_code, "name": t.name,
                    "action": t.action, "price": t.price, "shares": t.shares,
                    "amount": t.amount, "commission": t.commission,
                    "reason": t.reason, "category": t.category,
                }
                for t in self.trades
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
