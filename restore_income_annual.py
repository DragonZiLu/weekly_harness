#!/usr/bin/env python3
"""
restore_income_annual.py — 恢复 income 年报数据(1231行)

问题: income_{2015-2022,2024}.csv 被覆盖为季报数据(无1231年报行)
修复: 从tushare逐只下载operate_profit年报, 恢复到这些文件中

策略: 对每个受影响的年份,从tushare获取ZZ800成分股的income年报,
      将1231行合并回income_{year}.csv
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

# 受影响的年份(无1231年报行)
BAD_YEARS = [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2024]

def main():
    print("=" * 60)
    print("恢复 income 年报数据 (1231行)")
    print("=" * 60)
    
    # 获取ZZ800历史成分股 + 全市场codes作为下载目标
    # 先从现有income文件中获取所有ts_code
    all_codes = set()
    for year in BAD_YEARS:
        f = DATA_DIR / f"income_{year}.csv"
        if f.exists():
            df = pd.read_csv(f, dtype={"ts_code": str})
            all_codes.update(df["ts_code"].unique())
    
    # 加上ZZ800成分股(确保全覆盖)
    iw = pd.read_csv(IW_DIR / "index_weight_000906.SH.csv", dtype={"con_code": str})
    all_codes.update(iw["con_code"].unique())
    
    print(f"需要下载的目标: {len(all_codes)} 只股票")
    print(f"受影响年份: {BAD_YEARS}")
    
    # 逐年份恢复
    for year in BAD_YEARS:
        print(f"\n--- 恢复 {year} 年 income 年报 ---")
        fpath = DATA_DIR / f"income_{year}.csv"
        
        # 加载现有数据
        existing = pd.read_csv(fpath, dtype={"ts_code": str, "end_date": str, "ann_date": str})
        existing_codes = set(existing["ts_code"].unique())
        
        # 检查:是否已有1231行
        has_1231 = (existing["end_date"].astype(str).str[:8] == f"{year}1231").sum()
        print(f"  现有数据: {len(existing)}行, 1231行={has_1231}")
        
        if has_1231 > 0:
            print(f"  ✅ 已有年报数据,跳过")
            continue
        
        # 从tushare逐只下载income年报(1231)
        annual_rows = []
        err_count = 0
        done = 0
        
        target_codes = sorted(existing_codes)  # 只下载现有文件中的codes
        
        for code in target_codes:
            try:
                df = pro.income(ts_code=code, period=f"{year}1231",
                               fields="ts_code,ann_date,end_date,operate_profit")
                time.sleep(0.06)
                if df is not None and not df.empty:
                    # 只取1231行
                    m = df[df["end_date"].astype(str).str[:8] == f"{year}1231"]
                    if not m.empty:
                        annual_rows.append(m)
                done += 1
                if done % 200 == 0:
                    print(f"  {done}/{len(target_codes)} 已下载")
            except Exception as e:
                err_count += 1
                time.sleep(0.5)
        
        if annual_rows:
            annual_df = pd.concat(annual_rows, ignore_index=True)
            annual_df = annual_df.astype({"ts_code": str, "end_date": str, "ann_date": str})
            print(f"  获取 {len(annual_df)} 只股票的年报数据")
            
            # 合并到现有文件(追加1231行)
            # 只追加operate_profit列(保持与现有列兼容)
            merged = pd.concat([existing, annual_df], ignore_index=True)
            merged.to_csv(fpath, index=False)
            
            # 验证
            verify = pd.read_csv(fpath, dtype={"end_date": str})
            has_now = (verify["end_date"].astype(str).str[:8] == f"{year}1231").sum()
            print(f"  ✅ 合并完成: {len(verify)}行, 1231行={has_now}, errors={err_count}")
        else:
            print(f"  ⚠️ 无年报数据可恢复, errors={err_count}")
    
    # 最终验证
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)
    for year in range(2015, 2026):
        f = DATA_DIR / f"income_{year}.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f, dtype={"end_date": str})
        has_1231 = (df["end_date"].astype(str).str[:8] == f"{year}1231").sum()
        total = len(df)
        status = "✅" if has_1231 > 0 else "❌"
        print(f"  {year} {status}: {total}行, 1231={has_1231}")
    
    print("\n✅ 恢复完成")


if __name__ == "__main__":
    main()