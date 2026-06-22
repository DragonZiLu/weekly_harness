#!/usr/bin/env python3
"""run_800div_buyback.py — 800红利回购增强版：选股 → NAV → 报告"""
import sys, json, time, argparse
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

parser = argparse.ArgumentParser()
parser.add_argument('--nav-only', action='store_true', help='跳过选股，直接算NAV')
args = parser.parse_args()

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from dividend_buyback import DividendBuybackUniverse
from compute_nav_cached import get_adj_close_cached

TOP_N = 100; CAP = 0.10; MAX_TURNOVER = 0.20
OUT_DIR = PROJECT_ROOT / "output" / "800div_buyback"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REBALANCE_DATES = [
    "2015-06-15","2015-12-14","2016-06-13","2016-12-12",
    "2017-06-12","2017-12-11","2018-06-11","2018-12-17",
    "2019-06-17","2019-12-16","2020-06-15","2020-12-14",
    "2021-06-14","2021-12-13","2022-06-13","2022-12-12",
    "2023-06-12","2023-12-11","2024-06-17","2024-12-16",
    "2025-06-16","2025-12-15","2026-06-15",
]

# ═══════════════ Step 1: 选股 ═══════════════
if not args.nav_only:
    print("=" * 70)
    print("800红利 回购增强版（分红+注销式回购×0.3）")
    print("=" * 70)

    uni = DividendBuybackUniverse(index_code="000906.SH")
    uni.preload_all(rebalance_dates=REBALANCE_DATES)

    baskets = {}
    prev_codes = set()
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        try:
            raw = uni.get_dividend_basket(
                date_str=date_str, top_n=TOP_N,
                prev_basket_codes=prev_codes if i > 0 else None,
                max_turnover=MAX_TURNOVER, verbose=(i < 3 or i % 5 == 0),
            )
            stocks = sorted(raw.values(), key=lambda x: x["weight"], reverse=True)
            baskets[date_str] = stocks
            prev_codes = {s["ts_code"] for s in stocks}
            elapsed = time.time() - t0
            if i < 3 or i % 5 == 0:
                print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: "
                      f"{len(stocks)}只 ({elapsed:.0f}s)")
        except Exception as ex:
            print(f"  [{i+1}] {date_str}: ERROR — {ex}")
            baskets[date_str] = []

    with open(OUT_DIR / "all_baskets_2015_2026.json", "w") as f:
        json.dump(baskets, f, ensure_ascii=False, indent=2)
    valid = sum(1 for d in baskets if len(baskets[d]) >= 10)
    print(f"  ✅ {valid}/{len(baskets)}期有效 → {OUT_DIR}/")
else:
    print("加载已有 baskets...")
    with open(OUT_DIR / "all_baskets_2015_2026.json") as f:
        baskets = json.load(f)
    print(f"  ✅ {len(baskets)}期")

# ═══════════════ Step 2: NAV ═══════════════
print("\n" + "=" * 70)
print("计算 NAV")
print("=" * 70)

nav_periods = pd.DataFrame([
    {'rb_date': REBALANCE_DATES[i], 'next_rb': REBALANCE_DATES[i+1]}
    for i in range(len(REBALANCE_DATES)-1)
])

nav = 1.0; rows = []
for _, row in nav_periods.iterrows():
    rb, nrb = row['rb_date'], row['next_rb']
    stocks = baskets.get(rb, [])
    if len(stocks) < 5: continue

    w_ret, w_tot = 0.0, 0.0
    for s in stocks:
        r = get_adj_close_cached(s["ts_code"], rb, nrb, auto_fetch=False)
        if r:
            w_ret += s["weight"] * (r[1]/r[0] - 1)
            w_tot += s["weight"]
    if w_tot < 0.3: continue

    pr = w_ret / w_tot if w_tot > 0 else 0
    nav *= (1 + pr)
    rows.append({'rb_date': rb, 'next_rb': nrb, 'period_ret': pr*100, 'nav': nav})

nav_df = pd.DataFrame(rows)
nav_df.to_csv(OUT_DIR / "backtest_nav_tr.csv", index=False)

# ═══════════════ Step 3: 绩效 ═══════════════
if len(nav_df) < 2:
    print("数据不足")
    sys.exit(0)

total_periods = len(nav_df)
years = total_periods / 2.0  # 半年度
end_nav = nav_df["nav"].iloc[-1]
ann = ((end_nav)**(1/years)-1)*100 if years > 0 else 0

nav_series = pd.Series([1.0] + nav_df["nav"].tolist())
dd = (nav_series - nav_series.cummax()) / nav_series.cummax() * 100
max_dd = dd.min()

period_rets = nav_df["period_ret"].values / 100.0
if period_rets.std() > 0:
    ann_vol = period_rets.std() * np.sqrt(2)
    sharpe = ((period_rets.mean()*2 - 0.015)/ann_vol) if ann_vol > 0 else 0
else:
    sharpe = ann_vol = 0

print(f"\n  回购增强版（2015-06-15 → 2026-06-15，{total_periods}期）:")
print(f"    年化: {ann:.2f}% | 最大回撤: {max_dd:.2f}% | 夏普: {sharpe:.3f} | NAV: {end_nav:.3f}x")

# ── vs 原版800红利 ──
orig_path = PROJECT_ROOT / "output" / "800div" / "backtest_nav_tr.csv"
if orig_path.exists():
    orig = pd.read_csv(orig_path)
    o_years = len(orig) / 2.0
    o_nav = orig["nav"].iloc[-1]
    o_ann = ((o_nav)**(1/o_years)-1)*100 if o_years > 0 else 0
    print(f"  原版800红利: 年化 {o_ann:.2f}% | NAV {o_nav:.3f}x")
    print(f"  差异: {ann - o_ann:+.2f}pp")
