"""
Targeted download: all 932365 index constituents' financial data (2015-2025).
Appends to existing CSVs to fill data gaps.
"""
import sys, os, time, pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg
import tushare as ts

DATA_DIR = _PROJECT_ROOT / "data" / "fcf_financials"

MAX_WORKERS = 10
_rate_lock = threading.Lock()
_last_call_time = 0.0

def rate_limit():
    global _last_call_time
    with _rate_lock:
        now = time.time()
        wait = _last_call_time + 0.05 - now
        if wait > 0:
            time.sleep(wait)
        _last_call_time = time.time()

def get_annual_report(df, year):
    if df is None or df.empty:
        return None
    target = f"{year}1231"
    matches = df[df['end_date'].astype(str).str[:8] == target]
    if matches.empty:
        return None
    if len(matches) > 1 and 'report_type' in matches.columns:
        cons = matches[matches['report_type'] == '1']
        if not cons.empty:
            return cons.iloc[0].to_dict()
    return matches.iloc[0].to_dict()

def download_stock(code, year, pro_api):
    """Download all 3 reports for a single stock in a given year."""
    results = {"cf": None, "bs": None, "inc": None, "code": code, "ok": True}
    start_date = f"{year}0101"
    end_date = f"{year+1}1231"  # Very wide range to capture all filings
    
    try:
        rate_limit()
        cf = pro_api.cashflow(ts_code=code, start_date=start_date, end_date=end_date,
                              fields='ts_code,ann_date,f_ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta')
        results["cf"] = get_annual_report(cf, year)
        
        rate_limit()
        bs = pro_api.balancesheet(ts_code=code, start_date=start_date, end_date=end_date,
                                  fields='ts_code,ann_date,end_date,total_liab,money_cap,total_assets')
        results["bs"] = get_annual_report(bs, year)
        
        rate_limit()
        inc = pro_api.income(ts_code=code, start_date=start_date, end_date=end_date,
                            fields='ts_code,ann_date,end_date,'
                                   'revenue,oper_cost,biz_tax_surchg,'
                                   'sell_exp,admin_exp,fin_exp,invest_income')
        results["inc"] = get_annual_report(inc, year)
    except Exception as e:
        results["ok"] = False
    return results

def main():
    pro = ts.pro_api(tushare_cfg.token)
    
    # Get all 932365 constituents across all dates
    all_weights = pro.index_weight(index_code="932365.CSI", start_date="20241201", end_date="20260601")
    all_codes = sorted(set(all_weights["con_code"].tolist()))
    print(f"932365 所有历次成分股: {len(all_codes)} 只")
    
    years = range(2015, 2026)
    
    total_new = 0
    for year in years:
        cf_path = DATA_DIR / f"cashflow_{year}.csv"
        bs_path = DATA_DIR / f"balance_{year}.csv"
        inc_path = DATA_DIR / f"income_{year}.csv"
        
        # Find which 932365 stocks are missing
        existing_cf = set()
        if cf_path.exists():
            existing_cf = set(pd.read_csv(cf_path, dtype={"ts_code": str})["ts_code"].unique())
        
        need = [c for c in all_codes if c not in existing_cf]
        
        if not need:
            print(f"Year {year}: all {len(all_codes)} stocks already have data")
            continue
        
        print(f"\n📥 Year {year}: downloading {len(need)} missing 932365 stocks...")
        t0 = time.time()
        
        cf_rows, bs_rows, inc_rows = [], [], []
        err = 0
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_stock, code, year, pro): code for code in need}
            done = 0
            for future in as_completed(futures):
                done += 1
                try:
                    r = future.result()
                    if r["ok"]:
                        if r["cf"]: cf_rows.append(r["cf"])
                        if r["bs"]: bs_rows.append(r["bs"])
                        if r["inc"]: inc_rows.append(r["inc"])
                    else:
                        err += 1
                except:
                    err += 1
                if done % 20 == 0 or done == len(need):
                    e = time.time() - t0
                    print(f"  {done}/{len(need)} {e:.0f}s err={err}")
        
        def save_or_append(path, new_rows):
            if not new_rows: return
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
        
        added = len(cf_rows)
        if added > 0:
            total_new += added
            print(f"  ✅ Year {year}: added {added} new stocks in {time.time()-t0:.0f}s")
        else:
            print(f"  ⚠️ Year {year}: no new data in {time.time()-t0:.0f}s")
    
    print(f"\n✅ Done! Total new stock-years: {total_new}")

if __name__ == "__main__":
    main()
