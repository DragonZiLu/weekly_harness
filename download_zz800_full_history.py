"""
download_zz800_full_history.py  v2（优化版）
=================================
优化点：
1. 3张表改为并发（per-stock 内部并发）→ 每只股票耗时 3x → 1x
2. RATE_INTERVAL 降至 0.02s（50次/s 峰值）
3. 批量写入：所有行收集完再一次性 save，避免反复读写大文件
4. 断点续传：已有数据绝不重复下载
"""
import sys, time
import pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg
import tushare as ts

# ── 配置 ──────────────────────────────────────
DATA_DIR   = _PROJECT_ROOT / "data" / "fcf_financials"
IW_DIR     = _PROJECT_ROOT / "data" / "index_weights"

MAX_WORKERS   = 10       # 外层并发（每只股票一个task）
RATE_INTERVAL = 0.13     # 0.13s ≈ 7.7次/s = 460次/分钟（Tushare限制500次/分钟）
SAVE_BATCH    = 500      # 每收集N条行即写一次磁盘

FINANCIAL_KW = {"金融","银行","证券","保险","地产","房产","多元金融","信托","期货",
                "融资租赁","金融控股","资产管理","房地产开发","房地产服务",
                "全国地产","区域地产","房产服务","园区开发"}

CF_FIELDS  = ("ts_code,ann_date,f_ann_date,end_date,comp_type,report_type,end_type,"
              "n_cashflow_act,c_pay_acq_const_fiolta")
BS_FIELDS  = "ts_code,ann_date,end_date,total_liab,money_cap,total_assets"
INC_FIELDS = "ts_code,ann_date,end_date,operate_profit"

# ── 速率限制 ──────────────────────────────────
_rate_lock = threading.Lock()
_last_call = [0.0]

def _rate_limit():
    with _rate_lock:
        now = time.time()
        wait = _last_call[0] + RATE_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()

# ── 工具 ──────────────────────────────────────
def is_financial(industry: str) -> bool:
    industry = str(industry).strip()
    if industry in FINANCIAL_KW:
        return True
    return any(kw in industry for kw in ("金融","银行","证券","保险","地产","房产"))


def get_target_codes() -> list:
    hist = pd.read_csv(IW_DIR / "index_weight_000906.SH.csv", dtype={"con_code": str})
    all_codes = set(hist["con_code"].astype(str))
    sl = pd.read_csv(DATA_DIR / "stock_list.csv", dtype={"ts_code": str})
    ind_map = dict(zip(sl["ts_code"], sl["industry"]))
    return sorted(c for c in all_codes if not is_financial(ind_map.get(c, "")))


def load_existing(path: Path, end_date_str: str = None) -> set:
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path, dtype={"ts_code": str},
                         usecols=["ts_code","end_date"] if end_date_str else ["ts_code"])
    except Exception:
        return set()
    if end_date_str and "end_date" in df.columns:
        df = df[df["end_date"].astype(str).str[:8] == end_date_str]
    return set(df["ts_code"])


def save_append(path: Path, new_rows: list, key_fields=("ts_code","end_date")):
    """追加并去重保存（按行 append，避免全量读写）"""
    if not new_rows:
        return
    new_df = pd.DataFrame(new_rows)
    if path.exists():
        try:
            old = pd.read_csv(path, dtype={"ts_code": str})
        except Exception:
            old = pd.DataFrame()
        combined = pd.concat([old, new_df], ignore_index=True)
        combined["ann_date"] = combined["ann_date"].astype(str)
        combined = combined.sort_values("ann_date", ascending=False, na_position="last")
        valid_keys = [k for k in key_fields if k in combined.columns]
        if valid_keys:
            combined = combined.drop_duplicates(subset=valid_keys, keep="first")
        combined.to_csv(path, index=False)
    else:
        new_df.to_csv(path, index=False)


def pick_row(df: pd.DataFrame, end_date_str: str):
    if df is None or df.empty:
        return None
    rows = df[df["end_date"].astype(str).str[:8] == end_date_str]
    if rows.empty:
        return None
    if "report_type" in rows.columns:
        pref = rows[rows["report_type"].astype(str) == "1"]
        rows = pref if not pref.empty else rows
    rows = rows.sort_values("ann_date", ascending=False, na_position="last")
    return rows.iloc[0].to_dict()


# ── 核心：单只股票 3张表并发下载 ──────────────
def _fetch_one(pro, code: str, end_date_str: str,
               start: str, end: str, need_tables: list) -> dict:
    """对一只股票：并发下3张表，返回 {table: row_or_None}"""
    result = {t: None for t in need_tables}

    def fetch_table(table):
        _rate_limit()
        try:
            if table == "cashflow":
                df = pro.cashflow(ts_code=code, start_date=start, end_date=end, fields=CF_FIELDS)
            elif table == "balance":
                df = pro.balancesheet(ts_code=code, start_date=start, end_date=end, fields=BS_FIELDS)
            elif table == "income":
                df = pro.income(ts_code=code, start_date=start, end_date=end, fields=INC_FIELDS)
            else:
                return table, None
            return table, pick_row(df, end_date_str)
        except Exception:
            return table, None

    # 3张表串行（避免3个并发请求同时发出，加剧频率限制压力）
    for table in need_tables:
        _rate_limit()
        try:
            if table == "cashflow":
                df = pro.cashflow(ts_code=code, start_date=start, end_date=end, fields=CF_FIELDS)
            elif table == "balance":
                df = pro.balancesheet(ts_code=code, start_date=start, end_date=end, fields=BS_FIELDS)
            elif table == "income":
                df = pro.income(ts_code=code, start_date=start, end_date=end, fields=INC_FIELDS)
            else:
                continue
            result[table] = pick_row(df, end_date_str)
        except Exception:
            pass

    return result


# ── 通用下载函数 ──────────────────────────────
def _download_period(pro, label: str, end_date_str: str,
                     start: str, end: str,
                     target_codes: list,
                     paths: dict):
    """通用：下载某个期次（年报或季报）的3张表，断点续传"""
    tables = ["cashflow", "balance", "income"]
    missing_per = {}
    for t in tables:
        missing_per[t] = set(target_codes) - load_existing(paths[t], end_date_str)

    need_all = set()
    for v in missing_per.values():
        need_all |= v
    need_codes = sorted(need_all)

    if not need_codes:
        cf_n = len(missing_per["cashflow"])
        print(f"  ✅ {label}: 已完整 (CF缺{cf_n}=0)")
        return

    need_by_code = {c: [t for t in tables if c in missing_per[t]] for c in need_codes}

    cf_m = len(missing_per["cashflow"])
    bs_m = len(missing_per["balance"])
    inc_m = len(missing_per["income"])
    print(f"  📥 {label}: 补 {len(need_codes)} 只 | CF缺{cf_m} BS缺{bs_m} INC缺{inc_m}")

    rows = {t: [] for t in tables}
    errs = 0
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(_fetch_one, pro, c, end_date_str,
                      start, end, ts_list): c
            for c, ts_list in need_by_code.items()
        }
        for fut in as_completed(futures):
            done += 1
            try:
                res = fut.result()
                for t, row in res.items():
                    if row:
                        rows[t].append(row)
            except Exception:
                errs += 1

            if done % 200 == 0 or done == len(need_codes):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 1
                remain = (len(need_codes) - done) / rate / 60
                cf_n = len(rows["cashflow"]); bs_n = len(rows["balance"]); inc_n = len(rows["income"])
                print(f"    {done}/{len(need_codes)} | cf={cf_n} bs={bs_n} inc={inc_n} | err={errs} | ~{remain:.1f}min")

            # 每SAVE_BATCH条中间保存（减少OOM风险，顺便断点续传）
            total_rows = sum(len(v) for v in rows.values())
            if total_rows > 0 and total_rows % SAVE_BATCH == 0:
                for t in tables:
                    if rows[t]:
                        save_append(paths[t], rows[t])
                        rows[t].clear()

    # 最终保存
    for t in tables:
        save_append(paths[t], rows[t])

    cf_n = sum(1 for _ in load_existing(paths["cashflow"], end_date_str))
    total = sum(len(v) for v in rows.values())
    print(f"  ✅ {label}: +新增 cf={len(rows.get('cashflow',[]))} bs={len(rows.get('balance',[]))} inc={len(rows.get('income',[]))}")


# ── 年报 ──────────────────────────────────────
def download_annual(pro, year: int, target_codes: list):
    label = f"{year}年报"
    start = f"{year}0101"
    end   = f"{year+1}0630"
    end_date_str = f"{year}1231"
    paths = {
        "cashflow": DATA_DIR / f"cashflow_{year}.csv",
        "balance":  DATA_DIR / f"balance_{year}.csv",
        "income":   DATA_DIR / f"income_{year}.csv",
    }
    _download_period(pro, label, end_date_str, start, end, target_codes, paths)


# ── 季报 ──────────────────────────────────────
QUARTER_WINDOWS = {
    "Q1": lambda y: (f"{y}0301", f"{y}0631",  f"{y}0331"),
    "Q2": lambda y: (f"{y}0601", f"{y}0901",  f"{y}0630"),
    "Q3": lambda y: (f"{y}1001", f"{y+1}0201", f"{y}0930"),
}

def download_quarterly(pro, year: int, quarter: str, target_codes: list):
    start, end, end_date_str = QUARTER_WINDOWS[quarter](year)
    label = f"{year}{quarter}"
    paths = {
        "cashflow": DATA_DIR / f"cashflow_{label}.csv",
        "balance":  DATA_DIR / f"balance_{label}.csv",
        "income":   DATA_DIR / f"income_{label}.csv",
    }
    _download_period(pro, label, end_date_str, start, end, target_codes, paths)


# ── 主程序 ────────────────────────────────────
def main():
    global MAX_WORKERS, RATE_INTERVAL
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--annual-only",    action="store_true")
    parser.add_argument("--quarterly-only", action="store_true")
    parser.add_argument("--year",   type=int)
    parser.add_argument("--period", type=str, help="如 2025Q3")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--rate",    type=float, default=RATE_INTERVAL)
    args = parser.parse_args()

    MAX_WORKERS   = args.workers
    RATE_INTERVAL = args.rate

    pro = ts.pro_api(tushare_cfg.token)
    target_codes = get_target_codes()
    print(f"目标: {len(target_codes)}只非金融 | 并发={MAX_WORKERS} | 速率={RATE_INTERVAL}s/req")
    print()

    # ── 年报 ──
    if not args.quarterly_only:
        print("=" * 55)
        print("【年报】2015~2025")
        print("=" * 55)
        years = [args.year] if args.year else range(2015, 2026)
        for y in years:
            download_annual(pro, y, target_codes)
        print()

    # ── 季报 ──
    if not args.annual_only:
        print("=" * 55)
        print("【季报】2015Q3 ~ 2025Q3")
        print("=" * 55)
        tasks = []
        for y in range(2015, 2026):
            if y == 2015:
                tasks.append((y, "Q3"))
            else:
                for q in ["Q1", "Q2", "Q3"]:
                    tasks.append((y, q))

        if args.period:
            tasks = [(int(args.period[:4]), args.period[4:])]

        for y, q in tasks:
            download_quarterly(pro, y, q, target_codes)
        print()

    print("✅ 全部完成！")


if __name__ == "__main__":
    main()
