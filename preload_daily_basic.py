"""
preload_daily_basic.py — 预加载所有调仓日的 daily_basic 缓存
消除回测时实时拉取市值的API瓶颈
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

def date_to_key(d_str):
    return d_str.replace("-", "")

def main():
    pro = ts.pro_api(tushare_cfg.token)

    # 计算所有需要的日期（每个调仓日 ± 6天）
    needed_dates = set()
    for d_str in REBALANCE_DATES:
        base = datetime.strptime(date_to_key(d_str), "%Y%m%d")
        for delta in range(7):  # 0~6天前
            needed_dates.add((base - timedelta(days=delta)).strftime("%Y%m%d"))

    # 已有缓存
    cached = set(f.stem.replace("daily_basic_", "") for f in CACHE_DIR.glob("daily_basic_*.csv"))
    to_download = sorted(needed_dates - cached)

    print(f"需要日期: {len(needed_dates)} | 已缓存: {len(cached)} | 待下载: {len(to_download)}")
    print()

    done = 0
    for date_key in to_download:
        t0 = time.time()
        try:
            df = pro.daily_basic(trade_date=date_key, fields="ts_code,total_mv,circ_mv")
            if df is not None and not df.empty:
                out_path = CACHE_DIR / f"daily_basic_{date_key}.csv"
                df.to_csv(out_path, index=False)
                print(f"  ✅ {date_key}: {len(df)}只 ({time.time()-t0:.2f}s)")
            else:
                print(f"  ⚠️ {date_key}: 无数据（非交易日）")
        except Exception as e:
            print(f"  ❌ {date_key}: {e}")
        time.sleep(0.13)  # 频率限制
        done += 1
        if done % 10 == 0:
            print(f"  进度: {done}/{len(to_download)}")

    # 最终统计
    cached_final = set(f.stem.replace("daily_basic_", "") for f in CACHE_DIR.glob("daily_basic_*.csv"))
    print(f"\n完成! 缓存覆盖: {len(cached_final)}/{len(needed_dates)} 日期")


if __name__ == "__main__":
    main()