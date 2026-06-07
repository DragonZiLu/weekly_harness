"""Parallel income download - FY2024+2025 only (~8000 API calls, ~3 min)"""
import sys, os, time, pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

_PROJ = Path(__file__).parent.resolve()
sys.path.insert(0, str(_PROJ))
from config.settings import tushare_cfg
import tushare as ts

DATA_DIR = _PROJ / "data" / "fcf_financials"
_lock = threading.Lock()
_last = 0.0
_success = 0

def income_for(code, year, pro):
    global _success, _last
    try:
        with _lock:
            now = time.time()
            w = _last + 0.06 - now
            if w > 0: time.sleep(w)
            _last = time.time()
        df = pro.income(ts_code=code, start_date=f"{year}0101", end_date=f"{year+1}0630",
                        fields="ts_code,ann_date,f_ann_date,end_date,report_type,revenue,oper_cost,biz_tax_surchg,sell_exp,admin_exp,fin_exp,invest_income")
        if df is not None and not df.empty:
            tgt = f"{year}1231"
            m = df[df['end_date'].astype(str).str[:8] == tgt]
            if not m.empty:
                if len(m) > 1 and 'report_type' in m.columns:
                    c = m[m['report_type'] == '1']
                    if not c.empty: m = c
                _success += 1
                return m.iloc[0].to_dict()
    except:
        pass
    return None

def main():
    pro = ts.pro_api(tushare_cfg.token)
    for year in [2024, 2025]:
        dst = DATA_DIR / f"income_{year}.csv"
        if dst.exists():
            n = len(pd.read_csv(dst, dtype={"ts_code": str}))
            print(f"FY{year}: exists ({n} rows)")
            continue
        cf = DATA_DIR / f"cashflow_{year}.csv"
        if not cf.exists():
            print(f"FY{year}: no cashflow")
            continue
        codes = pd.read_csv(cf, dtype={"ts_code": str})["ts_code"].unique().tolist()
        print(f"FY{year}: downloading {len(codes)} stocks...")
        global _success; _success = 0
        results = []
        with ThreadPoolExecutor(max_workers=12) as pool:
            fs = {pool.submit(income_for, c, year, pro): c for c in codes}
            for i, f in enumerate(as_completed(fs)):
                if i % 500 == 0: print(f"  {i}/{len(codes)} ({_success} ok)")
                r = f.result()
                if r: results.append(r)
        df = pd.DataFrame(results)
        for c in ['ann_date','f_ann_date','end_date']:
            if c in df.columns: df[c] = df[c].astype(str)
        df.to_csv(dst, index=False)
        print(f"FY{year}: ✅ {len(df)} rows")
        time.sleep(1)

if __name__ == "__main__":
    main()
