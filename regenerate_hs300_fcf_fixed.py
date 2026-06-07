#!/usr/bin/env python3
"""
regenerate_hs300_fcf_fixed.py — 用修正后的逻辑重新生成 HS300 FCF 篮子

修正点（与 ZZ800 FCF 相同）：
1. EV = total_mv（总市值）而非 circ_mv（流通市值）
2. CSI300 成分股 6/12月取当月月末快照
3. TTM 回退机制（缺季度数据时回退年报）
4. 5yr OCF 宽松逻辑（缺失年份跳过）
"""

import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib
import weekly_harness.fcf_universe as fu_mod
importlib.reload(fu_mod)
from weekly_harness.fcf_universe import FcfUniverse

# Load existing HS300 baskets to get the dates
old_path = Path(__file__).resolve().parent / "output" / "hs300_fcf" / "all_baskets_2015_2026.json"
with open(old_path) as f:
    old_baskets = json.load(f)

output_path = Path(__file__).resolve().parent / "output" / "hs300_fcf_fixed" / "all_baskets_2015_2026.json"
output_path.parent.mkdir(parents=True, exist_ok=True)

# Initialize with HS300 index code
uni = FcfUniverse(index_code="000300.SH")
uni.preload_all()

# Regenerate all baskets with fixed logic
all_baskets = {}
dates = sorted(old_baskets.keys())
t_total = time.time()

for i, date in enumerate(dates):
    t0 = time.time()
    basket = uni.get_fcf_basket(date, top_n=50, use_ttm=True, verbose=False)
    t1 = time.time() - t0
    
    # Clean basket for serialization
    clean = []
    if basket:
        qw = basket.pop("__quality_warnings__", None)
        for code, info in basket.items():
            item = {
                "ts_code": code,
                "name": info.get("name", ""),
                "weight": round(info.get("weight", 0), 4),
                "fcf_yield": round(info.get("fcf_yield", 0), 6),
                "fcf": round(info.get("fcf", 0), 2) if info.get("fcf") else None,
                "industry": info.get("industry", ""),
                "total_mv": round(info.get("total_mv", 0), 2) if info.get("total_mv") else None,
                "ev": round(info.get("ev", 0), 2) if info.get("ev") else None,
                "profit_quality": round(info.get("profit_quality", 0), 4) if info.get("profit_quality") else None,
            }
            clean.append(item)
    
    all_baskets[date] = clean
    
    n = len(clean)
    elapsed_total = time.time() - t_total
    # Check for CMCC/CTCC
    if n > 0:
        codes = [s["ts_code"] for s in clean]
        cmcc = "600941.SH" in codes
        ctcc = "601728.SH" in codes
        flag = ""
        if cmcc: flag += " ⚠️中国移动"
        if ctcc: flag += " ⚠️中国电信"
        if not flag: flag = " ✓"
        print(f"[{i+1}/{len(dates)}] {date}: {t1:.1f}s, {n} stocks{flag} (total: {elapsed_total:.0f}s)")
    else:
        print(f"[{i+1}/{len(dates)}] {date}: {t1:.1f}s, 0 stocks (total: {elapsed_total:.0f}s)")

# Save
with open(output_path, 'w') as f:
    json.dump(all_baskets, f, ensure_ascii=False, indent=2)

print(f"\n✅ Saved {len(all_baskets)} periods to {output_path}")
print(f"Total time: {time.time() - t_total:.0f}s")
