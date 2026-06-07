#!/usr/bin/env python3
"""
generate_e40_baskets.py — E版篮子生成（缓冲区±40%）
"""
import sys, json, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
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

TOP_N        = 50
BUFFER_RATIO = 0.40
LOW_BOUND    = int(TOP_N * (1 - BUFFER_RATIO))   # 30: 必选前30
HIGH_BOUND   = int(TOP_N * (1 + BUFFER_RATIO))   # 70: 候选池

OUT_DIR = PROJECT_ROOT / "output" / "zz800_fcf_lenient_buffer_e40"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def apply_buffer(ranked, prev_codes, low_bound, high_bound, top_n):
    must       = ranked[:low_bound]
    buffer     = ranked[low_bound:high_bound]
    buffer_old = [s for s in buffer if s['ts_code'] in prev_codes]
    buffer_new = [s for s in buffer if s['ts_code'] not in prev_codes]
    selected   = must + buffer_old
    remaining  = top_n - len(selected)
    if remaining > 0:
        selected.extend(buffer_new[:remaining])
    return selected[:top_n]


def fcf_weights(stocks, cap=0.10, max_iter=100):
    if not stocks: return stocks
    fcf_vals = [max(s.get('fcf', 0), 0) for s in stocks]
    total = sum(fcf_vals)
    if total <= 0:
        w = 1.0 / len(stocks)
        for s in stocks: s['weight'] = round(w, 6)
        return stocks
    weights = [v / total for v in fcf_vals]
    for _ in range(max_iter):
        overflow = sum(w - cap for w in weights if w > cap)
        if overflow < 1e-9: break
        capped = [min(w, cap) for w in weights]
        below = sum(c for c in capped if c < cap)
        if below <= 0: break
        weights = [min(c + overflow*(c/below), cap) if c < cap else cap for c in capped]
    total_w = sum(weights)
    for s, w in zip(stocks, weights):
        s['weight'] = round(w / total_w, 6)
    return stocks


uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
uni.preload_all(download=False)

e_baskets   = {}
prev_codes  = set()
t0 = time.time()

print(f"E版(±40%缓冲区): TOP_N={TOP_N}, LOW={LOW_BOUND}, HIGH={HIGH_BOUND}")

for i, date_str in enumerate(REBALANCE_DATES):
    try:
        basket_raw = uni.get_fcf_basket(date_str, top_n=HIGH_BOUND, verbose=False, use_ttm=True)
        ranked = [dict(v, ts_code=k) for k, v in basket_raw.items()
                  if k != "__quality_warnings__" and isinstance(v, dict)]
        ranked.sort(key=lambda x: x.get('fcf_yield', 0), reverse=True)

        if i == 0 or not prev_codes:
            e_stocks = [dict(s) for s in ranked[:TOP_N]]
        else:
            e_stocks = [dict(s) for s in apply_buffer(ranked, prev_codes, LOW_BOUND, HIGH_BOUND, TOP_N)]

        fcf_weights(e_stocks)
        e_baskets[date_str] = e_stocks
        prev_codes = {s['ts_code'] for s in e_stocks}

        elapsed = time.time() - t0
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: {len(e_stocks)}只 ({elapsed:.0f}s)")

    except Exception as ex:
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
        e_baskets[date_str] = []

with open(OUT_DIR / "all_baskets_2015_2026.json", "w") as f:
    json.dump(e_baskets, f, ensure_ascii=False, indent=2)

valid = sum(1 for d in e_baskets if len(e_baskets[d]) >= 10)
print(f"\n✅ E版篮子已保存: {OUT_DIR}/all_baskets_2015_2026.json")
print(f"   有效期数: {valid}/{len(e_baskets)}  总耗时: {time.time()-t0:.0f}s")
