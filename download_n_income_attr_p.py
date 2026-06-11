"""
补充下载 ZZ800 成分股各年度年报的 n_income_attr_p（归母净利润）字段。
将数据保存为 income_{year}_annual.csv，包含宽格式字段。
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

MAX_WORKERS = 6
_rate_lock = threading.Lock()
_last_call_time = [0.0]

def rate_limit(min_interval=0.08):
    with _rate_lock:
        now = time.time()
        wait = _last_call_time[0] + min_interval - now
        if wait > 0:
            time.sleep(wait)
        _last_call_time[0] = time.time()

# 下载字段（年报全字段，重点要 n_income_attr_p）
INC_FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,revenue,total_revenue,oper_cost,operate_profit,total_profit,n_income,n_income_attr_p,basic_eps"

# 年份范围：2010-2025（覆盖 2015-03 起回测所需的 TTM 计算）
YEARS = list(range(2010, 2026))


def get_zz800_all_codes():
    """获取 ZZ800 历史上所有出现过成分股的股票代码"""
    iw_path = DATA_DIR.parent / "index_weights" / "index_weight_000906.SH.csv"
    if not iw_path.exists():
        print(f"⚠️ 未找到 {iw_path}，尝试扫描 index_weights 目录...")
        codes = set()
        for f in (DATA_DIR.parent / "index_weights").glob("index_weight_000906*"):
            df = pd.read_csv(f, dtype=str)
            if 'con_code' in df.columns:
                codes |= set(df['con_code'].astype(str))
        return sorted(codes) if codes else []

    df = pd.read_csv(iw_path, dtype=str)
    if 'con_code' in df.columns:
        codes = sorted(set(df['con_code'].astype(str)))
        print(f"从 index_weight_000906.SH.csv 获取 {len(codes)} 只 ZZ800 历史成分股")
        return codes
    return []


def download_annual_income(pro, code, year):
    """下载单只股票某年度年报的 income 数据"""
    end_date_str = f"{year}1231"
    start_date = f"{year}0101"
    end_date_query = f"{year+1}0430"  # 年报通常在次年4月30日前公布
    try:
        rate_limit()
        inc = pro.income(
            ts_code=code,
            start_date=start_date,
            end_date=end_date_query,
            fields=INC_FIELDS
        )
        if inc is not None and not inc.empty:
            # 精确匹配 end_date = YYYY1231
            rows = inc[inc['end_date'].astype(str).str[:8] == end_date_str]
            if not rows.empty:
                # 优先取合并报表 (report_type=1)
                if 'report_type' in rows.columns:
                    pref = rows[rows['report_type'].astype(str) == '1']
                    return (pref if not pref.empty else rows).iloc[-1].to_dict()
                return rows.iloc[-1].to_dict()
    except Exception as e:
        pass
    return None


def load_existing_codes(path, end_date_str):
    """读取已有文件中的股票代码"""
    if not path.exists():
        return set()
    df = pd.read_csv(path, dtype={"ts_code": str})
    if 'end_date' in df.columns:
        return set(df[df['end_date'].astype(str).str[:8] == end_date_str]['ts_code'])
    return set()


def main():
    pro = ts.pro_api(tushare_cfg.token)
    codes = get_zz800_all_codes()
    if not codes:
        print("❌ 无法获取 ZZ800 成分股列表")
        return

    print(f"目标: {len(codes)} 只 ZZ800 成分股 × {len(YEARS)} 年 = 约 {len(codes)*len(YEARS)} 次下载")
    print(f"并发: {MAX_WORKERS} workers\n")

    total_new = 0

    for year in YEARS:
        end_date_str = f"{year}1231"
        out_path = DATA_DIR / f"income_{year}_annual.csv"

        existing = load_existing_codes(out_path, end_date_str)
        need_codes = [c for c in codes if c not in existing]

        if not need_codes:
            print(f"✅ {year}年报: 已完整 ({len(existing)}只)")
            continue

        print(f"\n📥 {year}年报 (end={end_date_str}): 需要 {len(need_codes)}/{len(codes)} 只...")
        t0 = time.time()
        rows = []
        err_count = 0
        done = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(download_annual_income, pro, code, year): code
                for code in need_codes
            }
            for future in as_completed(futures):
                done += 1
                try:
                    row = future.result()
                    if row:
                        rows.append(row)
                except Exception:
                    err_count += 1
                if done % 200 == 0 or done == len(need_codes):
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 1
                    remain = (len(need_codes) - done) / rate / 60 if rate > 0 else 0
                    print(f"  {done}/{len(need_codes)} | got={len(rows)} | err={err_count} | ~{remain:.1f}min left")

        if rows:
            new_df = pd.DataFrame(rows)
            if out_path.exists():
                old = pd.read_csv(out_path, dtype={"ts_code": str})
                combined = pd.concat([old, new_df], ignore_index=True)
                combined['ann_date'] = combined['ann_date'].astype(str)
                combined = combined.drop_duplicates(subset=['ts_code', 'end_date'], keep='last')
                combined.to_csv(out_path, index=False)
            else:
                new_df.to_csv(out_path, index=False)
            added = len(rows)
            total_new += added
            print(f"  ✅ {year}: +{added}条 | 文件共 {len(pd.read_csv(out_path))} 条")
        else:
            print(f"  ⚠️ {year}: 0条新数据")

    print(f"\n✅ 全部完成！共新增 {total_new} 条年报 income 数据")
    print(f"文件位置: {DATA_DIR}/income_*_annual.csv")


if __name__ == "__main__":
    main()
