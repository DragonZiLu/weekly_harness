"""
800红利 逐期估值 — 快速版（22次API，全市场一次性拉取）
加权PE: ΣMV/ΣNP + Σ(w×PE) 双方法
"""
import json, time, os
from pathlib import Path
import pandas as pd, numpy as np
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")
import tushare as ts

pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
ROOT = Path(__file__).resolve().parent

BASKET_FILE = ROOT / "output" / "800div" / "all_baskets_2015_2026.json"
REBALANCE_DATES = [
    "2015-06-15","2015-12-14","2016-06-13","2016-12-12",
    "2017-06-12","2017-12-11","2018-06-11","2018-12-17",
    "2019-06-17","2019-12-16","2020-06-15","2020-12-14",
    "2021-06-14","2021-12-13","2022-06-13","2022-12-12",
    "2023-06-12","2023-12-11","2024-06-17","2024-12-16",
    "2025-06-16","2025-12-15","2026-06-15",
]

with open(BASKET_FILE) as f:
    all_baskets = json.load(f)

# 沪深300 PE
print("HS300 PE...")
hs = pro.index_dailybasic(ts_code="000300.SH", start_date="20150601", end_date="20260614", fields="trade_date,pe")
hs_map = dict(zip(hs["trade_date"].astype(str), hs["pe"].astype(float)))

rows = []
t0 = time.time()

for date_str in REBALANCE_DATES:
    basket = all_baskets.get(date_str, [])
    if not basket:
        continue
    codes_set = {s["ts_code"] for s in basket}
    weights = {s["ts_code"]: s.get("weight", 1.0/len(basket)) for s in basket}
    td = date_str.replace("-", "")

    # 一次拉全市场，若当天休市则回退找最近交易日
    df = None
    actual_td = td
    for offset in [0, -1, +1, -2, +2, -3, +3]:
        try_td = str(int(td) + offset)
        df = pro.daily_basic(trade_date=try_td, fields="ts_code,total_mv,pe_ttm,dv_ttm")
        if df is not None and not df.empty:
            actual_td = try_td
            break

    if df is None or df.empty:
        print(f"  {date_str}: 跳过 (无数据)")
        continue

    # 仅保留持仓中的股票
    df["in_basket"] = df["ts_code"].isin(codes_set)
    df_sub = df[df["in_basket"]].copy()
    n_found = len(df_sub)

    df_sub["total_mv"] = pd.to_numeric(df_sub["total_mv"], errors="coerce").fillna(0)
    df_sub["pe_ttm"] = pd.to_numeric(df_sub["pe_ttm"], errors="coerce").fillna(0)
    df_sub["dv_ttm"] = pd.to_numeric(df_sub["dv_ttm"], errors="coerce").fillna(0)
    df_sub["weight"] = df_sub["ts_code"].map(weights).fillna(0)

    # 方法1: ΣMV/ΣNP
    df_pe = df_sub[(df_sub["pe_ttm"] > 0) & (df_sub["pe_ttm"] < 200) & (df_sub["total_mv"] > 0)].copy()
    n_pe = len(df_pe)
    pe_mv = None
    if n_pe > 0:
        df_pe["np"] = df_pe["total_mv"] / df_pe["pe_ttm"]
        pe_mv = df_pe["total_mv"].sum() / df_pe["np"].sum() if df_pe["np"].sum() > 0 else None

    # 方法2: Σ(w_i × PE_i)
    df_w = df_sub[(df_sub["pe_ttm"] > 0) & (df_sub["pe_ttm"] < 200) & (df_sub["weight"] > 0)].copy()
    pe_w = None
    if len(df_w) > 0:
        ws = df_w["weight"].sum()
        pe_w = (df_w["pe_ttm"] * df_w["weight"]).sum() / ws if ws > 0 else None

    # 加权股息率
    df_dv = df_sub[(df_sub["dv_ttm"] > 0) & (df_sub["weight"] > 0)].copy()
    n_dv = len(df_dv)
    dv = None
    if n_dv > 0:
        ws = df_dv["weight"].sum()
        dv = (df_dv["dv_ttm"] * df_dv["weight"]).sum() / ws if ws > 0 else None

    # HS300 PE — 用实际交易日期
    hs_pe = None
    for offset in [0, -1, -2, -3, +1]:
        try_date = str(int(actual_td) + offset)
        if try_date in hs_map:
            hs_pe = hs_map[try_date]
            break

    rows.append({
        "调仓日": date_str, "持仓": len(basket), "命中PE": n_pe,
        "PE_ΣMV": round(pe_mv, 2) if pe_mv else None,
        "PE_Σw": round(pe_w, 2) if pe_w else None,
        "股息率": round(dv, 2) if dv else None,
        "命中DV": n_dv, "HS300_PE": round(hs_pe, 2) if hs_pe else None,
    })

    pe_mv_s = f"{pe_mv:.1f}x" if pe_mv else "N/A"
    pe_w_s = f"{pe_w:.1f}x" if pe_w else "N/A"
    dv_s = f"{dv:.1f}%" if dv else "N/A"
    hs_s = f"{hs_pe:.1f}x" if hs_pe else "N/A"
    print(f"  {date_str}: PE(MV/NP)={pe_mv_s} PE(w×PE)={pe_w_s} DV={dv_s} HS300={hs_s} ({n_pe}/{n_dv}/{len(basket)})")

elapsed = time.time() - t0

# ── 输出 ──
df_out = pd.DataFrame(rows).sort_values("调仓日")
print(f"\n{'='*100}")
print(f"【800红利 逐期估值 — 22次API, {elapsed:.1f}s】")
print(f"{'='*100}")
print(f"{'调仓日':<12} {'持仓':>4} {'PE_MV/NP':>8} {'PE_w×PE':>8} {'股息率':>8} {'HS300':>8} {'PE命中':>6} {'DV命中':>6}")
print("-" * 100)
for _, r in df_out.iterrows():
    mv = f"{r['PE_ΣMV']:.1f}x" if pd.notna(r['PE_ΣMV']) else "  N/A"
    w = f"{r['PE_Σw']:.1f}x" if pd.notna(r['PE_Σw']) else "  N/A"
    dv = f"{r['股息率']:.1f}%" if pd.notna(r['股息率']) else "  N/A"
    hs = f"{r['HS300_PE']:.1f}x" if pd.notna(r['HS300_PE']) else "  N/A"
    print(f"{r['调仓日']:<12} {int(r['持仓']):>4} {mv:>8} {w:>8} {dv:>8} {hs:>8} {int(r['命中PE']):>5}/{int(r['持仓'])} {int(r['命中DV']):>4}/{int(r['持仓'])}")

# 统计
all_mv = [r["PE_ΣMV"] for r in rows if r["PE_ΣMV"] is not None]
all_w = [r["PE_Σw"] for r in rows if r["PE_Σw"] is not None]
all_dv = [r["股息率"] for r in rows if r["股息率"] is not None]
all_hs = [r["HS300_PE"] for r in rows if r["HS300_PE"] is not None]

print(f"\n【统计】{len(rows)}期中有{len(all_mv)}期有PE数据")
if all_mv:
    print(f"  PE(ΣMV/ΣNP): 均值 {np.mean(all_mv):.1f}x | 最低 {np.min(all_mv):.1f}x | 最高 {np.max(all_mv):.1f}x")
if all_w:
    print(f"  PE(Σw×PE):   均值 {np.mean(all_w):.1f}x | 最低 {np.min(all_w):.1f}x | 最高 {np.max(all_w):.1f}x")
    if all_mv:
        g = [w/m for w,m in zip(all_w,all_mv)]
        print(f"  Σw×PE / ΣMV/ΣNP = {np.mean(g)*100:.0f}% (w×PE 高 {np.mean(g)*100-100:.0f}%)")
if all_dv:
    print(f"  加权股息率:   均值 {np.mean(all_dv):.1f}% | 最低 {np.min(all_dv):.1f}% | 最高 {np.max(all_dv):.1f}%")
if all_hs:
    print(f"  沪深300 PE:   均值 {np.mean(all_hs):.1f}x | 最低 {np.min(all_hs):.1f}x | 最高 {np.max(all_hs):.1f}x")
