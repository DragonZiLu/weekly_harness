"""
ZZ800 FCF E版 逐期行业集中度分析
"""
import json, os, sys
from pathlib import Path
import pandas as pd, numpy as np
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "weekly_harness"))

BASKET_FILE = ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "all_baskets_2015_2026.json"
NAV_FILE = ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "backtest_nav_tr.csv"

with open(BASKET_FILE) as f:
    baskets = json.load(f)

nav = pd.read_csv(NAV_FILE)
ret_map = dict(zip(nav["rb_date"], nav["period_ret"]))

# 申万一级行业映射（从 fcf_universe 的 sector 字段）
dates = sorted(baskets.keys())
dates = [d for d in dates if len(baskets[d]) >= 10]

print("=" * 110)
print("【ZZ800 FCF E版 — 逐期行业集中度】")
print("=" * 110)
print(f"{'调仓日':<12} {'持仓':>4} {'Top1行业':<12} {'%':>5} {'Top2行业':<12} {'%':>5} {'Top3行业':<12} {'%':>5} {'Top3合计%':>8} {'收益':>8}")
print("-" * 110)

all_industry_history = []

for date_str in dates:
    stocks = baskets[date_str]
    # 统计行业（用 sector 或 industry 字段）
    sectors = []
    for s in stocks:
        sec = s.get("sector", s.get("industry", "其他"))
        if sec and sec != "其他":
            sectors.append(sec)
        else:
            # 尝试从 name 推断
            sectors.append("其他")
    
    counter = Counter(sectors)
    total = len(stocks)
    top3 = counter.most_common(3)
    
    top1_name = top3[0][0] if len(top3) > 0 else "-"
    top1_pct = top3[0][1]/total*100 if len(top3) > 0 else 0
    top2_name = top3[1][0] if len(top3) > 1 else "-"
    top2_pct = top3[1][1]/total*100 if len(top3) > 1 else 0
    top3_name = top3[2][0] if len(top3) > 2 else "-"
    top3_pct = top3[2][1]/total*100 if len(top3) > 2 else 0
    top3_sum = top1_pct + top2_pct + top3_pct
    
    ret = ret_map.get(date_str)
    
    # 保存完整行业分布
    for sec, cnt in counter.most_common():
        all_industry_history.append({
            "date": date_str, "sector": sec, "count": cnt, "pct": cnt/total*100
        })
    
    flag = ""
    if ret is not None:
        flag = "🟢" if ret > 3 else ("🔴" if ret < -3 else "🟡")
    
    print(f"{date_str:<12} {total:>4} {top1_name:<12} {top1_pct:>4.0f}% {top2_name:<12} {top2_pct:>4.0f}% {top3_name:<12} {top3_pct:>4.0f}% {top3_sum:>7.0f}% {ret:>+7.1f}% {flag}" if ret else f"{date_str:<12} {total:>4} {top1_name:<12} {top1_pct:>4.0f}% {top2_name:<12} {top2_pct:>4.0f}% {top3_name:<12} {top3_pct:>4.0f}% {top3_sum:>7.0f}%")

# ── 行业热度变化 ──
df_hist = pd.DataFrame(all_industry_history)

# 按年份汇总
print(f"\n{'='*110}")
print("【行业权重变迁（按年份，取该年最后一期）】")
print(f"{'='*110}")

# 取每年最后一期
year_end_dates = {}
for d in dates:
    yr = d[:4]
    year_end_dates[yr] = d
year_ends = sorted(year_end_dates.values())

# 收集所有出现过的行业
all_sectors = sorted(df_hist["sector"].unique())

# 构建行业×年份矩阵
print(f"{'行业':<16}", end="")
for d in year_ends:
    print(f" {d[:7]:>7}", end="")
print()

for sec in all_sectors:
    print(f"{sec:<16}", end="")
    for d in year_ends:
        row = df_hist[(df_hist["date"]==d) & (df_hist["sector"]==sec)]
        pct = row["pct"].iloc[0] if len(row) > 0 else 0
        bar = "█" * int(pct/2) if pct > 0 else ""
        print(f" {pct:>5.0f}%", end="")
    print()

# ── 集中度趋势 ──
print(f"\n{'='*110}")
print("【Top3行业集中度趋势】")
print(f"{'='*110}")
for date_str in dates:
    stocks = baskets[date_str]
    sectors = [s.get("sector", s.get("industry", "其他")) for s in stocks]
    counter = Counter(sectors)
    top3_sum = sum(c for _, c in counter.most_common(3)) / len(stocks) * 100
    bar = "█" * int(top3_sum/2)
    ret = ret_map.get(date_str)
    ret_s = f" {ret:+.1f}%" if ret else ""
    print(f"  {date_str}: {top3_sum:>5.0f}% {bar}{ret_s}")
