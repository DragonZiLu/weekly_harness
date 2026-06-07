#!/usr/bin/env python3
"""
download_missing_early.py — 精准补全 2011-2014 年年报数据

只下载缺失的文件：
  - cashflow_2014.csv  (2011-2013 已有)
  - balance_2011.csv ~ balance_2014.csv
  - income_2011.csv ~ income_2014.csv

使用 period 参数直接拉年报（而非 end_date），避免拉回大量季度数据。
并行下载，5线程 + rate limit，预计约 30-60 分钟完成。
"""

import os, time, pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from dotenv import load_dotenv
import tushare as ts

# ── 配置 ──
load_dotenv(Path('/Users/luzilong/Work/weekly_harness/.env'))
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
ROOT = Path('/Users/luzilong/Work/weekly_harness')
DATA_DIR = ROOT / 'data' / 'fcf_financials'

MAX_WORKERS = 5         # 并行线程数
RATE_LIMIT = 0.20       # 每次API调用间隔（秒）
RETRY_DELAY = 0.5       # 失败重试间隔
MAX_RETRIES = 2

# 需要的数据：表格名 -> (年份列表, 需要的字段)
# 字段必须包含 ts_code, ann_date, f_ann_date, end_date（用于索引构建）
MISSING = {
    "cashflow": {
        "years": [2014],  # 2011-2013 已有
        "fields": "ts_code,ann_date,f_ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta",
    },
    "balancesheet": {
        "years": [2011, 2012, 2013, 2014],
        "fields": "ts_code,ann_date,f_ann_date,end_date,total_liab,money_cap,total_assets",
    },
    "income": {
        "years": [2011, 2012, 2013, 2014],
        "fields": "ts_code,ann_date,f_ann_date,end_date,operate_profit",
    },
}

print_lock = Lock()
file_lock = Lock()


def log(msg):
    with print_lock:
        print(msg)


def collect_codes() -> list:
    """收集 CSI800 + CSI300 历史成分股代码"""
    codes = set()
    for pattern in ['index_weight_000906.SH*.csv', 'index_weight_000300.SH*.csv']:
        for p in sorted(ROOT.glob(f'data/index_weights/{pattern}')):
            df = pd.read_csv(p, dtype={"con_code": str})
            if "con_code" in df.columns:
                codes.update(df["con_code"].tolist())
    return sorted(codes)


def download_one_stock(pro, table: str, code: str, year: int, fields: str):
    """下载单只股票的单年数据，返回 DataFrame 或 None"""
    period = f"{year}1231"
    for attempt in range(MAX_RETRIES + 1):
        try:
            if table == "cashflow":
                df = pro.cashflow(ts_code=code, period=period, fields=fields)
            elif table == "balancesheet":
                df = pro.balancesheet(ts_code=code, period=period, fields=fields)
            elif table == "income":
                df = pro.income(ts_code=code, period=period, fields=fields)
            
            time.sleep(RATE_LIMIT)
            
            if df is not None and not df.empty:
                # 只保留年报行（period 查询可能返回多条，取第一条年报）
                target = f"{year}1231"
                df = df[df['end_date'].astype(str).str[:8] == target]
                if not df.empty:
                    return df.head(1)
            return None
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                return None
    return None


def download_table_year(pro, table: str, year: int, fields: str, codes: list):
    """下载某表某年的全部股票数据，保存到 CSV"""
    out_path = DATA_DIR / f"{table}_{year}.csv"
    
    # 加载已有数据（如果有）
    existing_codes = set()
    existing_rows = []
    if out_path.exists():
        old = pd.read_csv(out_path, dtype={"ts_code": str, "ann_date": str, 
                                            "f_ann_date": str, "end_date": str})
        existing_codes = set(old["ts_code"].unique())
        existing_rows = [old]
        log(f"  {table}_{year}: 已有 {len(existing_codes)} 只，需补 {len(codes) - len(existing_codes & set(codes))} 只")
    else:
        log(f"  {table}_{year}: 全新下载，目标 {len(codes)} 只")
    
    need = [c for c in codes if c not in existing_codes]
    if not need:
        log(f"  {table}_{year}: ✅ 已完整，跳过")
        return
    
    # 并行下载
    new_rows = []
    failed = 0
    done = [0]
    done_lock = Lock()
    
    def worker(code):
        result = download_one_stock(pro, table, code, year, fields)
        with done_lock:
            done[0] += 1
            if done[0] % 200 == 0:
                log(f"  {table}_{year}: {done[0]}/{len(need)} ({done[0]*100//len(need)}%)")
        return (code, result)
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker, c): c for c in need}
        for f in as_completed(futures):
            code, df = f.result()
            if df is not None and not df.empty:
                new_rows.append(df)
            else:
                failed += 1
    
    # 合并并保存
    if new_rows:
        new_df = pd.concat(new_rows, ignore_index=True)
        new_df = new_df.drop_duplicates(subset=["ts_code", "end_date"], keep="first")
        all_rows = existing_rows + [new_df]
        result = pd.concat(all_rows, ignore_index=True)
        result.to_csv(out_path, index=False)
        log(f"  {table}_{year}: ✅ 新增 {len(new_df)} 只，失败 {failed}，总计 {len(result)} 只")
    else:
        log(f"  {table}_{year}: ⚠️ 无新数据，失败 {failed}")


def main():
    pro = ts.pro_api()
    
    # Step 1: 收集成分股代码
    log("Step 1: 收集 CSI800 + CSI300 历史成分股...")
    codes = collect_codes()
    log(f"  共 {len(codes)} 只唯一股票\n")
    
    # Step 2: 逐个表和年份下载
    total_tasks = sum(len(v["years"]) for v in MISSING.values())
    task_n = 0
    
    for table, info in MISSING.items():
        for year in info["years"]:
            task_n += 1
            log(f"\n=== [{task_n}/{total_tasks}] {table}_{year} ===")
            download_table_year(pro, table, year, info["fields"], codes)
    
    log("\n\n✅ 全部补全完成！")
    log("接下来可以运行：")
    log("  python regenerate_fixed_baskets_v2.py    # 重新生成 ZZ800 篮子")
    log("  python regenerate_hs300_fcf_fixed.py     # 重新生成 HS300 篮子")


if __name__ == "__main__":
    main()
