"""Minimal diagnostic: why are 60 932365 stocks missing from our Top 100? (no get_fcf_basket call)"""
import sys, time
from pathlib import Path
from collections import defaultdict

_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from weekly_harness.fcf_universe import FcfUniverse, IndexWeightCache, _is_financial_or_real_estate
import tushare as ts
from config.settings import tushare_cfg
import numpy as np

DATE = "2026-03-20"
uni = FcfUniverse()
uni.preload_all(download=False)
iwc = IndexWeightCache()
iwc.load()
pro = ts.pro_api(tushare_cfg.token)

# ── 932365 actual ──
avail = pro.index_weight(index_code="932365.CSI", start_date="20260301", end_date="20260401")
avail["trade_date"] = avail["trade_date"].astype(str)
closest = sorted(avail["trade_date"].unique())[0]
actual = avail[avail["trade_date"] == closest]
aw = dict(zip(actual["con_code"], actual["weight"]))

csi_codes = set(iwc.get_constituents(DATE))
info_map = uni._stock_basic.set_index("ts_code").to_dict("index") if uni._stock_basic is not None and not uni._stock_basic.empty else {}

# ── For our Top 100 cutoff, we need FCF yields of our selected stocks ──
# Since we can't call get_fcf_basket, reconstruct FCF yields for a sample
# from the last verbose run: cutoff=6.99%

print(f"932365 成分: {len(aw)} 只")
print(f"CSI全指: {len(csi_codes)} 只")

# ── Step 1: diagnose each 932365 stock ──
results = defaultdict(list)

for code in sorted(aw, key=lambda x: -float(aw[x])):
    w = float(aw[code])
    name = info_map.get(code, {}).get("name", "")
    
    # CSI?
    if code not in csi_codes:
        results["not_in_csi"].append((code, name, w))
        continue
    
    # Industry?
    ind = str(info_map.get(code, {}).get("industry", ""))
    if _is_financial_or_real_estate(ind):
        results["financial"].append((code, name, w))
        continue
    
    # Financial data
    ry = uni._get_available_report_year(DATE, code)
    fin = uni._fin_cache.get_annual_financials(code, ry)
    ocf = fin["oper_cf"]
    capex = fin["capex"]
    op = fin["oper_profit"]
    ta = fin["total_assets"]
    tl = fin["total_liab"]
    mc = fin["money_cap"]
    
    if ocf is None:
        results["no_ocf"].append((code, name, w))
        continue
    
    fcf = ocf - capex if capex is not None else None
    if fcf is None or fcf <= 0:
        results["neg_fcf"].append((code, name, w, fcf))
        continue
    
    # 5yr OCF
    ld = str(info_map.get(code, {}).get("list_date", ""))
    ocf_s = ry - 4
    try:
        if ld and len(ld) >= 4: ocf_s = max(ocf_s, int(ld[:4]))
    except: pass
    if ry >= 2019 or ocf_s > (ry - 4):
        if not uni._fin_cache.check_5yr_positive_ocf(code, ry, start_year=ocf_s):
            results["bad_ocf"].append((code, name, w))
            continue
    else:
        results["bad_ocf"].append((code, name, w))
        continue
    
    # Market cap → need API
    results["need_mv"].append((code, name, w, fcf, tl, mc, ta, op))
    time.sleep(0.05)

# ── Step 2: fetch market cap for need_mv stocks ──
need_mv = results.get("need_mv", [])
print(f"\n需要市值数据: {len(need_mv)} 只")

# Fetch daily_basic for target date
date_key = DATE.replace("-", "")
for delta in range(6):
    from datetime import datetime as dt_dt, timedelta as dt_td
    base_date = dt_dt.strptime(date_key, "%Y%m%d")
    d = (base_date - dt_td(days=delta)).strftime("%Y%m%d")
    try:
        df = pro.daily_basic(trade_date=d, fields="ts_code,total_mv")
        time.sleep(0.3)
        if df is not None and not df.empty:
            mv_map = dict(zip(df["ts_code"].astype(str), df["total_mv"]))
            print(f"  daily_basic {d}: {len(df)} stocks")
            break
    except:
        pass

# ── Step 3: compute FCF yield & compare ──
report = []
for code, name, w, fcf, tl, mc, ta, op in need_mv:
    total_mv = mv_map.get(code)
    if total_mv is None:
        results["no_mv"].append((code, name, w))
        continue
    
    # EV calculation
    if tl is None or mc is None:
        results["no_data"].append((code, name, w))
        continue
    
    ev = total_mv * 10000 + tl - mc  # 万元→元
    if ev <= 0:
        results["neg_ev"].append((code, name, w, ev/1e8))
        continue
    
    fcf_yield = fcf / ev * 100  # percentage
    report.append((code, name, w, fcf/1e8, ev/1e8, total_mv/10000, fcf_yield))

# ── Output ──
print(f"\n{'='*70}")
print(f"  932365 缺失标的 FCF率分析 (932365={closest})")
print(f"{'='*70}")

# Sort by FCF yield descending
report.sort(key=lambda x: -x[6])

# From last verbose run: our Top100 cutoff = 6.99%
OUR_CUTOFF = 6.99
above_cutoff = [r for r in report if r[6] >= OUR_CUTOFF]
below_cutoff = [r for r in report if r[6] < OUR_CUTOFF]

print(f"\n  FCF率 ≥ {OUR_CUTOFF}% (应入选): {len(above_cutoff)} 只")
for c, n, w, fcf, ev, mv, fy in above_cutoff[:10]:
    print(f"    {c} {n}: FCF率={fy:.1f}% FCF={fcf:.0f}亿 EV={ev:.0f}亿 MV={mv:.0f}亿 w={w:.1f}%")

print(f"\n  FCF率 < {OUR_CUTOFF}% (挤出): {len(below_cutoff)} 只")
for c, n, w, fcf, ev, mv, fy in sorted(below_cutoff, key=lambda x: -x[2])[:10]:
    print(f"    {c} {n}: FCF率={fy:.1f}% FCF={fcf:.0f}亿 EV={ev:.0f}亿 MV={mv:.0f}亿 w={w:.1f}%")

# Other reasons
for bucket, label in [("not_in_csi","不在CSI"),("financial","金融地产"),("no_ocf","无经营CF"),
                       ("neg_fcf","FCF≤0"),("bad_ocf","5年OCF"),("no_mv","无市值"),("no_data","数据不完整"),("neg_ev","EV≤0")]:
    items = results.get(bucket, [])
    if items:
        wsum = sum(x[2] for x in items)
        print(f"\n  {label}: {len(items)}只 (权重{wsum:.1f}%)")
        for row in items[:3]:
            print(f"    {row[0]} {row[1]}: w={row[2]:.1f}%")

print(f"\n✅ 分析完成")
