"""
run_dca.py — 红利周期定投计划入口
==================================

基于每周评分数据自动生成定投计划，支持三种模式：

  模式1: 生成计划（默认）—— 基于最新周评，生成本轮定投建议
    python run_dca.py
    python run_dca.py --monthly 20000 --frequency weekly

  模式2: 历史模拟 —— 模拟从某日开始定投的收益表现
    python run_dca.py --simulate --start 2024-01-01

  模式3: 持仓跟踪 —— 记录实际定投执行，追踪持仓和收益率
    python run_dca.py --track --summary                 # 查看持仓
    python run_dca.py --track --buy 600900.SH:5000     # 记录买入

定投规则：
  - 信号驱动：大胆攒股投入×1.5，积极布局×1.2，观察等待×1.0
  - 阶梯优化：低吸区额外加码30%，减仓区减半
  - 自动筛选：回避(分数<35)标的自动跳过

输出目录：
  data/dca/
  ├── dca_plan_{WEEK}.json     # 当期定投计划
  ├── dca_portfolio.json       # 持仓跟踪数据
  └── dca_sim_{START}.csv       # 模拟结果
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.dca_planner import main

if __name__ == "__main__":
    main()
