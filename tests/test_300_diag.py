"""CSI 300 FCF 缺失诊断：不用 get_fcf_basket，直接算"""
import sys, time, pandas as pd
from pathlib import Path

_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from weekly_harness.fcf_universe import FcfUniverse, _is_financial_or_real_estate
from config.settings import tushare_cfg
import tushare as ts

DATE = "2026-03-20"
uni = FcfUniverse(index_code="000300.SH")
uni.preload_all(download=False)
pro = ts.pro_api(tushare_cfg.token)
info_map = uni._stock_basic.set_index("ts_code").to_dict("index")

# Get 932366
avail = pro.index_weight(index_code="932366.CSI", start_date="20260301", end_date="20260401")
avail["trade_date"] = avail["trade_date"].astype(str)
closest = sorted(avail["trade_date"].unique())[0]
actual = avail[avail["trade_date"] == closest]
aw = dict(zip(actual["con_code"], actual["weight"]))

# Get our basket
basket = uni.get_fcf_basket(DATE, top_n=50, verbose=False)
our_codes = {k for k in basket if k != "__quality_warnings__"}

# CSI 300 constituents
constituents = uni._idx_cache.get_constituents(DATE)
csi_set = set(constituents)

buckets = {"not_in_csi": [], "financial": [], "no_data": [], "neg_fcf": [],
           "bad_pq": [], "bad_ocf": [], "ranked_out": []}

# PQ cutoff from our basket (approximate)
pq_candidates = []
for code in csi_set:
    ry = uni._get_available_report_year(DATE, code)
    fin = uni._fin_cache.get_annual_financials(code, ry)
    o = fin["oper_cf"]; p = fin["oper_profit"]; t = fin["total_assets"]
    if o is not None and p is not None and t is not None and t > 0:
        pq_candidates.append((o-p)/t)
import numpy as np
pq_cutoff = np.percentile(pq_candidates, 20) if pq_candidates else float("-inf")

# Get MV (circ_mv for EV, total_mv for display)
from datetime import datetime as dt_dt, timedelta as dt_td
date_key = DATE.replace("-", "")
for delta in range(6):
    b = dt_dt.strptime(date_key, "%Y%m%d")
    d = (b - dt_td(days=delta)).strftime("%Y%m%d")
    try:
        df = pro.daily_basic(trade_date=d, fields="ts_code,total_mv,circ_mv")
        if df is not None and not df.empty:
            mv_map = dict(zip(df["ts_code"].astype(str), df["total_mv"]))
            circ_map = dict(zip(df["ts_code"].astype(str), df["circ_mv"]))
            break
    except: pass

# Our Top50 cutoff (from verbose output)
OUR_CUTOFF = 3.47  # percent

missing = set(aw.keys()) - our_codes
print(f"932366={closest}, 我们={len(our_codes)}, 重叠={len(our_codes & set(aw.keys()))}")
print(f"缺失={len(missing)}只, PQ cutoff={pq_cutoff:.4f}\n")

for code in sorted(missing, key=lambda x: -float(aw[x])):
    w = float(aw[code])
    name = info_map.get(code, {}).get("name", "")
    
    if code not in csi_set:
        print(f"  ❌CSI {code} {name} w={w:.1f}%")
        continue
    ind = str(info_map.get(code, {}).get("industry", ""))
    if _is_financial_or_real_estate(ind):
        print(f"  🏦FIN {code} {name} w={w:.1f}%")
        continue
    
    ry = uni._get_available_report_year(DATE, code)
    fin = uni._fin_cache.get_annual_financials(code, ry)
    ocf = fin["oper_cf"]; capex = fin["capex"]
    op = fin["oper_profit"]; ta = fin["total_assets"]
    tl = fin["total_liab"]; mc = fin["money_cap"]
    
    if ocf is None: print(f"  📭NODATA {code} {name} w={w:.1f}%"); continue
    fcf = ocf - (capex or 0)
    if fcf <= 0: print(f"  📉NEG_FCF {code} {name} w={w:.1f}% FCF={fcf/1e8:.1f}亿"); continue
    
    pq = (ocf - op)/ta if (op and ta and ta>0) else None
    ld = str(info_map.get(code,{}).get("list_date",""))
    ocf_s = ry-4
    try:
        if ld and len(ld)>=4: ocf_s = max(ocf_s, int(ld[:4]))
    except: pass
    ocf_ok = ry>=2019 or ocf_s>(ry-4)
    if ocf_ok:
        ocf_ok = uni._fin_cache.check_5yr_positive_ocf(code, ry, start_year=ocf_s)
    if not ocf_ok: print(f"  ⏱️OCF {code} {name} w={w:.1f}%"); continue
    
    # 使用 circ_mv（流通市值）计算 EV，回退 total_mv
    mv_for_ev = circ_map.get(code) or mv_map.get(code)
    total_mv = mv_map.get(code)
    if mv_for_ev is None: print(f"  📭NOMV {code} {name} w={w:.1f}%"); continue
    ev = mv_for_ev*10000 + (tl or 0) - (mc or 0)
    if ev <= 0: print(f"  📉NEG_EV {code} {name} w={w:.1f}%"); continue
    fy = fcf/ev*100
    
    mv_display = mv_for_ev/10000
    pq_str = f"PQ={pq:.4f}" if pq else "PQ=None"
    flag = "⬆高于cutoff" if fy >= OUR_CUTOFF else f"⬇低于cutoff({OUR_CUTOFF}%)"
    print(f"  📋 {code} {name} w={w:.1f}% FCF率={fy:.2f}% FCF={fcf/1e8:.0f}亿 EV={ev/1e8:.0f}亿 circ_MV={mv_display:.0f}亿 {pq_str} {flag}")
