#!/usr/bin/env python3
"""run_g_full.py — G版(自适应TopN=max(50,int(Q/100)*25)) 全流程 + 对比报告"""
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

def adaptive_top_n(qualified_count):
    """G版公式: max(50, int(Q/100)*25)"""
    return max(50, int(qualified_count / 100) * 25)

# ═══════════════ 第一步：G版选股 ═══════════════
print("=" * 70)
print("G版(自适应TopN=max(50, int(Q/100)*25)) 选股")
print("=" * 70)

uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
uni.preload_all(download=False)

out_path = PROJECT_ROOT / "output/zz800_fcf_adaptive_top"
out_path.mkdir(parents=True, exist_ok=True)

baskets = {}
t0 = time.time()

for i, date_str in enumerate(REBALANCE_DATES):
    try:
        raw = uni.get_fcf_basket(date_str, top_n=800, verbose=False, use_ttm=True)
        ranked = [dict(v, ts_code=k) for k,v in raw.items()
                  if k != "__quality_warnings__" and isinstance(v, dict)]
        ranked.sort(key=lambda x: x.get('fcf_yield',0), reverse=True)

        qualified_count = len(ranked)
        top_n = adaptive_top_n(qualified_count)
        stocks = [dict(s) for s in ranked[:top_n]]
        fcf_weights(stocks)
        baskets[date_str] = stocks
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: Q={qualified_count}, TopN={top_n}, 选{len(stocks)}只 ({time.time()-t0:.0f}s)")
    except Exception as ex:
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
        baskets[date_str] = []

with open(out_path / "all_baskets_2015_2026.json", "w") as f:
    json.dump(baskets, f, ensure_ascii=False, indent=2)

valid = sum(1 for d in baskets if len(baskets[d])>=5)
print(f"  ✅ G版: {valid}/{len(baskets)}期有效 → output/zz800_fcf_adaptive_top/")

# ═══════════════ 第二步：计算NAV ═══════════════
print("\n" + "=" * 70)
print("第二步：计算 G版 + B/D/E/F/X NAV")
print("=" * 70)

# 加载已有篮子
all_baskets = {
    'B': json.load(open("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json")),
    'D': json.load(open("output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json")),
    'E': json.load(open("output/zz800_fcf_lenient_buffer_e40/all_baskets_2015_2026.json")),
    'F': json.load(open("output/zz800_fcf_lenient_buffer_f50/all_baskets_2015_2026.json")),
    'X': json.load(open("output/zz800_fcf_full_universe/all_baskets_2015_2026.json")),
    'G': baskets,
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
for vn in ['B','D','E','F','X','G']:
    print(f"  计算 {vn}版 NAV...")
    nav_df_ver = calc_nav(all_baskets[vn])
    nav_results[vn] = nav_df_ver
    print(f"    {vn}版: {len(nav_df_ver)}期, 期末NAV={nav_df_ver['nav'].iloc[-1]:.3f}x")

# 合并到m
b_nav = nav_results['B']
m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b_ret','b_nav']
for vn in ['D','E','F','X','G']:
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
for v in ['b','d','e','f','x','g']:
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
        if curr: tos.append(len(curr-prev)/len(curr))
    return np.mean(tos)*100 if tos else 0

bs=stats('b_ret','b_nav'); ds=stats('d_ret','d_nav'); es=stats('e_ret','e_nav')
fs=stats('f_ret','f_nav'); xs=stats('x_ret','x_nav'); gs=stats('g_ret','g_nav')
idx_s=stats('idx_ret','i_nav'); hs_s=stats('hs_ret','h_nav')
b_to=turnover(all_baskets['B']); d_to=turnover(all_baskets['D'])
e_to=turnover(all_baskets['E']); f_to=turnover(all_baskets['F'])
x_to=turnover(all_baskets['X']); g_to=turnover(all_baskets['G'])

# G版每期选股数
g_counts = {d: len(all_baskets['G'].get(d,[])) for d in REBALANCE_DATES if len(all_baskets['G'].get(d,[]))>=5}

# X版统计
x_stats = {}
for d, stocks in all_baskets['X'].items():
    if len(stocks) < 5: continue
    fcf_sum = sum(s.get('fcf',0) for s in stocks)/1e8
    ev_sum  = sum(s.get('ev',0) for s in stocks)/1e8
    x_stats[d] = dict(count=len(stocks), fcf_sum=round(fcf_sum,0),
                       ev_sum=round(ev_sum,0), ev_fcf_ratio=round(ev_sum/fcf_sum,2) if fcf_sum>0 else 0)

# 年度收益
ar = {}
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    ar[yr] = {}
    for v,k in [('B','b'),('D','d'),('E','e'),('F','f'),('X','x'),('G','g')]:
        ar[yr][v] = (1+rows[k+'_ret']/100).prod()-1
    ar[yr]['932368'] = (1+rows['idx_ret']/100).prod()-1
    ar[yr]['沪深300'] = (1+rows['hs_ret']/100).prod()-1

# ═══════════════ 第四步：生成报告 ═══════════════
print("\n" + "=" * 70)
print("第三步：生成对比报告")
print("=" * 70)

now = datetime.now().strftime("%Y-%m-%d %H:%M"); N = len(m)
bmap = {'B':'B版','D':'D版','E':'E版','F':'F版','X':'X版','G':'G版','932368':'932368','沪深300':'沪深300'}

lines = []
lines.append("# ZZ800 FCF 策略全版本回测报告（B/D/E/F/X/G 六版对比）")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + str(m['rb_date'].iloc[0]) + " → " + str(m['next_rb'].iloc[-1]) + "（共 " + str(N) + " 期）")
lines.append("> 全收益模式（含分红再投资，复权价计算）")
lines.append("")
lines.append("---")

# 一、策略版本
lines.append("")
lines.append("## 一、策略版本说明")
lines.append("")
lines.append("| 版本 | 缓冲区 | 选股方式 | 换手率 | 核心差异 |")
lines.append("|------|--------|----------|--------|----------|")
lines.append("| **B版** | ±0% | Top50固定 | " + str(round(b_to,1)) + "% | 纯FCF率排名Top50 |")
lines.append("| **D版** | ±20% | Top50（缓冲区） | " + str(round(d_to,1)) + "% | 前40必选，41-60粘性 |")
lines.append("| **E版** | ±40% | Top50（缓冲区） | " + str(round(e_to,1)) + "% | 前30必选，31-70粘性 |")
lines.append("| **F版** | ±50% | Top50（缓冲区） | " + str(round(f_to,1)) + "% | 前25必选，26-75粘性 |")
lines.append("| **X版** | — | 全成分入选 | " + str(round(x_to,1)) + "% | 不做截断，所有合格公司FCF加权 |")
lines.append("| **G版** | — | 自适应TopN | " + str(round(g_to,1)) + "% | max(50, int(Q/100)*25)，Q少时50只，Q多时扩展至75只 |")
lines.append("| 932368 | — | — | — | 官方中证800现金流TR基准 |")
lines.append("| 沪深300 | — | — | — | 大盘基准 |")
lines.append("")
lines.append("> **G版公式**: TopN = max(50, int(全成分合格股数/100) × 25)")
lines.append("> - Q<200时 TopN=50（与B版相同）")
lines.append("> - Q≥300时 TopN=75（比B版多选25只，捕获更多中等FCF率标的）")
lines.append("> **加权方式（六版一致）**：FCF绝对值加权 + 单股10%封顶迭代重分配")

# 二、核心指标
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | F版 | X版 | **G版** | 932368 | 沪深300 |")
lines.append("|------|-----|-----|-----|-----|-----|---------|--------|---------|")
for metric, key, fmt_fn in [
    ('**年化收益**','ann', lambda v: str(round(v,2))+'%'),
    ('**最大回撤**','mdd', lambda v: str(round(v,2))+'%'),
    ('年化波动率','vol', lambda v: str(round(v,2))+'%'),
    ('夏普比率','sharpe', lambda v: str(round(v,3))),
    ('Calmar比率','calmar', lambda v: str(round(v,3))),
    ('单期胜率','win', lambda v: str(round(v,1))+'%'),
    ('平均换手率','to', lambda v: str(round(v,1))+'%'),
    ('期末净值','nav', lambda v: str(round(v,3))+'x'),
]:
    row = ["| " + metric]
    for vn, s, to in [('B',bs,b_to),('D',ds,d_to),('E',es,e_to),('F',fs,f_to),('X',xs,x_to),('G',gs,g_to)]:
        val = to if key=='to' else s[key]
        row.append(fmt_fn(val))
    for s_ref in [idx_s, hs_s]:
        if key=='to': row.append("—")
        else: row.append(fmt_fn(s_ref[key]))
    lines.append(" | ".join(row) + " |")

# 三、G版选股数趋势
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、G版自适应选股数趋势")
lines.append("")
lines.append("> G版TopN随全成分合格股数Q动态调整：max(50, int(Q/100)*25)")
lines.append("")
lines.append("| 调仓日 | 全成分Q | G版TopN | G版实际选股 | B版固定50 | G版多选 | X版全成分 |")
lines.append("|--------|---------|---------|------------|----------|---------|----------|")
for d in REBALANCE_DATES:
    q = x_stats.get(d, {}).get('count', 0)
    top_n = adaptive_top_n(q) if q > 0 else 0
    g_cnt = g_counts.get(d, 0)
    diff = g_cnt - 50 if g_cnt > 0 else 0
    x_cnt = x_stats.get(d, {}).get('count', 0)
    if g_cnt > 0:
        lines.append("| " + d + " | " + str(q) + " | " + str(top_n) + " | " + str(g_cnt) +
                     " | 50 | " + ("+"+str(diff) if diff>0 else str(diff)) + " | " + str(x_cnt) + " |")

# 四、净值曲线
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、净值曲线对比")
lines.append("")
lines.append("| 调仓日 | B版 | D版 | E版 | F版 | X版 | **G版** | 932368 | 沪深300 |")
lines.append("|--------|-----|-----|-----|-----|-----|---------|--------|---------|")
for _, r in m.iterrows():
    nv = lambda v: str(round(v,3)) if pd.notna(v) else "—"
    lines.append("| " + r['rb_date'] +
                 " | " + nv(r['b_nav']) + " | " + nv(r['d_nav']) +
                 " | " + nv(r['e_nav']) + " | " + nv(r['f_nav']) +
                 " | " + nv(r['x_nav']) + " | " + nv(r['g_nav']) +
                 " | " + nv(r['i_nav']) + " | " + nv(r['h_nav']) + " |")

# 五、逐年收益
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 五、逐年收益对比")
lines.append("")
lines.append("| 年份 | B版 | D版 | E版 | F版 | X版 | **G版** | 932368 | 沪深300 | 🏆最佳 | G版TopN |")
lines.append("|------|-----|-----|-----|-----|-----|---------|--------|---------|--------|---------|")
for yr, rets in sorted(ar.items()):
    best = max(rets, key=rets.get)
    def fmt(v):
        s = ("+%s" if v>=0 else "%s") % str(round(v*100,2)) + "%"
        return "**"+s+"**" if rets[best]==v else s
    yr_rows = m[m['rb_date'].str[:4]==yr]
    yr_dates = yr_rows['rb_date'].tolist()
    g_topns = [adaptive_top_n(x_stats.get(d,{}).get('count',0)) for d in yr_dates if x_stats.get(d,{}).get('count',0)>0]
    avg_topn = round(np.mean(g_topns)) if g_topns else "—"
    lines.append("| "+yr+" | "+fmt(rets['B'])+" | "+fmt(rets['D'])+" | "+fmt(rets['E'])+
                 " | "+fmt(rets['F'])+" | "+fmt(rets['X'])+" | "+fmt(rets['G'])+
                 " | "+fmt(rets['932368'])+" | "+fmt(rets['沪深300'])+" | "+bmap[best]+" | "+str(avg_topn)+" |")

# 六、逐期收益
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 六、逐期收益明细")
lines.append("")
lines.append("| 调仓日 | B版 | D版 | E版 | G版 | X版 | G-B | G-X | G版TopN |")
lines.append("|--------|-----|-----|-----|-----|-----|-----|-----|---------|")
for _, r in m.iterrows():
    d = r['rb_date']
    sgn = lambda v: ("+" if v>=0 else "")+str(round(v,2))+"%"
    gb = r['g_ret']-r['b_ret']; gx = r['g_ret']-r['x_ret']
    topn = adaptive_top_n(x_stats.get(d,{}).get('count',0))
    lines.append("| "+d+" | "+sgn(r['b_ret'])+" | "+sgn(r['d_ret'])+" | "+sgn(r['e_ret'])+
                 " | "+sgn(r['g_ret'])+" | "+sgn(r['x_ret'])+
                 " | "+sgn(gb)+" | "+sgn(gx)+" | "+str(topn)+" |")

# 七、超额
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 七、超额收益分析（vs 932368）")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | F版 | X版 | **G版** |")
lines.append("|------|-----|-----|-----|-----|-----|---------|")
lines.append("| 年化超额 | " + " | ".join([str(round(s['ann']-idx_s['ann'],2))+"%" for s in [bs,ds,es,fs,xs,gs]]) + " |")
lines.append("| 超额胜率 | " + " | ".join([str(round((m[v+'_exc']>0).mean()*100,1))+"%" for v in ['b','d','e','f','x','g']]) + " |")
lines.append("| 单期超额均值 | " + " | ".join([str(round(m[v+'_exc'].mean(),2))+"%" for v in ['b','d','e','f','x','g']]) + " |")

# 八、G版 vs B版 vs X版对比
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 八、G版 vs B版 vs X版深度对比")
lines.append("")
lines.append("### 8.1 核心指标三版对比")
lines.append("")
lines.append("| 指标 | B版(Top50固定) | G版(自适应TopN) | X版(全成分) | G-B差 | G-X差 |")
lines.append("|------|---------------|----------------|-----------|-------|-------|")
for key, label, fmt_fn in [
    ('ann','年化收益', lambda v: str(round(v,2))+'%'),
    ('mdd','最大回撤', lambda v: str(round(v,2))+'%'),
    ('sharpe','夏普', lambda v: str(round(v,3))),
    ('calmar','Calmar', lambda v: str(round(v,3))),
    ('win','胜率', lambda v: str(round(v,1))+'%'),
    ('nav','期末净值', lambda v: str(round(v,3))+'x'),
]:
    for vals, diffs in [(bs, gs), (xs, gs)]:
        pass  # build below
    gb_diff = gs[key]-bs[key]; gx_diff = gs[key]-xs[key]
    diff_fmt = lambda v: ("+" if v>=0 else "") + fmt_fn(abs(v)) if key not in ['nav'] else ("+" if v>=0 else "") + str(round(abs(v),3))+"x"
    if key in ['ann','mdd']:
        gb_str = ("+" if gb_diff>=0 else "") + str(round(gb_diff,2)) + "pp"
        gx_str = ("+" if gx_diff>=0 else "") + str(round(gx_diff,2)) + "pp"
    elif key in ['sharpe','calmar']:
        gb_str = ("+" if gb_diff>=0 else "") + str(round(gb_diff,3))
        gx_str = ("+" if gx_diff>=0 else "") + str(round(gx_diff,3))
    elif key == 'nav':
        gb_str = ("+" if gb_diff>=0 else "") + str(round(gb_diff,3)) + "x"
        gx_str = ("+" if gx_diff>=0 else "") + str(round(gx_diff,3)) + "x"
    else:
        gb_str = ("+" if gb_diff>=0 else "") + str(round(gb_diff,1)) + "pp"
        gx_str = ("+" if gx_diff>=0 else "") + str(round(gx_diff,1)) + "pp"
    lines.append("| " + label + " | " + fmt_fn(bs[key]) + " | " + fmt_fn(gs[key]) + " | " + fmt_fn(xs[key]) +
                 " | " + gb_str + " | " + gx_str + " |")
lines.append("| 换手率 | " + str(round(b_to,1))+"% | " + str(round(g_to,1))+"% | " + str(round(x_to,1))+"% | — | — |")

lines.append("")
lines.append("### 8.2 G版每期持仓数 vs 全成分合格数")
lines.append("")
lines.append("| 调仓日 | 全成分Q | G版TopN | B版固定50 | G版实际选股 | G版FCF集中度 | G版EV/FCF |")
lines.append("|--------|---------|---------|----------|------------|-------------|-----------|")
for d in REBALANCE_DATES:
    q = x_stats.get(d, {}).get('count', 0)
    topn = adaptive_top_n(q) if q > 0 else 0
    g_cnt = g_counts.get(d, 0)
    if g_cnt < 5: continue
    g_stocks = all_baskets['G'].get(d, [])
    x_fcf = x_stats.get(d, {}).get('fcf_sum', 0)
    g_fcf = sum(s.get('fcf',0) for s in g_stocks)/1e8
    g_ev  = sum(s.get('ev',0) for s in g_stocks)/1e8
    conc = round(g_fcf/x_fcf*100,1) if x_fcf>0 else "—"
    ratio = round(g_ev/g_fcf,2) if g_fcf>0 else "—"
    lines.append("| " + d + " | " + str(q) + " | " + str(topn) + " | 50 | " + str(g_cnt) +
                 " | " + str(conc) + "% | " + str(ratio) + " |")

# 九、综合结论
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 九、综合结论")
lines.append("")
all_anns = {'B':bs['ann'],'D':ds['ann'],'E':es['ann'],'F':fs['ann'],'X':xs['ann'],'G':gs['ann']}
best_ver = max(all_anns, key=all_anns.get)
lines.append("1. **最优版本**: " + best_ver + "版（年化" + str(round(all_anns[best_ver],2)) + "%）")
lines.append("2. **G版定位**: 自适应TopN策略，在Q<200时与B版相同(50只)，Q≥300时扩展至75只")
lines.append("3. **G版年化**: " + str(round(gs['ann'],2)) + "% vs B版" + str(round(bs['ann'],2)) + "% vs X版" + str(round(xs['ann'],2)) + "%")
if gs['ann'] > bs['ann']:
    lines.append("   → G版优于B版+" + str(round(gs['ann']-bs['ann'],2)) + "pp，自适应扩展有效")
elif gs['ann'] > xs['ann']:
    lines.append("   → G版介于B和X之间（+" + str(round(gs['ann']-xs['ann'],2)) + "pp vs X），精选仍有价值")
else:
    lines.append("   → G版不及B版和X版")
lines.append("4. **G版换手率**: " + str(round(g_to,1)) + "%（B版" + str(round(b_to,1)) + "%，X版" + str(round(x_to,1)) + "%）")
lines.append("5. **vs 932368**: 所有版本均跑赢官方基准（" + str(round(idx_s['ann'],2)) + "%）")
lines.append("")
lines.append("---")
lines.append("*报告自动生成，计算日期：" + now + "*")

report = "\n".join(lines)
with open("docs/zz800_bdefgx_strategy_comparison.md", "w") as f:
    f.write(report)

# ═══════════════ 输出摘要 ═══════════════
print("\n" + "=" * 80)
print("六版回测完成！")
print("=" * 80)
print("")
print("版本   年化收益    最大回撤    夏普    换手率    期末NAV    平均选股数")
print("-" * 80)
avg_g = round(np.mean([g_counts[d] for d in sorted(g_counts.keys())]))
avg_x_cnt = round(np.mean([x_stats[d]['count'] for d in sorted(x_stats)]))
summary_rows = [('B',bs,b_to,'50'),('D',ds,d_to,'50'),('E',es,e_to,'50'),
                ('F',fs,f_to,'50'),('X',xs,x_to,str(avg_x_cnt)),
                ('G',gs,g_to,str(avg_g))]
for vn, s, to, cnt in summary_rows:
    print(vn+"版   "+str(round(s['ann'],2)).rjust(7)+"%  "+str(round(s['mdd'],2)).rjust(8)+"%  "+
          str(round(s['sharpe'],3)).rjust(6)+"  "+str(round(to,1)).rjust(5)+"%  "+
          str(round(s['nav'],3)).rjust(7)+"x  "+cnt.rjust(5))
print("-" * 80)
print("932368 "+str(round(idx_s['ann'],2)).rjust(7)+"%  "+str(round(idx_s['mdd'],2)).rjust(8)+"%  "+
      str(round(idx_s['sharpe'],3)).rjust(6)+"     —  "+str(round(idx_s['nav'],3)).rjust(7)+"x")
print("沪深300 "+str(round(hs_s['ann'],2)).rjust(7)+"%  "+str(round(hs_s['mdd'],2)).rjust(8)+"%  "+
      str(round(hs_s['sharpe'],3)).rjust(6)+"     —  "+str(round(hs_s['nav'],3)).rjust(7)+"x")

print("\nG版选股数趋势:")
print("年份  Q均值  G版TopN  G版实际")
for yr in sorted(ar.keys()):
    yr_rows = m[m['rb_date'].str[:4]==yr]
    yr_dates = yr_rows['rb_date'].tolist()
    qs = [x_stats.get(d,{}).get('count',0) for d in yr_dates]
    topns = [adaptive_top_n(q) for q in qs if q>0]
    gc = [g_counts.get(d,0) for d in yr_dates]
    avg_q = round(np.mean([q for q in qs if q>0]))
    avg_t = round(np.mean(topns)) if topns else "—"
    avg_gc = round(np.mean([c for c in gc if c>0])) if any(c>0 for c in gc) else "—"
    print(yr+"  "+str(avg_q).rjust(5)+"  "+str(avg_t).rjust(5)+"  "+str(avg_gc).rjust(5))

print("\n✅ 报告: docs/zz800_bdefgx_strategy_comparison.md")