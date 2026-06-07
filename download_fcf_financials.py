"""
FCF financial data downloader for Q1 2026 comparison.
Downloads annual report data (cashflow + balance + income) for 2020-2025.
Uses start_date only (no restrictive end_date) to get annual reports.
"""
import sys, os, time, pandas as pd
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg
import tushare as ts

DATA_DIR = _PROJECT_ROOT / "data" / "fcf_financials"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDED = {"银行", "证券", "保险", "多元金融", "信托", "期货", "融资租赁", "金融控股",
            "资产管理", "房地产开发", "房地产服务", "全国地产", "区域地产", "房产服务", "园区开发"}
EXCLUDE_KW = ["金融", "银行", "证券", "保险", "地产", "房产"]

def is_excluded(ind):
    ind = str(ind).strip()
    if ind in EXCLUDED: return True
    for kw in EXCLUDE_KW:
        if kw in ind: return True
    return False

def get_annual_report(df, year):
    """Extract the annual report row from a dataframe (end_date == year1231)."""
    if df is None or df.empty:
        return None
    target = f"{year}1231"
    matches = df[df['end_date'].astype(str).str[:8] == target]
    if matches.empty:
        return None
    # Prefer report_type=1 (consolidated) if both exist
    if len(matches) > 1 and 'report_type' in matches.columns:
        cons = matches[matches['report_type'] == '1']
        if not cons.empty:
            return cons.iloc[0].to_dict()
    return matches.iloc[0].to_dict()

def main():
    pro = ts.pro_api(tushare_cfg.token)
    
    # Load stocks
    stocks = pd.read_csv(DATA_DIR / "stock_list.csv", dtype={"ts_code": str, "industry": str})
    
    # Load CSI All Share constituents
    iw = pd.read_csv(_PROJECT_ROOT / "data" / "index_weights" / "index_weight_000985.SH.csv",
                     dtype={"con_code": str, "trade_date": str})
    latest_date = iw["trade_date"].max()
    constituents = set(iw[iw["trade_date"] == latest_date]["con_code"].tolist())
    
    # Build industry map
    ind_map = dict(zip(stocks['ts_code'], stocks['industry']))
    
    # Filter: in CSI All Share AND non-financial
    target_codes = [c for c in constituents if c in ind_map and not is_excluded(ind_map.get(c, ''))]
    print(f"CSI All Share constituents: {len(constituents)}")
    print(f"Non-financial: {len(target_codes)}")
    
    # Download years FY2015-2025 (need 2015+ for 5-year OCF check starting 2020)
    years = range(2015, 2026)
    
    for year in years:
        cf_path = DATA_DIR / f"cashflow_{year}.csv"
        bs_path = DATA_DIR / f"balance_{year}.csv"
        inc_path = DATA_DIR / f"income_{year}.csv"
        
        all_exist = cf_path.exists() and bs_path.exists() and inc_path.exists()
        if all_exist:
            n = len(pd.read_csv(cf_path, dtype={"ts_code": str}))
            print(f"Year {year}: cache exists ({n} stocks)")
            continue
        
        # Determine what's already done
        done = set()
        if cf_path.exists():
            done = set(pd.read_csv(cf_path, dtype={"ts_code": str})["ts_code"].unique())
        
        need = [c for c in target_codes if c not in done]
        if not need:
            print(f"Year {year}: all done")
            continue
        
        print(f"\n📥 Year {year}: {len(need)} stocks to download...")
        
        cf_rows, bs_rows, inc_rows = [], [], []
        start_date = f"{year}0101"
        # Use a broad end_date so annual reports are included
        end_date = f"{year+1}0331"
        
        done_count = 0
        err_count = 0
        t0 = time.time()
        
        for i, code in enumerate(need):
            ok = False
            for attempt in range(2):
                try:
                    # Cashflow — 用 period 参数直接按报告期查询
                    cf = pro.cashflow(ts_code=code, period=f'{year}1231',
                                     fields='ts_code,ann_date,f_ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta')
                    time.sleep(0.06)
                    row = get_annual_report(cf, year)
                    if row:
                        cf_rows.append(row)
                    
                    # Balance sheet — 用 period 参数
                    bs = pro.balancesheet(ts_code=code, period=f'{year}1231',
                                         fields='ts_code,ann_date,end_date,total_liab,money_cap,total_assets')
                    time.sleep(0.06)
                    row = get_annual_report(bs, year)
                    if row:
                        bs_rows.append(row)
                    
                    # Income — 用 period 参数直接按报告期查询
                    inc = pro.income(ts_code=code, period=f'{year}1231',
                                    fields='ts_code,ann_date,end_date,operate_profit')
                    time.sleep(0.06)
                    row = get_annual_report(inc, year)
                    if row:
                        inc_rows.append(row)
                    
                    ok = True
                    break
                except Exception as e:
                    if attempt == 0:
                        time.sleep(0.5)
                    else:
                        err_count += 1
            
            done_count += 1
            if done_count % 200 == 0:
                elapsed = time.time() - t0
                rate = elapsed / done_count
                remaining = rate * (len(need) - done_count)
                print(f"  {done_count}/{len(need)} ({done_count*100//len(need)}%) "
                      f"est={remaining/60:.0f}min remaining, errors={err_count}")
        
        # Save
        def save_or_append(path, new_rows):
            if not new_rows:
                return
            new_df = pd.DataFrame(new_rows)
            if path.exists():
                old = pd.read_csv(path, dtype={"ts_code": str})
                combined = pd.concat([old, new_df], ignore_index=True)
                combined.to_csv(path, index=False)
            else:
                new_df.to_csv(path, index=False)
        
        save_or_append(cf_path, cf_rows)
        save_or_append(bs_path, bs_rows)
        save_or_append(inc_path, inc_rows)
        
        elapsed = time.time() - t0
        print(f"  Year {year} done in {elapsed/60:.1f}min: "
              f"cf={len(cf_rows)}, bs={len(bs_rows)}, inc={len(inc_rows)}, errors={err_count}")

    print("\n✅ Download complete!")

if __name__ == "__main__":
    main()
