"""
补全季报数据 — 针对ZZ800+HS300目标股票，补全所有缺失的季报期次
目标：cashflow/balance/income 的 Q1(0331) / Q2(0630) / Q3(0930) 各期

缺口清单（诊断结果）：
- 2019Q3 ~ 2022Q3: cashflow 只有4-6只（应有~76只目标股票）
- 2023Q1/Q2: cashflow ~50只（缺26只）
- 2024Q1/Q2: cashflow ~50只（缺26只）
- 2025Q1: cashflow 44只（缺32只），balance 45只（缺31只），income 44只（缺32只）
- 2025Q2: cashflow 58只（缺18只），balance 23只（缺53只），income 22只（缺54只）
- 2025Q3: cashflow 9只（缺67只），balance 17只（缺59只），income 64只（缺12只）
"""
import sys, time, pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg
import tushare as ts

DATA_DIR = _PROJECT_ROOT / "data" / "fcf_financials"

MAX_WORKERS = 8
_rate_lock = threading.Lock()
_last_call_time = [0.0]

def rate_limit(min_interval=0.06):
    with _rate_lock:
        now = time.time()
        wait = _last_call_time[0] + min_interval - now
        if wait > 0:
            time.sleep(wait)
        _last_call_time[0] = time.time()

# 季报期次配置：(period_label, end_date_str, start_date, end_date_for_query)
# start/end_date 是tushare查询窗口
QUARTERLY_PERIODS = [
    # 2019~2022 Q3（三季报，严重缺失）
    ("2019Q3", "20190930", "20190801", "20191231"),
    ("2020Q3", "20200930", "20200801", "20201231"),
    ("2021Q3", "20210930", "20210801", "20211231"),
    ("2022Q3", "20220930", "20220801", "20221231"),
    # 2023 Q1/Q2
    ("2023Q1", "20230331", "20230301", "20230531"),
    ("2023Q2", "20230630", "20230601", "20230831"),
    # 2024 Q1/Q2
    ("2024Q1", "20240331", "20240301", "20240531"),
    ("2024Q2", "20240630", "20240601", "20240831"),
    # 2025 Q1/Q2/Q3（全三张表）
    ("2025Q1", "20250331", "20250301", "20250531"),
    ("2025Q2", "20250630", "20250601", "20250831"),
    ("2025Q3", "20250930", "20251001", "20260131"),  # Q3公告在10-11月
]

CF_FIELDS = "ts_code,ann_date,f_ann_date,end_date,comp_type,report_type,end_type,n_cashflow_act,c_pay_acq_const_fiolta"
BS_FIELDS = "ts_code,ann_date,end_date,total_liab,money_cap,total_assets"
INC_FIELDS = "ts_code,ann_date,end_date,operate_profit"


def download_one_stock_quarter(pro, code, end_date_str, start_date, end_date):
    """下载单只股票某季报期次的三张表，返回 (cf_row, bs_row, inc_row)"""
    cf_row = bs_row = inc_row = None
    try:
        rate_limit()
        cf = pro.cashflow(
            ts_code=code, start_date=start_date, end_date=end_date,
            fields=CF_FIELDS
        )
        if cf is not None and not cf.empty:
            rows = cf[cf['end_date'].astype(str).str[:8] == end_date_str]
            # 优先取 report_type=1（合并报表），避免重复
            if not rows.empty:
                if 'report_type' in rows.columns:
                    pref = rows[rows['report_type'].astype(str) == '1']
                    cf_row = (pref if not pref.empty else rows).iloc[-1].to_dict()
                else:
                    cf_row = rows.iloc[-1].to_dict()
    except Exception:
        pass

    try:
        rate_limit()
        bs = pro.balancesheet(
            ts_code=code, start_date=start_date, end_date=end_date,
            fields=BS_FIELDS
        )
        if bs is not None and not bs.empty:
            rows = bs[bs['end_date'].astype(str).str[:8] == end_date_str]
            if not rows.empty:
                bs_row = rows.iloc[-1].to_dict()
    except Exception:
        pass

    try:
        rate_limit()
        inc = pro.income(
            ts_code=code, start_date=start_date, end_date=end_date,
            fields=INC_FIELDS
        )
        if inc is not None and not inc.empty:
            rows = inc[inc['end_date'].astype(str).str[:8] == end_date_str]
            if not rows.empty:
                inc_row = rows.iloc[-1].to_dict()
    except Exception:
        pass

    return cf_row, bs_row, inc_row


def load_existing_codes(path, end_date_str):
    """从CSV中读取已有某期次的股票代码集合"""
    if not path.exists():
        return set()
    df = pd.read_csv(path, dtype={"ts_code": str})
    if 'end_date' in df.columns:
        return set(df[df['end_date'].astype(str).str[:8] == end_date_str]['ts_code'])
    return set()


def append_rows(path, rows):
    """追加新行到CSV（去重）"""
    if not rows:
        return 0
    new_df = pd.DataFrame(rows)
    if path.exists():
        old = pd.read_csv(path, dtype={"ts_code": str})
        combined = pd.concat([old, new_df], ignore_index=True)
        # 去重（同一ts_code+end_date保留最新）
        combined['ann_date'] = combined['ann_date'].astype(str)
        combined = combined.sort_values('ann_date', ascending=False, na_position='last')
        if 'end_date' in combined.columns:
            combined = combined.drop_duplicates(subset=['ts_code', 'end_date'], keep='first')
        combined.to_csv(path, index=False)
    else:
        new_df.to_csv(path, index=False)
    return len(rows)


def get_target_codes():
    """获取ZZ800+HS300最新成分股（作为补全目标）"""
    iw_dir = _PROJECT_ROOT / "data/index_weights"
    codes = set()
    for f in iw_dir.glob("index_weight_932368.CSI_*.csv"):
        df = pd.read_csv(f)
        codes |= set(df['con_code'].astype(str))
    for f in iw_dir.glob("index_weight_932366.CSI_*.csv"):
        df = pd.read_csv(f)
        codes |= set(df['con_code'].astype(str))
    # 也包含历史权重文件中的所有成分股
    return codes


def main():
    pro = ts.pro_api(tushare_cfg.token)
    target_codes = sorted(get_target_codes())
    print(f"目标股票: {len(target_codes)}只（ZZ800+HS300历史+当前成分）")

    total_added = 0

    for period_label, end_date_str, start_date, end_date in QUARTERLY_PERIODS:
        cf_path = DATA_DIR / f"cashflow_{period_label}.csv"
        bs_path = DATA_DIR / f"balance_{period_label}.csv"
        inc_path = DATA_DIR / f"income_{period_label}.csv"

        # 识别已有的股票
        existing_cf = load_existing_codes(cf_path, end_date_str)
        existing_bs = load_existing_codes(bs_path, end_date_str)
        existing_inc = load_existing_codes(inc_path, end_date_str)

        # 需要补的：三张表中任意一张缺失的股票
        need_codes = [c for c in target_codes if c not in existing_cf or c not in existing_bs or c not in existing_inc]

        if not need_codes:
            print(f"✅ {period_label}: 已完整（{len(existing_cf)}只）")
            continue

        print(f"\n📥 {period_label} (end={end_date_str}): 补 {len(need_codes)}/{len(target_codes)} 只...")
        t0 = time.time()
        cf_rows, bs_rows, inc_rows = [], [], []
        err_count = 0
        done = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(download_one_stock_quarter, pro, code, end_date_str, start_date, end_date): code
                for code in need_codes
            }
            for future in as_completed(futures):
                done += 1
                try:
                    cf_r, bs_r, inc_r = future.result()
                    if cf_r: cf_rows.append(cf_r)
                    if bs_r: bs_rows.append(bs_r)
                    if inc_r: inc_rows.append(inc_r)
                except Exception:
                    err_count += 1
                if done % 100 == 0 or done == len(need_codes):
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 1
                    remain = (len(need_codes) - done) / rate / 60
                    print(f"  {done}/{len(need_codes)} | cf={len(cf_rows)} bs={len(bs_rows)} inc={len(inc_rows)} | err={err_count} | ~{remain:.0f}min left")

        added = append_rows(cf_path, cf_rows) + append_rows(bs_path, bs_rows) + append_rows(inc_path, inc_rows)
        total_added += added
        print(f"  ✅ {period_label}: +{added}行 | cf={len(cf_rows)} bs={len(bs_rows)} inc={len(inc_rows)}")

    print(f"\n✅ 全部完成！新增 {total_added} 行")


if __name__ == "__main__":
    main()
