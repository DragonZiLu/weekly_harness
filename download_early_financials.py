"""
补全 2011-2014 年报数据（CSI800 + CSI300 成分股）
逐股下载模式，与 download_fcf_financials.py 一致。
只下载缺失的股票，增量合并到现有文件。
"""
import sys, os, time, pandas as pd
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg
import tushare as ts

DATA_DIR = _PROJECT_ROOT / "data" / "fcf_financials"
pro = ts.pro_api(tushare_cfg.token)

# Collect CSI800 + CSI300 constituent codes
all_codes = set()
for p in sorted(_PROJECT_ROOT.glob('data/index_weights/index_weight_000906.SH*.csv')):
    df = pd.read_csv(p, dtype={"con_code": str})
    all_codes.update(df["con_code"].tolist())
for p in sorted(_PROJECT_ROOT.glob('data/index_weights/index_weight_000300.SH*.csv')):
    df = pd.read_csv(p, dtype={"con_code": str})
    all_codes.update(df["con_code"].tolist())
codes_list = sorted(all_codes)
print(f"CSI800+CSI300: {len(codes_list)} codes")

def get_annual_report(df, year):
    if df is None or df.empty:
        return None
    target = f"{year}1231"
    matches = df[df['end_date'].astype(str).str[:8] == target]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()

def download_missing(year, table, field_list, codes):
    """Download missing stocks for a specific year and table, merge into existing CSV."""
    file_path = DATA_DIR / f"{table}_{year}.csv"
    
    # Load existing data
    existing_df = None
    existing_codes = set()
    if file_path.exists():
        existing_df = pd.read_csv(file_path, dtype={"ts_code": str, "end_date": str})
        # Check which codes have annual data for this year
        ann_rows = existing_df[existing_df["end_date"].astype(str).str[:8] == f"{year}1231"]
        existing_codes = set(ann_rows["ts_code"].unique())
    
    missing = [c for c in codes if c not in existing_codes]
    if not missing:
        print(f"  {table}_{year}: ✅ all {len(codes)} stocks covered")
        return
    
    print(f"  {table}_{year}: {len(existing_codes)} covered, downloading {len(missing)} missing...")
    
    new_rows = []
    err_count = 0
    t0 = time.time()
    
    for i, code in enumerate(missing):
        for attempt in range(2):
            try:
                if table == "cashflow":
                    df = pro.cashflow(ts_code=code, period=f'{year}1231',
                                     fields='ts_code,ann_date,f_ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta')
                    time.sleep(0.06)
                    row = get_annual_report(df, year)
                elif table == "balance":
                    df = pro.balancesheet(ts_code=code, period=f'{year}1231',
                                         fields='ts_code,ann_date,f_ann_date,end_date,total_liab,money_cap,total_assets')
                    time.sleep(0.06)
                    row = get_annual_report(df, year)
                elif table == "income":
                    df = pro.income(ts_code=code, period=f'{year}1231',
                                    fields='ts_code,ann_date,f_ann_date,end_date,operate_profit')
                    time.sleep(0.06)
                    row = get_annual_report(df, year)
                
                if row:
                    new_rows.append(row)
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(0.5)
                else:
                    err_count += 1
        
        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            rate = elapsed / (i + 1)
            remaining = rate * (len(missing) - i - 1)
            print(f"    {i+1}/{len(missing)} ({(i+1)*100//len(missing)}%) "
                  f"est={remaining/60:.0f}min, errors={err_count}")
    
    # Merge and save
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        new_df = new_df.drop_duplicates(subset=["ts_code", "end_date"], keep="first")
        
        if existing_df is not None:
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
        else:
            combined = new_df
        
        combined.to_csv(file_path, index=False)
        
        # Verify
        ann_after = combined[combined["end_date"].astype(str).str[:8] == f"{year}1231"]
        covered = len(set(ann_after["ts_code"].unique()) & set(codes))
        print(f"  {table}_{year}: ✅ now {covered}/{len(codes)} stocks covered (added {len(new_rows)} rows)")
    else:
        print(f"  {table}_{year}: ⚠️ no new data downloaded, errors={err_count}")

# Download cashflow for 2011-2014 (most critical for 5yr OCF)
print("\n=== Cashflow (5yr OCF check) ===")
for year in [2011, 2012, 2013, 2014]:
    download_missing(year, "cashflow", 
                     "ts_code,ann_date,f_ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta",
                     codes_list)

# Download balance for 2011-2014 (needed for EV calculation at those periods)
print("\n=== Balance Sheet (EV calculation) ===")
for year in [2011, 2012, 2013, 2014]:
    download_missing(year, "balance",
                     "ts_code,ann_date,f_ann_date,end_date,total_liab,money_cap,total_assets",
                     codes_list)

# Download income for 2011-2014 (needed for profit quality at those periods)
print("\n=== Income (profit quality) ===")
for year in [2011, 2012, 2013, 2014]:
    download_missing(year, "income",
                     "ts_code,ann_date,f_ann_date,end_date,operate_profit",
                     codes_list)

print("\n✅ 2011-2014 年报数据补全完成!")
