#!/usr/bin/env python3
"""
分析 ZZ800 E版 FCF/EV 当前在历史中的水平
"""
import json
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 加载E版baskets
e_file = ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "all_baskets_2015_2026.json"
with open(e_file) as f:
    e_baskets = json.load(f)

print("=" * 80)
print("ZZ800 E版 — FCF/EV 历史水平分析")
print("=" * 80)
print(f"总调仓期数: {len(e_baskets)}")

# ────────────────────────────────────────
# 1. 计算每期持仓的 FCF/EV 统计量
# ────────────────────────────────────────
periods = []
for date_str in sorted(e_baskets.keys()):
    stocks = e_baskets.get(date_str, [])
    if not stocks:
        continue
    fcf_ev_list = [s.get('fcf_yield', 0) * 100 for s in stocks if s.get('fcf_yield') is not None]  # 转百分比
    if not fcf_ev_list:
        continue
    periods.append({
        'date': date_str,
        'count': len(stocks),
        'median': np.median(fcf_ev_list),
        'mean': np.mean(fcf_ev_list),
        'min': np.min(fcf_ev_list),
        'max': np.max(fcf_ev_list),
        'p25': np.percentile(fcf_ev_list, 25),
        'p75': np.percentile(fcf_ev_list, 75),
        'std': np.std(fcf_ev_list),
        'fcf_ev_list': fcf_ev_list,
    })

# ────────────────────────────────────────
# 2. 整体历史分布
# ────────────────────────────────────────
all_medians = [p['median'] for p in periods]
all_means = [p['mean'] for p in periods]

latest = periods[-1] if periods else None

print(f"\n历史数据范围: {periods[0]['date']} → {latest['date']}")
print(f"有效期数: {len(periods)}")

print(f"\n{'─'*80}")
print(f"FCF/EV 中位数 历史分布（{len(periods)}个调仓日）")
print(f"{'─'*80}")
print(f"  历史最高: {max(all_medians):.2f}%  ({periods[all_medians.index(max(all_medians))]['date']})")
print(f"  历史最低: {min(all_medians):.2f}%  ({periods[all_medians.index(min(all_medians))]['date']})")
print(f"  历史均值: {np.mean(all_medians):.2f}%")
print(f"  历史中位数: {np.median(all_medians):.2f}%")
print(f"  当前值: {latest['median']:.2f}%  ({latest['date']})")

# 当前在历史中的分位
pct = sum(1 for m in all_medians if m <= latest['median']) / len(all_medians) * 100
print(f"\n  ★ 当前 FCF/EV 中位数 = {latest['median']:.2f}%")
print(f"  ★ 处于历史 {pct:.0f} 分位 (共{len(all_medians)}期)")
print(f"  ★ 比历史均值 ({np.mean(all_medians):.2f}%) {'高' if latest['median'] > np.mean(all_medians) else '低'}"
      f" {abs(latest['median']-np.mean(all_medians)):.2f}pp")

# ────────────────────────────────────────
# 3. 历年对比
# ────────────────────────────────────────
print(f"\n{'─'*80}")
print("历年 FCF/EV 中位数变化")
print(f"{'─'*80}")
print(f"{'年份':<10s} {'期数':>5s} {'中位数%':>8s} {'均值%':>8s} {'最低%':>8s} {'最高%':>8s} {'vs历史':>10s}")
print("-" * 60)

yearly = {}
for p in periods:
    yr = p['date'][:4]
    if yr not in yearly:
        yearly[yr] = {'medians': [], 'means': [], 'count': 0}
    yearly[yr]['medians'].append(p['median'])
    yearly[yr]['means'].append(p['mean'])
    yearly[yr]['count'] += 1

hist_median = np.median(all_medians)
for yr in sorted(yearly.keys()):
    y = yearly[yr]
    ym = np.mean(y['medians'])
    ymean = np.mean(y['means'])
    ymin = min(y['medians'])
    ymax = max(y['medians'])
    vs = "高于历史" if ym > hist_median else "低于历史" if ym < hist_median else "≈历史"
    marker = " ★" if yr == latest['date'][:4] else ""
    print(f"{yr+marker:<10s} {y['count']:>5d} {ym:>8.2f} {ymean:>8.2f} {ymin:>8.2f} {ymax:>8.2f} {vs:>10s}")

# ────────────────────────────────────────
# 4. 最新一期的详细内部结构
# ────────────────────────────────────────
print(f"\n{'─'*80}")
print(f"最新期持仓 FCF/EV 内部分布 ({latest['date']}，共{latest['count']}只)")
print(f"{'─'*80}")
print(f"  Mean:   {latest['mean']:.2f}%")
print(f"  Median: {latest['median']:.2f}%")
print(f"  Std:    {latest['std']:.2f}%")
print(f"  Min:    {latest['min']:.2f}%")
print(f"  P25:    {latest['p25']:.2f}%")
print(f"  P75:    {latest['p75']:.2f}%")
print(f"  Max:    {latest['max']:.2f}%")

print(f"\n  分布区间:")
bins = [0, 2, 4, 6, 8, 10, 15, 100]
labels = ["0-2%", "2-4%", "4-6%", "6-8%", "8-10%", "10-15%", ">15%"]
for i in range(len(bins)-1):
    count = sum(1 for v in latest['fcf_ev_list'] if bins[i] <= v < bins[i+1])
    bar = "█" * count
    print(f"    FCF/EV {labels[i]:>7s}: {count:>3d} {bar}")

# ────────────────────────────────────────
# 5. 分位数带可视化
# ────────────────────────────────────────
print(f"\n{'─'*80}")
print("历史 FCF/EV 中位数走势 + 当前分位判定")
print(f"{'─'*80}")
print(f"{'调仓日':<14s} {'持股数':>6s} {'中位数%':>8s} {'分位':>6s} {'标记':>4s}")
print("-" * 50)

pcts = [sum(1 for m in all_medians if m <= p['median']) / len(all_medians) * 100 for p in periods]
for p, pct_val in zip(periods, pcts):
    flag = "←现在" if p['date'] == latest['date'] else \
           "←最高" if p['median'] == max(all_medians) else \
           "←最低" if p['median'] == min(all_medians) else ""
    bar = "▓" * int(pct_val/10)
    print(f"{p['date']:<14s} {p['count']:>6d} {p['median']:>8.2f}% {pct_val:>5.0f}%  {bar} {flag}")

print(f"\n{'─'*80}")
print(f"结论")
print(f"{'─'*80}")
if pct <= 20:
    level = "历史低位（FCF/EV 偏低 = 持仓偏贵）"
elif pct <= 40:
    level = "历史中低位（FCF/EV 略低 = 持仓略贵）"
elif pct <= 60:
    level = "历史中枢区间（估值合理）"
elif pct <= 80:
    level = "历史中高位（FCF/EV 略高 = 持仓性价比较好）"
else:
    level = "历史最高位（FCF/EV 极高 = 现金流极度充裕，估值极便宜）"

print(f"\n当前 E 版持仓 FCF/EV 中位数：{latest['median']:.2f}%，处于历史 {pct:.0f} 分位")
print(f"判定：{level}")
print(f"\n注：FCF/EV 越高 = 每单位企业价值产生的自由现金流越多 = 持仓越便宜/性价比越高")
if pct > 50:
    print(f"当前 FCF/EV 比 {100-pct:.0f}% 的历史时期更高 → 当前位置的现金流性价比处于历史中上水平 ✅")
else:
    print(f"当前 FCF/EV 比 {pct:.0f}% 的历史时期更低 → 当前位置的现金流性价比较低")
