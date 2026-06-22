"""
FCF E版 逐期估值+收益对齐
46期季度调仓，全市场 daily_basic 批量拉取
"""
import json, time, os
from pathlib import Path
import pandas as pd, numpy as np
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")
import tushare as ts

pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
ROOT = Path(__file__).resolve().parent

BASKET_FILE = ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "all_baskets_2015_2026.json"
NAV_FILE = ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "backtest_nav_tr.csv"

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

# 加载E版收益
nav = pd.read_csv(NAV_FILE)
ret_map = dict(zip(nav["rb_date"], nav["period_ret"]))

# HS300 PE
print("HS300 PE...")
hs = pro.index_dailybasic(ts_code="000300.SH", start_date="20150301", end_date="20260614", fields="trade_date,pe")
hs_map = dict(zip(hs["trade_date"].astype(str), hs["pe"].astype(float)))

rows = []
t0 = time.time()

for date_str in REBALANCE_DATES:
    basket = all_baskets.get(date_str, [])
    if not basket:
        print(f"  {date_str}: 空篮子 跳过")
        continue

    codes = [s["ts_code"] for s in basket]
    weights = {s["ts_code"]: s.get("weight", 1.0/len(basket)) for s in basket}
    td = date_str.replace("-", "")

    # 全市场拉取（休市回退）
    df = None
    actual_td = td
    for offset in [0, -1, +1, -2, +2, -3, +3]:
        try_td = str(int(td) + offset)
        df = pro.daily_basic(trade_date=try_td, fields="ts_code,total_mv,pe_ttm,dv_ttm")
        if df is not None and not df.empty:
            actual_td = try_td
            break

    if df is None or df.empty:
        pe_mv = None
        n_pe = 0
        dv = None
    else:
        df["in_basket"] = df["ts_code"].isin(set(codes))
        df_sub = df[df["in_basket"]].copy()
        df_sub["total_mv"] = pd.to_numeric(df_sub["total_mv"], errors="coerce").fillna(0)
        df_sub["pe_ttm"] = pd.to_numeric(df_sub["pe_ttm"], errors="coerce").fillna(0)
        df_sub["dv_ttm"] = pd.to_numeric(df_sub["dv_ttm"], errors="coerce").fillna(0)
        df_sub["weight"] = df_sub["ts_code"].map(weights).fillna(0)

        df_pe = df_sub[(df_sub["pe_ttm"] > 0) & (df_sub["pe_ttm"] < 200) & (df_sub["total_mv"] > 0)].copy()
        n_pe = len(df_pe)
        pe_mv = None
        if n_pe > 0:
            df_pe["np"] = df_pe["total_mv"] / df_pe["pe_ttm"]
            pe_mv = df_pe["total_mv"].sum() / df_pe["np"].sum() if df_pe["np"].sum() > 0 else None

        df_w = df_sub[(df_sub["pe_ttm"] > 0) & (df_sub["pe_ttm"] < 200) & (df_sub["weight"] > 0)].copy()
        pe_w = None
        if len(df_w) > 0:
            ws = df_w["weight"].sum()
            pe_w = (df_w["pe_ttm"] * df_w["weight"]).sum() / ws if ws > 0 else None

        df_dv = df_sub[(df_sub["dv_ttm"] > 0) & (df_sub["weight"] > 0)].copy()
        dv = None
        if len(df_dv) > 0:
            ws = df_dv["weight"].sum()
            dv = (df_dv["dv_ttm"] * df_dv["weight"]).sum() / ws if ws > 0 else None

    # HS300 PE
    hs_pe = None
    for offset in [0, -1, -2, -3, +1]:
        try_date = str(int(actual_td) + offset)
        if try_date in hs_map:
            hs_pe = hs_map[try_date]
            break

    # 收益
    ret = ret_map.get(date_str)

    # 加权 FCF/EV（篮子自带 fcf_yield + weight）
    fcf_ev = None
    fcf_vals = [(s.get("fcf_yield", 0), s.get("weight", 0)) for s in basket if s.get("fcf_yield") and s.get("weight")]
    if fcf_vals:
        w_sum = sum(w for _, w in fcf_vals)
        fcf_ev = sum(f * w for f, w in fcf_vals) / w_sum * 100 if w_sum > 0 else None  # → %

    rows.append({
        "调仓日": date_str, "持仓": len(basket),
        "PE_MV": round(pe_mv, 2) if pe_mv else None,
        "PE_w": round(pe_w, 2) if pe_w else None,
        "DV": round(dv, 2) if dv else None,
        "FCF_EV": round(fcf_ev, 2) if fcf_ev else None,
        "HS300": round(hs_pe, 2) if hs_pe else None,
        "收益": round(ret, 1) if ret is not None else None,
    })

    pe_s = f"{pe_mv:.1f}x" if pe_mv else "N/A"
    ret_s = f"{ret:+.1f}%" if ret is not None else "—"
    print(f"  {date_str}: PE={pe_s} DV={dv:.1f}% ret={ret_s} ({n_pe}/{len(basket)})" if dv else f"  {date_str}: PE={pe_s} DV=N/A ret={ret_s}")

elapsed = time.time() - t0
df_out = pd.DataFrame(rows)

# ── 输出 ──
print(f"\n{'='*100}")
print(f"【FCF E版 估值+收益对齐 — {len(rows)}期, {elapsed:.1f}s】")
print(f"{'='*100}")
print(f"{'调仓日':<12} {'PE(MV)':>7} {'FCF/EV':>7} {'DV':>6} {'HS300':>7} {'收益':>8} 判")
print("-" * 100)

for _, r in df_out.iterrows():
    mv = f"{r['PE_MV']:.1f}x" if pd.notna(r['PE_MV']) else "  N/A"
    fev = f"{r['FCF_EV']:.1f}%" if pd.notna(r['FCF_EV']) else " N/A"
    dv = f"{r['DV']:.1f}%" if pd.notna(r['DV']) else " N/A"
    hs = f"{r['HS300']:.1f}x" if pd.notna(r['HS300']) else " N/A"
    ret = f"{r['收益']:+.1f}%" if pd.notna(r['收益']) else "   —"
    if pd.notna(r['收益']):
        flag = "🟢" if r['收益'] > 3 else ("🔴" if r['收益'] < -3 else "🟡")
    else:
        flag = "⏳"
    print(f"{r['调仓日']:<12} {mv:>7} {fev:>7} {dv:>6} {hs:>7} {ret:>8} {flag}")

# 统计
all_pe = [r["PE_MV"] for r in rows if r["PE_MV"] is not None]
all_ret = [r["收益"] for r in rows if r["收益"] is not None]
all_dv = [r["DV"] for r in rows if r["DV"] is not None]
all_fcf = [r["FCF_EV"] for r in rows if r["FCF_EV"] is not None]

print(f"\n【统计】{len(rows)}期中{len(all_pe)}期有PE, {len(all_ret)}期有收益")
if all_pe:
    print(f"  PE(MV/NP):  均值 {np.mean(all_pe):.1f}x | 最低 {np.min(all_pe):.1f}x | 最高 {np.max(all_pe):.1f}x")
if all_fcf:
    print(f"  FCF/EV:     均值 {np.mean(all_fcf):.1f}% | 最低 {np.min(all_fcf):.1f}% | 最高 {np.max(all_fcf):.1f}%")
if all_dv:
    print(f"  股息率:     均值 {np.mean(all_dv):.1f}% | 最低 {np.min(all_dv):.1f}% | 最高 {np.max(all_dv):.1f}%")

# 按PE区间的收益分布
print(f"\n【PE估值区间 → 季度收益分布】")
print(f"{'PE区间':<12} {'期数':>4} {'正收益比':>8} {'平均收益':>8} {'最大收益':>8} {'最小收益':>8}")
bins = [
    (0, 8, "≤ 8x"),
    (8, 10, "8-10x"),
    (10, 12, "10-12x"),
    (12, 15, "12-15x"),
    (15, 20, "15-20x"),
    (20, 50, "20-50x"),
    (50, 999, "> 50x"),
]
for lo, hi, label in bins:
    subset = [(r["PE_MV"], r["收益"]) for r in rows if r["PE_MV"] is not None and r["收益"] is not None and lo <= r["PE_MV"] < hi]
    if subset:
        rets = [s[1] for s in subset]
        pos_pct = sum(1 for x in rets if x > 0) / len(rets) * 100
        print(f"{label:<12} {len(subset):>4} {pos_pct:>7.0f}% {np.mean(rets):>+7.1f}% {np.max(rets):>+7.1f}% {np.min(rets):>+7.1f}%")

# 按 FCF/EV 区间的收益分布
print(f"\n【FCF/EV区间 → 季度收益分布】")
print(f"{'FCF/EV':<12} {'期数':>4} {'正收益比':>8} {'平均收益':>8}")
bins_fcf = [
    (0, 5, "< 5%"),
    (5, 8, "5-8%"),
    (8, 12, "8-12%"),
    (12, 20, "12-20%"),
    (20, 99, "> 20%"),
]
for lo, hi, label in bins_fcf:
    subset = [(r["FCF_EV"], r["收益"]) for r in rows if r["FCF_EV"] is not None and r["收益"] is not None and lo <= r["FCF_EV"] < hi]
    if subset:
        rets = [s[1] for s in subset]
        pos_pct = sum(1 for x in rets if x > 0) / len(rets) * 100
        print(f"{label:<12} {len(subset):>4} {pos_pct:>7.0f}% {np.mean(rets):>+7.1f}%")
