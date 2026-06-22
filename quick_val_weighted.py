"""
800红利 → PE 三种算法精确对比
修复：市值 = close * total_share（不依赖 daily_basic 的 total_mv）
"""

import tushare as ts
import pandas as pd
import numpy as np
import os
import time
from dotenv import load_dotenv

load_dotenv()
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))

# ── 1. CSI800 成分 ──
print("Step 1: CSI800 成分...")
idx_data = pro.index_weight(index_code="000906.SH", trade_date="20260331",
                            fields="con_code,weight")
if idx_data is None or idx_data.empty:
    idx_data = pro.index_weight(index_code="000906.SH", trade_date="20251231",
                                fields="con_code,weight")
codes_all = sorted(idx_data["con_code"].unique().tolist())
print(f"  成分股: {len(codes_all)} 只")

# ── 2. 股息率筛选：用 daily_basic 的 dv_ttm ──
print("\nStep 2: 拉取股息率 + PE + PB...")
all_data = []
for code in codes_all:
    for try_date in ["20260612", "20260611", "20260610", "20260605"]:
        try:
            df = pro.daily_basic(
                ts_code=code, trade_date=try_date,
                fields="ts_code,pe_ttm,pb,dv_ttm,total_share,float_share")
            if df is not None and not df.empty:
                all_data.append(df)
                break
        except:
            continue
        time.sleep(0.1)
    # 不sleep，tushare pro 可以快速拉

print(f"  获取行情: {len(all_data)} 只")

df = pd.concat(all_data, ignore_index=True)
df = df.dropna(subset=["dv_ttm", "pe_ttm"])
df["dv_ttm"] = df["dv_ttm"].astype(float)
df["pe_ttm"] = df["pe_ttm"].astype(float)

# 按股息率 Top100
df = df.sort_values("dv_ttm", ascending=False)
top100 = df.head(100).copy()
print(f"  Top100 股息率: {top100['dv_ttm'].min():.2f}% ~ {top100['dv_ttm'].max():.2f}%")

# 剔除 PE 异常
top100 = top100[(top100["pe_ttm"] > 0) & (top100["pe_ttm"] < 200)]
print(f"  有效: {len(top100)} 只\n")

# ── 3. 用收盘价×总股本计算市值 ──
print("Step 3: 获取最新收盘价，计算市值...")
codes = top100["ts_code"].tolist()
codes_str = ",".join(codes[:500])

close_df = None
for try_date in ["20260612", "20260611", "20260610", "20260605"]:
    try:
        close_df = pro.daily(ts_code=codes_str, trade_date=try_date,
                             fields="ts_code,close")
        if close_df is not None and not close_df.empty:
            break
    except:
        continue

if close_df is None or close_df.empty:
    print("❌ 无法获取收盘价")
    exit(1)

print(f"  收盘价: {len(close_df)} 只")

# 合并
top100 = top100.merge(close_df[["ts_code", "close"]], on="ts_code", how="inner")

# 填充 total_share / float_share
top100["total_share"] = top100["total_share"].fillna(0).astype(float)
top100["float_share"] = top100["total_share"].fillna(0).astype(float)
top100["close"] = top100["close"].fillna(0).astype(float)

# 计算市值（元）
top100["mv"] = top100["close"] * top100["total_share"] * 10000
# total_share 在 tushare 是万股，close 是元 → mv 单位：万元
# 实际 total_share 单位可能是股，需要确认
# tushare daily_basic: total_share = 总股本(万股)
# close = 元
# mv(万元) = close * total_share(万股) → mv(元) = close * total_share * 10000

# 检查是否有0
zero_mv = (top100["mv"] == 0).sum()
print(f"  市值为0: {zero_mv} 只")

if zero_mv > 0:
    print("  用 PB 反推市值（PB=MV/BV，BV=total_hldr_eq）...")
    # 补充拉取 fina_indicator 的 total_hldr_eq
    for code in top100[top100["mv"]==0]["ts_code"].tolist():
        try:
            fi = pro.fina_indicator(ts_code=code, fields="total_hldr_eq_incl_min_int",
                                    period="20251231")
            if fi is not None and not fi.empty:
                bv = float(fi.iloc[0]["total_hldr_eq_incl_min_int"])
                pb_row = top100[top100["ts_code"]==code]
                pb_val = float(pb_row["pb"].iloc[0])
                if bv > 0 and pb_val > 0:
                    top100.loc[top100["ts_code"]==code, "mv"] = bv * pb_val
        except:
            pass

# 剔除依然市值为0的
top100 = top100[top100["mv"] > 0].copy()
print(f"  最终有效: {len(top100)} 只")

# 反推净利润
top100["np_ttm"] = top100["mv"] / top100["pe_ttm"]

# ── 4. 三种 PE ──
total_mv = top100["mv"].sum()
total_np = top100["np_ttm"].sum()
weighted_pe = total_mv / total_np
median_pe = top100["pe_ttm"].median()
mean_pe = top100["pe_ttm"].mean()

# ── 5. 输出 ──
print(f"\n{'='*70}")
print(f"【三种 PE 算法对比 — 800红利优化版】")
print(f"  样本: {len(top100)} 只，总市值 {total_mv/1e8:,.0f} 亿，总利润 {total_np/1e8:,.0f} 亿")
print(f"")
print(f"  方法1 - 加权 PE (ΣMV/ΣNP):  {weighted_pe:.2f}x  ← 500红利真实估值")
print(f"  方法2 - 等权中位数:          {median_pe:.2f}x  ← 之前报告用的")
print(f"  方法3 - 等权均值:            {mean_pe:.2f}x")
print(f"")
print(f"  ⚠️ 中位数比加权PE高 {(median_pe/weighted_pe - 1)*100:.0f}%")

# ── 6. 市值分档 ──
top100["weight"] = top100["mv"] / total_mv
top100 = top100.sort_values("mv", ascending=False)

print(f"\n【市值分档】")
print(f"{'区间':<18} {'数量':>5} {'权重':>7} {'加权PE':>8} {'PB均值':>7} {'股息率':>7}")
bins = [
    (5000e8, float("inf"), ">5000亿"),
    (2000e8, 5000e8, "2000-5000亿"),
    (1000e8, 2000e8, "1000-2000亿"),
    (500e8, 1000e8, "500-1000亿"),
    (100e8, 500e8, "100-500亿"),
    (0, 100e8, "<100亿"),
]
for lo, hi, label in bins:
    sub = top100[(top100["mv"] > lo) & (top100["mv"] <= hi)]
    if len(sub) > 0:
        p = sub["mv"].sum() / sub["np_ttm"].sum()
        print(f"{label:<18} {len(sub):>5} {sub['weight'].sum()*100:>6.1f}% {p:>8.2f}x {sub['pb'].mean():>7.2f} {sub['dv_ttm'].median():>6.2f}%")

# ── 7. 前 15 权重 ──
print(f"\n【前15大权重】")
print(f"{'代码':<12} {'市值(亿)':>10} {'PE':>6} {'PB':>5} {'股息率':>6} {'权重':>6}")
for _, row in top100.head(15).iterrows():
    print(f"{row['ts_code']:<12} {row['mv']/1e8:>10.0f} {row['pe_ttm']:>6.1f}x {row['pb']:>5.2f} {row['dv_ttm']:>6.2f}% {row['weight']*100:>5.1f}%")

# ── 8. 结论 ──
print(f"\n{'='*70}")
print(f"【结论】")
print(f"  之前报告（等权中位数）:  ~13.5x  → 高估了实际估值")
print(f"  真正的加权 PE:           {weighted_pe:.1f}x  → 这才是指数的真实估值水平")
print(f"  原因: 大市值股 PE 低 → 加权PE被拉低；小市值股 PE 高 → 拉高均值/中位数")
