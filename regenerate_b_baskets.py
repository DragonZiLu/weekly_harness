#!/usr/bin/env python3
"""
regenerate_b_baskets.py — B版篮子生成（fixed+宽松OCF）

规则: total_mv + TTM + 宽松5yr OCF (strict_ocf=False)
对比: A版(circ_mv+无TTM+宽松OCF) vs B版 → 纯EV+TTM影响
"""
import sys, json, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
# fcf_universe.py 在 weekly_harness/ 子目录
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from fcf_universe import FcfUniverse

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


def generate_baskets(index_code, label, output_dir_name):
    out_dir = PROJECT_ROOT / "output" / output_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "all_baskets_2015_2026.json"

    print(f"\n{'='*60}")
    print(f"  {label} — B版篮子 (total_mv + TTM + 宽松OCF)")
    print(f"{'='*60}")

    # strict_ocf=False → 宽松OCF; use_ttm=True → TTM口径; total_mv已是默认EV
    uni = FcfUniverse(index_code=index_code, strict_ocf=False)
    uni.preload_all(download=False)

    all_baskets = {}
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        pt0 = time.time()
        try:
            basket_dict = uni.get_fcf_basket(date_str, top_n=50, verbose=False, use_ttm=True)
            # strip __quality_warnings__
            items = [(k, v) for k, v in basket_dict.items() if k != "__quality_warnings__"]
            n = len(items)
            all_baskets[date_str] = [{"ts_code": k, **v} for k, v in items]
            elapsed = time.time() - pt0
            total_e = time.time() - t0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: {n} stocks "
                  f"({elapsed:.1f}s | total {total_e:.0f}s)")
        except Exception as e:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR - {e}")
            all_baskets[date_str] = []

    with open(out_path, "w") as f:
        json.dump(all_baskets, f, ensure_ascii=False, indent=2)

    valid = sum(1 for d in all_baskets if len(all_baskets[d]) >= 10)
    total_t = time.time() - t0
    print(f"\n  ✅ Saved → {out_path}")
    print(f"  总期数: {len(all_baskets)}, 有效(≥10): {valid}, 耗时: {total_t:.0f}s ({total_t/60:.1f}min)")
    return all_baskets


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", choices=["zz800", "hs300", "both"], default="both")
    args = parser.parse_args()

    if args.index in ("zz800", "both"):
        generate_baskets("000906.SH", "ZZ800 FCF", "zz800_fcf_fixed_lenient")

    if args.index in ("hs300", "both"):
        generate_baskets("000300.SH", "HS300 FCF", "hs300_fcf_fixed_lenient")

    print("\n\nAll done!")
