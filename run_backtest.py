"""
run_backtest.py — 红利周期轮动策略回测入口
==========================================

使用方法：
  # 默认回测（100万，2024-01-01 至今，季度调仓）
  python run_backtest.py

  # 自定义参数
  python run_backtest.py --start 2023-01-01 --end 2026-05-01 --cash 500000

  # 周度调仓回测
  python run_backtest.py --freq weekly

  # 调整仓位参数
  python run_backtest.py --max-weight 0.12 --mid-weight 0.08

  # 仅生成当季调仓计划（不回测）
  python run_backtest.py --plan-only
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# 路径设置
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.portfolio import Portfolio
from weekly_harness.strategy import DividendCycleStrategy, StrategyParams
from weekly_harness.backtest import BacktestEngine


def run_backtest(args):
    """执行回测"""
    print("\n" + "=" * 70)
    print("  📊 红利周期轮动策略 — 回测系统")
    print(f"  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 策略参数
    params = StrategyParams(
        max_weight=args.max_weight / 100,
        mid_weight=args.mid_weight / 100,
        min_weight=args.min_weight / 100,
        rebalance_threshold=args.rebalance_threshold / 100,
    )

    # 回测引擎
    engine = BacktestEngine(
        strategy_params=params,
        initial_cash=args.cash,
        commission_rate=args.commission / 100,
        slippage=args.slippage / 100,
        rebalance_freq=args.freq,
    )

    start_time = time.time()
    results = engine.run(
        start_date=args.start,
        end_date=args.end,
        benchmark_code=args.benchmark,
        verbose=True,
    )
    elapsed = time.time() - start_time

    # 生成报告
    if "error" not in results:
        engine.generate_backtest_report(results)

    print(f"\n  ⏱️  回测耗时: {elapsed:.1f} 秒")
    print("=" * 70)


def run_plan_only(args):
    """仅生成当周调仓计划（不执行回测）"""
    print("\n" + "=" * 70)
    print("  📋 红利周期轮动策略 — 当季调仓计划")
    print(f"  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 运行评估获取评分
    from dividend_evaluator import DividendCycleEvaluator

    evaluator = DividendCycleEvaluator()
    raw_json = evaluator.evaluate_all_to_json()

    scores = raw_json.get("scores", {})
    if not scores:
        print("  ❌ 无法获取评分数据")
        return

    # 策略参数
    params = StrategyParams(
        max_weight=args.max_weight / 100,
        mid_weight=args.mid_weight / 100,
        min_weight=args.min_weight / 100,
    )
    strategy = DividendCycleStrategy(params)

    # 当前无持仓，所有权重为0
    current_weights = {}
    for ts_code in scores:
        current_weights[ts_code] = 0.0

    # 生成调仓指令
    actions = strategy.generate_rebalance_actions(scores, current_weights)
    strategy.print_rebalance_plan(actions, total_value=args.cash)


def main():
    parser = argparse.ArgumentParser(
        description="红利周期轮动策略 — 回测系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认回测（季度调仓）
  python run_backtest.py

  # 周度调仓回测
  python run_backtest.py --freq weekly

  # 自定义回测期间
  python run_backtest.py --start 2023-06-01 --end 2026-05-01

  # 仅生成当季调仓计划
  python run_backtest.py --plan-only

  # 调整仓位参数
  python run_backtest.py --max-weight 12 --mid-weight 8 --min-weight 3
        """,
    )

    # 基本参数
    parser.add_argument("--start", default="2024-01-01", help="回测开始日期 (默认: 2024-01-01)")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="回测结束日期 (默认: 今天)")
    parser.add_argument("--cash", type=float, default=1000000, help="初始资金 (默认: 100万)")
    parser.add_argument("--benchmark", default="000300.SH,515180.SH", help="基准指数，逗号分隔多基准 (默认: 沪深300,易方达红利ETF)")
    parser.add_argument("--freq", default="quarterly", choices=["quarterly", "weekly"],
                        help="调仓频率: quarterly=季度, weekly=周度 (默认: quarterly)")

    # 策略参数
    parser.add_argument("--max-weight", type=float, default=15, help="大胆攒股单标的权重%% (默认: 15)")
    parser.add_argument("--mid-weight", type=float, default=10, help="积极布局单标的权重%% (默认: 10)")
    parser.add_argument("--min-weight", type=float, default=5, help="观察等待单标的权重%% (默认: 5)")
    parser.add_argument("--rebalance-threshold", type=float, default=2, help="调仓阈值%% (默认: 2)")

    # 交易参数
    parser.add_argument("--commission", type=float, default=0.1, help="交易费率%% (默认: 0.1)")
    parser.add_argument("--slippage", type=float, default=0.1, help="滑点%% (默认: 0.1)")

    # 模式
    parser.add_argument("--plan-only", action="store_true", help="仅生成当周调仓计划（不回测）")

    args = parser.parse_args()

    if args.plan_only:
        run_plan_only(args)
    else:
        run_backtest(args)


if __name__ == "__main__":
    main()
