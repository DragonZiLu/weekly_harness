"""
Strategy — 红利周期轮动策略
============================
基于红利周期评分的季度调仓策略。

策略核心：
  1. 每季度末运行 DividendCycleEvaluator，获取所有标的评分
  2. 根据评分决定目标仓位权重
  3. 考虑类别分散化约束
  4. 生成调仓指令（买入/卖出/持有）

策略规则：
  ┌────────────┬───────────┬────────────────────┐
  │ 评分区间    │ 信号      │ 目标仓位权重         │
  ├────────────┼───────────┼────────────────────┤
  │ ≥80        │ 大胆攒股   │ max_weight (默认15%) │
  │ 65-79      │ 积极布局   │ mid_weight  (默认10%)│
  │ 50-64      │ 观察等待   │ min_weight  (默认5%) │
  │ <50        │ 暂缓/回避  │ 0%                  │
  └────────────┴───────────┴────────────────────┘

类别权重上限（防止过度集中）：
  - 弱周期红利: ≤40%
  - 消费成长红利: ≤30%
  - 周期资源红利: ≤20%
  - ETF红利: ≤15%

调仓触发条件：
  - 评分跨过阈值（如从65降到64，需减仓）
  - 当前权重偏离目标权重超过 rebalance_threshold (默认2%)
  - 每季度至少检查一次（季度末最后一个交易日）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ─── 策略参数 ──────────────────────────────────────────────────

@dataclass
class StrategyParams:
    """策略参数"""

    # ── 仓位映射 ──
    max_weight: float = 0.15        # 大胆攒股(≥80) 单标的最大权重
    mid_weight: float = 0.10        # 积极布局(65-79) 单标的权重
    min_weight: float = 0.05        # 观察等待(50-64) 单标的权重

    # ── 类别权重上限 ──
    category_limits: Dict[str, float] = field(default_factory=lambda: {
        "弱周期红利": 0.40,
        "消费成长红利": 0.30,
        "周期资源红利": 0.20,
        "ETF红利": 0.15,
    })

    # ── 调仓参数 ──
    rebalance_threshold: float = 0.02  # 权重偏离超过2%触发调仓
    min_trade_amount: float = 1000.0   # 最小交易金额（元），低于此不交易

    # ── 评分阈值 ──
    score_strong_buy: float = 80.0
    score_buy: float = 65.0
    score_watch: float = 50.0

    # ── 风控参数 ──
    max_single_stock: float = 0.20    # 单标的最大权重（硬上限）
    max_positions: int = 12           # 最大持仓数
    cash_reserve: float = 0.05       # 最低现金保留比例


# ─── 调仓指令 ──────────────────────────────────────────────────

@dataclass
class RebalanceAction:
    """调仓指令"""
    ts_code: str
    name: str
    category: str
    action: str              # "buy" / "sell" / "hold"
    current_weight: float    # 当前权重
    target_weight: float     # 目标权重
    weight_delta: float      # 权重变动
    score: float             # 当前评分
    verdict: str             # 信号
    reason: str = ""        # 调仓原因

    @property
    def is_trade(self) -> bool:
        return self.action in ("buy", "sell")


# ─── 策略引擎 ──────────────────────────────────────────────────

class DividendCycleStrategy:
    """
    红利周期轮动策略

    输入：各标的评分数据（来自 DividendCycleEvaluator）
    输出：调仓指令列表（买入/卖出/持有）
    """

    def __init__(self, params: Optional[StrategyParams] = None):
        self.params = params or StrategyParams()

    def score_to_target_weight(self, score: float, category: str = "") -> float:
        """评分 → 目标仓位权重"""
        p = self.params
        if score >= p.score_strong_buy:
            return p.max_weight
        elif score >= p.score_buy:
            return p.mid_weight
        elif score >= p.score_watch:
            return p.min_weight
        else:
            return 0.0

    def _apply_category_constraints(
        self,
        raw_targets: Dict[str, float],
        scores: Dict[str, Dict],
    ) -> Dict[str, float]:
        """
        应用类别权重上限约束

        如果某类别总权重超限，按比例缩减该类别内所有标的权重
        """
        p = self.params
        adjusted = dict(raw_targets)

        # 按类别分组
        category_stocks: Dict[str, List[str]] = {}
        for ts_code, weight in adjusted.items():
            cat = scores.get(ts_code, {}).get("category", "")
            if cat:
                category_stocks.setdefault(cat, []).append(ts_code)

        # 逐类别检查上限
        for cat, stocks in category_stocks.items():
            cat_limit = p.category_limits.get(cat, 1.0)
            cat_total = sum(adjusted.get(s, 0) for s in stocks)

            if cat_total > cat_limit:
                # 按比例缩减
                scale = cat_limit / cat_total
                for s in stocks:
                    adjusted[s] *= scale

        return adjusted

    def _apply_hard_constraints(
        self,
        raw_targets: Dict[str, float],
    ) -> Dict[str, float]:
        """
        应用硬约束：
          - 单标的权重不超过 max_single_stock
          - 总权重不超过 (1 - cash_reserve)
          - 持仓数不超过 max_positions
        """
        p = self.params

        # 1. 单标的硬上限
        for ts_code in raw_targets:
            raw_targets[ts_code] = min(raw_targets[ts_code], p.max_single_stock)

        # 2. 持仓数限制：只保留权重最高的 N 只
        if sum(1 for w in raw_targets.values() if w > 0) > p.max_positions:
            sorted_codes = sorted(raw_targets.keys(), key=lambda c: raw_targets[c], reverse=True)
            for code in sorted_codes[p.max_positions:]:
                raw_targets[code] = 0.0

        # 3. 总权重约束
        total_target = sum(raw_targets.values())
        max_total = 1.0 - p.cash_reserve
        if total_target > max_total:
            scale = max_total / total_target
            for ts_code in raw_targets:
                raw_targets[ts_code] *= scale

        return raw_targets

    def generate_rebalance_actions(
        self,
        scores: Dict[str, Dict],
        current_weights: Dict[str, float],
    ) -> List[RebalanceAction]:
        """
        生成调仓指令

        Parameters
        ----------
        scores : dict
            各标的评分数据 {ts_code: {"name": ..., "total_score": ..., "category": ..., "verdict": ...}}
        current_weights : dict
            当前持仓权重 {ts_code: weight}

        Returns
        -------
        list[RebalanceAction]
        """
        p = self.params

        # Step 1: 计算原始目标权重
        raw_targets = {}
        for ts_code, score_data in scores.items():
            s = score_data.get("total_score", 0)
            raw_targets[ts_code] = self.score_to_target_weight(s, score_data.get("category", ""))

        # Step 2: 类别约束
        adjusted = self._apply_category_constraints(raw_targets, scores)

        # Step 3: 硬约束
        final_targets = self._apply_hard_constraints(adjusted)

        # Step 4: 生成调仓指令
        actions = []
        all_codes = set(list(final_targets.keys()) + list(current_weights.keys()))

        for ts_code in all_codes:
            name = scores.get(ts_code, {}).get("name", ts_code)
            category = scores.get(ts_code, {}).get("category", "")
            score_val = scores.get(ts_code, {}).get("total_score", 0)
            verdict = scores.get(ts_code, {}).get("verdict", "")
            div_yield = scores.get(ts_code, {}).get("div_yield", 0)

            curr_w = current_weights.get(ts_code, 0.0)
            target_w = final_targets.get(ts_code, 0.0)
            delta = target_w - curr_w

            # 构建详细理由
            score_desc = f"评分{score_val:.0f}({verdict})"
            yield_desc = f"股息率{div_yield:.2f}%" if div_yield > 0 else ""

            # 判断动作
            if abs(delta) < p.rebalance_threshold:
                action = "hold"
                reason = f"{score_desc}，权重偏离{abs(delta)*100:.1f}%<阈值{p.rebalance_threshold*100:.0f}%"
            elif delta > 0:
                action = "buy"
                if curr_w == 0:
                    reason = f"新买入，{score_desc}，{yield_desc}，目标权重{target_w*100:.1f}%"
                else:
                    reason = f"加仓，{score_desc}，{yield_desc}，权重{curr_w*100:.1f}%→{target_w*100:.1f}%"
            else:
                action = "sell"
                if target_w == 0:
                    reason = f"清仓，{score_desc}，低于观察阈值，当前权重{curr_w*100:.1f}%"
                else:
                    reason = f"减仓，{score_desc}，权重{curr_w*100:.1f}%→{target_w*100:.1f}%"

            # 零权重非持仓跳过
            if target_w == 0 and curr_w == 0:
                continue

            # 最小交易金额过滤
            if action in ("buy", "sell") and abs(delta) < p.min_trade_amount / 100_0000:
                action = "hold"
                reason += f"（交易金额<{p.min_trade_amount:.0f}元，跳过）"

            actions.append(RebalanceAction(
                ts_code=ts_code,
                name=name,
                category=category,
                action=action,
                current_weight=curr_w,
                target_weight=target_w,
                weight_delta=delta,
                score=score_val,
                verdict=verdict,
                reason=reason,
            ))

        # 按优先级排序：先卖后买（卖出释放资金）
        actions.sort(key=lambda a: (0 if a.action == "sell" else 1 if a.action == "buy" else 2))

        return actions

    def print_rebalance_plan(self, actions: List[RebalanceAction], total_value: float = 100_0000):
        """打印调仓计划"""
        p = self.params

        print("\n" + "=" * 70)
        print("  📋 红利周期轮动策略 — 调仓计划")
        print("=" * 70)

        # 需要交易的
        trades = [a for a in actions if a.is_trade]
        holds = [a for a in actions if not a.is_trade and a.target_weight > 0]

        if trades:
            print("\n  ── 需要调仓 ──")
            print(f"  {'标的':<12} {'动作':<6} {'当前%':>6} {'目标%':>6} {'变动%':>6} {'评分':>4} {'原因'}")
            print("  " + "-" * 65)
            for a in trades:
                action_str = "🟢买入" if a.action == "buy" else "🔴卖出"
                print(
                    f"  {a.name:<10} {action_str:<6} "
                    f"{a.current_weight*100:>5.1f} {a.target_weight*100:>5.1f} "
                    f"{a.weight_delta*100:>+5.1f} {a.score:>4.0f} {a.reason[:30]}"
                )

        if holds:
            print("\n  ── 继续持有 ──")
            print(f"  {'标的':<12} {'权重%':>6} {'评分':>4} {'信号'}")
            print("  " + "-" * 45)
            for a in holds:
                print(
                    f"  {a.name:<10} {a.target_weight*100:>5.1f} "
                    f"{a.score:>4.0f} {a.verdict}"
                )

        # 摘要
        total_target = sum(a.target_weight for a in actions)
        buys = [a for a in trades if a.action == "buy"]
        sells = [a for a in trades if a.action == "sell"]

        print(f"\n  ── 摘要 ──")
        print(f"  持仓数: {len([a for a in actions if a.target_weight > 0])} 只")
        print(f"  总仓位: {total_target*100:.1f}%  |  现金: {(1-total_target)*100:.1f}%")
        print(f"  买入: {len(buys)} 只  |  卖出: {len(sells)} 只")
        if total_value > 0:
            buy_amount = sum(a.weight_delta * total_value for a in buys)
            sell_amount = sum(-a.weight_delta * total_value for a in sells)
            print(f"  预计买入金额: {buy_amount:,.0f} 元  |  预计卖出金额: {sell_amount:,.0f} 元")

        # 类别分布
        cat_weights = {}
        for a in actions:
            if a.target_weight > 0:
                cat_weights[a.category] = cat_weights.get(a.category, 0) + a.target_weight

        print(f"\n  ── 类别分布 ──")
        for cat, w in sorted(cat_weights.items(), key=lambda x: -x[1]):
            limit = p.category_limits.get(cat, 1.0)
            bar = "█" * int(w / limit * 20) + "░" * (20 - int(w / limit * 20))
            print(f"  {cat:<8} {w*100:>5.1f}% / {limit*100:.0f}%  [{bar}]")

        print()
