#!/usr/bin/env python3
"""run_i_full.py — I版：E版+OCF/营业利润>1.0 二维质量过滤
选股: Top50, EV加权, ±40%缓冲, TTM, 宽松OCF
新增: OCF/营业利润>1.0 (利润亏损/过小直接排除)
对比基准: E版 (年化16.43%), 932368 (10.02%)
"""
import sys, json, time, argparse, os
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from fcf_universe import FcfUniverse
from compute_nav_cached import get_adj_close_cached

# ═══════════════ 参数 ═══════════════
parser = argparse.ArgumentParser()
parser.add_argument('--nav-only', action='store_true', help='跳过选股，用已有basket算NAV+报告')
args = parser.parse_args()

TOP_N = 50
BUFFER = 0.40  # E版缓冲区
OUT_DIR = PROJECT_ROOT / "output" / "zz800_fcf_2d_quality"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════ 调仓日(季度) ═══════════════
REBALANCE_DATES = [
    "2015-03-16", "2015-06-15", "2015-09-14", "2015-12-14",
    "2016-03-14", "2016-06-13", "2016-09-12", "2016-12-12",
    "2017-03-13", "2017-06-12", "2017-09-11", "2017-12-11",
    "2018-03-12", "2018-06-11", "2018-09-10", "2018-12-17",
    "2019-03-18", "2019-06-17", "2019-09-16", "2019-12-16",
    "2020-03-16", "2020-06-15", "2020-09-14", "2020-12-14",
    "2021-03-15", "2021-06-14", "2021-09-13", "2021-12-13",
    "2022-03-14", "2022-06-13", "2022-09-12", "2022-12-12",
    "2023-03-13", "2023-06-12", "2023-09-11", "2023-12-11",
    "2024-03-18", "2024-06-17", "2024-09-16", "2024-12-16",
    "2025-03-17", "2025-06-16", "2025-09-15", "2025-12-15",
    "2026-03-16",
]

RUN_SELECTION = not args.nav_only

# ═══════════════ 第一步：选股 ═══════════════
if RUN_SELECTION:
    print("=" * 70)
    print("I版：E版 + OCF/营业利润>1.0 二维质量过滤")
    print("=" * 70)

    uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
    uni.preload_all()

    baskets = {}
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        try:
            raw = uni.get_fcf_basket(
                date_str=date_str,
                top_n=TOP_N,
                verbose=True,
                use_ttm=True,
                use_ocf_profit_filter=True,  # ★ 二维质量过滤
            )
            if not raw:
                print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: 无数据，跳过")
                baskets[date_str] = []
                continue

            # raw = {ts_code: {name, weight, ...}}, 加回 ts_code 方便后续排序
            stocks = []
            for code, info in raw.items():
                info["ts_code"] = code
                stocks.append(info)
            stocks.sort(key=lambda x: x["weight"], reverse=True)
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: {len(stocks)}只, "
                  f"首仓={stocks[0]['name']}({stocks[0]['weight']*100:.1f}%) "
                  f"({elapsed:.0f}s)")
            baskets[date_str] = stocks
        except Exception as ex:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
            baskets[date_str] = []

    # 应用E版缓冲区
    print(f"\n  应用±{int(BUFFER*100)}%缓冲区...")
    buffered = {}
    prev_codes = set()
    for i, date_str in enumerate(REBALANCE_DATES):
        raw = baskets.get(date_str, [])
        if not raw:
            buffered[date_str] = []
            continue
        ranked = sorted(raw, key=lambda x: -abs(x.get("fcf_yield", 0)))
        if i == 0 or not prev_codes:
            picked = ranked[:TOP_N]
        else:
            must_keep = int(TOP_N * (1 - BUFFER))
            keep_list = [s for s in ranked if s["ts_code"] in prev_codes]
            keep = keep_list[:must_keep]
            keep_codes = {s["ts_code"] for s in keep}
            new_list = [s for s in ranked if s["ts_code"] not in keep_codes]
            needed = TOP_N - len(keep)
            picked = keep + new_list[:needed]
        prev_codes = {s["ts_code"] for s in picked}
        buffered[date_str] = picked

    with open(OUT_DIR / "all_baskets_2015_2026.json", "w") as f:
        json.dump(buffered, f, ensure_ascii=False, indent=2)
    valid = sum(1 for d in buffered if len(buffered[d]) >= 40)
    print(f"  ✅ I版: {valid}/{len(buffered)}期有效 → {OUT_DIR}/")
    baskets = buffered

else:
    print("  加载已有 baskets...")
    with open(OUT_DIR / "all_baskets_2015_2026.json") as f:
        baskets = json.load(f)
    print(f"  ✅ I版: {len(baskets)} 期")

# ═══════════════ 第二步：计算NAV ═══════════════
print("\n" + "=" * 70)
print("第二步：计算 NAV")
print("=" * 70)

nav_df = pd.DataFrame([
    {'rb_date': REBALANCE_DATES[i], 'next_rb': REBALANCE_DATES[i+1]}
    for i in range(len(REBALANCE_DATES)-1)
])

nav = 1.0; nav_rows = []
for _, row in nav_df.iterrows():
    rb, nrb = row['rb_date'], row['next_rb']
    stocks = baskets.get(rb, [])
    if len(stocks) < 10: continue
    w_ret, w_tot = 0.0, 0.0
    for s in stocks:
        r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
        if r:
            w_ret += s['weight'] * (r[1]/r[0] - 1)
            w_tot += s['weight']
    if w_tot < 0.3: continue
    pr = w_ret / w_tot
    nav *= (1 + pr)
    nav_rows.append({'rb_date': rb, 'next_rb': nrb, 'period_ret': pr*100, 'nav': nav})

df_nav = pd.DataFrame(nav_rows)
df_nav.to_csv(OUT_DIR / "backtest_nav_tr.csv", index=False)

N = len(df_nav); years = N / 4
cagr = (nav ** (1/years) - 1) * 100
mdd = ((df_nav['nav'] - df_nav['nav'].cummax()) / df_nav['nav'].cummax()).min() * 100
vol = df_nav['period_ret'].std() * (4**0.5)
sharpe = (cagr - 2.5) / vol if vol > 0 else 0

print(f"  I版: {N}期, NAV={nav:.4f}x, 年化={cagr:.2f}%, 回撤={mdd:.2f}%")

# ── 对比E版 ──
e_nav = pd.read_csv(PROJECT_ROOT / "output/zz800_fcf_lenient_buffer_e40/backtest_nav_tr.csv")
e_final = e_nav['nav'].iloc[-1]; e_y = len(e_nav) / 4
e_c = (e_final ** (1/e_y) - 1) * 100
e_m = ((e_nav['nav'] - e_nav['nav'].cummax()) / e_nav['nav'].cummax()).min() * 100
e_v = e_nav['period_ret'].std() * (4**0.5)
e_sh = (e_c - 2.5) / e_v if e_v > 0 else 0

print(f'\n{"="*65}')
print(f'  I版(二维质量) vs E版')
print(f'{"="*65}')
print(f'  {"版本":<20} {"年化":>8} {"回撤":>9} {"夏普":>7} {"NAV":>8}')
print(f'  {"-"*55}')
print(f'  {"I版(OCF/利润>1.0)":<20} {cagr:>7.2f}% {mdd:>8.2f}% {sharpe:>7.3f} {nav:>7.3f}x')
print(f'  {"E版":<20} {e_c:>7.2f}% {e_m:>8.2f}% {e_sh:>7.3f} {e_final:>7.3f}x')
print(f'')
print(f'  I版 vs E版: {cagr - e_c:+.2f}pp')

# ── 逐年 ──
print(f'\n  {"年":<6} {"I版":>10} {"E版":>10} {"超额":>10}')
print(f'  {"-"*40}')
for yr in range(2016, 2027):
    i_yr = df_nav[df_nav['rb_date'].str[:4] == str(yr)]
    e_yr = e_nav[e_nav['rb_date'].str[:4] == str(yr)]
    if i_yr.empty: continue
    ri = (1 + i_yr['period_ret']/100).prod() - 1
    re = (1 + e_yr['period_ret']/100).prod() - 1
    print(f'  {yr:<6} {ri*100:>+8.2f}% {re*100:>+8.2f}% {(ri-re)*100:>+9.2f}pp')
