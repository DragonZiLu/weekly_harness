"""快速拉取 2026-06-15 E版新篮子 + 估值"""
import json, time, os, sys
from pathlib import Path
import pandas as pd, numpy as np
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")
import tushare as ts

pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "weekly_harness"))

from weekly_harness.fcf_universe import FcfUniverse
from run_bdefx_full import apply_buffer, fcf_weights

DATE = "2026-06-15"
TRADE_DATE = "20260615"  # 实际用 20260612（周五）
TOP_N = 50
BUF_LOW, BUF_HIGH = 30, 70  # E版 ±40%

# ── 1. 跑选股 ──
print(f"Step 1: 运行 FCF 选股 {DATE}...")
uni = FcfUniverse(index_code="000906.SH", use_ttm=True)
uni.preload_all(download=False)

raw = uni.get_fcf_basket(DATE, top_n=800, verbose=False, use_ttm=True)
if not raw:
    print("❌ 选股失败")
    sys.exit(1)

ranked = [dict(v, ts_code=k) for k, v in raw.items()
          if k != "__quality_warnings__" and isinstance(v, dict)]
ranked.sort(key=lambda x: x.get('fcf_yield', 0), reverse=True)
print(f"  排名池: {len(ranked)} 只")

# 加载上期篮子用于缓冲区
prev_file = ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "all_baskets_2015_2026.json"
with open(prev_file) as f:
    prev_all = json.load(f)
prev_dates = sorted(prev_all.keys())
prev_basket = prev_all.get("2026-03-16", [])
prev_codes = {s["ts_code"] for s in prev_basket} if prev_basket else set()
print(f"  上期(2026-03-16): {len(prev_codes)} 只")

# E版 buffer
stocks = [dict(s) for s in apply_buffer(ranked, prev_codes, BUF_LOW, BUF_HIGH, TOP_N)]
fcf_weights(stocks)
print(f"  E版篮子: {len(stocks)} 只")

# ── 2. 拉估值 ──
print(f"\nStep 2: 拉取估值数据...")
codes = [s["ts_code"] for s in stocks]

# 全市场拉取（2026-06-15 无数据，回退到 2026-06-12）
df = None
for td in ["20260615", "20260612", "20260611", "20260610"]:
    df = pro.daily_basic(trade_date=td, fields="ts_code,total_mv,pe_ttm,dv_ttm")
    if df is not None and not df.empty:
        actual_td = td
        break

# HS300
hs = pro.index_dailybasic(ts_code="000300.SH", trade_date=actual_td, fields="trade_date,pe")
hs_pe = float(hs.iloc[0]["pe"]) if hs is not None and not hs.empty else None

# 过滤持仓
df["in_basket"] = df["ts_code"].isin(set(codes))
df_sub = df[df["in_basket"]].copy()
df_sub["total_mv"] = pd.to_numeric(df_sub["total_mv"], errors="coerce").fillna(0)
df_sub["pe_ttm"] = pd.to_numeric(df_sub["pe_ttm"], errors="coerce").fillna(0)
df_sub["dv_ttm"] = pd.to_numeric(df_sub["dv_ttm"], errors="coerce").fillna(0)

# 加权 PE (ΣMV/ΣNP)
df_pe = df_sub[(df_sub["pe_ttm"] > 0) & (df_sub["pe_ttm"] < 200) & (df_sub["total_mv"] > 0)].copy()
n_pe = len(df_pe)
df_pe["np"] = df_pe["total_mv"] / df_pe["pe_ttm"]
pe_mv = df_pe["total_mv"].sum() / df_pe["np"].sum() if df_pe["np"].sum() > 0 else None

# 加权股息率
weights = {s["ts_code"]: s.get("weight", 1.0/len(stocks)) for s in stocks}
df_sub["weight"] = df_sub["ts_code"].map(weights).fillna(0)
df_dv = df_sub[(df_sub["dv_ttm"] > 0) & (df_sub["weight"] > 0)].copy()
dv = (df_dv["dv_ttm"] * df_dv["weight"]).sum() / df_dv["weight"].sum() if len(df_dv) > 0 else None

# 加权 FCF/EV（篮子自带）
fcf_ev_vals = [(s.get("fcf_yield", 0), s.get("weight", 0)) for s in stocks if s.get("fcf_yield") and s.get("weight")]
fcf_ev = sum(f * w for f, w in fcf_ev_vals) / sum(w for _, w in fcf_ev_vals) * 100 if fcf_ev_vals else None

# ── 3. 输出 ──
print(f"\n{'='*60}")
print(f"【E版 2026-06-15 新调仓 — {len(stocks)}只】")
print(f"{'='*60}")
print(f"  PE(MV/NP):  {pe_mv:.1f}x   ({n_pe}/{len(stocks)}只有PE)")
print(f"  FCF/EV:     {fcf_ev:.1f}%")
print(f"  股息率:     {dv:.1f}%")
print(f"  HS300 PE:   {hs_pe:.1f}x")
print(f"  上期(2026-03-16): PE=14.9x FCF/EV=10.2% DV=3.5% HS300=14.8x → 收益 -14.9%")

# 换手率
old_codes = prev_codes
new_codes = {s["ts_code"] for s in stocks}
added = new_codes - old_codes
removed = old_codes - new_codes
kept = old_codes & new_codes
turnover = len(added) / len(stocks) * 100
print(f"\n  换手率: {turnover:.1f}% (新进{len(added)}只, 剔除{len(removed)}只, 保留{len(kept)}只)")

# Top 5
print(f"\n  Top 5 权重:")
for i, s in enumerate(stocks[:5]):
    print(f"    {i+1}. {s.get('name', s['ts_code']):<10} {s['ts_code']:<12} w={s['weight']*100:.1f}% FCF/EV={s.get('fcf_yield',0)*100:.1f}%")

# vs 上期对比
print(f"\n{'='*60}")
print(f"【两期对比】")
print(f"{'指标':<15} {'2026-03-16':>15} {'2026-06-15':>15} {'变化':>10}")
print(f"{'PE':<15} {'14.9x':>15} {f'{pe_mv:.1f}x':>15} {f'{(pe_mv/14.9-1)*100:+.0f}%' if pe_mv else 'N/A':>10}")
print(f"{'FCF/EV':<15} {'10.2%':>15} {f'{fcf_ev:.1f}%':>15} {f'{(fcf_ev/10.2-1)*100:+.0f}%' if fcf_ev else 'N/A':>10}")
print(f"{'股息率':<15} {'3.5%':>15} {f'{dv:.1f}%':>15} {f'{(dv/3.5-1)*100:+.0f}%' if dv else 'N/A':>10}")
print(f"{'HS300 PE':<15} {'14.8x':>15} {f'{hs_pe:.1f}x':>15} {f'{(hs_pe/14.8-1)*100:+.0f}%' if hs_pe else 'N/A':>10}")
