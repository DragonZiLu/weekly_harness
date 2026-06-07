"""
backtest_etf_top.py — 红利ETF持仓Top策略回测入口
=================================================

策略说明：
  以主流红利ETF（510880/515180/512890/561580）的历史重仓股
  作为动态策略池，每期调仓日用该时点已公告的最新持仓数据
  严格无未来信息地构建候选池，再用红利周期评分体系打分调仓。

数据可靠性保证：
  - 基于 fund_portfolio.ann_date（实际公告日）过滤
  - 回测时只用当天已公告的持仓，无未来信息泄露
  - 例如 2016-01-10 只能看到 2015-09-30 季报，
    2016-02-01 才能看到 2015-12-31 年报

ETF列表：
  510880.SH  华夏红利ETF         中证红利指数  2007年起（历史最深）
  515180.SH  易方达中证红利ETF   中证红利指数  2019年起
  512890.SH  红利低波ETF         红利低波动    2019年起
  561580.SH  央企红利ETF华泰柏瑞 中证央企红利  2023年起

使用方法：
  # 完整历史回测（2015~2026）
  python backtest_etf_top.py --start 2015-01-01 --end 2026-05-01

  # 只用2019年后（3只ETF）
  python backtest_etf_top.py --start 2019-01-01

  # 只用510880（历史最深，2007年起）
  python backtest_etf_top.py --etf-codes 510880.SH --start 2015-01-01

  # 要求至少2只ETF都持有（共识标的，更保守）
  python backtest_etf_top.py --min-etf 2 --start 2019-01-01

  # 对比模式：同时跑精选32 vs ETF Top
  python backtest_etf_top.py --compare --start 2015-01-01
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.portfolio import Portfolio
from weekly_harness.strategy import DividendCycleStrategy, StrategyParams
from weekly_harness.backtest import BacktestEngine


def run_etf_top_backtest(args):
    """执行ETF持仓Top策略回测"""
    print("\n" + "=" * 70)
    print("  📊 红利ETF持仓Top策略 — 回测系统")
    print(f"  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    etf_codes = args.etf_codes.split(",") if args.etf_codes else None

    print(f"\n  策略配置:")
    print(f"  - universe: etf_top（红利ETF持仓动态策略池）")
    etf_list = etf_codes if etf_codes else ["510880.SH(华夏红利ETF)", "515180.SH(易方达中证红利ETF)",
                                             "512890.SH(红利低波ETF)", "561580.SH(央企红利ETF)"]
    print(f"  - ETF来源: {etf_list}")
    print(f"  - 每ETF取前{args.top_n}大持仓，至少{args.min_etf}只ETF同时持有")
    print(f"  - 数据保证: 严格用 ann_date 过滤，无未来信息泄露")

    params = StrategyParams(
        max_weight=args.max_weight / 100,
        mid_weight=args.mid_weight / 100,
        min_weight=args.min_weight / 100,
        rebalance_threshold=args.rebalance_threshold / 100,
    )

    engine = BacktestEngine(
        strategy_params=params,
        initial_cash=args.cash,
        commission_rate=args.commission / 100,
        slippage=args.slippage / 100,
        rebalance_freq=args.freq,
        universe="etf_top",
        etf_top_n=args.top_n,
        etf_min_count=args.min_etf,
        etf_codes=etf_codes,
    )

    start_time = time.time()
    results = engine.run(
        start_date=args.start,
        end_date=args.end,
        benchmark_code=args.benchmark,
        verbose=True,
    )
    elapsed = time.time() - start_time

    if "error" not in results:
        # 构建输出路径标签
        etf_tag = "全部ETF" if not etf_codes else "+".join([c.replace(".SH","").replace(".SZ","") for c in etf_codes])
        min_etf_tag = f"_共识{args.min_etf}" if args.min_etf > 1 else ""
        folder = f"ETF持仓Top_{etf_tag}{min_etf_tag}"
        out_dir = Path(__file__).parent / "data" / "backtest" / folder
        engine.generate_backtest_report(results, output_dir=out_dir)
        print(f"\n  📄 报告已保存至: {out_dir}")

    print(f"\n  ⏱️  回测耗时: {elapsed:.1f} 秒")
    print("=" * 70)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="红利ETF持仓Top策略回测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 2015年起完整回测（只有510880数据）
  python backtest_etf_top.py --start 2015-01-01

  # 2019年起（3只ETF均可用）
  python backtest_etf_top.py --start 2019-01-01

  # 要求至少2只ETF共同持有（更高共识度）
  python backtest_etf_top.py --start 2019-01-01 --min-etf 2

  # 只用510880历史最长数据
  python backtest_etf_top.py --etf-codes 510880.SH --start 2015-01-01

  # 取更大的持仓池（每只ETF取前50大）
  python backtest_etf_top.py --top-n 50 --start 2019-01-01
        """
    )

    parser.add_argument("--start", default="2015-01-01", help="回测开始日期 (默认: 2015-01-01)")
    parser.add_argument("--end", default="2026-05-01", help="回测结束日期 (默认: 2026-05-01)")
    parser.add_argument("--cash", type=float, default=1_000_000, help="初始资金 (默认: 100万)")

    # ETF配置
    parser.add_argument(
        "--etf-codes", default=None,
        help="指定ETF代码，逗号分隔 (默认: 510880.SH,515180.SH,512890.SH,561580.SH)"
    )
    parser.add_argument(
        "--top-n", type=int, default=30,
        help="每只ETF取前N大持仓 (默认: 30)"
    )
    parser.add_argument(
        "--min-etf", type=int, default=1,
        help="标的至少出现在N只ETF中才纳入 (默认: 1=并集, 2=共识)"
    )

    # 调仓参数
    parser.add_argument("--freq", default="quarterly",
                        choices=["quarterly", "semiannual", "monthly"],
                        help="调仓频率 (默认: quarterly)")
    parser.add_argument("--benchmark", default="000300.SH,515180.SH",
                        help="基准指数 (默认: 沪深300,易方达红利ETF)")

    # 仓位参数
    parser.add_argument("--max-weight", type=float, default=15.0,
                        help="单股最大权重%% (默认: 15)")
    parser.add_argument("--mid-weight", type=float, default=8.0,
                        help="中等权重%% (默认: 8)")
    parser.add_argument("--min-weight", type=float, default=3.0,
                        help="最小权重%% (默认: 3)")
    parser.add_argument("--rebalance-threshold", type=float, default=5.0,
                        help="调仓触发阈值%% (默认: 5)")

    # 交易成本
    parser.add_argument("--commission", type=float, default=0.1,
                        help="佣金率%% (默认: 0.1)")
    parser.add_argument("--slippage", type=float, default=0.1,
                        help="滑点%% (默认: 0.1)")

    args = parser.parse_args()
    run_etf_top_backtest(args)


if __name__ == "__main__":
    main()
