#!/usr/bin/env python3
"""restore_income_2024.py — 只恢复2024年income年报"""
import sys, os, time, pandas as pd, tushare as ts
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))
DATA_DIR = Path(__file__).parent / "data" / "fcf_financials"

iw = pd.read_csv(DATA_DIR.parent / "index_weights" / "index_weight_000906.SH.csv", dtype={"con_code": str})
codes = sorted(iw["con_code"].unique())
print(f"ZZ800: {len(codes)}只 → 下载2024年报")

rows, err, done = [], 0, 0
for code in codes:
    try:
        df = pro.income(ts_code=code, period="20241231",
                       fields="ts_code,ann_date,end_date,operate_profit")
        time.sleep(0.06)
        if df is not None and not df.empty:
            m = df[df["end_date"].astype(str).str[:8] == "20241231"]
            if not m.empty: rows.append(m)
        done += 1
        if done % 200 == 0: print(f"  {done}/{len(codes)}")
    except:
        err += 1; time.sleep(0.5)

print(f"获取 {len(rows)} 只年报, errors={err}")
if rows:
    annual = pd.concat(rows, ignore_index=True)
    annual = annual.astype({"ts_code": str, "end_date": str, "ann_date": str})
    existing = pd.read_csv(DATA_DIR / "income_2024.csv", dtype={"ts_code": str, "end_date": str, "ann_date": str})
    merged = pd.concat([existing, annual], ignore_index=True)
    merged.to_csv(DATA_DIR / "income_2024.csv", index=False)
    v = pd.read_csv(DATA_DIR / "income_2024.csv", dtype={"end_date": str})
    n = (v["end_date"].astype(str).str[:8] == "20241231").sum()
    print(f"✅ 2024: total={len(v)}, 1231={n}")
else:
    print("⚠️ 无数据")