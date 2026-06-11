#!/usr/bin/env python3
"""
preload_800div_prices.py — 预下载 800 红利指数复现所需的所有 daily_basic 价格快照

仿 preload_daily_basic_v2.py 模式：一次性下载所有需要的交易日 close 数据到
data/price_snapshots/{trade_date}.csv，之后 run_800div_full.py 零 API 调用。

用法：
  python preload_800div_prices.py
"""
import sys, time
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from config.settings import tushare_cfg
import tushare as ts

# 半年度调仓日（6月/12月第二个星期五的下一交易日）
REBALANCE_DATES = [
    "2015-06-15","2015-12-14","2016-06-13","2016-12-12",
    "2017-06-12","2017-12-11","2018-06-11","2018-12-17",
    "2019-06-17","2019-12-16","2020-06-15","2020-12-14",
    "2021-06-14","2021-12-13","2022-06-13","2022-12-12",
    "2023-06-12","2023-12-11","2024-06-17","2024-12-16",
    "2025-06-16","2025-12-15","2026-06-15",
]

PRICE_DIR = PROJECT_ROOT / "data" / "price_snapshots"
PRICE_DIR.mkdir(parents=True, exist_ok=True)


def main():
    pro = ts.pro_api(tushare_cfg.token)

    # ── 1. 加载交易日历 ──
    cal_file = PROJECT_ROOT / "data" / "trade_cal.csv"
    if cal_file.exists():
        cal = pd.read_csv(cal_file, dtype={"cal_date": str})
        trading_days = set(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    else:
        print("📥 下载交易日历...")
        cal = pro.trade_cal(exchange="SSE", start_date="20100101", end_date="20261231")
        cal.to_csv(cal_file, index=False)
        trading_days = set(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())

    # ── 2. 收集所有需要的价格日期 ──
    needed_dates = set()
    for d_str in REBALANCE_DATES:
        needed_dates.add(d_str)
        latest_year = int(d_str[:4]) - 1
        for yr in range(latest_year - 2, latest_year + 1):
            needed_dates.add(f"{yr}-12-31")
            needed_dates.add(f"{yr}-06-30")

    # 转换为最近交易日
    trade_date_map = {}  # {date_str: trade_date_YYYYMMDD}
    from datetime import date as dt_date
    for d in sorted(needed_dates):
        d_ymd = d.replace("-", "")
        base = dt_date(int(d_ymd[:4]), int(d_ymd[4:6]), int(d_ymd[6:8]))
        for delta in range(7):
            td = (base - timedelta(days=delta)).strftime("%Y%m%d")
            if td in trading_days:
                trade_date_map[d] = td
                break
        else:
            trade_date_map[d] = d_ymd

    # ── 3. 检查已有缓存 ──
    unique_trade_dates = set(trade_date_map.values())
    to_download = [td for td in sorted(unique_trade_dates)
                   if not (PRICE_DIR / f"{td}.csv").exists()]
    already = len(unique_trade_dates) - len(to_download)

    if not to_download:
        print(f"✅ 所有 {already} 个交易日价格已缓存，无需下载")
        return

    # ── 4. 批量下载缺失日期 ──
    print(f"📥 已有 {already} 个，需下载 {len(to_download)} 个交易日价格...")
    print()

    done = 0
    total = len(to_download)
    t_start = time.time()

    for trade_d in to_download:
        t0 = time.time()
        try:
            df = pro.daily_basic(trade_date=trade_d, fields="ts_code,trade_date,close")
            if df is not None and not df.empty:
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df[df["close"] > 0][["ts_code", "close"]]
                out_path = PRICE_DIR / f"{trade_d}.csv"
                df.to_csv(out_path, index=False)

                elapsed = time.time() - t0
                done += 1
                avg_t = (time.time() - t_start) / done
                eta = avg_t * (total - done)

                if done <= 3 or done % 10 == 0:
                    print(f"  [{done:3d}/{total}] {trade_d}: {len(df)}只 "
                          f"| {elapsed:.1f}s | eta {eta:.0f}s")
            else:
                print(f"  [{done:3d}/{total}] {trade_d}: ⚠️ 无数据")
                done += 1
        except Exception as e:
            print(f"  [{done:3d}/{total}] {trade_d}: ❌ {e}")
            done += 1

        time.sleep(0.20)

    total_t = time.time() - t_start
    print(f"\n✅ 完成! {done} 个日期, 耗时 {total_t:.0f}s ({total_t/60:.1f}min)")
    print(f"   价格快照目录: {PRICE_DIR}/ (共 {len(to_download) + already} 个文件)")


if __name__ == "__main__":
    main()
