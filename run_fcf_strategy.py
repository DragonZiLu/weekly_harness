#!/usr/bin/env python3
"""
FCF策略选股运行脚本
===================

统一入口, 支持沪深300FCF和中证800FCF策略。

Usage:
    # 运行沪深300FCF策略 (最新调仓日)
    python run_fcf_strategy.py --strategy hs300_fcf

    # 运行中证800FCF策略
    python run_fcf_strategy.py --strategy zz800_fcf

    # 指定调仓日期
    python run_fcf_strategy.py --strategy hs300_fcf --date 2025-06-16

    # 同时运行两个策略
    python run_fcf_strategy.py --strategy all

    # 首次运行, 下载缺失数据
    python run_fcf_strategy.py --strategy hs300_fcf --download

    # 输出为CSV
    python run_fcf_strategy.py --strategy hs300_fcf --format csv
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent
_STRATEGIES_DIR = _PROJECT_ROOT / "strategies"


def load_strategy_config(strategy_name: str) -> dict:
    """加载策略配置"""
    config_path = _STRATEGIES_DIR / strategy_name / "strategy.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"策略配置不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_latest_rebalance_date() -> str:
    """计算最近一个调仓日 (3/6/9/12月第二个星期五下一交易日)"""
    today = datetime.now()

    # 简化: 找到当前季度对应的调仓月
    quarter_month = (today.month - 1) // 3 * 3 + 3  # 3,6,9,12
    if quarter_month > 12:
        quarter_month = 12

    # 尝试当前季度的调仓日
    rebalance_date = _find_second_friday_next_day(today.year, quarter_month)

    # 如果调仓日还在未来, 用上一季度
    if rebalance_date > today:
        prev_quarter = quarter_month - 3
        if prev_quarter <= 0:
            prev_quarter = 12
            prev_year = today.year - 1
        else:
            prev_year = today.year
        rebalance_date = _find_second_friday_next_day(prev_year, prev_quarter)

    return rebalance_date.strftime("%Y-%m-%d")


def _find_second_friday_next_day(year: int, month: int) -> datetime:
    """找到某月第二个星期五的下一交易日(简化为下一周一)"""
    # 找第一个星期五
    first_day = datetime(year, month, 1)
    # 星期五=4 (0=Mon)
    first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
    if first_friday < first_day:
        first_friday += timedelta(days=7)
    second_friday = first_friday + timedelta(days=7)

    # 下一交易日: 如果周五, 下一交易日是下周一
    next_tradeday = second_friday + timedelta(days=3)  # 周五+3=周一
    # 如果恰好是周末, 取周一
    while next_tradeday.weekday() >= 5:
        next_tradeday += timedelta(days=1)

    return next_tradeday


def run_strategy(strategy_name: str, date_str: str, download: bool = False,
                 verbose: bool = True, output_format: str = "table"):
    """运行单个策略"""
    config = load_strategy_config(strategy_name)
    index_code = config["universe"]["index_code"]
    top_n = config["selection"]["top_n"]
    use_ttm = config["selection"]["use_ttm"]

    print(f"\n{'='*60}")
    print(f"📊 策略: {config['description']}")
    print(f"📅 调仓日期: {date_str}")
    print(f"🏦 样本空间: {config['universe']['index_name']} ({index_code})")
    print(f"🔢 选股数量: {top_n}")
    print(f"📈 数据口径: {'TTM' if use_ttm else '年报'}")
    print(f"{'='*60}\n")

    sys.path.insert(0, str(_PROJECT_ROOT))
    from weekly_harness.fcf_universe import FcfUniverse

    uni = FcfUniverse(index_code=index_code)
    uni.preload_all(download=download)

    basket = uni.get_fcf_basket(date_str, top_n=top_n, use_ttm=use_ttm, verbose=verbose)

    if not basket:
        print("❌ 未选出任何标的")
        return None

    # 移除质量告警键
    basket.pop("__quality_warnings__", None)

    # 按权重降序排列
    sorted_items = sorted(basket.items(), key=lambda x: x[1].get("weight", 0), reverse=True)

    if output_format == "csv":
        _output_csv(sorted_items, strategy_name, date_str, config)
    elif output_format == "json":
        _output_json(sorted_items, strategy_name, date_str)
    else:
        _output_table(sorted_items, strategy_name, config)

    return basket


def _output_table(sorted_items, strategy_name, config):
    """表格输出"""
    print(f"\n{'─'*90}")
    print(f"  {'排名':>4}  {'代码':<12} {'名称':<8} {'FCF率':>8} {'FCF(亿)':>10} "
          f"{'EV(亿)':>10} {'PQ':>8} {'权重%':>7}  {'行业'}")
    print(f"{'─'*90}")

    for i, (code, info) in enumerate(sorted_items, 1):
        fcf_yield = info.get("fcf_yield", 0) * 100
        fcf = info.get("fcf", 0) / 1e8
        ev = info.get("ev", 0) / 1e8
        pq = info.get("profit_quality")
        weight = info.get("weight", 0) * 100
        industry = info.get("industry", "")
        name = info.get("name", code)

        pq_str = f"{pq:.4f}" if pq is not None else "N/A"
        cap_tag = "🔴" if weight >= 9.9 else "  "

        print(f"  {i:>4}  {code:<12} {name:<8} {fcf_yield:>7.2f}% {fcf:>10.1f} "
              f"{ev:>10.1f} {pq_str:>8} {weight:>6.2f}% {cap_tag} {industry}")

    print(f"{'─'*90}")

    # 汇总统计
    weights = [info.get("weight", 0) for _, info in sorted_items]
    capped = sum(1 for w in weights if w >= 0.099)
    total_weight = sum(weights) * 100
    print(f"\n  📊 汇总: {len(sorted_items)}只 | 封顶标的{capped}只 | "
          f"总权重{total_weight:.1f}% | "
          f"最大权重{max(weights)*100:.2f}% | 最小权重{min(weights)*100:.2f}%")

    # 行业分布
    industries = {}
    for _, info in sorted_items:
        ind = info.get("industry", "未知")
        industries[ind] = industries.get(ind, 0) + 1
    top_ind = sorted(industries.items(), key=lambda x: -x[1])[:5]
    print(f"  🏭 行业TOP5: {', '.join(f'{k}({v})' for k,v in top_ind)}")


def _output_csv(sorted_items, strategy_name, date_str, config):
    """CSV输出"""
    import pandas as pd

    rows = []
    for i, (code, info) in enumerate(sorted_items, 1):
        rows.append({
            "rank": i,
            "ts_code": code,
            "name": info.get("name", ""),
            "fcf_yield": f"{info.get('fcf_yield', 0)*100:.2f}%",
            "fcf_1e8": f"{info.get('fcf', 0)/1e8:.1f}",
            "ev_1e8": f"{info.get('ev', 0)/1e8:.1f}",
            "profit_quality": f"{info.get('profit_quality', 0):.4f}" if info.get("profit_quality") else "",
            "weight": f"{info.get('weight', 0)*100:.2f}%",
            "industry": info.get("industry", ""),
            "sector": info.get("sector", ""),
        })

    df = pd.DataFrame(rows)
    outdir = _PROJECT_ROOT / "output" / strategy_name
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"fcf_basket_{date_str.replace('-','')}.csv"
    df.to_csv(outpath, index=False, encoding="utf-8-sig")
    print(f"\n✅ CSV已保存: {outpath}")


def _output_json(sorted_items, strategy_name, date_str):
    """JSON输出"""
    result = {
        "strategy": strategy_name,
        "date": date_str,
        "stocks": [
            {"rank": i, "ts_code": code, **info}
            for i, (code, info) in enumerate(sorted_items, 1)
        ]
    }
    outdir = _PROJECT_ROOT / "output" / strategy_name
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"fcf_basket_{date_str.replace('-','')}.json"
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON已保存: {outpath}")


def main():
    parser = argparse.ArgumentParser(description="FCF策略选股")
    parser.add_argument("--strategy", type=str, default="all",
                        choices=["hs300_fcf", "zz800_fcf", "all"],
                        help="策略名称 (hs300_fcf/zz800_fcf/all)")
    parser.add_argument("--date", type=str, default=None,
                        help="调仓日期 YYYY-MM-DD (默认: 最近调仓日)")
    parser.add_argument("--download", action="store_true",
                        help="首次运行时下载缺失数据")
    parser.add_argument("--format", type=str, default="table",
                        choices=["table", "csv", "json"],
                        help="输出格式")
    parser.add_argument("--quiet", action="store_true",
                        help="安静模式 (不打印筛选过程)")

    args = parser.parse_args()

    date_str = args.date or get_latest_rebalance_date()

    if args.strategy == "all":
        strategies = ["hs300_fcf", "zz800_fcf"]
    else:
        strategies = [args.strategy]

    for strat in strategies:
        run_strategy(strat, date_str, download=args.download,
                     verbose=not args.quiet, output_format=args.format)


if __name__ == "__main__":
    main()
