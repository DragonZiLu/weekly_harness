"""
Targeted fix: re-download only missing stocks with correct end_date ({year+1}0630).
Appends to existing CSVs.
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

EXCLUDED = {"银行","证券","保险","多元金融","信托","期货","融资租赁","金融控股",
            "资产管理","房地产开发","房地产服务","全国地产","区域地产","房产服务","园区开发"}
EXCLUDE_KW = ["金融","银行","证券","保险","地产","房产"]

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

def is_excluded(ind):
    ind = str(ind).strip()
    if ind in EXCLUDED: return True
    for kw in EXCLUDE_KW:
        if kw in ind: return True
    return False

def get_annual_report(df, year):
    if df is None or df.empty: return None
    target = f"{year}1231"
    matches = df[df['end_date'].astype(str).str[:8] == target]
    if matches.empty: return None
    if len(matches) > 1 and 'report_type' in matches.columns:
        cons = matches[matches['report_type'] == '1']
        if not cons.empty: return cons.iloc[0].to_dict()
    return matches.iloc[0].to_dict()

def download_stock(code, year, pro_api):
    results = {"cf": None, "bs": None, "inc": None, "code": code, "ok": True}
    start_date = f"{year}0101"
    end_date = f"{year+1}0630"  # FIXED: use June 30 to capture April filings
    
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
    
    stocks = pd.read_csv(DATA_DIR / "stock_list.csv", dtype={"ts_code": str, "industry": str})
    iw = pd.read_csv(_PROJECT_ROOT / "data/index_weights/index_weight_000985.SH.csv",
                     dtype={"con_code": str, "trade_date": str})
    latest_date = iw["trade_date"].max()
    constituents = set(iw[iw["trade_date"] == latest_date]["con_code"].tolist())
    ind_map = dict(zip(stocks['ts_code'], stocks['industry']))
    target_codes = [c for c in constituents if c in ind_map and not is_excluded(ind_map.get(c,''))]
    
    total_new = 0
    
    for year in range(2015, 2026):
        cf_path = DATA_DIR / f"cashflow_{year}.csv"
        bs_path = DATA_DIR / f"balance_{year}.csv"
        inc_path = DATA_DIR / f"income_{year}.csv"
        
        # Identify missing stocks
        existing = set()
        if cf_path.exists():
            existing = set(pd.read_csv(cf_path, dtype={"ts_code": str})["ts_code"].unique())
        
        need = [c for c in target_codes if c not in existing]
        
        if not need:
            print(f"Year {year}: complete ({len(existing)} stocks)")
            continue
        
        print(f"\n📥 Year {year}: {len(need)} missing to fix...")
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
                if done % 500 == 0 or done == len(need):
                    e = time.time() - t0
                    r = done / e if e > 0 else 0
                    rm = (len(need) - done) / r / 60 if r > 0 else 0
                    print(f"  {done}/{len(need)} {e:.0f}s ~{rm:.0f}min err={err}")
        
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
        
        added = len(cf_rows) + len(bs_rows) + len(inc_rows)
        total_new += added
        print(f"  ✅ Year {year}: +{added} rows, now {len(pd.read_csv(cf_path, dtype={'ts_code':str}) if cf_path.exists() else [])} cf")
    
    print(f"\n✅ Done! Total new rows: {total_new}")

if __name__ == "__main__":
    main()
