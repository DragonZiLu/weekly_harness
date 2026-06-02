"""
单只标的快速评估 — 使用红利周期三层估值体系
用法: python eval_single.py 山东高速 --code 600350.SH
"""
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dividend_evaluator import DividendCycleEvaluator, ReportGenerator, SECTOR_THRESHOLDS, SECTOR_LIFECYCLE


def main():
    import argparse
    parser = argparse.ArgumentParser(description="单只标的红利周期评估")
    parser.add_argument("name", help="标的名称")
    parser.add_argument("--code", required=True, help="ts_code，如 600350.SH")
    parser.add_argument("--category", default="弱周期红利", choices=["弱周期红利", "消费成长红利", "周期资源红利", "ETF红利"])
    parser.add_argument("--sector", default="交通", help="细分行业")
    parser.add_argument("--certainty", default="A-", choices=["AA", "A", "A-", "B+", "B", "B-"])
    parser.add_argument("--moat", default="垄断路桥资产·特许经营权", help="护城河描述")
    parser.add_argument("--comment", default="", help="点评")
    args = parser.parse_args()

    meta = {
        "ts_code": args.code,
        "category": args.category,
        "certainty": args.certainty,
        "moat": args.moat,
        "comment": args.comment,
        "sector": args.sector,
    }

    print(f"\n{'='*65}")
    print(f"  🔍 红利周期投资 — 单只标的评估")
    print(f"  标的: {args.name} ({args.code})")
    print(f"  行业: {args.sector} | 类别: {args.category} | 确定性: {args.certainty}")
    print(f"  护城河: {args.moat}")
    print(f"{'='*65}\n")

    evaluator = DividendCycleEvaluator()
    result = evaluator.evaluate_company(args.name, args.code, meta)

    # 输出详细结果
    print(f"\n{'─'*60}")
    print(f"  📊 估值核心数据")
    print(f"{'─'*60}")
    print(f"  {'当前股价:':<16} {result['close']:.2f} 元")
    print(f"  {'TTM市盈率:':<16} {result['pe_ttm']:.1f}x")
    print(f"  {'市净率:':<16} {result['pb']:.2f}x")
    print(f"  {'现金股息率:':<16} {result['div_yield']:.2f}%")
    print(f"  {'等效分红率:':<16} {result['eff_yield']:.2f}%  (含回购 {result['buyback_yield']:.1f}%)")
    print(f"  {'股债息差:':<16} {result['bond_spread_bp']:.0f} BP")
    print(f"  {'ROE:':<16} {result['roe']:.1f}%")
    print(f"  {'净利润增速:':<16} {result['net_profit_growth']:+.1f}%")
    print(f"  {'总市值:':<16} {result['total_mv']:.0f} 亿元")
    print(f"  {'数据来源:':<16} {result['source']}")

    print(f"\n{'─'*60}")
    print(f"  📊 红利周期三层评估 (总分 {result['total_score']:.0f}/100)")
    print(f"{'─'*60}")
    print(f"  {'S1 股息率 (×30):':<22} {result['s1_div']:>5.0f}  {result['r1']}")
    print(f"  {'S2 股债息差 (×25):':<22} {result['s2_spread']:>5.0f}  {result['r2']}")
    print(f"  {'S3 等效分红 (×20):':<22} {result['s3_eff']:>5.0f}  {result['r3']}")
    print(f"  {'S4 确定性 (×15):':<22} {result['s4_certainty']:>5.0f}  {result['r4']}")
    print(f"  {'S5 成长性 (×10):':<22} {result['s5_growth']:>5.0f}  {result['r5']}")
    print(f"  {'─'*60}")
    print(f"  {'总分:':<22} {result['total_score']:>5.0f}/100  →  {result['verdict']}")

    # 操作建议
    print(f"\n{'─'*60}")
    print(f"  🎯 操作建议")
    print(f"{'─'*60}")
    print(f"  建议: {result['verdict']}")
    print(f"  策略: {result['advice']}")

    # 行业锚
    sector = args.sector
    thresholds = SECTOR_THRESHOLDS.get(sector, {})
    lifecycle = SECTOR_LIFECYCLE.get(sector, {})
    if thresholds:
        print(f"\n{'─'*60}")
        print(f"  🏷️  行业锚: {sector}")
        print(f"{'─'*60}")
        print(f"  {thresholds.get('comment', '')}")
        print(f"  生命周期: {lifecycle.get('stage', 'N/A')} | 分红潜力: {lifecycle.get('dividend_potential', 'N/A')}")
        print(f"  观察线: {thresholds.get('watch', 0):.1f}% | 买入线: {thresholds.get('buy', 0):.1f}%")
        print(f"  加仓线: {thresholds.get('add', 0):.1f}% | 满仓线: {thresholds.get('full', 0):.1f}%")
        print(f"  减仓线: {thresholds.get('reduce', 0):.1f}%")

    # 阶梯攒股价格
    ladder = result.get("ladder", {})
    if ladder:
        print(f"\n{'─'*60}")
        print(f"  🪜 阶梯攒股价格表 (基于行业 {sector} 锚)")
        print(f"{'─'*60}")
        print(f"  当前股价: {result['close']:.2f} 元")
        print(f"  观察价 (开始关注): {ladder.get('watch', 0):.2f} 元")
        print(f"  买入价 (建仓底仓): {ladder.get('buy', 0):.2f} 元")
        print(f"  加仓价 (加大仓位): {ladder.get('add', 0):.2f} 元")
        print(f"  满仓价 (全力攒股): {ladder.get('full', 0):.2f} 元")

    # 网格交易
    grid = result.get("grid", {})
    if grid:
        print(f"\n{'─'*60}")
        print(f"  📐 网格交易区间")
        print(f"{'─'*60}")
        print(f"  当前区间: {grid.get('zone', 'N/A')} | {grid.get('desc', '')}")

    # 预期股息率
    fwd = result.get("forward_div_yield", 0)
    fwd_dps = result.get("forward_dps", 0)
    if fwd > 0:
        print(f"\n{'─'*60}")
        print(f"  🔮 前瞻指标")
        print(f"{'─'*60}")
        print(f"  预期DPS: {fwd_dps:.4f} 元/股")
        print(f"  预期股息率: {fwd:.2f}%")

    # 分红奶牛信号
    cow = result.get("dividend_cow", {})
    if cow and cow.get("signal") != "无":
        print(f"\n{'─'*60}")
        print(f"  🐄 分红奶牛信号: {cow.get('signal', '无')}")
        print(f"{'─'*60}")
        print(f"  {cow.get('reason', '')}")
        print(f"  行业阶段: {cow.get('stage', 'N/A')} | 分红潜力: {cow.get('dividend_potential', 'N/A')}")

    # 分红复投测算
    drip = result.get("drip_10y", {})
    if drip:
        print(f"\n{'─'*60}")
        print(f"  💰 分红复投测算 (100万投入，假设股价不涨)")
        print(f"{'─'*60}")
        print(f"  第1年分红: {drip.get('第1年分红', 0):.1f} 万")
        print(f"  第5年成本股息率: {drip.get('第5年成本股息率', 0):.1f}%")
        print(f"  第10年成本股息率: {drip.get('第10年成本股息率', 0):.1f}%")
        print(f"  第10年年分红: {drip.get('第10年年分红', 0):.1f} 万")

    print(f"\n{'─'*60}")
    print(f"  📝 {result.get('comment', '')}")
    print(f"  📝 {result.get('note', '')}")
    print(f"\n  ⚠️ 免责声明: 本评估仅供学习研究，不构成投资建议。")
    print()


if __name__ == "__main__":
    main()
