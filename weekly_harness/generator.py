"""
Generator — 数据获取 + 评分计算器
===================================
对应 Harness 框架中的 Generator 角色：
  - 接收 Planner 输出的 weekly_plan
  - 调用 dividend_evaluator 核心逻辑（TushareDataFetcher + DividendCycleEvaluator）
  - 输出 raw_scores.json（Generator → Validator 的 artifact）

职责边界：只管"产出原始评分数据"，不做数据质量判断（那是 Validator 的事）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# ─── 路径设置 ─────────────────────────────────────────────────
_HARNESS_DIR = Path(__file__).parent
_PROJECT_ROOT = _HARNESS_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dividend_evaluator import DividendCycleEvaluator  # noqa: E402


class WeeklyGenerator:
    """
    每周评分生成器

    封装 DividendCycleEvaluator，将 evaluate_all_to_json() 的输出
    写入 raw_scores.json artifact。
    """

    def __init__(self):
        self.evaluator = DividendCycleEvaluator()

    def run(
        self,
        plan: Dict,
        artifacts_dir: Optional[Path] = None,
    ) -> Dict:
        """
        执行数据获取 + 评分

        Parameters
        ----------
        plan : dict
            来自 Planner 的 weekly_plan artifact
        artifacts_dir : Path, optional
            artifact 输出目录

        Returns
        -------
        dict : raw_scores artifact
        """
        print("\n" + "─" * 50)
        print("  [Phase 2] Generator — 数据获取 & 评分计算")
        print("─" * 50)

        bond_yield = plan.get("bond_yield_10y", 1.65)
        week = plan.get("week", datetime.now().strftime("%G-W%V"))

        # 初始化评估引擎（每次新实例，避免上周数据污染）
        self.evaluator = DividendCycleEvaluator()
        # 覆盖国债收益率（使用 Planner 传入的实时值）
        self.evaluator.BOND_YIELD = bond_yield

        # 执行评估（内部打印进度）
        raw_json = self.evaluator.evaluate_all_to_json(bond_yield_override=bond_yield)

        # 打印摘要
        summary = raw_json.get("summary", {})
        print(f"\n  📊 评分摘要：")
        print(f"     🔥 大胆攒股: {len(summary.get('strong_buy', []))} 只")
        print(f"     ✅ 积极布局: {len(summary.get('buy', []))} 只")
        print(f"     👀 观察等待: {len(summary.get('watch', []))} 只")
        print(f"     ⏸️  暂缓/回避: "
              f"{len(summary.get('hold', [])) + len(summary.get('avoid', []))} 只")

        # 保存 artifact
        if artifacts_dir:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            scores_path = artifacts_dir / "raw_scores.json"
            with open(scores_path, "w", encoding="utf-8") as f:
                json.dump(raw_json, f, ensure_ascii=False, indent=2)
            print(f"  💾 raw_scores.json → {scores_path}")

        print(f"  ✅ Generator 完成\n")
        return raw_json
