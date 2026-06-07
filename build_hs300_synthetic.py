#!/usr/bin/env python3
"""
build_hs300_synthetic.py — 从 ZZ800 成分股中取市值 Top 300 近似 HS300 历史成分
===========================================================================
ZZ800 = HS300(300只) + CSI500(500只)
HS300 是大市值端，从 ZZ800 全集中按市值排序取前300是合理近似。
"""
import sys, os, time
from pathlib import Path
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
import tushare as ts

pro = ts.pro_api()
DATA_DIR = PROJECT_ROOT / "data"
IDX_DIR = DATA_DIR / "index_weights"
DAILY_DIR = DATA_DIR / "fcf_financials" / "daily_basic_cache"

# 1. 加载 ZZ800 成分股数据
zz800 = pd.read_csv(IDX_DIR / "index_weight_000906.SH.csv", dtype={"trade_date": str, "con_code": str})
dates = sorted(zz800["trade_date"].unique())
print(f"ZZ800: {len(zz800)} rows, {len(dates)} trade_dates")
print(f"  范围: {dates[0]} ~ {dates[-1]}")

# 2. 对每个日期，获取 ZZ800 全部成分股的市值，取 Top 300
all_hs300_rows = []

for i, dt in enumerate(dates):
    # 获取当日 ZZ800 成分股
    members = zz800[zz800["trade_date"] == dt]["con_code"].unique()
    
    # 从 daily_basic 缓存获取市值
    fname = DAILY_DIR / f"daily_basic_{dt}.csv"
    if not fname.exists():
        print(f"  ⚠️ {dt}: daily_basic 文件缺失，跳过")
        continue
    
    basic = pd.read_csv(fname, dtype={"ts_code": str})
    basic = basic[basic["ts_code"].isin(members)].copy()
    
    if len(basic) < 300:
        # 用最近可用日期补充
        found = False
        for offset in range(1, 30):
            prev_dt = pd.to_datetime(dt) - pd.Timedelta(days=offset)
            prev_dt_str = prev_dt.strftime("%Y%m%d")
            pf = DAILY_DIR / f"daily_basic_{prev_dt_str}.csv"
            if pf.exists():
                prev_basic = pd.read_csv(pf, dtype={"ts_code": str})
                prev_basic = prev_basic[prev_basic["ts_code"].isin(members)]
                if len(prev_basic) >= 300:
                    basic = prev_basic.copy()
                    found = True
                    break
        if not found:
            print(f"  ⚠️ {dt}: 仅有 {len(basic)} 个成分股权重数据")
            if len(basic) == 0:
                continue
    
    # 取 total_mv 前 300
    basic["total_mv"] = pd.to_numeric(basic["total_mv"], errors="coerce")
    top300 = basic.nlargest(300, "total_mv")
    
    # 计算权重（按总市值）
    total_mv_sum = top300["total_mv"].sum()
    for _, row in top300.iterrows():
        all_hs300_rows.append({
            "index_code": "000300.SH",
            "con_code": row["ts_code"],
            "trade_date": dt,
            "weight": row["total_mv"] / total_mv_sum,
        })
    
    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{len(dates)}: {dt} → {len(top300)} stocks")

# 3. 与现有 HS300 缓存合并（保留 2016+ 的真实数据）
synth = pd.DataFrame(all_hs300_rows)
synth["trade_date"] = synth["trade_date"].astype(str)

existing = IDX_DIR / "index_weight_000300.SH.csv"
if existing.exists():
    old = pd.read_csv(existing, dtype={"trade_date": str, "con_code": str})
    # 只保留 2016+ 的真实数据
    old = old[old["trade_date"] >= "20160101"]
    # 合并：合成数据 (2008-2015) + 真实数据 (2016+)
    combined = pd.concat([synth, old], ignore_index=True)
else:
    combined = synth

combined = combined.drop_duplicates(subset=["con_code", "trade_date"])
combined.to_csv(existing, index=False)

print(f"\n✅ HS300 合成权重完成！")
print(f"   合成部分: {len(synth)} 行 ({synth['trade_date'].min()} ~ {synth['trade_date'].max()})")
print(f"   真实部分: {len(old) if existing.exists() else 0} 行 (2016+)")
print(f"   最终文件: {len(combined)} 行")
