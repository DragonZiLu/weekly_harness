#!/usr/bin/env python3
"""
regenerate_lenient_baskets.py — B版篮子生成（fixed+宽松OCF）

使用 total_mv + TTM + 宽松5yr OCF 规则，生成 ZZ800 和 HS300 篮子。
"""
import sys, json, time
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from fcf_universe import FcfUniverse

# 调仓日期列表（与现有篮子对齐）
REBALANCE_DATES = [
    "2015-03-16", "2015-06-15", "2015-09-14", "2015-12-14",
    "2016-03-14", "2016-06-13", "2016-09-12", "2016-12-12",
    "2017-03-13", "2017-06-12", "2017-09-11", "2017-12-11",
    "2018-03-12", "2018-06-11", "2018-09-17", "2018-12-17",
    "2019-03-11", "2019-06-17", "2019-09-16", "2019-12-16",
    "2020-03-16", "2020-06-15", "2020-09-14", "2020-12-14",
    "2021-03-15", "2021-06-14", "2021-09-13", "2021-12-13",
    "2022-03-14", "2022-06-13", "2022-09-12", "2022-12-12",
    "2023-03-13", "2023-06-12", "2023-09-11", "2023-12-11",
    "2024-03-11", "2024-06-17", "2024-09-16", "2024-12-16",
    "2025-03-17", "2025-06-16", "2025-09-15", "2025-12-15",
    "2026-03-16", "2026-06-15",
]

def generate_baskets(index_code, label, output_dir):
    """生成指定指数的B版篮子"""
    output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "all_baskets_2015_2026.json"

    print(f"\n{'='*60}")
    print(f"  生成 {label} B版篮子 (fixed+宽松OCF)")
    print(f"{'='*60}")

    uni = FcfUniverse(index_code=index_code, strict_ocf=False)
    uni.preload_all(download=False)

    all_baskets = {}
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        period_t0 = time.time()
        try:
            basket = uni.get_fcf_basket(date_str, top_n=50, verbose=False, use_ttm=True)
            n = len(basket) - (1 if "__quality_warnings__" in basket else 0)
            all_baskets[date_str] = basket

            # 记录前3只股票名称
            items = [(k, v) for k, v in basket.items() if k != "__quality_warnings__"]
            names = [v.get("name", "") for _, v in items[:3]]

            elapsed = time.time() - period_t0
            total_elapsed = time.time() - t0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: {n} stocks, "
                  f"elapsed={elapsed:.1f}s (total={total_elapsed:.0f}s)")
        except Exception as e:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR - {e}")
            all_baskets[date_str] = []

    # Save
    # Convert dict-of-dicts to list-of-dicts format
    save_baskets = {}
    for d, basket in all_baskets.items():
        if isinstance(basket, dict):
            items = [(k, v) for k, v in basket.items() if k != "__quality_warnings__"]
            save_baskets[d] = [{"ts_code": k, **v} for k, v in items]
        else:
            save_baskets[d] = basket

    with open(output_path, "w") as f:
        json.dump(save_baskets, f, ensure_ascii=False, indent=2)

    valid = sum(1 for d in save_baskets if len(save_baskets[d]) >= 10)
    print(f"\n  ✅ 保存到 {output_path}")
    print(f"  总期数: {len(save_baskets)}, 有效(≥10): {valid}")
    total_time = time.time() - t0
    print(f"  总耗时: {total_time:.0f}s ({total_time/60:.1f}min)")

    return save_baskets


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", choices=["zz800", "hs300", "both"], default="both")
    args = parser.parse_args()

    if args.index in ("zz800", "both"):
        generate_baskets("000906.SH", "ZZ800", "output/zz800_fcf_fixed_lenient")

    if args.index in ("hs300", "both"):
        generate_baskets("000300.SH", "HS300", "output/hs300_fcf_fixed_lenient")
