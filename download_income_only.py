"""Targeted income data download - only downloads missing income files"""
import sys, os, time, pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

_PROJ = Path(__file__).parent
sys.path.insert(0, str(_PROJ))
from config.settings import tushare_cfg
import tushare as ts

DATA_DIR = _PROJ / "data" / "fcf_financials"
_rate_lock = threading.Lock()
_last_call = 0.0

def rate_limit():
    global _last_call
    with _rate_lock:
        now = time.time()
        wait = _last_call + 0.06 - now
        if wait > 0: time.sleep(wait)
        _last_call = time.time()

def download_income(code, year, pro):
    """Download income statement for a single stock in a given year."""
    try:
        rate_limit()
        df = pro.income(ts_code=code, start_date=f"{year}0101", end_date=f"{year+1}0630",
                        fields="ts_code,ann_date,f_ann_date,end_date,report_type,"
                               "revenue,oper_cost,biz_tax_surchg,sell_exp,admin_exp,"
                               "fin_exp,invest_income")
        if df is None or df.empty:
            return None
        target = f"{year}1231"
        match = df[df['end_date'].astype(str).str[:8] == target]
        if match.empty:
            return None
        if len(match) > 1 and 'report_type' in match.columns:
            cons = match[match['report_type'] == '1']
            if not cons.empty:
                match = cons
        return match.iloc[0].to_dict()
    except Exception as e:
        return None

def main():
    pro = ts.pro_api(tushare_cfg.token)
    
    # Get stock codes from existing cashflow files (only need 2024)
    years = range(2015, 2026)
    
    for year in years:
        inc_path = DATA_DIR / f"income_{year}.csv"
        if inc_path.exists():
            n = len(pd.read_csv(inc_path, dtype={"ts_code": str}))
            print(f"FY{year}: income exists ({n} rows), skip")
            continue
        
        cf_path = DATA_DIR / f"cashflow_{year}.csv"
        if not cf_path.exists():
            print(f"FY{year}: no cashflow data, skip")
            continue
        
        codes = pd.read_csv(cf_path, dtype={"ts_code": str})["ts_code"].unique().tolist()
        print(f"FY{year}: downloading income for {len(codes)} stocks...", end=" ", flush=True)
        
        results = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(download_income, c, year, pro): c for c in codes}
            for i, f in enumerate(as_completed(futures)):
                if i % 500 == 0:
                    print(f"{i}/{len(codes)}...", end=" ", flush=True)
                r = f.result()
                if r:
                    results.append(r)
        
        if results:
            df = pd.DataFrame(results)
            for col in ['ann_date','f_ann_date','end_date']:
                if col in df.columns:
                    df[col] = df[col].astype(str)
            df.to_csv(inc_path, index=False)
            print(f"✅ {len(results)} rows saved")
        else:
            print(f"❌ 0 rows")
        
        time.sleep(1)

if __name__ == "__main__":
    main()
