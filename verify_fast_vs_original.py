#!/usr/bin/env python3
"""
verify_fast_vs_original.py — 用相同数据对比快速版与原始版，确保逻辑正确。

取 3 个关键日期 (2017Q1/2020Q3/2024Q2)，用原始和快速两种方式生成篮子，
逐只对比所有字段。
"""
import sys, json, time, types
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from fcf_universe import FcfUniverse

# 复用快速版的预加载函数
from regenerate_b_baskets_fast import preload_daily_basic, make_fast_market_cap

TEST_DATES = ["2017-06-12", "2020-09-14", "2024-06-17"]

def run_original(date_str: str) -> dict:
    """原始方式生成篮子"""
    uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
    uni.preload_all(download=False)
    basket = uni.get_fcf_basket(date_str, top_n=50, verbose=False, use_ttm=True)
    return {k: v for k, v in basket.items() if k != "__quality_warnings__"}

def run_fast(date_str: str, preloaded: dict) -> dict:
    """快速内存方式生成篮子"""
    uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
    uni.preload_all(download=False)
    uni._batch_fetch_market_cap = types.MethodType(
        make_fast_market_cap(preloaded), uni
    )
    basket = uni.get_fcf_basket(date_str, top_n=50, verbose=False, use_ttm=True)
    return {k: v for k, v in basket.items() if k != "__quality_warnings__"}

def compare_baskets(orig: dict, fast: dict, label: str) -> bool:
    """逐只对比两个篮子"""
    orig_codes = set(orig.keys())
    fast_codes = set(fast.keys())

    if orig_codes != fast_codes:
        only_orig = orig_codes - fast_codes
        only_fast = fast_codes - orig_codes
        print(f"  ❌ {label}: 股票不同!")
        print(f"     原始({len(orig_codes)}只) 独有: {sorted(only_orig)}")
        print(f"     快速({len(fast_codes)}只) 独有: {sorted(only_fast)}")
        return False

    fields = ["fcf", "ev", "fcf_yield", "total_mv", "weight"]
    all_ok = True
    for code in sorted(orig_codes):
        o, f = orig[code], fast[code]
        for field in fields:
            vo, vf = o.get(field), f.get(field)
            if vo is None and vf is None:
                continue
            if vo is None or vf is None:
                print(f"  ❌ {label} {code} {field}: {vo} vs {vf}")
                all_ok = False
                continue
            if abs(vo) < 1e-20 and abs(vf) < 1e-20:
                continue
            rel_diff = abs(vo - vf) / max(abs(vo), abs(vf), 1e-20)
            tolerance = 1e-4 if field == "weight" else 1e-6
            if rel_diff > tolerance:
                print(f"  ❌ {label} {code} {field}: {vo:.8e} vs {vf:.8e} (rel_diff={rel_diff:.2e})")
                all_ok = False

    if all_ok:
        print(f"  ✅ {label}: {len(orig_codes)} 只完全相同")
    return all_ok

if __name__ == "__main__":
    print("=" * 60)
    print("  验证: 快速版 vs 原始版 (相同数据)")
    print("=" * 60)

    print("\n📥 预加载 daily_basic...")
    preloaded = preload_daily_basic()
    print(f"  ✅ {len(preloaded)} 个交易日")

    all_pass = True
    for date_str in TEST_DATES:
        print(f"\n{'─'*40}")
        print(f"  📅 {date_str}")

        # 原始版
        t0 = time.time()
        orig = run_original(date_str)
        t_orig = time.time() - t0

        # 快速版
        t0 = time.time()
        fast = run_fast(date_str, preloaded)
        t_fast = time.time() - t0

        print(f"     原始={len(orig)}只 ({t_orig:.1f}s) | 快速={len(fast)}只 ({t_fast:.1f}s) "
              f"| 加速 {t_orig/t_fast:.1f}x")

        if not compare_baskets(orig, fast, date_str):
            all_pass = False

    print(f"\n{'='*60}")
    if all_pass:
        print("  🎉 全部通过！快速版与原始版结果完全一致。")
    else:
        print("  ❌ 存在差异，需要修复。")
    print(f"{'='*60}")
