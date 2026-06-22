"""
HS300 FCF 最优版（D版 ±20% buffer）估值+收益对齐
"""
import json, time, os
from pathlib import Path
import pandas as pd, numpy as np
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")
import tushare as ts

pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
ROOT = Path(__file__).resolve().parent

BASKET_FILE = ROOT / "output" / "hs300_fcf_lenient_buffer" / "all_baskets_2015_2026.json"
NAV_FILE = ROOT / "output" / "hs300_fcf_lenient_buffer" / "backtest_nav_tr.csv"

REBALANCE_DATES = [
    "2015-03-16","2015-06-15","2015-09-14","2015-12-14",
    "2016-03-14","2016-06-13","2016-09-12","2016-12-12",
    "2017-03-13","2017-06-12","2017-09-11","2017-12-11",
    "2018-03-12","2018-06-11","2018-09-17","2018-12-17",
    "2019-03-11","2019-06-17","2019-09-16","2019-12-16",
    "2020-03-16","2020-06-15","2020-09-14","2020-12-14",
    "2021-03-15","2021-06-14","2021-09-13","2021-12-13",
    "2022-03-14","2022-06-13","2022-09-12","2022-12-12",
    "2023-03-13","2023-06-12","2023-09-11","2023-12-11",
    "2024-03-11","2024-06-17","2024-09-16","2024-12-16",
    "2025-03-17","2025-06-16","2025-09-15","2025-12-15",
    "2026-03-16","2026-06-15",
]

with open(BASKET_FILE) as f:
    all_baskets = json.load(f)

nav = pd.read_csv(NAV_FILE)
ret_map = dict(zip(nav["rb_date"], nav["period_ret"]))

# HS300 PE
hs = pro.index_dailybasic(ts_code="000300.SH", start_date="20150301", end_date="20260614", fields="trade_date,pe")
hs_map = dict(zip(hs["trade_date"].astype(str), hs["pe"].astype(float)))

rows = []
t0 = time.time()

for date_str in REBALANCE_DATES:
    basket = all_baskets.get(date_str, [])
    if not basket:
        continue
    codes = [s["ts_code"] for s in basket]
    weights = {s["ts_code"]: s.get("weight", 1.0/len(basket)) for s in basket}
    td = date_str.replace("-", "")

    df = None; actual_td = td
    for offset in [0, -1, +1, -2, +2, -3, +3]:
        try_td = str(int(td) + offset)
        df = pro.daily_basic(trade_date=try_td, fields="ts_code,total_mv,pe_ttm,dv_ttm")
        if df is not None and not df.empty:
            actual_td = try_td; break

    pe_mv = None; dv = None; n_pe = 0
    if df is not None and not df.empty:
        df["in_basket"] = df["ts_code"].isin(set(codes))
        df_sub = df[df["in_basket"]].copy()
        df_sub["total_mv"] = pd.to_numeric(df_sub["total_mv"],errors="coerce").fillna(0)
        df_sub["pe_ttm"] = pd.to_numeric(df_sub["pe_ttm"],errors="coerce").fillna(0)
        df_sub["dv_ttm"] = pd.to_numeric(df_sub["dv_ttm"],errors="coerce").fillna(0)
        df_sub["weight"] = df_sub["ts_code"].map(weights).fillna(0)

        df_pe = df_sub[(df_sub["pe_ttm"]>0)&(df_sub["pe_ttm"]<200)&(df_sub["total_mv"]>0)]
        n_pe = len(df_pe)
        if n_pe > 0:
            df_pe["np"] = df_pe["total_mv"] / df_pe["pe_ttm"]
            pe_mv = df_pe["total_mv"].sum() / df_pe["np"].sum()

        df_dv = df_sub[(df_sub["dv_ttm"]>0)&(df_sub["weight"]>0)]
        if len(df_dv) > 0:
            ws = df_dv["weight"].sum()
            dv = (df_dv["dv_ttm"]*df_dv["weight"]).sum()/ws

    fcf_ev_list = [(s.get("fcf_yield",0), s.get("weight",0)) for s in basket if s.get("fcf_yield") and s.get("weight")]
    fcf_ev = sum(f*w for f,w in fcf_ev_list)/sum(w for _,w in fcf_ev_list)*100 if fcf_ev_list else None

    hs_pe = None
    for offset in [0, -1, -2, -3, +1]:
        try_date = str(int(actual_td) + offset)
        if try_date in hs_map:
            hs_pe = hs_map[try_date]; break

    ret = ret_map.get(date_str)
    rows.append({"date":date_str,"n":len(basket),"PE":round(pe_mv,2) if pe_mv else None,
                 "FCF_EV":round(fcf_ev,2) if fcf_ev else None,"DV":round(dv,2) if dv else None,
                 "HS300":round(hs_pe,2) if hs_pe else None,"ret":round(ret,1) if ret is not None else None})
    
    pe_s = f"{pe_mv:.1f}x" if pe_mv else "N/A"; ret_s = f"{ret:+.1f}%" if ret is not None else "—"
    print(f"  {date_str}: PE={pe_s} FCF/EV={fcf_ev:.1f}% ret={ret_s}" if fcf_ev else f"  {date_str}: PE={pe_s} ret={ret_s}")

elapsed = time.time() - t0
df_out = pd.DataFrame(rows).sort_values("date")

print(f"\n{'='*95}")
print(f"【HS300 FCF D版 逐期 — {len(rows)}期, {elapsed:.1f}s】")
print(f"{'='*95}")
print(f"{'日期':<12} {'PE':>7} {'FCF/EV':>7} {'DV':>6} {'HS300':>7} {'收益':>8} 判")
for _, r in df_out.iterrows():
    pe = f"{r['PE']:.1f}x" if pd.notna(r['PE']) else " N/A"
    fev = f"{r['FCF_EV']:.1f}%" if pd.notna(r['FCF_EV']) else " N/A"
    dv = f"{r['DV']:.1f}%" if pd.notna(r['DV']) else " N/A"
    hs = f"{r['HS300']:.1f}x" if pd.notna(r['HS300']) else " N/A"
    ret = f"{r['ret']:+.1f}%" if pd.notna(r['ret']) else " —"
    flag = "🟢" if pd.notna(r['ret']) and r['ret']>3 else ("🔴" if pd.notna(r['ret']) and r['ret']<-3 else "🟡")
    print(f"{r['date']:<12} {pe:>7} {fev:>7} {dv:>6} {hs:>7} {ret:>8} {flag}")

all_pe = [r["PE"] for r in rows if r["PE"]]
all_fcf = [r["FCF_EV"] for r in rows if r["FCF_EV"]]
all_ret = [r["ret"] for r in rows if r["ret"] is not None]

print(f"\nPE(MV/NP): 均值{np.mean(all_pe):.1f}x 最低{np.min(all_pe):.1f}x 最高{np.max(all_pe):.1f}x")
print(f"FCF/EV:    均值{np.mean(all_fcf):.1f}% 最低{np.min(all_fcf):.1f}% 最高{np.max(all_fcf):.1f}%")

print(f"\n【PE区间→收益】")
bins = [(0,8,"≤8x"),(8,10,"8-10x"),(10,12,"10-12x"),(12,15,"12-15x"),(15,20,"15-20x"),(20,99,">20x")]
for lo,hi,label in bins:
    sub = [(r["PE"],r["ret"]) for r in rows if r["PE"] and r["ret"] and lo<=r["PE"]<hi]
    if sub:
        rets=[s[1] for s in sub]
        print(f"  {label:<8} {len(sub):>4}期 胜率{sum(1 for x in rets if x>0)/len(rets)*100:.0f}%  均值{np.mean(rets):+.1f}%")

# ── 最新期详细 ──
latest_date = REBALANCE_DATES[-1]
print(f"\n{'='*95}")
print(f"【最新期 {latest_date} 全部持仓】")
stocks = all_baskets.get(latest_date, [])
codes = [s["ts_code"] for s in stocks]

# 拉最新行情
df_now = None
for td in ["20260615","20260612","20260611"]:
    df_now = pro.daily_basic(trade_date=td, fields="ts_code,pe_ttm,total_mv,dv_ttm")
    if df_now is not None and not df_now.empty: break

srows = []
for s in stocks:
    c = s["ts_code"]; w = s.get("weight",0)*100; f = s.get("fcf_yield",0)*100
    pq = s.get("profit_quality",0)
    mr = df_now[df_now["ts_code"]==c] if df_now is not None else None
    pe = float(mr.iloc[0]["pe_ttm"]) if mr is not None and len(mr)>0 and pd.notna(mr.iloc[0]["pe_ttm"]) else None
    dv = float(mr.iloc[0]["dv_ttm"]) if mr is not None and len(mr)>0 and pd.notna(mr.iloc[0]["dv_ttm"]) else None
    srows.append((s.get("name","?"),c,w,f,pe,pq,dv))
srows.sort(key=lambda x: x[2], reverse=True)

print(f"{'#':>3} {'名称':<8} {'代码':<12} {'权重':>5} {'FCF/EV':>7} {'PE':>7} {'DV':>5} {'PQ':>5}")
for i,(n,c,w,f,pe,pq,dv) in enumerate(srows,1):
    pe_s = f'{pe:.1f}x' if pe else ' N/A'; dv_s = f'{dv:.1f}%' if dv else ' N/A'
    print(f'{i:>3} {n:<8} {c:<12} {w:>4.1f}% {f:>6.1f}% {pe_s:>7} {dv_s:>5} {pq:>.2f}')
