#!/usr/bin/env python3
"""
download_daily_basic_cache.py — 一次性下载所有调仓日的 daily_basic 数据到本地
=================================================================

daily_basic 包含 total_mv（总市值）和 circ_mv（流通市值），
是 fcf_universe.py 中唯一的非缓存API调用瓶颈。

本脚本将所有调仓日的 daily_basic 数据批量下载并缓存为 CSV，
后续 get_fcf_basket() 可直接读本地数据，无需每次实时调用API。
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

CACHE_DIR = PROJECT_ROOT / "data" / "fcf_financials" / "daily_basic_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_rebalance_dates():
    """获取所有历史调仓日（3/6/9/12月第二个周五的下一交易日）"""
    dates = []
    for year in range(2015, 2027):
        for month in [3, 6, 9, 12]:
            # 找该月第二个周五
            first_day = datetime(year, month, 1)
            # 找第一个周五
            first_friday = first_day
            while first_friday.weekday() != 4:  # Friday = 4
                first_friday += timedelta(days=1)
            # 第二个周五 = 第一个 + 7天
            second_friday = first_friday + timedelta(days=7)
            # 下一交易日 = 第二个周五 + 1天（假设周六非交易日）
            # 实际上有时是周一，取第二个周五后的下一个工作日
            next_trade = second_friday
            if next_trade.weekday() == 4:  # 周五
                next_trade += timedelta(days=1)  # 周六 → 取周一
                if next_trade.weekday() == 5:
                    next_trade += timedelta(days=2)
            elif next_trade.weekday() == 5:  # 周六
                next_trade += timedelta(days=2)  # → 周一
            elif next_trade.weekday() == 6:  # 周日
                next_trade += timedelta(days=1)  # → 周一
            
            # 修正：第二个周五的下一交易日通常是下周一
            # 但有时是周五本身（如2024-12-16）
            # 简化：直接取第二个周五日期作为近似
            rb_date = second_friday.strftime("%Y-%m-%d")
            dates.append(rb_date)
    
    return sorted(dates)


def download_daily_basic_for_date(trade_date_str: str) -> bool:
    """下载指定交易日的 daily_basic 数据并缓存"""
    date_key = trade_date_str.replace("-", "")
    cache_file = CACHE_DIR / f"daily_basic_{date_key}.csv"
    
    if cache_file.exists():
        # 已缓存，跳过
        return True
    
    # 尝试最近6个交易日（调仓日可能非交易日）
    from datetime import datetime as dt, timedelta as td
    base = dt.strptime(date_key, "%Y%m%d")
    
    for delta in range(6):
        d = (base - td(days=delta)).strftime("%Y%m%d")
        cache_file_delta = CACHE_DIR / f"daily_basic_{d}.csv"
        
        if cache_file_delta.exists():
            return True
        
        try:
            df = pro.daily_basic(
                trade_date=d,
                fields="ts_code,trade_date,total_mv,circ_mv,pe_ttm,pb",
            )
            if df is not None and not df.empty:
                df.to_csv(cache_file_delta, index=False)
                print(f"  ✅ {d}: {len(df)} stocks cached")
                return True
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ {d}: {str(e)[:50]}")
            time.sleep(1)
    
    return False


def main():
    rebalance_dates = get_rebalance_dates()
    print(f"调仓日期数: {len(rebalance_dates)}")
    print(f"缓存目录: {CACHE_DIR}")
    
    # 检查已有缓存
    existing = list(CACHE_DIR.glob("daily_basic_*.csv"))
    print(f"已有缓存: {len(existing)} 个交易日")
    
    # 需要下载的日期
    to_download = []
    for date in rebalance_dates:
        date_key = date.replace("-", "")
        # 检查最近6天是否有缓存
        base = datetime.strptime(date_key, "%Y%m%d")
        found = False
        for delta in range(6):
            d = (base - timedelta(days=delta)).strftime("%Y%m%d")
            if (CACHE_DIR / f"daily_basic_{d}.csv").exists():
                found = True
                break
        if not found:
            to_download.append(date)
    
    print(f"需要下载: {len(to_download)} 个日期")
    print(f"已缓存可复用: {len(rebalance_dates) - len(to_download)} 个日期")
    
    success = 0
    failed = 0
    for i, date in enumerate(to_download):
        print(f"\n[{i+1}/{len(to_download)}] 下载 {date}...")
        ok = download_daily_basic_for_date(date)
        if ok:
            success += 1
        else:
            failed += 1
            print(f"  ❌ {date}: 下载失败")
    
    print(f"\n{'='*60}")
    print(f"完成: {success} 成功, {failed} 失败")
    print(f"总缓存文件: {len(list(CACHE_DIR.glob('daily_basic_*.csv')))}")


if __name__ == "__main__":
    main()