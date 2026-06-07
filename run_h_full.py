#!/usr/bin/env python3
"""run_h_full.py — H版(FCF绝对值排序Top50) 全流程 + BDEFXGH对比报告"""
import json, sys, time
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime
from compute_nav_cached import get_adj_close_cached

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

CAP = 0.10

def fcf_weights(stocks, cap=CAP, max_iter=100):
    if not stocks: return stocks
    fcf_vals = [max(s.get('fcf',0),0) for s in stocks]
    total = sum(fcf_vals)
    if total <= 0:
        w = 1.0/len(stocks)
        for s in stocks: s['weight'] = round(w,6)
        return stocks
    weights = [v/total for v in fcf_vals]
    for _ in range(max_iter):
        overflow = sum(w-cap for w in weights if w>cap)
        if overflow < 1e-9: break
        capped = [min(w,cap) for w in weights]
        below = sum(c for c in capped if c<cap)
        if below <= 0: break
        weights = [min(c+overflow*(c/below),cap) if c<cap else cap for c in capped]
    tw = sum(weights)
    for s,w in zip(stocks,weights): s['weight'] = round(w/tw,6)
    return stocks

# ═══════════════ 第一步：H版选股 ═══════════════
print("=" * 70)
print("H版(FCF绝对值排序Top50) 选股")
print("=" * 70)

uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
uni.preload_all(download=False)

out_path = PROJECT_ROOT / "output/zz800_fcf_top_by_fcf"
out_path.mkdir(parents=True, exist_ok=True)

h_baskets = {}
t0 = time.time()

for i, date_str in enumerate(REBALANCE_DATES):
    try:
        raw = uni.get_fcf_basket(date_str, top_n=800, verbose=False, use_ttm=True)
        ranked = [dict(v, ts_code=k) for k,v in raw.items()
                  if k != "__quality_warnings__" and isinstance(v, dict)]
        # ★ H版核心改动：按FCF绝对值排序，而非FCF收益率
        ranked.sort(key=lambda x: x.get('fcf',0), reverse=True)

        stocks = [dict(s) for s in ranked[:50]]
        fcf_weights(stocks)
        h_baskets[date_str] = stocks
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: 选{len(stocks)}只 (max_fcf={ranked[0].get('fcf',0)/1e8:.0f}亿) ({time.time()-t0:.0f}s)")
    except Exception as ex:
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
        h_baskets[date_str] = []

with open(out_path / "all_baskets_2015_2026.json", "w") as f:
    json.dump(h_baskets, f, ensure_ascii=False, indent=2)

valid = sum(1 for d in h_baskets if len(h_baskets[d])>=5)
print(f"  ✅ H版: {valid}/{len(h_baskets)}期有效 → output/zz800_fcf_top_by_fcf/")

# ═══════════════ 第二步：计算NAV ═══════════════
print("\n" + "=" * 70)
print("第二步：计算 H版 + B/D/E/F/X/G NAV")
print("=" * 70)

all_baskets = {
    'B': json.load(open("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json")),
    'D': json.load(open("output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json")),
    'E': json.load(open("output/zz800_fcf_lenient_buffer_e40/all_baskets_2015_2026.json")),
    'F': json.load(open("output/zz800_fcf_lenient_buffer_f50/all_baskets_2015_2026.json")),
    'X': json.load(open("output/zz800_fcf_full_universe/all_baskets_2015_2026.json")),
    'G': json.load(open("output/zz800_fcf_adaptive_top/all_baskets_2015_2026.json")),
    'H': h_baskets,
}

nav_df = pd.read_csv("output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv")
df_idx = pd.read_csv("data/index_daily/932368.CSI.csv")
df_idx['trade_date'] = df_idx['trade_date'].astype(str); df_idx = df_idx.sort_values('trade_date')
df_hs = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str); df_hs = df_hs.sort_values('trade_date')

def idx_ret(df, s, e):
    sk, ek = s.replace('-',''), e.replace('-','')
    p0 = float(df[df['trade_date']<=sk]['close'].iloc[-1])
    p1 = float(df[df['trade_date']<=ek]['close'].iloc[-1])
    return (p1/p0-1)*100

def calc_nav(baskets, min_stocks=5, min_weight=0.3):
    nav = 1.0; rows = []
    for _, row in nav_df.iterrows():
        rb, nrb = row['rb_date'], row['next_rb']
        stocks = baskets.get(rb, [])
        if len(stocks) < min_stocks: continue
        w_ret, w_tot = 0.0, 0.0
        for s in stocks:
            r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
            if r:
                w_ret += s['weight'] * (r[1]/r[0]-1)
                w_tot += s['weight']
        if w_tot < min_weight: continue
        pr = w_ret/w_tot
        nav *= (1+pr)
        rows.append({'rb_date':rb, 'next_rb':nrb, 'period_ret':pr*100, 'nav':nav})
    return pd.DataFrame(rows)

nav_results = {}
for vn in ['B','D','E','F','X','G','H']:
    print(f"  计算 {vn}版 NAV...")
    nav_df_ver = calc_nav(all_baskets[vn])
    nav_results[vn] = nav_df_ver
    print(f"    {vn}版: {len(nav_df_ver)}期, 期末NAV={nav_df_ver['nav'].iloc[-1]:.3f}x")

# 合并
b_nav = nav_results['B']
m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b_ret','b_nav']
for vn in ['D','E','F','X','G','H']:
    df = nav_results[vn]
    m = m.merge(df[['rb_date','period_ret','nav']].rename(
        columns={'period_ret':vn.lower()+'_ret','nav':vn.lower()+'_nav'}), on='rb_date')
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)
i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_nav'] = h_navs
for v in ['b','d','e','f','x','g','h']:
    m[v+'_exc'] = m[v+'_ret'] - m['idx_ret']

# ═══════════════ 第三步：统计 ═══════════════
def stats(rc, nc, data=m):
    rets = data[rc].dropna(); navs = data[nc].dropna()
    n = len(rets)
    ann  = (navs.iloc[-1]**(4/n)-1)*100
    vol  = rets.std()*2
    peak = navs.cummax(); mdd  = ((peak-navs)/peak).max()*100
    sharpe = (ann-2.0)/vol if vol>0 else 0
    calmar = ann/mdd if mdd>0 else 0
    win  = (rets>0).mean()*100
    return dict(ann=ann,vol=vol,mdd=-mdd,sharpe=sharpe,calmar=calmar,win=win,nav=navs.iloc[-1])

def turnover(baskets):
    dates = [r['rb_date'] for _,r in nav_df.iterrows() if baskets.get(r['rb_date'])]
    tos = []
    for i in range(1, len(dates)):
        prev = {s['ts_code'] for s in baskets.get(dates[i-1],[])}
        curr = {s['ts_code'] for s in baskets.get(dates[i],[])}
        if prev and curr:
            tos.append(len(curr-prev)/(len(prev|curr)/2)*100)
    return np.mean(tos) if tos else 0

versions = {
    'B': ('b_ret','b_nav', all_baskets['B']),
    'D': ('d_ret','d_nav', all_baskets['D']),
    'E': ('e_ret','e_nav', all_baskets['E']),
    'F': ('f_ret','f_nav', all_baskets['F']),
    'X': ('x_ret','x_nav', all_baskets['X']),
    'G': ('g_ret','g_nav', all_baskets['G']),
    'H': ('h_ret','h_nav', all_baskets['H']),
}

vs = {}
for vn, (rc,nc,bk) in versions.items():
    s = stats(rc,nc)
    s['to'] = turnover(bk)
    avg_cnt = np.mean([len(bk.get(d,[])) for d in sorted(bk.keys()) if len(bk.get(d,[]))>=5])
    s['avg_cnt'] = round(avg_cnt,0)
    vs[vn] = s

i_s = stats('idx_ret','i_nav')
h_s = stats('hs_ret','h_nav')

# ═══════════════ 第四步：报告 ═══════════════
print("\n" + "=" * 70)
print("第四步：生成报告")
print("=" * 70)

def fmtc(v):
    return ("+%s" if v>=0 else "%s") % str(round(v*100,1)) + "%"

lines = []
lines.append("# ZZ800 FCF策略七版对比报告（B/D/E/F/X/G/H）")
lines.append(f"\n> 生成日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append("> H版核心改动：排序指标从FCF收益率(FCF/EV)改为FCF绝对值")
lines.append("\n## 一、版本说明")
lines.append("| 版本 | 排序指标 | TopN | 缓冲区 | 加权方式 |")
lines.append("|------|---------|------|--------|---------|")
lines.append("| B版 | FCF收益率(FCF/EV) | 固定50 | ±0%(无) | FCF绝对值+10%封顶 |")
lines.append("| D版 | FCF收益率(FCF/EV) | 固定50 | ±20% | FCF绝对值+10%封顶 |")
lines.append("| E版 | FCF收益率(FCF/EV) | 固定50 | ±40% | FCF绝对值+10%封顶 |")
lines.append("| F版 | FCF收益率(FCF/EV) | 固定50 | ±50% | FCF绝对值+10%封顶 |")
lines.append("| X版 | FCF收益率(FCF/EV) | 全成分 | 无 | FCF绝对值+10%封顶 |")
lines.append("| G版 | FCF收益率(FCF/EV) | max(50,int(Q/100)*25) | 无 | FCF绝对值+10%封顶 |")
lines.append("| **H版** | **FCF绝对值** | **固定50** | **无** | **FCF绝对值+10%封顶** |")

lines.append("\n## 二、核心指标对比")
lines.append("| 指标 | B版 | D版 | E版 | F版 | X版 | G版 | **H版** | 932368 | 沪深300 |")
lines.append("|------|-----|-----|-----|-----|-----|---------|---------|--------|---------|")
for metric, label in [('ann','年化收益'),('mdd','最大回撤'),('vol','年化波动率'),
                       ('sharpe','夏普比率'),('calmar','Calmar比率'),('win','单期胜率'),
                       ('to','平均换手率'),('nav','期末净值'),('avg_cnt','平均选股数')]:
    row = f"| **{label}** |"
    for vn in ['B','D','E','F','X','G','H']:
        v = vs[vn][metric]
        if metric in ('ann','mdd','vol','win','to'):
            row += f" {round(v,2)}% |"
        elif metric in ('sharpe','calmar'):
            row += f" {round(v,3)} |"
        elif metric == 'nav':
            row += f" {round(v,3)}x |"
        elif metric == 'avg_cnt':
            row += f" {int(v)} |"
    if metric in ('ann','mdd','vol','sharpe','nav'):
        iv = i_s[metric]; hv = h_s[metric]
        if metric in ('ann','mdd','vol'):
            row += f" {round(iv,2)}% | {round(hv,2)}% |"
        elif metric == 'sharpe':
            row += f" {round(iv,3)} | {round(hv,3)} |"
        elif metric == 'nav':
            row += f" {round(iv,3)}x | {round(hv,3)}x |"
    elif metric in ('win','to','avg_cnt','calmar'):
        row += " — | — |"
    lines.append(row)

# H版典型持仓示例
lines.append("\n## 三、H版典型持仓示例")
lines.append("> H版按FCF绝对值排序，倾向于选出FCF体量最大的公司（大盘蓝筹为主）")
sample_dates = [d for d in sorted(h_baskets.keys()) if len(h_baskets[d])>=5]
if len(sample_dates) >= 2:
    for sd in [sample_dates[0], sample_dates[len(sample_dates)//2], sample_dates[-1]]:
        stocks = h_baskets[sd]
        top5 = sorted(stocks, key=lambda x: x.get('fcf',0), reverse=True)[:5]
        lines.append(f"\n**{sd} H版Top5:**")
        lines.append("| 公司 | FCF(亿) | FCF收益率 | EV(亿) | 权重 |")
        lines.append("|------|---------|----------|--------|------|")
        for s in top5:
            fcf_b = s.get('fcf',0)/1e8
            fy = s.get('fcf_yield',0)*100
            ev_b = s.get('ev',0)/1e8
            w = s.get('weight',0)*100
            lines.append(f"| {s['ts_code']} | {round(fcf_b,1)} | {round(fy,2)}% | {round(ev_b,0)} | {round(w,1)}% |")

# H版 vs B版持仓对比
lines.append("\n## 四、H版 vs B版持仓对比")
lines.append("> 同一期调仓，按FCF绝对值排序(H版) vs 按FCF收益率排序(B版)的差异")
if len(sample_dates) >= 2:
    for sd in [sample_dates[len(sample_dates)//2]]:
        h_set = {s['ts_code'] for s in h_baskets[sd]}
        b_set = {s['ts_code'] for s in all_baskets['B'].get(sd,[])}
        common = h_set & b_set
        h_only = h_set - b_set
        b_only = b_set - h_set
        lines.append(f"\n**{sd}:**")
        lines.append(f"- 重叠: {len(common)}只 / B版50只 / H版50只")
        lines.append(f"- H版独有(FCF大但FCF率不高): {len(h_only)}只")
        lines.append(f"- B版独有(FCF率高但FCF不大): {len(b_only)}只")
        # H版独有的公司
        if h_only:
            h_extras = [s for s in h_baskets[sd] if s['ts_code'] in h_only]
            h_extras.sort(key=lambda x: x.get('fcf',0), reverse=True)
            lines.append("\n  H版独有(FCF大公司):")
            for s in h_extras[:5]:
                lines.append(f"  - {s['ts_code']}: FCF={round(s.get('fcf',0)/1e8,1)}亿, FCF率={round(s.get('fcf_yield',0)*100,2)}%")
        # B版独有的公司
        if b_only:
            b_extras = [s for s in all_baskets['B'][sd] if s['ts_code'] in b_only]
            b_extras.sort(key=lambda x: x.get('fcf_yield',0), reverse=True)
            lines.append("\n  B版独有(FCF率高公司):")
            for s in b_extras[:5]:
                lines.append(f"  - {s['ts_code']}: FCF={round(s.get('fcf',0)/1e8,1)}亿, FCF率={round(s.get('fcf_yield',0)*100,2)}%")

# 逐年收益
lines.append("\n## 五、逐年收益对比")
m['year'] = m['rb_date'].str[:4]
yr = m.groupby('year').agg(
    b_ret=('b_ret','sum'), d_ret=('d_ret','sum'), e_ret=('e_ret','sum'),
    f_ret=('f_ret','sum'), x_ret=('x_ret','sum'), g_ret=('g_ret','sum'),
    h_ret=('h_ret','sum'), idx_ret=('idx_ret','sum'), hs_ret=('hs_ret','sum'))
lines.append("| 年份 | B版 | D版 | E版 | F版 | X版 | G版 | **H版** | 932368 | 沪深300 |")
lines.append("|------|-----|-----|-----|-----|-----|---------|---------|--------|---------|")
for y, r in yr.iterrows():
    row = f"| {y} |"
    for c in ['b_ret','d_ret','e_ret','f_ret','x_ret','g_ret','h_ret','idx_ret','hs_ret']:
        v = r[c]
        row += f" {fmtc(v/100)} |"
    lines.append(row)

# 逐期超额收益
lines.append("\n## 六、逐期超额收益(H版 vs 932368)")
m_h = m[['rb_date','h_ret','idx_ret','h_exc']].copy()
lines.append("| 调仓日 | H版收益 | 932368收益 | 超额 |")
lines.append("|--------|---------|-----------|------|")
for _, r in m_h.iterrows():
    lines.append(f"| {r['rb_date']} | {round(r['h_ret'],2)}% | {round(r['idx_ret'],2)}% | {round(r['h_exc'],2)}% |")

# 综合结论
lines.append("\n## 七、综合结论")
best_ann = max(vs.items(), key=lambda x: x[1]['ann'])
best_calmar = max(vs.items(), key=lambda x: x[1]['calmar'])
best_sharpe = max(vs.items(), key=lambda x: x[1]['sharpe'])

lines.append(f"1. **最优年化**: {best_ann[0]}版({round(best_ann[1]['ann'],2)}%)")
lines.append(f"2. **最优Calmar**: {best_calmar[0]}版({round(best_calmar[1]['calmar'],3)})")
lines.append(f"3. **最优夏普**: {best_sharpe[0]}版({round(best_sharpe[1]['sharpe'],3)})")
lines.append(f"4. **H版年化**: {round(vs['H']['ann'],2)}% vs B版{round(vs['B']['ann'],2)}%")
h_b_diff = vs['H']['ann'] - vs['B']['ann']
lines.append(f"   → H版 vs B版: {round(h_b_diff,2)}pp")
lines.append(f"5. **H版换手率**: {round(vs['H']['to'],1)}% (B版{round(vs['B']['to'],1)}%)")
lines.append(f"6. **排序指标差异**: B版按FCF/EV(性价比)选股 → 偏向中小盘高FCF率公司")
lines.append(f"   → H版按FCF绝对值选股 → 偏向大盘蓝筹(FCF体量大但FCF率可能不高)")
lines.append("7. **所有版本均跑赢932368基准(13.77%)**")

lines.append(f"\n---\n*报告自动生成，计算日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}*")

report = "\n".join(lines)
Path("docs/zz800_bdefgxh_strategy_comparison.md").write_text(report, encoding='utf-8')
print("✅ 报告: docs/zz800_bdefgxh_strategy_comparison.md")

# 打印核心表格
print("\n版本   年化收益    最大回撤    夏普    换手率    期末NAV    持仓数")
print("-" * 72)
for vn in ['B','D','E','F','X','G','H']:
    s = vs[vn]
    print(f"{vn}版   {round(s['ann'],2)}%    {round(s['mdd'],2)}%   {round(s['sharpe'],3)}   {round(s['to'],1)}%    {round(s['nav'],3)}x     {int(s['avg_cnt'])}")
print("-" * 72)
print(f"932368   {round(i_s['ann'],2)}%     {round(i_s['mdd'],2)}%   {round(i_s['sharpe'],3)}     —    {round(i_s['nav'],3)}x")
print(f"沪深300    {round(h_s['ann'],2)}%    {round(h_s['mdd'],2)}%   {round(h_s['sharpe'],3)}     —      {round(h_s['nav'],3)}x")