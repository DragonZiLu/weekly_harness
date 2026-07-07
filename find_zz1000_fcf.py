#!/usr/bin/env python3
"""
在中证1000成分股中，找出满足 ZZ800 E版 FCF 选股标准的标的。
E版标准：FCF>0, EV>0, 5年OCF>0, 盈利质量前80%, 排除金融/地产, 按FCF/EV排名
"""

import sys
sys.path.insert(0, 'weekly_harness')
from fcf_universe import FcfUniverse

DATE = '2026-06-15'

# ── 1. ZZ1000 选股池 ──
print("=" * 60)
print("中证1000 FCF选股（E版标准）")
print("=" * 60)

uni1000 = FcfUniverse(index_code='000852.SH', strict_ocf=False)
uni1000.preload_all(download=False)
basket1000 = uni1000.get_fcf_basket(DATE, top_n=100, use_ttm=True)

items = sorted(basket1000.items(), key=lambda x: x[1].get('fcf_yield', 0), reverse=True)

print(f"\n通过过滤总数: {len(basket1000)} 只\n")
print(f"{'排名':<5} {'代码':<12} {'名称':<10} {'FCF/EV':>7} {'FCF(亿)':>8} {'EV(亿)':>8} {'行业'}")
print("-" * 72)
for i, (code, info) in enumerate(items):
    fcf_yield = info.get('fcf_yield', 0) * 100
    fcf = info.get('fcf', 0) / 1e8
    ev = info.get('ev', 0) / 1e8
    name = info.get('name', '?')[:10]
    sector = info.get('sector', '?')
    print(f"{i+1:<5} {code:<12} {name:<10} {fcf_yield:>6.2f}% {fcf:>8.1f} {ev:>8.1f} {sector}")

# ── 2. 与 ZZ800 E版对比 ──
print("\n" + "=" * 60)
print("与 ZZ800 E版 对比")
print("=" * 60)

uni800 = FcfUniverse(index_code='000906.SH', strict_ocf=False)
uni800.preload_all(download=False)
basket800 = uni800.get_fcf_basket(DATE, top_n=50, use_ttm=True)

yields_800 = sorted([v.get('fcf_yield', 0)*100 for v in basket800.values()], reverse=True)
cutoff_e = yields_800[-1] if len(yields_800) >= 50 else (yields_800[-1] if yields_800 else 0)

print(f"ZZ800 E版 通过过滤: {len(basket800)} 只")
print(f"ZZ800 E版 Top50 FCF/EV 范围: {yields_800[0]:.2f}% ~ {cutoff_e:.2f}%")

# ZZ1000中达到E版cutoff且不在ZZ800中的
zz800_codes = set(basket800.keys())
above_cutoff = {k: v for k, v in basket1000.items()
                if v.get('fcf_yield', 0)*100 >= cutoff_e}
new_to_e = {k: v for k, v in above_cutoff.items() if k not in zz800_codes}

print(f"\nZZ1000中 FCF/EV ≥ E版cutoff({cutoff_e:.2f}%): {len(above_cutoff)} 只")
print(f"其中不在ZZ800中的（纯ZZ1000贡献）: {len(new_to_e)} 只")

if new_to_e:
    print(f"\n{'代码':<12} {'名称':<10} {'FCF/EV':>7} {'FCF(亿)':>8} {'EV(亿)':>8} {'行业'}")
    print("-" * 60)
    for code, info in sorted(new_to_e.items(), key=lambda x: x[1].get('fcf_yield', 0), reverse=True):
        fy = info.get('fcf_yield', 0)*100
        fcf = info.get('fcf', 0)/1e8
        ev = info.get('ev', 0)/1e8
        name = info.get('name', '?')[:10]
        sector = info.get('sector', '?')
        print(f"{code:<12} {name:<10} {fy:>6.2f}% {fcf:>8.1f} {ev:>8.1f} {sector}")

print("\n完成！")
