#!/usr/bin/env python3
"""
regenerate_bd_baskets.py — B版+D版篮子同步生成

选样规则：
  - B版: total_mv + TTM + 宽松OCF, FCF率降序 Top(HIGH_BOUND) → 取Top(TOP_N)
  - D版: 同B版基础 + 缓冲区±20%（前80%必选，81-120粘性保留上期股票）

加权规则（两版一致，与官方932368一致）：
  - FCF绝对值加权 + 单股10%封顶迭代重分配

两者共用相同调仓日期，NAV可直接用同一日期链对比

用法:
  python regenerate_bd_baskets.py              # 默认 top_n=50
  python regenerate_bd_baskets.py --top-n 100  # top_n=100
"""
import sys, json, time, argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from fcf_universe import FcfUniverse

REBALANCE_DATES = [
    "2012-03-12","2012-06-11","2012-09-10","2012-12-10",
    "2013-03-11","2013-06-17","2013-09-16","2013-12-16",
    "2014-03-17","2014-06-16","2014-09-15","2014-12-15",
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

BUFFER_RATIO = 0.20


def apply_buffer(ranked_stocks, prev_codes, top_n, low_bound, high_bound):
    """缓冲区逻辑：前 low_bound 必选，第 low_bound+1~high_bound 优先保留上期股票"""
    must       = ranked_stocks[:low_bound]
    buffer     = ranked_stocks[low_bound:high_bound]
    buffer_old = [s for s in buffer if s['ts_code'] in prev_codes]
    buffer_new = [s for s in buffer if s['ts_code'] not in prev_codes]
    selected   = must + buffer_old
    remaining  = top_n - len(selected)
    if remaining > 0:
        selected.extend(buffer_new[:remaining])
    return selected[:top_n]


def fcf_weights(stocks, cap=0.10, max_iter=100):
    """FCF绝对值加权 + 单股10%封顶迭代重分配"""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=50, help='持股数量 (默认50)')
    args = parser.parse_args()

    TOP_N      = args.top_n
    LOW_BOUND  = int(TOP_N * (1 - BUFFER_RATIO))   # 缓冲区下界
    HIGH_BOUND = int(TOP_N * (1 + BUFFER_RATIO))   # 候选池上界（也是拉取数量）

    # 输出路径根据 top_n 区分
    suffix = "" if TOP_N == 50 else f"_top{TOP_N}"
    b_out = PROJECT_ROOT / "output" / f"zz800_fcf_fixed_lenient{suffix}"
    d_out = PROJECT_ROOT / "output" / f"zz800_fcf_lenient_buffer{suffix}"
    b_out.mkdir(parents=True, exist_ok=True)
    d_out.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  ZZ800 B版+D版篮子同步生成")
    print(f"  TOP_N={TOP_N}  候选池={HIGH_BOUND}  缓冲区={BUFFER_RATIO*100:.0f}%")
    print(f"  (前{LOW_BOUND}必选, {LOW_BOUND+1}-{HIGH_BOUND}粘性)")
    print(f"  B版输出: {b_out}")
    print(f"  D版输出: {d_out}")
    print(f"{'='*60}")

    uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
    uni.preload_all(download=False)

    b_baskets  = {}
    d_baskets  = {}
    d_prev_codes = set()
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        pt0 = time.time()
        try:
            basket_raw = uni.get_fcf_basket(date_str, top_n=HIGH_BOUND, verbose=False, use_ttm=True)
            ranked = [
                dict(v, ts_code=k)
                for k, v in basket_raw.items()
                if k != "__quality_warnings__" and isinstance(v, dict)
            ]
            ranked.sort(key=lambda x: x.get('fcf_yield', 0), reverse=True)

            # B版: Top_N，FCF加权
            b_stocks = [dict(s) for s in ranked[:TOP_N]]
            fcf_weights(b_stocks)
            b_baskets[date_str] = b_stocks

            # D版: 缓冲区 + FCF加权
            if i == 0 or not d_prev_codes:
                d_stocks = [dict(s) for s in ranked[:TOP_N]]
            else:
                d_stocks = [dict(s) for s in apply_buffer(ranked, d_prev_codes, TOP_N, LOW_BOUND, HIGH_BOUND)]
            fcf_weights(d_stocks)
            d_baskets[date_str] = d_stocks
            d_prev_codes = {s['ts_code'] for s in d_stocks}

            diff    = len({s['ts_code'] for s in d_stocks} - {s['ts_code'] for s in b_stocks})
            elapsed = time.time() - pt0
            total_e = time.time() - t0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: B={len(b_stocks)} D={len(d_stocks)}(diff={diff}) ({elapsed:.1f}s | {total_e:.0f}s)")

        except Exception as e:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {e}")
            b_baskets[date_str] = []
            d_baskets[date_str] = []

    with open(b_out / "all_baskets_2015_2026.json", "w") as f:
        json.dump(b_baskets, f, ensure_ascii=False, indent=2)
    with open(d_out / "all_baskets_2015_2026.json", "w") as f:
        json.dump(d_baskets, f, ensure_ascii=False, indent=2)

    b_valid = sum(1 for d in b_baskets if len(b_baskets[d]) >= 10)
    d_valid = sum(1 for d in d_baskets if len(d_baskets[d]) >= 10)
    total_t = time.time() - t0
    print(f"\n  ✅ B版 → {b_out}/all_baskets_2015_2026.json (有效{b_valid}/{len(b_baskets)}期)")
    print(f"  ✅ D版 → {d_out}/all_baskets_2015_2026.json (有效{d_valid}/{len(d_baskets)}期)")
    print(f"  总耗时: {total_t:.0f}s ({total_t/60:.1f}min)")


if __name__ == "__main__":
    main()
