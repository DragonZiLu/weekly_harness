"""
preload_daily_basic_v2.py — 补充 free_share + close 字段到 daily_basic 缓存
用于计算真正的自由流通市值（free_share × close），替代 circ_mv
"""
import sys, time
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from config.settings import tushare_cfg
import tushare as ts

PROJECT_ROOT = Path(__file__).parent
CACHE_DIR = PROJECT_ROOT / "data" / "fcf_financials" / "daily_basic_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

REBALANCE_DATES = [
    "2015-03-16","2015-06-15","2015-09-14","2015-12-14",
    "2016-03-14","2016-06-13","2016-09-12","2016-12-12",
    "2017-03-13","2017-06-12","2017-09-11","2017-12-11",
    "2018-03-12","2018-06-11","2018-09-17","2018-12-17",
    "2019-03-11","2019-06-17","2019-09-16","2019-12-16",
    "2020-03-16","2020-06-15","2020-09-14","2020-12-14",
    "2021-03-15","2021-06-14","2021-09-13","2021-12-13",
    "2022-03-14","2022-06-13","2022-09-12","2022-12-12",
    "2023-03-13","2023-06-12","2023-09-11","2023-12-11",
    "2024-03-11","2024-06-17","2024-09-16","2024-12-16",
    "2025-03-17","2025-06-16","2025-09-15","2025-12-15",
    "2026-03-16","2026-06-15",
]

NEW_FIELDS = "ts_code,close,total_mv,circ_mv,free_share,total_share"


def main():
    pro = ts.pro_api(tushare_cfg.token)

    # 计算所有需要的日期（每个调仓日 ± 6天）
    needed_dates = set()
    for d_str in REBALANCE_DATES:
        base = datetime.strptime(d_str.replace("-", ""), "%Y%m%d")
        for delta in range(7):
            needed_dates.add((base - timedelta(days=delta)).strftime("%Y%m%d"))

    # 已有缓存（v1 只有 total_mv,circ_mv → 需要覆盖）
    to_download = sorted(needed_dates)
    print(f"总日期: {len(to_download)} | 字段: {NEW_FIELDS}")
    print()

    done = 0
    total = len(to_download)
    t_start = time.time()

    for date_key in to_download:
        t0 = time.time()
        try:
            df = pro.daily_basic(trade_date=date_key, fields=NEW_FIELDS)
            if df is not None and not df.empty:
                out_path = CACHE_DIR / f"daily_basic_{date_key}.csv"
                df.to_csv(out_path, index=False)
                free_ok = df["free_share"].notna().sum()
                elapsed = time.time() - t0

                # ETA
                done += 1
                avg_t = (time.time() - t_start) / done
                eta = avg_t * (total - done)

                # 只每隔5条打印详情
                if done <= 3 or done % 20 == 0:
                    print(f"  [{done:3d}/{total}] {date_key}: {len(df)}只 "
                          f"free_share覆盖{free_ok} | {elapsed:.1f}s | eta {eta/60:.1f}min")
            else:
                print(f"  [{done:3d}/{total}] {date_key}: ⚠️ 无数据")
                done += 1
        except Exception as e:
            print(f"  [{done:3d}/{total}] {date_key}: ❌ {e}")
            done += 1

        time.sleep(0.25)

    total_t = time.time() - t_start
    print(f"\n✅ 完成! {done} 日期, 耗时 {total_t/60:.1f}min")


if __name__ == "__main__":
    main()
