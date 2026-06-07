#!/usr/bin/env python3
"""
regenerate_fixed_baskets_v2.py — 用优化后的 fcf_universe 重新生成所有篮子
====================================================

优化点：
1. 市值数据本地缓存 (daily_basic_cache) — 0.08s vs 80s
2. 财务数据 dict 索引 — 0ms vs 20ms/call
3. 总耗时预计 ~10分钟 vs 之前 ~60分钟
"""

import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib
import weekly_harness.fcf_universe as fu_mod
importlib.reload(fu_mod)
from weekly_harness.fcf_universe import FcfUniverse

# Load existing baskets to get the dates
old_path = Path(__file__).resolve().parent / "output" / "zz800_fcf" / "all_baskets_2015_2026.json"
with open(old_path) as f:
    old_baskets = json.load(f)

output_path = Path(__file__).resolve().parent / "output" / "zz800_fcf_fixed" / "all_baskets_2015_2026.json"
output_path.parent.mkdir(parents=True, exist_ok=True)

# Initialize
uni = FcfUniverse(index_code="000906.SH")
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

print(f"\n✅ Done! Saved to {output_path}")
print(f"Total time: {time.time() - t_total:.0f}s")