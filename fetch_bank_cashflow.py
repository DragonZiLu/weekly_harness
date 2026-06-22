#!/usr/bin/env python3
"""
补齐银行股 FCF 现金流数据（2015-2025），从 Tushare 拉取并追加到缓存
"""
import pandas as pd
import numpy as np
import tushare as ts
import os
import time
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent
load_dotenv(PROJECT_DIR / ".env")

ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

BANKS = [
    "601398.SH",  # 工商银行
    "601939.SH",  # 建设银行
    "601288.SH",  # 农业银行
    "601988.SH",  # 中国银行
    "600036.SH",  # 招商银行
    "601328.SH",  # 交通银行
    "600016.SH",  # 民生银行
    "600000.SH",  # 浦发银行
    "601166.SH",  # 兴业银行
    "000001.SZ",  # 平安银行
    "002142.SZ",  # 宁波银行
    "600919.SH",  # 江苏银行
    "601009.SH",  # 南京银行
    "600015.SH",  # 华夏银行
    "601818.SH",  # 光大银行
    "601997.SH",  # 贵阳银行
    "600926.SH",  # 杭州银行
    "601229.SH",  # 上海银行
    "002839.SZ",  # 张家港行
    "601838.SH",  # 成都银行
]

CF_DIR = PROJECT_DIR / "data/fcf_financials"


def fetch_bank_cashflow(ts_code: str, year: int):
    """从 Tushare 拉取某只银行股某年的现金流数据"""
    start = f"{year}0101"
    end = f"{year}1231"
    
    try:
        df = pro.cashflow(
            ts_code=ts_code,
            start_date=start,
            end_date=end,
            fields="ts_code,ann_date,f_ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta,comp_type,report_type,end_type"
        )
        if df is not None and not df.empty:
            return df
    except Exception as e:
        print(f"    ⚠️ {ts_code} {year}: {e}")
    return None


def main():
    print("=" * 60)
    print("🏦 补齐银行股现金流数据 (2015-2025)")
    print("=" * 60)
    
    total_new = 0
    
    for year in range(2015, 2026):
        cf_file = CF_DIR / f"cashflow_{year}.csv"
        if not cf_file.exists():
            print(f"  ⚠️ {year}: 文件不存在，跳过")
            continue
        
        # 读取现有数据
        existing = pd.read_csv(cf_file)
        
        # 检查哪些银行已存在
        existing_banks = set(existing[existing["ts_code"].isin(BANKS)]["ts_code"].unique())
        missing_banks = [b for b in BANKS if b not in existing_banks]
        
        if not missing_banks:
            print(f"  ✅ {year}: 所有银行已有数据")
            continue
        
        print(f"  📥 {year}: 需补齐 {len(missing_banks)} 只银行...")
        
        new_rows = []
        for ts_code in missing_banks:
            df = fetch_bank_cashflow(ts_code, year)
            if df is not None and not df.empty:
                new_rows.append(df)
                print(f"    ✅ {ts_code}: {len(df)} 条")
                total_new += len(df)
            else:
                print(f"    ⚠️ {ts_code}: 无数据")
            time.sleep(0.15)  # 控制频率
        
        if new_rows:
            new_df = pd.concat(new_rows, ignore_index=True)
            # 对齐列
            for col in existing.columns:
                if col not in new_df.columns:
                    new_df[col] = None
            new_df = new_df[existing.columns]
            
            # 追加并去重
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined.drop_duplicates(subset=["ts_code", "end_date"], keep="last", inplace=True)
            combined.to_csv(cf_file, index=False)
            print(f"    💾 {year}: 追加 {len(new_df)} 条，文件现有 {len(combined)} 条")
    
    print(f"\n✅ 完成！共补充 {total_new} 条银行现金流记录")


if __name__ == "__main__":
    main()
