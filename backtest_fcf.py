"""
backtest_fcf.py — 中证全指自由现金流指数策略回测入口
======================================================

策略说明：
  基于中证全指自由现金流指数（932365）编制方案，每季度从 CSI 全指中
  筛选 100 只自由现金流率最高的标的，按 FCF 加权（上限 10%）。

指数编制方案要点：
  - 样本空间：中证全指（000985.SH）
  - 剔除：金融、房地产行业
  - 筛选条件：
    · 自由现金流 > 0
    · 企业价值 > 0
    · 连续 5 年经营现金流为正
    · 盈利质量前 80%
  - 选取：自由现金流率前 100 名
  - 加权：自由现金流加权，单样本 ≤10%
  - 调仓：每季度（3/6/9/12 月第二个周五的下一交易日）
  - 基日：2013-12-31，基点 1000

数据获取：
  首次运行需下载全市场财务数据（cashflow/balancesheet/income），
  之后回测直接读取本地缓存。

使用方法：
  # 首次运行：下载财务数据 + 回测
  python backtest_fcf.py --start 2020-01-01 --download

  # 后续运行：直接回测（使用缓存数据）
  python backtest_fcf.py --start 2020-01-01

  # 完整历史回测（2015年起）
  python backtest_fcf.py --start 2015-01-01

  # 选取前 50 只（更集中）
  python backtest_fcf.py --top-n 50 --start 2020-01-01
"""

from __future__ import annotations

import argparse
import sys
import time
import os
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.portfolio import Portfolio
from weekly_harness.strategy import StrategyParams
from weekly_harness.backtest import BacktestEngine


def run_fcf_backtest(args):
    """执行 FCF 指数策略回测"""
    print("\n" + "=" * 70)
    print("  📊 中证全指自由现金流指数 — 策略回测")
    print(f"  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    index_label = "沪深300" if "300" in args.index_code else "中证全指"
    print(f"\n  策略配置:")
    print(f"  - universe: fcf100（{index_label}自由现金流精选）")
    print(f"  - 样本空间: {args.index_code}")
    print(f"  - 选股数量: Top {args.top_n}")
    print(f"  - 调仓频率: 每季度（3/6/9/12月第二个周五）")
    print(f"  - 加权方式: 自由现金流加权（单标的上限10%）")

    # 检查财务数据是否已下载
    data_dir = _PROJECT_ROOT / "data" / "fcf_financials"
    has_data = data_dir.exists() and any(data_dir.glob("cashflow_*.csv"))
    if args.download and not has_data:
        print(f"\n  📥 首次运行，将下载全市场财务数据...")
        print(f"  ⚠️  下载约需 10-15 分钟（每年约 4 次 API 请求 × 15年）")
    elif not has_data:
        print(f"\n  ⚠️  未检测到财务数据缓存！")
        print(f"  💡 请先运行: python backtest_fcf.py --download")
        print(f"  💡 这将下载 2010-2026 年的全市场财务数据（约需 10-15 分钟）")
        return None

    params = StrategyParams(
        max_weight=args.max_weight / 100,
        mid_weight=0.05,           # FCF 模式不通过评分，但保留兼容
        min_weight=0.02,
        rebalance_threshold=args.rebalance_threshold / 100,
        max_positions=args.top_n,  # FCF 模式最多持有 top_n 只
    )

    engine = BacktestEngine(
        strategy_params=params,
        initial_cash=args.cash,
        commission_rate=args.commission / 100,
        slippage=args.slippage / 100,
        rebalance_freq="quarterly",  # FCF 模式自动使用专用调仓日
        universe="fcf100",
        fcf_top_n=args.top_n,
        fcf_download=args.download,
        fcf_index_code=args.index_code,
    )

    start_time = time.time()
    results = engine.run(
        start_date=args.start,
        end_date=args.end,
        benchmark_code=args.benchmark,
        verbose=True,
    )
    elapsed = time.time() - start_time

    if results and "error" not in results:
        # 构建输出路径
        suffix = "300" if "300" in args.index_code else "ALL"
        tag = f"FCF{suffix}_TOP{args.top_n}"
        out_dir = _PROJECT_ROOT / "data" / "backtest" / tag
        engine.generate_backtest_report(results, output_dir=out_dir)
        print(f"\n  📄 报告已保存至: {out_dir}")

    if results:
        print(f"\n  ⏱️  回测耗时: {elapsed:.1f} 秒")
    print("=" * 70)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="中证全指自由现金流指数策略回测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 沪深300自由现金流回测（剔除金融地产，Top 50）
  python backtest_fcf.py --index-code 000300.SH --top-n 50 --start 2016-01-01

  # 全指自由现金流回测
  python backtest_fcf.py --start 2020-01-01

  # 首次运行（下载数据 + 回测）
  python backtest_fcf.py --start 2020-01-01 --download
        """
    )

    parser.add_argument("--start", default="2020-01-01", help="回测开始日期 (默认: 2020-01-01)")
    parser.add_argument("--end", default="2026-06-01", help="回测结束日期 (默认: 2026-06-01)")
    parser.add_argument("--cash", type=float, default=1_000_000, help="初始资金 (默认: 100万)")

    # 数据下载
    parser.add_argument(
        "--download", action="store_true", default=False,
        help="首次运行需下载全市场财务数据（约需 10-15 分钟）"
    )

    # 选股参数
    parser.add_argument(
        "--index-code", default="000985.SH",
        help="样本空间指数代码 (默认: 000985.SH 中证全指; CSI 300: 000300.SH)"
    )
    parser.add_argument(
        "--top-n", type=int, default=100,
        help="选取前 N 只标的 (默认: 100，对齐指数编制)"
    )

    # 仓位参数
    parser.add_argument("--max-weight", type=float, default=10.0,
                        help="单股最大权重%% (默认: 10，对齐指数上限)")
    parser.add_argument("--rebalance-threshold", type=float, default=2.0,
                        help="调仓触发阈值%% (默认: 2)")

    # 交易成本
    parser.add_argument("--commission", type=float, default=0.1,
                        help="佣金率%% (默认: 0.1)")
    parser.add_argument("--slippage", type=float, default=0.1,
                        help="滑点%% (默认: 0.1)")

    # 基准
    parser.add_argument("--benchmark", default="000300.SH",
                        help="基准指数 (默认: 沪深300)")

    args = parser.parse_args()
    run_fcf_backtest(args)


if __name__ == "__main__":
    main()
