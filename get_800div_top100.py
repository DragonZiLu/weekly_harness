#!/usr/bin/env python3
"""拉取中证800红利指数(931644)复现策略最新 Top100 选股"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
sys.path.insert(0, str(PROJECT_ROOT))

from dividend_universe import DividendUniverse

DATE = "2026-06-15"
TOP_N = 100

print(f"中证800红利指数(931644)复现 — {DATE} Top{TOP_N} 选股")
print("=" * 70)

uni = DividendUniverse(index_code="000906.SH")
uni.preload_all(rebalance_dates=[DATE])

basket = uni.get_dividend_basket(date_str=DATE, top_n=TOP_N, verbose=True)

if not basket:
    print("❌ 未选出任何标的")
    sys.exit(1)

stocks = sorted(basket.values(), key=lambda x: x.get("weight", 0), reverse=True)

print("\n" + "=" * 70)
print(f"  Top{TOP_N} 持仓（按权重降序）")
print("=" * 70)
print(f"{'排名':<5} {'代码':<10} {'名称':<10} {'行业':<14} {'三年均股息率':>10} {'权重':>8}")
print("-" * 60)

for i, s in enumerate(stocks, 1):
    code = s.get("ts_code", "")
    name = s.get("name", "")
    industry = s.get("industry", "")
    dy = s.get("div_yield_3y", 0)
    w = s.get("weight", 0) * 100  # 转为百分比显示
    print(f"{i:<5} {code:<10} {name:<10} {industry:<14} {dy:>8.2f}% {w:>7.2f}%")

total_w = sum(s.get("weight", 0) for s in stocks)
avg_dy = sum(s.get("div_yield_3y", 0) for s in stocks) / len(stocks) if stocks else 0
print("-" * 60)
print(f"  合计: {len(stocks)}只, 权重合计={total_w*100:.2f}%, 平均股息率={avg_dy:.2f}%")

# 行业分布
from collections import Counter
industry_cnt = Counter(s.get("industry", "未知") for s in stocks)
print(f"\n行业分布 Top10:")
for ind, cnt in industry_cnt.most_common(10):
    print(f"  {ind}: {cnt}只")
