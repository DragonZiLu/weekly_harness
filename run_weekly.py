"""
run_weekly.py — 红利周期投资周度评估入口
======================================
基于 Harness 设计框架，串联四个阶段：
  Phase 1: Planner    — 确定评估范围 + 获取国债利率
  Phase 2: Generator  — 拉取 tushare 数据 + 计算评分
  Phase 3: Validator  — 数据质量校验 + 置信度标注
  Phase 4: Reporter   — 生成周报 + 历史对比 + 信号告警

使用方法：
  # 每周手动运行
  cd /Users/luzilong/Work/weekly_harness
  python run_weekly.py

  # cron 自动化（每周五16:30收盘后）
  30 16 * * 5 cd /Users/luzilong/Work/weekly_harness && python run_weekly.py >> logs/weekly.log 2>&1

输出目录：
  data/
  ├── weekly_history.csv              # 历史时间序列（累积）
  ├── weekly_reports/
  │   └── 2026-W21/
  │       ├── report.md              # 本周周报
  │       ├── chart.png              # 本周图表
  │       ├── artifacts/
  │       │   ├── weekly_plan.json   # Planner 输出
  │       │   ├── raw_scores.json    # Generator 输出
  │       │   ├── validation_report.json  # Validator 输出
  │       │   └── signals.json       # Reporter 输出
  ├── dividend_report.md     # 最新周报（兼容旧路径）
  └── dividend_chart.png     # 最新图表（兼容旧路径）
"""

from __future__ import annotations

import sys
import json
import time
from datetime import datetime
from pathlib import Path

# ─── 路径设置 ─────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.planner import WeeklyPlanner
from weekly_harness.generator import WeeklyGenerator
from weekly_harness.validator import WeeklyValidator
from weekly_harness.reporter import WeeklyReporter


def run_weekly_evaluation():
    """
    完整的每周评估流程

    遵循 Harness 框架：每个 Phase 输出显式 artifact，
    问题可追溯，下游组件通过 artifact 而非直接调用传递数据。
    """
    start_time = time.time()

    now = datetime.now()
    iso_week = now.strftime("%G-W%V")

    print("\n" + "=" * 60)
    print("  🌊 红利周期投资 — 周度量化评估系统")
    print(f"  📅 {now.strftime('%Y-%m-%d %H:%M:%S')}  |  {iso_week}")
    print("  🏗️  Harness 框架: Planner → Generator → Validator → Reporter")
    print("=" * 60)

    # artifacts 目录：每周独立
    week_dir = _PROJECT_ROOT / "data" / "weekly_reports" / iso_week
    artifacts_dir = week_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────
    # Phase 1: Planner
    # ─────────────────────────────────────────────────────────
    try:
        planner = WeeklyPlanner()
        plan = planner.run(artifacts_dir=artifacts_dir)
    except Exception as e:
        print(f"\n❌ Planner 失败: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ─────────────────────────────────────────────────────────
    # Phase 2: Generator
    # ─────────────────────────────────────────────────────────
    try:
        generator = WeeklyGenerator()
        raw_scores = generator.run(plan=plan, artifacts_dir=artifacts_dir)
    except Exception as e:
        print(f"\n❌ Generator 失败: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ─────────────────────────────────────────────────────────
    # Phase 3: Validator
    # ─────────────────────────────────────────────────────────
    try:
        validator = WeeklyValidator()
        validation = validator.run(raw_scores=raw_scores, artifacts_dir=artifacts_dir)
    except Exception as e:
        print(f"\n❌ Validator 失败: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ─────────────────────────────────────────────────────────
    # Phase 4: Reporter
    # ─────────────────────────────────────────────────────────
    try:
        reporter = WeeklyReporter()
        signals = reporter.run(
            raw_scores=raw_scores,
            validation=validation,
            artifacts_dir=artifacts_dir,
        )
    except Exception as e:
        print(f"\n❌ Reporter 失败: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ─────────────────────────────────────────────────────────
    # 完成摘要
    # ─────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"  ✅ 周度评估完成！耗时 {elapsed:.1f} 秒")
    print("=" * 60)

    print(f"\n📁 本周报告目录:")
    print(f"   {week_dir}")

    print(f"\n📄 关键文件:")
    print(f"   报告:  data/weekly_reports/{iso_week}/report.md")
    print(f"   图表:  data/weekly_reports/{iso_week}/chart.png")
    print(f"   历史:  data/weekly_history.csv")

    # 打印买入信号
    strong_buy_names = [
        raw_scores["scores"][ts]["name"]
        for ts in raw_scores.get("summary", {}).get("strong_buy", [])
        if ts in raw_scores.get("scores", {})
    ]
    if strong_buy_names:
        print(f"\n🔥 当前「大胆攒股」标的: {', '.join(strong_buy_names)}")

    new_signals = signals.get("new_strong_buy", [])
    if new_signals:
        print(f"\n🚨 新进入「大胆攒股」: {', '.join([s['name'] for s in new_signals])}")

    downgrades = signals.get("signal_downgrades", [])
    if downgrades:
        print(f"\n⚠️  信号下调: {', '.join([s['name'] for s in downgrades])}")

    print()


if __name__ == "__main__":
    run_weekly_evaluation()
