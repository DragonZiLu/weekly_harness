#!/usr/bin/env python3
"""
regenerate_hs300_bd_baskets.py — HS300 B版+D版篮子同步生成（内存加速版）

对标 regenerate_bd_baskets.py（ZZ800版），参数和逻辑完全对齐：
  - B版: total_mv + TTM + 宽松OCF, FCF率降序 Top50, FCF加权
  - D版: 同B版基础 + 缓冲区±20%（前40必选, 41-60粘性保留上期股票）

与 ZZ800 版的差异仅在于 index_code: 000300.SH（vs 000906.SH）

加速原理（同 regenerate_b_baskets_fast.py）：
  预加载所有 daily_basic CSV 到内存，避免每期 API 调用，~0.5s/期

用法:
  python regenerate_hs300_bd_baskets.py              # 生成 B + D 版
  python regenerate_hs300_bd_baskets.py --top-n 100  # top_n=100
"""
import sys, json, time, argparse, types
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Set

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from fcf_universe import FcfUniverse

# ══════════════════════════════════════════════════════════════════
# 调仓日期（与 ZZ800 B+D 版完全一致，包含 2015-03-16）
# ══════════════════════════════════════════════════════════════════
REBALANCE_DATES = [
    "2012-03-12", "2012-06-11", "2012-09-10", "2012-12-10",
    "2013-03-11", "2013-06-17", "2013-09-16", "2013-12-16",
    "2014-03-17", "2014-06-16", "2014-09-15", "2014-12-15",
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

INDEX_CODE = "000300.SH"
BUFFER_RATIO = 0.20
CACHE_DIR = PROJECT_ROOT / "data" / "fcf_financials" / "daily_basic_cache"


# ══════════════════════════════════════════════════════════════════
# 内存缓存：预加载 daily_basic（同 regenerate_b_baskets_fast.py）
# ══════════════════════════════════════════════════════════════════

def preload_daily_basic() -> Dict[str, Dict[str, Dict[str, float]]]:
    """一次性加载所有 daily_basic CSV 到内存。"""
    if not CACHE_DIR.exists():
        print(f"⚠️  daily_basic 缓存目录不存在: {CACHE_DIR}")
        return {}
    preloaded: Dict[str, Dict[str, Dict[str, float]]] = {}
    for f in sorted(CACHE_DIR.glob("daily_basic_*.csv")):
        date_str = f.stem.replace("daily_basic_", "")
        try:
            df = pd.read_csv(f, dtype={"ts_code": str})
        except Exception:
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
    """返回替代 _batch_fetch_market_cap 的快速内存版。"""
    def fast_market_cap(self, pro, date_str: str, ts_codes: List[str]) -> Dict[str, float]:
        code_set = set(ts_codes)
        date_key = date_str.replace("-", "")
        base = datetime.strptime(date_key, "%Y%m%d")
        result: Dict[str, float] = {}
        for delta in range(6):
            d = (base - timedelta(days=delta)).strftime("%Y%m%d")
            day_data = preloaded.get(d)
            if day_data is None:
                continue
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
        return result
    return fast_market_cap


# ══════════════════════════════════════════════════════════════════
# 缓冲区逻辑（与 ZZ800 版完全一致）
# ══════════════════════════════════════════════════════════════════

def apply_buffer(ranked_stocks: List[dict], prev_codes: Set[str],
                 top_n: int, low_bound: int, high_bound: int) -> List[dict]:
    """缓冲区选样：前 low_bound 必选，缓冲区优先保留上期股票。"""
    must = ranked_stocks[:low_bound]
    buffer = ranked_stocks[low_bound:high_bound]
    buffer_old = [s for s in buffer if s['ts_code'] in prev_codes]
    buffer_new = [s for s in buffer if s['ts_code'] not in prev_codes]
    selected = must + buffer_old
    remaining = top_n - len(selected)
    if remaining > 0:
        selected.extend(buffer_new[:remaining])
    return selected[:top_n]


def fcf_weights(stocks: List[dict], cap: float = 0.10, max_iter: int = 100) -> List[dict]:
    """FCF 绝对值加权 + 单股 10% 封顶迭代重分配（与 ZZ800 版一致）。"""
    if not stocks:
        return stocks
    fcf_vals = [max(s.get('fcf', 0), 0) for s in stocks]
    total = sum(fcf_vals)
    if total <= 0:
        w = 1.0 / len(stocks)
        for s in stocks:
            s['weight'] = round(w, 6)
        return stocks
    weights = [v / total for v in fcf_vals]
    for _ in range(max_iter):
        overflow = sum(w - cap for w in weights if w > cap)
        if overflow < 1e-9:
            break
        capped = [min(w, cap) for w in weights]
        below_cap_total = sum(c for c in capped if c < cap)
        if below_cap_total <= 0:
            break
        weights = [
            min(c + overflow * (c / below_cap_total), cap) if c < cap else cap
            for c in capped
        ]
    total_w = sum(weights)
    for s, w in zip(stocks, weights):
        s['weight'] = round(w / total_w, 6)
    return stocks


# ══════════════════════════════════════════════════════════════════
# 主逻辑
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=50, help='持股数量 (默认50)')
    args = parser.parse_args()

    TOP_N = args.top_n
    LOW_BOUND = int(TOP_N * (1 - BUFFER_RATIO))
    HIGH_BOUND = int(TOP_N * (1 + BUFFER_RATIO))

    suffix = "" if TOP_N == 50 else f"_top{TOP_N}"
    b_out = PROJECT_ROOT / "output" / f"hs300_fcf_fixed_lenient{suffix}"
    d_out = PROJECT_ROOT / "output" / f"hs300_fcf_lenient_buffer{suffix}"
    b_out.mkdir(parents=True, exist_ok=True)
    d_out.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  HS300 B版+D版篮子同步生成 [FAST]")
    print(f"  TOP_N={TOP_N}  候选池={HIGH_BOUND}  缓冲区={BUFFER_RATIO*100:.0f}%")
    print(f"  (前{LOW_BOUND}必选, {LOW_BOUND+1}-{HIGH_BOUND}粘性)")
    print(f"  B版输出: {b_out}")
    print(f"  D版输出: {d_out}")
    print(f"{'='*60}")

    # ── 预加载 daily_basic 内存缓存 ──
    print("\n📥 预加载 daily_basic 缓存到内存...")
    t_preload = time.time()
    preloaded = preload_daily_basic()
    print(f"  ✅ {len(preloaded)} 个交易日, 耗时 {time.time()-t_preload:.1f}s")

    # ── 初始化 FcfUniverse 并注入内存缓存 ──
    uni = FcfUniverse(index_code=INDEX_CODE, strict_ocf=False)
    uni.preload_all(download=False)
    uni._batch_fetch_market_cap = types.MethodType(
        make_fast_market_cap(preloaded), uni
    )

    b_baskets = {}
    d_baskets = {}
    d_prev_codes: Set[str] = set()
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        pt0 = time.time()
        try:
            # 拉取 HIGH_BOUND 只候选（缓冲区范围足够）
            basket_raw = uni.get_fcf_basket(
                date_str, top_n=HIGH_BOUND, verbose=False, use_ttm=True
            )
            ranked = [
                dict(v, ts_code=k)
                for k, v in basket_raw.items()
                if k != "__quality_warnings__" and isinstance(v, dict)
            ]
            ranked.sort(key=lambda x: x.get('fcf_yield', 0), reverse=True)

            # ── B版: Top_N, FCF加权 ──
            b_stocks = [dict(s) for s in ranked[:TOP_N]]
            fcf_weights(b_stocks)
            b_baskets[date_str] = b_stocks

            # ── D版: 缓冲区 + FCF加权 ──
            if i == 0 or not d_prev_codes:
                d_stocks = [dict(s) for s in ranked[:TOP_N]]
            else:
                d_stocks = [dict(s) for s in apply_buffer(
                    ranked, d_prev_codes, TOP_N, LOW_BOUND, HIGH_BOUND
                )]
            fcf_weights(d_stocks)
            d_baskets[date_str] = d_stocks
            d_prev_codes = {s['ts_code'] for s in d_stocks}

            diff = len(
                {s['ts_code'] for s in d_stocks} - {s['ts_code'] for s in b_stocks}
            )
            elapsed = time.time() - pt0
            total_e = time.time() - t0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: "
                  f"B={len(b_stocks)} D={len(d_stocks)}(diff={diff}) "
                  f"({elapsed:.1f}s | total {total_e:.0f}s)")

        except Exception as e:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {e}")
            b_baskets[date_str] = []
            d_baskets[date_str] = []

    # ── 保存 ──
    with open(b_out / "all_baskets_2015_2026.json", "w") as f:
        json.dump(b_baskets, f, ensure_ascii=False, indent=2)
    with open(d_out / "all_baskets_2015_2026.json", "w") as f:
        json.dump(d_baskets, f, ensure_ascii=False, indent=2)

    b_valid = sum(1 for d in b_baskets if len(b_baskets[d]) >= 10)
    d_valid = sum(1 for d in d_baskets if len(d_baskets[d]) >= 10)
    total_t = time.time() - t0
    print(f"\n  ✅ B版 → {b_out}/all_baskets_2015_2026.json "
          f"(有效{b_valid}/{len(b_baskets)}期)")
    print(f"  ✅ D版 → {d_out}/all_baskets_2015_2026.json "
          f"(有效{d_valid}/{len(d_baskets)}期)")
    print(f"  总耗时: {total_t:.0f}s ({total_t/60:.1f}min)")


if __name__ == "__main__":
    main()
