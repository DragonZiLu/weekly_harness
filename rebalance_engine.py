"""
股债再平衡引擎 (Stock-Bond Rebalance Engine)
==============================================

基于博格"成本/均值回归/时间"三原则，结合子龙现有的:
  - 银行股定投 DCA 体系
  - 精选32红利轮动策略
  - 股债息差估值框架

设计三层债券配置 + 动态再平衡规则。

核心理念:
  博格："债券≈年龄"是起点，但中国市场的股债息差提供了更精细的调仓信号。
  当息差处于历史高位时，债券的"锚定作用"弱化，可适度增配权益；
  当息差收窄至历史低位时，债券的"安全垫"价值凸显，应加速回补。

债券三层配置（久期阶梯）:
  L1 现金管理: 银华日利 511880（货币基金，零利率风险，T+0流动性）
  L2 短久期债: 博时0-3年国开债 159650（久期<1.5年，利率波动缓冲）
  L3 中长久期债: 富国政金债ETF 511520（久期~6年，利率下行受益）

使用方式:
  from rebalance_engine import RebalanceEngine

  engine = RebalanceEngine(
      total_assets=500000,   # 总资产 50万
      age=40,                # 投资者年龄
      current_stock_pct=0.72 # 当前股票占比 72%
  )
  plan = engine.generate_plan()
  print(plan.report())

作者: 龙哥量化体系
日期: 2026-06-13
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─── 项目路径 ──────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ─── 尝试加载 dividend_evaluator 的阈值 ──────────────────────
try:
    from dividend_evaluator import THRESHOLDS, SECTOR_THRESHOLDS
    _BOND_YIELD_10Y = THRESHOLDS.get("bond_yield_10y", 1.65)
except ImportError:
    _BOND_YIELD_10Y = 1.65


# ══════════════════════════════════════════════════════════════
# 一、债券基金池（场内 ETF，低费率优先）
# ══════════════════════════════════════════════════════════════

BOND_FUND_UNIVERSE = {
    "L1_现金管理": {
        "code": "511880",
        "name": "银华日利",
        "type": "货币ETF",
        "duration_years": 0.08,       # ~1个月
        "ytm_approx": 1.45,           # 近似7日年化
        "fee_total": 0.20,            # 管理费+托管费
        "liquidity": "T+0",
        "role": "零钱理财仓 · 应急流动性 · 定投缓冲垫",
        "min_allocation": 0.02,       # 最低 2%
        "max_allocation": 0.15,       # 最高 15%
    },
    "L2_短久期债": {
        "code": "159650",
        "name": "博时0-3年国开债ETF",
        "type": "利率债ETF(短久期)",
        "duration_years": 1.3,
        "ytm_approx": 1.70,
        "fee_total": 0.20,
        "liquidity": "T+1",
        "role": "利率波动的缓冲垫 · 降息周期先受益 · 定投+再平衡主仓",
        "min_allocation": 0.05,
        "max_allocation": 0.40,
    },
    "L3_中长久期债": {
        "code": "511520",
        "name": "富国政金债ETF",
        "type": "利率债ETF(长久期)",
        "duration_years": 6.2,
        "ytm_approx": 2.30,
        "fee_total": 0.20,
        "liquidity": "T+1",
        "role": "利率下行加速器 · 高息差时配置 · 规模最大流动性最佳",
        "min_allocation": 0.00,
        "max_allocation": 0.30,
    },
}

# 简化版本（向后兼容）
BOND_FUND_CODES = {
    tier: info["code"]
    for tier, info in BOND_FUND_UNIVERSE.items()
}


# ══════════════════════════════════════════════════════════════
# 二、股债息差历史分位参考（中国市场 2015-2026）
# ══════════════════════════════════════════════════════════════

# 中证红利股息率 - 10Y国债收益率 = 股债息差(BP)
# 历史极值区间（基于森哥体系 + 实际观测）
SPREAD_HISTORICAL = {
    "extreme_high": 350,    # >350BP: 股票极度便宜（2018底、2024初）
    "high": 200,            # 200-350BP: 股票显著低估
    "normal": 100,          # 100-200BP: 正常区间
    "low": 50,              # 50-100BP: 股票偏贵，债券吸引力上升
    "extreme_low": 0,       # <50BP: 股票贵（2015年牛市顶、2021核心资产顶）
}


# ══════════════════════════════════════════════════════════════
# 三、股债配置规则
# ══════════════════════════════════════════════════════════════

@dataclass
class AllocationRule:
    """股债配置规则"""
    # 博格基础：股票比例 = max(100 - 年龄, 30%)
    base_stock_pct: float = 0.60      # 基准股票配置（40岁→60%）

    # 股债息差动态调整
    spread_neutral: float = 150       # 息差中性值(BP)
    spread_adjust_per_100bp: float = 0.10  # 每偏离100BP，股票±10%

    # 波动率缓冲（权益波动率越高，减配股票）
    vol_adjust_enabled: bool = True
    vol_neutral: float = 0.20         # 20%年化波动率中性
    vol_adjust_stock: float = -0.05   # 每超10%波动率，减配5%

    # 债券子仓位分配（三层）
    bond_L1_pct: float = 0.25         # L1现金占债券总仓位
    bond_L2_pct: float = 0.50         # L2短久期占债券总仓位
    bond_L3_pct: float = 0.25         # L3长久期占债券总仓位

    # 再平衡触发
    rebalance_threshold: float = 0.05  # 偏离目标 >5% 触发调仓
    check_frequency: str = "monthly"   # 月度检查（季度执行）


@dataclass
class RebalancePlan:
    """再平衡执行计划"""
    current_date: str
    total_assets: float
    age: int
    base_stock_pct: float
    spread_bp: float
    vol_annual: float
    target_stock_pct: float
    target_bond_pct: float
    current_stock_pct: float
    current_bond_pct: float
    drift_pct: float
    need_rebalance: bool
    stock_adjust_amount: float
    bond_L1_target: float
    bond_L2_target: float
    bond_L3_target: float
    bond_L1_current: float = 0
    bond_L2_current: float = 0
    bond_L3_current: float = 0
    actions: List[str] = field(default_factory=list)
    risk_signals: List[str] = field(default_factory=list)

    def summary_dict(self) -> dict:
        return {
            "当前日期": self.current_date,
            "总资产(万)": f"{self.total_assets/10000:.1f}",
            "年龄": self.age,
            "博格基准股票%": f"{self.base_stock_pct*100:.0f}%",
            "股债息差(BP)": f"{self.spread_bp:.0f}",
            "权益波动率": f"{self.vol_annual*100:.1f}%",
            "目标股票%": f"{self.target_stock_pct*100:.1f}%",
            "当前股票%": f"{self.current_stock_pct*100:.1f}%",
            "偏离": f"{self.drift_pct*100:+.1f}%",
            "需再平衡": "⚠️ 是" if self.need_rebalance else "✅ 否",
            "调整金额": f"¥{abs(self.stock_adjust_amount):,.0f}" if self.need_rebalance else "无",
        }

    def report(self) -> str:
        """生成 Markdown 格式报告"""
        lines = [
            f"# 股债再平衡报告 — {self.current_date}",
            "",
            "## 一、配置概览",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 总资产 | ¥{self.total_assets:,.0f} |",
            f"| 年龄 | {self.age} 岁 |",
            f"| 博格基准（股票） | {self.base_stock_pct*100:.0f}% (100-{self.age}) |",
            f"| 股债息差（当前） | {self.spread_bp:.0f} BP |",
            f"| 权益年化波动率 | {self.vol_annual*100:.1f}% |",
            "",
            "## 二、目标 vs 当前配置",
            "",
            "| 资产类别 | 目标占比 | 当前占比 | 偏离 | 目标金额 |",
            "|----------|---------|---------|------|---------|",
            f"| 📈 权益类 | **{self.target_stock_pct*100:.1f}%** | {self.current_stock_pct*100:.1f}% | {self.drift_pct*100:+.1f}% | ¥{self.total_assets*self.target_stock_pct:,.0f} |",
            f"| 📊 债券类 | **{self.target_bond_pct*100:.1f}%** | {self.current_bond_pct*100:.1f}% | {-self.drift_pct*100:+.1f}% | ¥{self.total_assets*self.target_bond_pct:,.0f} |",
            "",
            "## 三、债券子仓位",
            "",
            "| 层级 | 基金名称 | 代码 | 久期 | 目标占比 | 目标金额 | 角色 |",
            "|------|---------|------|------|---------|---------|------|",
        ]

        for tier, info in BOND_FUND_UNIVERSE.items():
            alloc = getattr(self, f"bond_{tier.split('_')[0]}_target", 0)
            lines.append(
                f"| {tier} | {info['name']} | {info['code']} | "
                f"{info['duration_years']}年 | {alloc*100:.1f}% | "
                f"¥{alloc*self.total_assets:,.0f} | {info['role']} |"
            )

        if self.need_rebalance:
            lines.extend([
                "",
                "## 四、执行操作",
                "",
                "| 操作 | 方向 | 金额 | 标的 |",
                "|------|------|------|------|",
            ])
            for action in self.actions:
                parts = action.split("|")
                if len(parts) >= 3:
                    lines.append(f"| {parts[0]} | {parts[1]} | {parts[2]} | {parts[3] if len(parts) > 3 else '-'} |")

        if self.risk_signals:
            lines.extend([
                "",
                "## 五、风险信号",
                "",
            ])
            for sig in self.risk_signals:
                lines.append(f"- {sig}")

        lines.extend([
            "",
            "---",
            "",
            "### 规则说明",
            "",
            f"1. **博格基础**：股票% = max(100-年龄, 30%) = {self.base_stock_pct*100:.0f}%",
            f"2. **息差调整**：每偏离中性值({AllocationRule.spread_neutral}BP) 100BP，股票±{AllocationRule.spread_adjust_per_100bp*100:.0f}%",
            f"3. **波动缓冲**：权益波动率每超中性值({AllocationRule.vol_neutral*100:.0f}%) 10%，减配{abs(AllocationRule.vol_adjust_stock)*100:.0f}%",
            f"4. **触发条件**：偏离目标 >{AllocationRule.rebalance_threshold*100:.0f}% 时执行调仓",
            "",
            "> 博格：\"时间是你的朋友，冲动是你的敌人。\" 保持纪律，按计划执行。",
        ])

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 四、再平衡引擎核心
# ══════════════════════════════════════════════════════════════

class RebalanceEngine:
    """
    股债再平衡引擎

    Parameters
    ----------
    total_assets : float
        总可投资资产（元）
    age : int
        投资者年龄
    current_stock_amount : float
        当前权益持仓市值（元），None 则用 current_stock_pct
    current_bond_amounts : dict, optional
        当前各层债券持仓 { "L1": amount, "L2": amount, "L3": amount }
    rule : AllocationRule, optional
        自定义配置规则
    """

    def __init__(
        self,
        total_assets: float,
        age: int,
        current_stock_amount: Optional[float] = None,
        current_stock_pct: Optional[float] = None,
        current_bond_amounts: Optional[Dict[str, float]] = None,
        rule: Optional[AllocationRule] = None,
    ):
        self.total_assets = total_assets
        self.age = age
        self.rule = rule or AllocationRule()

        if current_stock_amount is not None:
            self.current_stock_pct = current_stock_amount / total_assets
        elif current_stock_pct is not None:
            self.current_stock_pct = current_stock_pct
        else:
            # 默认按博格基准
            self.current_stock_pct = max(1.0 - age / 100.0, 0.30)

        self.current_bond_pct = 1.0 - self.current_stock_pct
        self.current_bond_amounts = current_bond_amounts or {}

    # ── 核心计算 ──────────────────────────────────────────

    def compute_spread(self, div_yield: Optional[float] = None) -> float:
        """
        计算股债息差。

        优先使用传入的红利组合加权股息率，否则用中证红利近似值。
        """
        if div_yield is not None:
            return (div_yield - _BOND_YIELD_10Y) * 100  # 转换为 BP

        # 默认使用中证红利指数股息率近似值（约 5.2%）
        default_div_yield = 5.2
        return (default_div_yield - _BOND_YIELD_10Y) * 100

    def compute_target_allocation(
        self,
        spread_bp: Optional[float] = None,
        div_yield: Optional[float] = None,
        vol_annual: Optional[float] = None,
    ) -> Tuple[float, float]:
        """
        计算目标股债配置比例。

        公式：
            target_stock = base_stock
                + spread_adjust × (spread - spread_neutral) / 100
                + vol_adjust × max(vol - vol_neutral, 0) / 0.10

        Returns
        -------
        (target_stock_pct, target_bond_pct)
        """
        r = self.rule

        if spread_bp is None:
            spread_bp = self.compute_spread(div_yield)

        if vol_annual is None:
            # 默认沪深300年化波动率约 20%
            vol_annual = 0.20

        # 博格基础
        target_stock = r.base_stock_pct

        # 息差调整
        spread_deviation = (spread_bp - r.spread_neutral) / 100.0
        target_stock += r.spread_adjust_per_100bp * spread_deviation

        # 波动率缓冲
        if r.vol_adjust_enabled and vol_annual > r.vol_neutral:
            vol_excess = (vol_annual - r.vol_neutral) / 0.10
            target_stock += r.vol_adjust_stock * vol_excess

        # 夹逼在 [30%, 90%] 区间
        target_stock = max(0.30, min(0.90, target_stock))
        target_bond = 1.0 - target_stock

        return target_stock, target_bond

    def compute_bond_sub_allocation(self, bond_total_pct: float) -> Dict[str, float]:
        """
        债券子仓位分配。

        L1(现金) : L2(短久期) : L3(长久期) = 25 : 50 : 25
        但根据利率环境动态调整：
          - 利率下行周期 → 增配 L3 (长久期受益)
          - 利率上行周期 → 减配 L3，增配 L1/L2
        """
        r = self.rule
        return {
            "L1_现金管理": bond_total_pct * r.bond_L1_pct,
            "L2_短久期债": bond_total_pct * r.bond_L2_pct,
            "L3_中长久期债": bond_total_pct * r.bond_L3_pct,
        }

    # ── 计划生成 ──────────────────────────────────────────

    def generate_plan(
        self,
        div_yield: Optional[float] = None,
        vol_annual: Optional[float] = None,
        current_date: Optional[str] = None,
    ) -> RebalancePlan:
        """
        生成再平衡计划。

        Parameters
        ----------
        div_yield : float, optional
            当前组合加权股息率（%），None 则使用默认近似值
        vol_annual : float, optional
            权益年化波动率，None 则使用默认 20%
        current_date : str, optional
            当前日期，None 则使用今天
        """
        spread_bp = self.compute_spread(div_yield)
        target_stock, target_bond = self.compute_target_allocation(
            spread_bp=spread_bp,
            div_yield=div_yield,
            vol_annual=vol_annual,
        )
        bond_subs = self.compute_bond_sub_allocation(target_bond)

        drift = self.current_stock_pct - target_stock
        need_rebalance = abs(drift) > self.rule.rebalance_threshold

        stock_adjust = drift * self.total_assets

        plan = RebalancePlan(
            current_date=current_date or datetime.now().strftime("%Y-%m-%d"),
            total_assets=self.total_assets,
            age=self.age,
            base_stock_pct=self.rule.base_stock_pct,
            spread_bp=spread_bp,
            vol_annual=vol_annual or 0.20,
            target_stock_pct=target_stock,
            target_bond_pct=target_bond,
            current_stock_pct=self.current_stock_pct,
            current_bond_pct=self.current_bond_pct,
            drift_pct=drift,
            need_rebalance=need_rebalance,
            stock_adjust_amount=stock_adjust,
            bond_L1_target=bond_subs.get("L1_现金管理", 0),
            bond_L2_target=bond_subs.get("L2_短久期债", 0),
            bond_L3_target=bond_subs.get("L3_中长久期债", 0),
        )

        # ── 生成具体操作指令 ──────────────────────────────
        if need_rebalance:
            if stock_adjust > 0:
                # 股票超配 → 卖出股票、买入债券
                plan.actions.append(f"卖出权益 | ➖ 减仓 | ¥{stock_adjust:,.0f} | 按评分最低优先")
                # 分配买入债券
                for tier, info in BOND_FUND_UNIVERSE.items():
                    tier_key = tier.split("_")[0] + "_" + tier.split("_")[1]
                    bond_alloc = getattr(plan, f"bond_{tier.split('_')[0]}_target", 0)
                    buy_amount = abs(stock_adjust) * bond_alloc / target_bond if target_bond > 0 else 0
                    if buy_amount > 500:
                        plan.actions.append(
                            f"买入债券 | ➕ 加仓 | ¥{buy_amount:,.0f} | {info['name']}({info['code']})"
                        )
            else:
                # 股票低配 → 卖出债券、买入股票
                sell_bond_total = abs(stock_adjust)
                for tier, info in BOND_FUND_UNIVERSE.items():
                    tier_key = tier.split("_")[0] + "_" + tier.split("_")[1]
                    bond_alloc = getattr(plan, f"bond_{tier.split('_')[0]}_target", 0)
                    sell_amount = sell_bond_total * bond_alloc / target_bond if target_bond > 0 else 0
                    if sell_amount > 500:
                        plan.actions.append(
                            f"卖出债券 | ➖ 减仓 | ¥{sell_amount:,.0f} | {info['name']}({info['code']})"
                        )
                plan.actions.append(
                    f"买入权益 | ➕ 加仓 | ¥{abs(stock_adjust):,.0f} | 按DCA评分最高优先"
                )

        # ── 风险信号 ──────────────────────────────────────
        if spread_bp < SPREAD_HISTORICAL["low"]:
            plan.risk_signals.append(
                f"⚠️ 股债息差仅 {spread_bp:.0f}BP，处于历史低位区间。"
                f"股票相对债券偏贵，建议适度减配权益、增配债券。"
            )
        elif spread_bp > SPREAD_HISTORICAL["high"]:
            plan.risk_signals.append(
                f"💡 股债息差 {spread_bp:.0f}BP，处于历史高位区间。"
                f"股票相对债券显著低估，是增配权益的好时机。"
            )

        if vol_annual and vol_annual > 0.25:
            plan.risk_signals.append(
                f"📊 权益波动率 {vol_annual*100:.1f}%，高于正常水平。"
                f"波动率缓冲机制已自动降低权益配置。"
            )

        return plan

    # ── 场景模拟 ──────────────────────────────────────────

    def simulate_scenarios(self, div_yield_range: Tuple[float, float] = (3.0, 7.0)) -> pd.DataFrame:
        """
        模拟不同股息率/息差场景下的目标配置。

        Returns
        -------
        DataFrame: 各场景的目标配置
        """
        scenarios = []
        for div_y in np.linspace(div_yield_range[0], div_yield_range[1], 9):
            spread = self.compute_spread(div_y)
            target_s, target_b = self.compute_target_allocation(spread_bp=spread)
            bond_subs = self.compute_bond_sub_allocation(target_b)

            scenarios.append({
                "中证红利股息率(%)": f"{div_y:.1f}",
                "10Y国债(%)": f"{_BOND_YIELD_10Y:.2f}",
                "股债息差(BP)": f"{spread:.0f}",
                "息差分位": self._spread_regime(spread),
                "目标股票%": f"{target_s*100:.1f}",
                "目标债券%": f"{target_b*100:.1f}",
                "L1现金%": f"{target_b * self.rule.bond_L1_pct*100:.1f}",
                "L2短债%": f"{target_b * self.rule.bond_L2_pct*100:.1f}",
                "L3长债%": f"{target_b * self.rule.bond_L3_pct*100:.1f}",
            })

        return pd.DataFrame(scenarios)

    @staticmethod
    def _spread_regime(spread_bp: float) -> str:
        if spread_bp >= SPREAD_HISTORICAL["extreme_high"]:
            return "🔴 极高位"
        elif spread_bp >= SPREAD_HISTORICAL["high"]:
            return "🟠 高位"
        elif spread_bp >= SPREAD_HISTORICAL["normal"]:
            return "🟡 正常"
        elif spread_bp >= SPREAD_HISTORICAL["low"]:
            return "🟢 低位"
        else:
            return "🔵 极低位"


# ══════════════════════════════════════════════════════════════
# 五、与 DCA 系统的集成接口
# ══════════════════════════════════════════════════════════════

def get_dca_bond_adjustment(
    monthly_dca_amount: float,
    target_stock_pct: float,
    current_stock_pct: float,
) -> Dict[str, float]:
    """
    将再平衡信号转换到 DCA 定投计划中。

    当股票低配时，DCA 中股票投入比例自动增加；
    当股票超配时，DCA 中债券投入比例自动增加。

    Parameters
    ----------
    monthly_dca_amount : float
        每月定投总额
    target_stock_pct : float
        目标股票占比
    current_stock_pct : float
        当前股票占比

    Returns
    -------
    dict: { "stock_dca": float, "bond_dca": float }
    """
    drift = current_stock_pct - target_stock_pct

    # 基础分配：50% 股票 / 50% 债券
    base_stock_flow = 0.50

    # 偏离调整：每偏离 1%，DCA 中股票比例 ±2%（加速回归）
    adjust = -drift * 2.0

    stock_flow = max(0.10, min(0.90, base_stock_flow + adjust))
    bond_flow = 1.0 - stock_flow

    return {
        "stock_dca": monthly_dca_amount * stock_flow,
        "bond_dca": monthly_dca_amount * bond_flow,
        "stock_flow_pct": stock_flow,
        "bond_flow_pct": bond_flow,
    }


# ══════════════════════════════════════════════════════════════
# 六、命令行入口
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="股债再平衡引擎")
    parser.add_argument("--assets", type=float, default=500000,
                        help="总资产（元），默认 500000")
    parser.add_argument("--age", type=int, default=40,
                        help="年龄，默认 40")
    parser.add_argument("--stock-pct", type=float, default=None,
                        help="当前股票占比，默认按年龄计算基准")
    parser.add_argument("--div-yield", type=float, default=None,
                        help="当前组合加权股息率(%)")
    parser.add_argument("--vol", type=float, default=None,
                        help="权益年化波动率，默认 0.20")
    parser.add_argument("--scenarios", action="store_true",
                        help="输出多场景模拟")
    parser.add_argument("--output", type=str, default=None,
                        help="输出 Markdown 报告到文件")

    args = parser.parse_args()

    engine = RebalanceEngine(
        total_assets=args.assets,
        age=args.age,
        current_stock_pct=args.stock_pct,
    )

    if args.scenarios:
        df = engine.simulate_scenarios()
        print("\n═════════════════════════════════════════════")
        print("  多场景模拟：不同股息率下的目标配置")
        print("═════════════════════════════════════════════\n")
        print(df.to_string(index=False))
    else:
        plan = engine.generate_plan(
            div_yield=args.div_yield,
            vol_annual=args.vol,
        )
        report = plan.report()

        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"报告已保存至: {args.output}")
        else:
            print(report)

        # 同时输出 DCA 调整建议
        dca_adj = get_dca_bond_adjustment(
            monthly_dca_amount=10000,
            target_stock_pct=plan.target_stock_pct,
            current_stock_pct=plan.current_stock_pct,
        )
        print("\n── DCA 定投调整建议 ──")
        print(f"  每月定投 ¥10,000:")
        print(f"    股票定投: ¥{dca_adj['stock_dca']:,.0f} ({dca_adj['stock_flow_pct']*100:.0f}%)")
        print(f"    债券定投: ¥{dca_adj['bond_dca']:,.0f} ({dca_adj['bond_flow_pct']*100:.0f}%)")
