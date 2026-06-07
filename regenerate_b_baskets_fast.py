#!/usr/bin/env python3
"""
regenerate_b_baskets_fast.py — B版篮子生成（内存缓存加速版）

与 regenerate_b_baskets.py 完全相同的结果，但通过预加载所有
daily_basic CSV 到内存，避免每个调仓期重复读盘和 Tushare API 调用。

加速原理：
  原始版: 每期 → _batch_fetch_market_cap → 读 CSV/调 API（~22s/期）
  加速版: 启动时一次性加载 45 个 CSV → 每期纯内存查找（<0.5s/期）

用法：
  python regenerate_b_baskets_fast.py              # 生成 + 与原始版对比
  python regenerate_b_baskets_fast.py --gen-only   # 仅生成，不对比
  python regenerate_b_baskets_fast.py --cmp-only   # 仅对比已有输出
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
load_dotenv(PROJECT_ROOT / ".env")

from fcf_universe import FcfUniverse

# ══════════════════════════════════════════════════════════════════
# 调仓日期（与 regenerate_b_baskets.py 完全一致）
# ══════════════════════════════════════════════════════════════════
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

CACHE_DIR = PROJECT_ROOT / "data" / "fcf_financials" / "daily_basic_cache"


# ══════════════════════════════════════════════════════════════════
# 预加载 daily_basic 到内存
# ══════════════════════════════════════════════════════════════════

def preload_daily_basic() -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    一次性加载所有 daily_basic CSV 到内存。

    返回格式:
    {
        "20150313": {
            "000001.SZ": {"total_mv": 1.23e11, "circ_mv": 1.20e11},
            "000002.SZ": {"total_mv": 2.34e11, "circ_mv": 2.30e11},
            ...
        },
        ...
    }
    """
    if not CACHE_DIR.exists():
        print(f"⚠️  daily_basic 缓存目录不存在: {CACHE_DIR}")
        return {}

    preloaded: Dict[str, Dict[str, Dict[str, float]]] = {}
    csv_files = sorted(CACHE_DIR.glob("daily_basic_*.csv"))

    for f in csv_files:
        # 从文件名提取日期: daily_basic_20150313.csv → 20150313
        date_str = f.stem.replace("daily_basic_", "")
        try:
            df = pd.read_csv(f, dtype={"ts_code": str})
        except Exception as e:
            print(f"⚠️  读取失败 {f.name}: {e}")
            continue

        day_data: Dict[str, Dict[str, float]] = {}
        for _, row in df.iterrows():
            code = str(row["ts_code"])
            total_mv = row.get("total_mv")
            circ_mv = row.get("circ_mv")
            entry = {}
            if pd.notna(total_mv) and float(total_mv) > 0:
                entry["total_mv"] = float(total_mv)
            if pd.notna(circ_mv) and float(circ_mv) > 0:
                entry["circ_mv"] = float(circ_mv)
            if entry:
                day_data[code] = entry

        preloaded[date_str] = day_data

    return preloaded


def make_fast_market_cap(preloaded: Dict[str, Dict[str, Dict[str, float]]]):
    """
    返回一个替代 _batch_fetch_market_cap 的快速版本。
    完全复用原始方法的 6 天回溯逻辑，但数据来自内存。
    """

    def fast_market_cap(self, pro, date_str: str, ts_codes: List[str]) -> Dict[str, float]:
        code_set = set(ts_codes)
        date_key = date_str.replace("-", "")
        base = datetime.strptime(date_key, "%Y%m%d")
        result: Dict[str, float] = {}

        # 回溯最多 6 天（与原始逻辑完全一致）
        for delta in range(6):
            d = (base - timedelta(days=delta)).strftime("%Y%m%d")
            day_data = preloaded.get(d)
            if day_data is None:
                continue

            # 从内存中提取数据（与 _extract_mv_from_daily_basic_df 逻辑一致）
            for code in ts_codes:
                if code not in code_set:
                    continue
                stock_data = day_data.get(code)
                if stock_data is None:
                    continue
                if code not in result and "total_mv" in stock_data:
                    result[code] = stock_data["total_mv"]
                circ_key = f"{code}_circ"
                if circ_key not in result and "circ_mv" in stock_data:
                    result[circ_key] = stock_data["circ_mv"]

            if result:
                return result

        # 缓存未命中 — 不会调用 API（与原始版不同），返回空结果
        return result

    return fast_market_cap


# ══════════════════════════════════════════════════════════════════
# 篮子生成（逻辑与 regenerate_b_baskets.py 完全一致）
# ══════════════════════════════════════════════════════════════════

def generate_baskets_fast(index_code: str, label: str, output_dir_name: str,
                          preloaded: Dict) -> Dict:
    """使用内存加速版生成篮子。"""
    out_dir = PROJECT_ROOT / "output" / output_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "all_baskets_2015_2026.json"

    print(f"\n{'='*60}")
    print(f"  {label} — B版篮子 (total_mv + TTM + 宽松OCF) [FAST]")
    print(f"{'='*60}")

    uni = FcfUniverse(index_code=index_code, strict_ocf=False)
    uni.preload_all(download=False)

    # 猴子补丁：替换 _batch_fetch_market_cap 为内存版本
    import types
    uni._batch_fetch_market_cap = types.MethodType(
        make_fast_market_cap(preloaded), uni
    )

    all_baskets = {}
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        pt0 = time.time()
        try:
            basket_dict = uni.get_fcf_basket(date_str, top_n=50, verbose=False, use_ttm=True)
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


# ══════════════════════════════════════════════════════════════════
# 对比验证
# ══════════════════════════════════════════════════════════════════

def compare_outputs(original_path: Path, fast_path: Path) -> bool:
    """逐期、逐只、逐字段对比两个版本的输出。"""
    print(f"\n{'='*60}")
    print(f"  对比验证: 原始版 vs 加速版")
    print(f"{'='*60}")

    if not original_path.exists():
        print(f"  ❌ 原始版输出不存在: {original_path}")
        print(f"     请先运行 regenerate_b_baskets.py 生成原始版")
        return False

    if not fast_path.exists():
        print(f"  ❌ 加速版输出不存在: {fast_path}")
        return False

    with open(original_path) as f:
        orig = json.load(f)
    with open(fast_path) as f:
        fast = json.load(f)

    # 1. 检查期数一致
    orig_dates = set(orig.keys())
    fast_dates = set(fast.keys())

    if orig_dates != fast_dates:
        missing_in_fast = orig_dates - fast_dates
        extra_in_fast = fast_dates - orig_dates
        if missing_in_fast:
            print(f"  ❌ 加速版缺少期次: {sorted(missing_in_fast)}")
        if extra_in_fast:
            print(f"  ❌ 加速版多余期次: {sorted(extra_in_fast)}")
        return False
    print(f"  ✅ 期次数一致: {len(orig_dates)} 期")

    # 2. 逐期逐只对比
    total_diff_periods = 0
    total_diff_stocks = 0
    total_diff_fields = 0
    total_diff_weight = 0  # 权重微小差异
    diff_details: List[str] = []

    compare_fields = ["fcf", "ev", "fcf_yield", "total_mv", "weight"]

    for date_str in sorted(orig_dates):
        orig_basket = {s["ts_code"]: s for s in orig[date_str]}
        fast_basket = {s["ts_code"]: s for s in fast[date_str]}

        orig_codes = set(orig_basket.keys())
        fast_codes = set(fast_basket.keys())

        if orig_codes != fast_codes:
            # 股票列表不同
            only_orig = orig_codes - fast_codes
            only_fast = fast_codes - orig_codes
            detail = f"  ⚠️  {date_str}: 原始 {len(orig_codes)} 只 vs 加速 {len(fast_codes)} 只"
            if only_orig:
                detail += f" | 仅在原始: {sorted(only_orig)[:5]}"
            if only_fast:
                detail += f" | 仅在加速: {sorted(only_fast)[:5]}"
            diff_details.append(detail)
            total_diff_periods += 1
            total_diff_stocks += len(only_orig) + len(only_fast)
            continue

        # 逐字段对比
        period_diff = False
        for code in sorted(orig_codes):
            o = orig_basket[code]
            f = fast_basket[code]
            for field in compare_fields:
                v_o = o.get(field)
                v_f = f.get(field)
                if v_o is None and v_f is None:
                    continue
                if v_o is None or v_f is None:
                    diff_details.append(
                        f"  ⚠️  {date_str} {code} {field}: {v_o} vs {v_f}"
                    )
                    total_diff_fields += 1
                    period_diff = True
                    continue

                # 浮点数比较
                if abs(v_o) < 1e-20 and abs(v_f) < 1e-20:
                    continue
                rel_diff = abs(v_o - v_f) / max(abs(v_o), abs(v_f), 1e-20)

                if field == "weight":
                    # 权重容忍 1e-4（万分之一）
                    if rel_diff > 1e-4 and abs(v_o - v_f) > 1e-4:
                        diff_details.append(
                            f"  ⚠️  {date_str} {code} weight: "
                            f"{v_o:.6f} vs {v_f:.6f} (diff {abs(v_o-v_f):.6f})"
                        )
                        total_diff_weight += 1
                        period_diff = True
                else:
                    # 其他字段容忍 1e-6（百万分之一）
                    if rel_diff > 1e-6 and abs(v_o - v_f) > 1e-6:
                        diff_details.append(
                            f"  ⚠️  {date_str} {code} {field}: "
                            f"{v_o:.8e} vs {v_f:.8e}"
                        )
                        total_diff_fields += 1
                        period_diff = True

        if period_diff:
            total_diff_periods += 1

    # 3. 输出结论
    if total_diff_periods == 0 and total_diff_stocks == 0:
        print(f"\n  🎉 完全一致！所有 {len(orig_dates)} 期结果相同。")
        return True
    else:
        print(f"\n  ⚠️  发现差异:")
        print(f"    差异期次: {total_diff_periods}/{len(orig_dates)}")
        print(f"    差异标的: {total_diff_stocks}")
        print(f"    差异字段: {total_diff_fields}")
        print(f"    权重差异: {total_diff_weight}")
        if diff_details:
            print(f"\n  详细差异（最多显示 30 条）:")
            for d in diff_details[:30]:
                print(d)
            if len(diff_details) > 30:
                print(f"  ... 共 {len(diff_details)} 条差异")
        return False


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--gen-only", action="store_true", help="仅生成，不对比")
    parser.add_argument("--cmp-only", action="store_true", help="仅对比已有输出")
    parser.add_argument("--index", choices=["zz800", "hs300", "both"], default="zz800")
    args = parser.parse_args()

    if args.cmp_only:
        # 仅对比模式
        orig_path = PROJECT_ROOT / "output" / "zz800_fcf_fixed_lenient" / "all_baskets_2015_2026.json"
        fast_path = PROJECT_ROOT / "output" / "zz800_fcf_fixed_lenient_fast" / "all_baskets_2015_2026.json"
        ok = compare_outputs(orig_path, fast_path)
        sys.exit(0 if ok else 1)

    # ── 预加载 daily_basic 数据 ──
    print("📥 预加载 daily_basic 缓存到内存...")
    t_preload = time.time()
    preloaded = preload_daily_basic()
    dates_loaded = len(preloaded)
    stocks_total = sum(len(v) for v in preloaded.values())
    print(f"  ✅ 加载完成: {dates_loaded} 个交易日, {stocks_total} 条记录 "
          f"({time.time()-t_preload:.1f}s)")

    # ── 生成加速版篮子 ──
    if args.index in ("zz800", "both"):
        generate_baskets_fast("000906.SH", "ZZ800 FCF", "zz800_fcf_fixed_lenient_fast", preloaded)

    if args.index in ("hs300", "both"):
        generate_baskets_fast("000300.SH", "HS300 FCF", "hs300_fcf_fixed_lenient_fast", preloaded)

    # ── 对比验证 ──
    if not args.gen_only and args.index in ("zz800", "both"):
        orig_path = PROJECT_ROOT / "output" / "zz800_fcf_fixed_lenient" / "all_baskets_2015_2026.json"
        fast_path = PROJECT_ROOT / "output" / "zz800_fcf_fixed_lenient_fast" / "all_baskets_2015_2026.json"

        if orig_path.exists():
            ok = compare_outputs(orig_path, fast_path)
            if ok:
                print("\n🎉 验证通过！加速版与原始版结果完全一致。")
            else:
                print("\n⚠️  存在差异，请检查上述差异详情。")
        else:
            print(f"\n⚠️  原始版输出不存在 ({orig_path})，跳过对比。")
            print("    提示：等原始版跑完后运行 --cmp-only 进行对比。")

    print("\n✅ 全部完成！")
