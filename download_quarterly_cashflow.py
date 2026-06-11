"""
download_quarterly_cashflow.py — 下载全量季度 OCF + Capex（FCF 盈利规则所需）

输出：data/fcf_financials/quarterly_cashflow.csv
"""
import sys, time, os
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
import tushare as ts

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

OUTPUT = PROJECT_ROOT / "data" / "fcf_financials" / "quarterly_cashflow.csv"
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
FIELDS = "ts_code,end_date,n_cashflow_act,c_pay_acq_const_fiolta"


def main():
    t_start = time.time()

    print("[1/2] 加载 ZZ800 成分股...", flush=True)
    from weekly_harness.fcf_universe import IndexWeightCache
    cache = IndexWeightCache("000906.SH")
    cache.load()
    all_stocks = sorted(set(str(r["con_code"]) for _, r in cache._weights.iterrows()))
    print(f"      {len(all_stocks)} 只", flush=True)

    done_set = set()
    if OUTPUT.exists():
        existing = pd.read_csv(OUTPUT, dtype={"ts_code": str, "end_date": str})
        done_set = set(existing["ts_code"].unique())
        print(f"      已缓存: {len(done_set)} 只", flush=True)

    stocks = [s for s in all_stocks if s not in done_set]
    if not stocks:
        print("✅ 全部已缓存", flush=True)
        return

    print(f"[2/2] 下载 {len(stocks)} 只... 预计 {len(stocks)*0.3/60:.0f}min", flush=True)

    results, errors = [], 0
    for i, code in enumerate(stocks):
        try:
            df = pro.cashflow(ts_code=code, start_date="20140101", end_date="20260630", fields=FIELDS)
            time.sleep(0.25)
            if df is not None and not df.empty:
                df = df[df["end_date"].astype(str).str.match(r".*(0331|0630|0930)$")].copy()
                df = df.drop_duplicates(subset=["end_date"], keep="last")
                if len(df) > 0:
                    results.append(df)
        except Exception as e:
            errors += 1
            time.sleep(1.5)
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / (i + 1) * (len(stocks) - i - 1)
            print(f"  [{i+1:4d}/{len(stocks)}] {elapsed:.0f}s | eta {eta/60:.1f}min | 错误{errors}", flush=True)

    if results:
        all_df = pd.concat(results, ignore_index=True)
        if OUTPUT.exists():
            old = pd.read_csv(OUTPUT, dtype={"ts_code": str, "end_date": str})
            all_df = pd.concat([old, all_df], ignore_index=True)
            all_df = all_df.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
        all_df.to_csv(OUTPUT, index=False)
        nq = len(all_df)
        print(f"\n✅ {len(all_df['ts_code'].unique())} 只, {nq} 条, "
              f"{nq-len(existing) if OUTPUT.exists() else nq} 新增, "
              f"{(time.time()-t_start)/60:.1f}min, 错误{errors}", flush=True)
    else:
        print(f"\n⚠️ 无新数据, 错误{errors}", flush=True)


if __name__ == "__main__":
    main()
