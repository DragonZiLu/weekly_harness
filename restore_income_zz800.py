#!/usr/bin/env python3
"""
restore_income_zz800.py — 只恢复 ZZ800 成分股的 income 年报(1231)数据

受影响年份: 2015-2022, 2024 (income_{year}.csv 缺少1231年报行)
目标: ~1955只ZZ800成分股 × 9年 ≈ ~16000次API ≈ ~16min
"""
import sys, os, time
import pandas as pd
import tushare as ts
from pathlib import Path
from dotenv import load_dotenv

PROJ = Path(__file__).parent
DATA_DIR = PROJ / "data" / "fcf_financials"
IW_DIR = PROJ / "data" / "index_weights"

load_dotenv(PROJ / ".env")
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))

BAD_YEARS = [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2024]

def main():
    # ZZ800成分股
    iw = pd.read_csv(IW_DIR / "index_weight_000906.SH.csv", dtype={"con_code": str})
    zz800_codes = sorted(iw["con_code"].unique())
    print(f"ZZ800历史成分股: {len(zz800_codes)}只")
    
    total_api_calls = len(zz800_codes) * len(BAD_YEARS)
    print(f"预计API调用: {total_api_calls}次 ≈ {total_api_calls * 0.06 / 60:.1f}min")
    
    for year in BAD_YEARS:
        fpath = DATA_DIR / f"income_{year}.csv"
        print(f"\n--- {year}年 ---")
        
        # 加载现有数据(季报,无1231)
        existing = pd.read_csv(fpath, dtype={"ts_code": str, "end_date": str, "ann_date": str})
        has_1231 = (existing["end_date"].astype(str).str[:8] == f"{year}1231").sum()
        print(f"  现有: {len(existing)}行, 1231={has_1231}")
        
        if has_1231 > 0:
            print(f"  ✅ 已有年报,跳过")
            continue
        
        # 逐只下载ZZ800成分股的income年报
        rows = []
        err = 0
        done = 0
        
        for code in zz800_codes:
            try:
                df = pro.income(ts_code=code, period=f"{year}1231",
                               fields="ts_code,ann_date,end_date,operate_profit")
                time.sleep(0.06)
                if df is not None and not df.empty:
                    m = df[df["end_date"].astype(str).str[:8] == f"{year}1231"]
                    if not m.empty:
                        rows.append(m)
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(zz800_codes)} ({done*100//len(zz800_codes)}%)")
            except Exception as e:
                err += 1
                time.sleep(0.5)
        
        if rows:
            annual_df = pd.concat(rows, ignore_index=True)
            annual_df = annual_df.astype({"ts_code": str, "end_date": str, "ann_date": str})
            print(f"  获取 {len(annual_df)} 只股票年报")
            
            # 合并: 季报 + 年报
            merged = pd.concat([existing, annual_df], ignore_index=True)
            merged.to_csv(fpath, index=False)
            
            # 验证
            v = pd.read_csv(fpath, dtype={"end_date": str})
            now_1231 = (v["end_date"].astype(str).str[:8] == f"{year}1231").sum()
            print(f"  ✅ {len(v)}行, 1231={now_1231}, errors={err}")
        else:
            print(f"  ⚠️ 无数据, errors={err}")
    
    # 最终验证
    print("\n" + "=" * 50)
    print("验证结果:")
    for year in range(2015, 2026):
        f = DATA_DIR / f"income_{year}.csv"
        if not f.exists(): continue
        df = pd.read_csv(f, dtype={"end_date": str})
        n = (df["end_date"].astype(str).str[:8] == f"{year}1231").sum()
        s = "✅" if n > 0 else "❌"
        print(f"  {year} {s}: 1231={n}, total={len(df)}")

if __name__ == "__main__":
    main()