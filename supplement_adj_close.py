#!/usr/bin/env python3
"""
补充缺失的 adj_close 缓存数据。
从 Tushare 拉取复权行情数据，生成与现有缓存格式一致的 CSV。
"""

import os
import sys
import time
import pandas as pd
from pathlib import Path
from typing import List, Set

# 项目路径
ROOT = Path(__file__).parent
ADJ_CACHE_DIR = ROOT / "data" / "adj_close_cache"
BAGGERS_CACHE = ROOT / "data" / "baggers"

# Tushare 配置
from config.settings import tushare_cfg
import tushare as ts


def init_pro():
    if not tushare_cfg.token:
        print("❌ TUSHARE_TOKEN 未配置")
        sys.exit(1)
    return ts.pro_api(tushare_cfg.token)


def get_missing_codes() -> List[str]:
    """找出 pool 中有但 adj_close 缓存缺失的标的"""
    # 读取成分池
    pool = set()
    for idx_file in BAGGERS_CACHE.glob("index_*.csv"):
        df = pd.read_csv(idx_file)
        if "con_code" in df.columns:
            pool.update(df["con_code"].tolist())
    print(f"成分池: {len(pool)} 只")

    # 已有缓存
    cached = set()
    for f in ADJ_CACHE_DIR.glob("*.csv"):
        cached.add(f.stem)
    print(f"已有缓存: {len(cached)} 只")

    missing = sorted(pool - cached)
    print(f"缺失: {len(missing)} 只")
    return missing


def pull_and_save(pro, missing: List[str], batch_size: int = 50) -> tuple:
    """
    分批拉取复权行情，保存为 adj_close 缓存文件。
    直接使用 pro.daily 拉取日线 (含 close) + 自行计算后复权，
    或直接用 qfq 复权因子。

    Tushare 限制: 每次最多查 5000 条，推荐按 ts_code 逐只拉取。
    使用 daily 接口获取基础数据，然后用 adj_factor 算复权价。
    """
    success = 0
    fail = 0

    # 日期范围：覆盖回测需要（至少 2015-2026）
    start_date = "20150101"
    end_date = "20260624"

    total = len(missing)
    for i, ts_code in enumerate(missing):
        try:
            # 拉取日线行情
            df_daily = pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,close"
            )

            if df_daily is None or df_daily.empty:
                print(f"  ⚠️ [{i+1}/{total}] {ts_code}: 无日线数据")
                fail += 1
                continue

            # 拉取复权因子
            df_adj = pro.adj_factor(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,adj_factor"
            )

            if df_adj is None or df_adj.empty:
                print(f"  ⚠️ [{i+1}/{total}] {ts_code}: 无复权因子，仅存原始价格")
                df_daily["adj_factor"] = 1.0
            else:
                df_daily["trade_date"] = df_daily["trade_date"].astype(str)
                df_adj["trade_date"] = df_adj["trade_date"].astype(str)
                df_daily = df_daily.merge(
                    df_adj[["trade_date", "adj_factor"]],
                    on="trade_date",
                    how="left"
                )
                df_daily["adj_factor"] = df_daily["adj_factor"].fillna(1.0)

            # 计算后复权价
            df_daily["adj_close"] = df_daily["close"] * df_daily["adj_factor"]
            df_daily = df_daily.sort_values("trade_date")

            # 保存
            cache_path = ADJ_CACHE_DIR / f"{ts_code}.csv"
            df_daily[["ts_code", "trade_date", "close", "adj_factor", "adj_close"]].to_csv(
                cache_path, index=False
            )

            success += 1
            if (i + 1) % 20 == 0:
                print(f"  📦 [{i+1}/{total}] 已完成 {success} 只, 失败 {fail} 只...")
                time.sleep(1)  # 每 20 只休息 1 秒防止限流

            # Tushare 频率限制：约 200次/分钟
            time.sleep(0.15)

        except Exception as e:
            print(f"  ❌ [{i+1}/{total}] {ts_code}: {e}")
            fail += 1
            time.sleep(0.5)

    return success, fail


def main():
    print("🔍 扫描缺失的 adj_close 缓存...")
    missing = get_missing_codes()

    if not missing:
        print("✅ 所有标的已覆盖，无需补充！")
        return

    print(f"\n📥 开始补充 {len(missing)} 只标的的复权行情数据...")
    print(f"   日期范围: 2015-01-01 → 2026-06-24")
    print(f"   预计耗时: ~{len(missing) * 0.35 / 60:.0f} 分钟\n")

    pro = init_pro()
    success, fail = pull_and_save(pro, missing)

    print(f"\n{'='*50}")
    print(f"✅ 补充完成：成功 {success} 只，失败 {fail} 只")

    # 最终统计
    cached = len(list(ADJ_CACHE_DIR.glob("*.csv")))
    print(f"adj_close_cache 总计: {cached} 只")


if __name__ == "__main__":
    main()
