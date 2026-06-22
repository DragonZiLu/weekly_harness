#!/usr/bin/env python3
"""SP500风格 — 行业权重对齐万得全A 实验 (优化版)"""
import sys, time, json
from collections import defaultdict
from pathlib import Path
import pandas as pd, numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from sp500_style import Sp500StyleEngine
from compute_nav_cached import get_adj_close_cached

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
TARGET_N = 100

# ============================================================
# 1. 预加载
# ============================================================
t0 = time.time()
print("加载引擎...")
engine = Sp500StyleEngine()
engine.preload(download_stock_basic=True)

# ============================================================
# 2. 预计算所有期的万得全A行业权重 & eligible产业权重 (一次性batch)
# ============================================================
print("预计算行业目标权重 (一次性)...")
all_stock_codes = list(engine._name_dict.keys())  # ~5500只

wa_targets = {}  # date_str → {industry → weight}
for i, date_str in enumerate(REBALANCE_DATES):
    mv_all = engine._load_market_cap(date_str, all_stock_codes)
    wa_t = defaultdict(float)
    for code, mv in mv_all.items():
        ind = engine._get_industry(code) or "其他"
        wa_t[ind] += mv.get("total_mv", 0)
    total = sum(wa_t.values())
    if total > 0:
        wa_targets[date_str] = {ind: v/total for ind, v in wa_t.items()}
    else:
        wa_targets[date_str] = {}
    if (i+1) % 15 == 0:
        print(f"  {i+1}/{len(REBALANCE_DATES)}期 ({time.time()-t0:.0f}s)")

print(f"预计算完成 ({time.time()-t0:.1f}s)")

# ============================================================
# 3. 选股函数 (使用预计算的万得全A权重)
# ============================================================
def select_basket_wa(date_str):
    dt = pd.Timestamp(date_str[:10])
    if 1 <= dt.month <= 3: ref_year, ref_quarter = dt.year-1, 3
    elif 4 <= dt.month <= 6: ref_year, ref_quarter = dt.year, 1
    elif 7 <= dt.month <= 9: ref_year, ref_quarter = dt.year, 2
    else: ref_year, ref_quarter = dt.year, 3

    # Step 1-3: 盈利+流动性过滤
    all_codes = engine._idx_cache.get_constituents(date_str)
    passed = []; failed = []
    for code in all_codes:
        ok, reason = engine._get_profitability_check(code, ref_year, ref_quarter, date_str, use_both=True)
        if ok: passed.append(code)
        elif reason: failed.append((code, reason))
    if len(passed) < TARGET_N:
        passed = list(set(passed) | {c for c,r in failed if "无" in r})

    mv_data = engine._load_market_cap(date_str, passed)
    eligible = []
    for code in passed:
        mv = mv_data.get(code)
        if mv is None or mv.get("total_mv", 0) <= 0:
            continue
        if mv["circ_mv"] / mv["total_mv"] >= 0.15:
            eligible.append(code)
    if len(eligible) < TARGET_N:
        eligible = passed

    # Step 4: 行业分组 + D'Hondt(with 万得全A target)
    industry_groups = defaultdict(list)
    for code in eligible:
        ind = engine._get_industry(code) or "其他"
        industry_groups[ind].append(code)

    wa_t = wa_targets.get(date_str, {})
    active_targets = {ind: wa_t.get(ind,0) for ind in industry_groups if ind and ind != "nan"}
    total_a = sum(active_targets.values())
    
    # 如果万得全A目标为空，回退到原版D'Hondt（eligible池自身市值权重）
    if total_a == 0 or len(active_targets) == 0:
        industry_mv = {}
        for ind, codes in industry_groups.items():
            industry_mv[ind] = sum(mv_data.get(c,{}).get("circ_mv",0) for c in codes)
        total_imv = sum(industry_mv.values())
        active_targets = {ind: mv/total_imv for ind, mv in industry_mv.items()} if total_imv>0 else {ind: 1.0/len(industry_groups) for ind in industry_groups}
    else:
        active_targets = {ind: v/total_a for ind, v in active_targets.items()}

    slots = {ind: 0 for ind in industry_groups}
    for _ in range(TARGET_N):
        best = max(active_targets, key=lambda x: active_targets[x]/(slots[x]+1))
        slots[best] += 1

    # Step 5: 行业内选股
    selected = []
    for ind in sorted(slots, key=lambda x: -slots[x]):
        n = slots[ind]
        codes = sorted(industry_groups[ind],
                       key=lambda c: mv_data.get(c,{}).get("circ_mv",0), reverse=True)
        selected.extend(codes[:min(n, len(codes))])

    if len(selected) < TARGET_N:
        remaining = [c for c in eligible if c not in set(selected)]
        remaining.sort(key=lambda c: mv_data.get(c,{}).get("circ_mv",0), reverse=True)
        selected.extend(remaining[:TARGET_N - len(selected)])

    # Step 6: 加权+10%上限
    wv = [max(mv_data.get(c,{}).get("free_float_mv",0), 0) for c in selected]
    tw = sum(wv)
    raw = [w/tw for w in wv] if tw>0 else [1.0/len(selected)]*len(selected)
    for _ in range(100):
        overflow = sum(max(w-0.1,0) for w in raw)
        if overflow < 1e-9: break
        capped = [min(w,0.1) for w in raw]
        below = sum(c for c in capped if c<0.1)
        if below <= 0: break
        raw = [min(c+overflow*(c/below),0.1) if c<0.1 else 0.1 for c in capped]
    tw2 = sum(raw)
    final = [w/tw2 for w in raw]

    basket = {}
    for code, w in zip(selected, final):
        mv = mv_data.get(code,{})
        basket[code] = {"name": engine._get_stock_name(code),
                         "industry": engine._get_industry(code),
                         "total_mv": mv.get("total_mv",0),
                         "circ_mv": mv.get("circ_mv",0),
                         "free_float_mv": mv.get("free_float_mv",0),
                         "weight": round(w,6)}
    return basket

# ============================================================
# 4. 全量选股
# ============================================================
print("\n选股 (46期)...")
t1 = time.time()
all_baskets = {}
for i, date_str in enumerate(REBALANCE_DATES):
    try:
        b = select_basket_wa(date_str)
        all_baskets[date_str] = b
        print(f"  [{i+1:2d}/{len(REBALANCE_DATES)}] {date_str}: {len(b)}只 ({time.time()-t1:.0f}s)")
    except Exception as ex:
        import traceback
        print(f"  [{i+1:2d}] {date_str}: ❌ {ex}")
        if i < 2: traceback.print_exc()
        all_baskets[date_str] = {}
print(f"选股完成 ({time.time()-t1:.1f}s)")

# ============================================================
# 5. NAV计算
# ============================================================
print("\n计算 NAV...")
nav_periods = pd.DataFrame([
    {"rb_date": REBALANCE_DATES[i], "next_rb": REBALANCE_DATES[i+1]}
    for i in range(len(REBALANCE_DATES)-1)
])
nav = 1.0; rows = []
for _, row in nav_periods.iterrows():
    rb, nrb = row["rb_date"], row["next_rb"]
    stocks = all_baskets.get(rb,{})
    valid = [(k,v) for k,v in stocks.items() if isinstance(v,dict)]
    if len(valid) < 5: continue
    wr, wt = 0.0, 0.0
    for code, info in valid:
        r = get_adj_close_cached(code, rb, nrb, auto_fetch=False)
        if r: wr += info["weight"]*(r[1]/r[0]-1); wt += info["weight"]
    if wt < 0.3: continue
    pr = wr/wt; nav *= (1+pr)
    rows.append({"rb_date":rb,"next_rb":nrb,"period_ret":pr*100,"nav":nav})
nav_df = pd.DataFrame(rows)

# ============================================================
# 6. 绩效输出
# ============================================================
end_nav = nav_df["nav"].iloc[-1]
years = len(nav_df)/4.0
ann = ((end_nav)**(1/years)-1)*100
s = pd.Series([1.0]+nav_df["nav"].tolist())
max_dd = ((s-s.cummax())/s.cummax()*100).min()
rets = nav_df["period_ret"].values/100.0
sharpe = (rets.mean()*4-0.015)/(rets.std()*np.sqrt(4)) if rets.std()>0 else 0

print(f"\n{'='*60}")
print(f"绩效: SP500风格 对齐万得全A (target_n=100)")
print(f"  年化: {ann:.2f}% | 最大回撤: {max_dd:.2f}% | 夏普: {sharpe:.3f} | NAV: {end_nav:.4f}x")
print(f"  有效期数: {len(nav_df)}/{len(REBALANCE_DATES)-1}")

# vs 原版
try:
    orig = pd.read_csv("output/sp500_style_100/nav_daily.csv")
    oe = orig["nav"].iloc[-1]; oy = len(orig)/4.0
    oa = ((oe)**(1/oy)-1)*100
    os = pd.Series([1.0]+orig["nav"].tolist())
    od = ((os-os.cummax())/os.cummax()*100).min()
    orr = orig["period_ret"].values/100.0
    osh = (orr.mean()*4-0.015)/(orr.std()*np.sqrt(4)) if orr.std()>0 else 0
    print(f"\n对比:")
    print(f"  {'':<15} {'年化':>8} {'最大回撤':>10} {'夏普':>8} {'NAV':>8}")
    print(f"  {'原版v6-Top100':<15} {oa:>7.2f}% {od:>9.2f}% {osh:>7.3f} {oe:>7.4f}x")
    print(f"  {'对齐全A':<15} {ann:>7.2f}% {max_dd:>9.2f}% {sharpe:>7.3f} {end_nav:>7.4f}x")
    print(f"  {'差异':<15} {ann-oa:>+7.2f}%")
except Exception as e:
    print(f"  原版对比失败: {e}")

# ============================================================
# 7. 最新期行业权重对比
# ============================================================
print(f"\n{'='*60}")
print("最新期行业权重对比 (2026-06-15)")
orig_b = engine.select_basket("2026-06-15", target_n=100, use_both=True, verbose=False)
wa_b = all_baskets.get("2026-06-15",{})

def iw(b):
    r = defaultdict(float)
    for c,i in b.items():
        if isinstance(i,dict): r[i.get("industry","")] += i.get("weight",0)
    return r

o_iw = iw(orig_b); w_iw = iw(wa_b)
wa_t = wa_targets["2026-06-15"]

inds = sorted(set(list(o_iw)+list(w_iw)+list(wa_t)), key=lambda x: -wa_t.get(x,0))
print(f"  {'行业':<10} {'原版v6':>8} {'对齐版':>8} {'全A目标':>8} {'原版Δ':>7} {'对齐Δ':>7}")
print(f"  {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*7}")
to, tw = 0,0
for ind in inds[:25]:
    o=o_iw.get(ind,0)*100; w=w_iw.get(ind,0)*100; t=wa_t.get(ind,0)*100
    od=o-t; wd=w-t; to+=abs(od); tw+=abs(wd)
    print(f"  {ind:<10} {o:>7.2f}% {w:>7.2f}% {t:>7.2f}% {od:>+6.2f}pp {wd:>+6.2f}pp")
print(f"  {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*7}")
print(f"  总偏离: 原版 {to:.1f}pp → 对齐 {tw:.1f}pp ({(1-tw/to)*100:.0f}%改善)")

print(f"\n✅ 完成 ({time.time()-t0:.0f}s)")
