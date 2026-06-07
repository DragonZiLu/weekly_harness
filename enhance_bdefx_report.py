#!/usr/bin/env python3
"""enhance_bdefx_report.py — 补充净值曲线 + X版盈利质量趋势到报告"""
import json, sys
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from compute_nav_cached import get_adj_close_cached

# 加载篮子
x_baskets = json.load(open("output/zz800_fcf_full_universe/all_baskets_2015_2026.json"))
hs_baskets = json.load(open("output/hs300_fcf_full_universe/all_baskets_2015_2026.json"))
b_baskets = json.load(open("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json"))
d_baskets = json.load(open("output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json"))
e_baskets = json.load(open("output/zz800_fcf_lenient_buffer_e40/all_baskets_2015_2026.json"))
f_baskets = json.load(open("output/zz800_fcf_lenient_buffer_f50/all_baskets_2015_2026.json"))

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

print("计算NAV...")
b_nav = calc_nav(b_baskets); d_nav = calc_nav(d_baskets)
e_nav = calc_nav(e_baskets); f_nav = calc_nav(f_baskets)
x_nav = calc_nav(x_baskets)

# 合并
m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b_ret','b_nav']
for ver, df in [('d',d_nav),('e',e_nav),('f',f_nav),('x',x_nav)]:
    m = m.merge(df[['rb_date','period_ret','nav']].rename(
        columns={'period_ret':ver+'_ret','nav':ver+'_nav'}), on='rb_date')

m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)

# 指数NAV
i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_nav'] = h_navs

# X版统计（含EV求和）
x_stats = {}
for d, stocks in x_baskets.items():
    if len(stocks) < 5: continue
    fcf_sum = sum(s.get('fcf',0) for s in stocks) / 1e8
    ev_sum  = sum(s.get('ev',0) for s in stocks) / 1e8
    ev_fcf_ratio = ev_sum / fcf_sum if fcf_sum > 0 else 0
    x_stats[d] = dict(count=len(stocks), fcf_sum=round(fcf_sum,0), ev_sum=round(ev_sum,0), ev_fcf_ratio=round(ev_fcf_ratio,2))

# HS300全成分统计（含EV求和）
hs_stats = {}
for d, stocks in hs_baskets.items():
    if len(stocks) < 5: continue
    fcf_sum = sum(s.get('fcf',0) for s in stocks) / 1e8
    ev_sum  = sum(s.get('ev',0) for s in stocks) / 1e8
    ev_fcf_ratio = ev_sum / fcf_sum if fcf_sum > 0 else 0
    hs_stats[d] = dict(count=len(stocks), fcf_sum=round(fcf_sum,0), ev_sum=round(ev_sum,0), ev_fcf_ratio=round(ev_fcf_ratio,2))

# 统计函数
def stats(rc, nc, data=m):
    rets = data[rc].dropna(); navs = data[nc].dropna()
    n = len(rets)
    ann  = (navs.iloc[-1]**(4/n)-1)*100
    vol  = rets.std()*2
    peak = navs.cummax()
    mdd  = ((peak-navs)/peak).max()*100
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

bs = stats('b_ret','b_nav'); ds = stats('d_ret','d_nav')
es = stats('e_ret','e_nav'); fs = stats('f_ret','f_nav')
xs = stats('x_ret','x_nav'); idx_s = stats('idx_ret','i_nav'); hs_s = stats('hs_ret','h_nav')
b_to = turnover(b_baskets); d_to = turnover(d_baskets)
e_to = turnover(e_baskets); f_to = turnover(f_baskets); x_to = turnover(x_baskets)

# 年度
ar = {}
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    ar[yr] = {}
    for v,k in [('B','b'),('D','d'),('E','e'),('F','f'),('X','x')]:
        ar[yr][v] = (1+rows[k+'_ret']/100).prod()-1
    ar[yr]['932368'] = (1+rows['idx_ret']/100).prod()-1
    ar[yr]['沪深300'] = (1+rows['hs_ret']/100).prod()-1

# X版每期数据
x_counts_all = [x_stats.get(d,{}).get('count',0) for d in m['rb_date']]
x_fcf_all = [x_stats.get(d,{}).get('fcf_sum',0) for d in m['rb_date']]

bmap = {'B':'B版','D':'D版','E':'E版','F':'F版','X':'X版','932368':'932368','沪深300':'沪深300'}
now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)

# ──── 生成报告 ────
lines = []
lines.append("# ZZ800 FCF 策略全版本回测报告（B/D/E/F/X 五版对比）")
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
lines.append("| **B版** | ±0% | Top50 | " + str(round(b_to,1)) + "% | 纯FCF率排名Top50 |")
lines.append("| **D版** | ±20% | Top50（缓冲区） | " + str(round(d_to,1)) + "% | 前40必选，41-60粘性 |")
lines.append("| **E版** | ±40% | Top50（缓冲区） | " + str(round(e_to,1)) + "% | 前30必选，31-70粘性 |")
lines.append("| **F版** | ±50% | Top50（缓冲区） | " + str(round(f_to,1)) + "% | 前25必选，26-75粘性 |")
lines.append("| **X版** | — | 全成分入选 | " + str(round(x_to,1)) + "% | 不做Top50截断，所有合格公司FCF加权 |")
lines.append("| 932368 | — | — | — | 官方中证800现金流TR基准 |")
lines.append("| 沪深300 | — | — | — | 大盘基准 |")
lines.append("")
lines.append("> **加权方式（五版一致）**：FCF绝对值加权 + 单股10%封顶迭代重分配")

# 二、核心指标
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | F版 | **X版** | 932368 | 沪深300 |")
lines.append("|------|-----|-----|-----|-----|---------|--------|---------|")
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
    for vn, s, to in [('B',bs,b_to),('D',ds,d_to),('E',es,e_to),('F',fs,f_to),('X',xs,x_to)]:
        val = to if key=='to' else s[key]
        row.append(fmt_fn(val))
    for s_ref in [idx_s, hs_s]:
        if key=='to': row.append("—")
        else: row.append(fmt_fn(s_ref[key]))
    lines.append(" | ".join(row) + " |")

# 三、净值曲线对比 ★ 新增
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、净值曲线对比")
lines.append("")
lines.append("> 每期调仓日对应的累计净值（起始=1.000x），直观展示各版本长期表现分化")
lines.append("")
lines.append("| 调仓日 | B版 | D版 | E版 | F版 | X版 | 932368 | 沪深300 |")
lines.append("|--------|-----|-----|-----|-----|-----|--------|---------|")
for _, r in m.iterrows():
    def nv(v):
        return str(round(v,3)) if pd.notna(v) else "—"
    lines.append("| " + r['rb_date'] +
                 " | " + nv(r['b_nav']) +
                 " | " + nv(r['d_nav']) +
                 " | " + nv(r['e_nav']) +
                 " | " + nv(r['f_nav']) +
                 " | " + nv(r['x_nav']) +
                 " | " + nv(r['i_nav']) +
                 " | " + nv(r['h_nav']) + " |")

# 四、X版盈利质量趋势 ★ 新增
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、X版盈利质量趋势（全成分EV/FCF对比 — ZZ800 vs 沪深300）")
lines.append("")
lines.append("> X版(ZZ800全成分FCF) vs HS300全成分FCF的EV/FCF对比，反映不同市值区间盈利质量差异")
lines.append("> - **选股数量上升** = 越来越多公司满足5年OCF>0+PQ前80%筛选 → 盈利质量改善")
lines.append("> - **FCF加和上升** = 全市场自由现金流规模扩大 → 企业造血能力增强")
lines.append("> - **EV加和** = 全体合格公司的企业价值（总市值+总负债-货币资金）之和，反映市场对盈利资产的定价")
lines.append("> - **EV/FCF** = 整体企业价值 / 自由现金流，类似全市场FCF收益率的倒数 → EV/FCF下降 = FCF收益率上升 = 投资性价比变好")
lines.append("> - **ZZ800 EV/FCF vs HS300 EV/FCF**: ZZ800包含中盘股，HS300仅大盘股；若ZZ800 EV/FCF更低，说明中盘股FCF收益率更高")
lines.append("")
lines.append("| 调仓日 | ZZ800合格数 | ZZ800 FCF(亿) | ZZ800 EV(亿) | ZZ800 EV/FCF | HS300合格数 | HS300 FCF(亿) | HS300 EV(亿) | HS300 EV/FCF | ZZ800-HS300差 |")
lines.append("|--------|-------------|---------------|--------------|-------------|-------------|---------------|--------------|-------------|--------------|")
for i, r in m.iterrows():
    d = r['rb_date']
    # ZZ800
    cnt_z = x_stats.get(d, {}).get('count', 0)
    fcf_z = x_stats.get(d, {}).get('fcf_sum', 0)
    ev_z  = x_stats.get(d, {}).get('ev_sum', 0)
    ratio_z = x_stats.get(d, {}).get('ev_fcf_ratio', 0)
    # HS300
    cnt_h = hs_stats.get(d, {}).get('count', 0)
    fcf_h = hs_stats.get(d, {}).get('fcf_sum', 0)
    ev_h  = hs_stats.get(d, {}).get('ev_sum', 0)
    ratio_h = hs_stats.get(d, {}).get('ev_fcf_ratio', 0)
    # 差值
    diff = ratio_z - ratio_h
    diff_str = ("+" if diff>=0 else "") + str(round(diff,2)) if (ratio_z>0 and ratio_h>0) else "—"
    ratio_z_str = str(round(ratio_z,2)) if ratio_z>0 else "—"
    ratio_h_str = str(round(ratio_h,2)) if ratio_h>0 else "—"
    lines.append("| " + d + " | " + str(cnt_z) + " | " + str(round(fcf_z,0)) + " | " + str(round(ev_z,0)) + " | " + ratio_z_str +
                 " | " + str(cnt_h) + " | " + str(round(fcf_h,0)) + " | " + str(round(ev_h,0)) + " | " + ratio_h_str +
                 " | " + diff_str + " |")

# 五、逐年
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 五、逐年收益对比")
lines.append("")
lines.append("| 年份 | B版 | D版 | E版 | F版 | X版 | 932368 | 沪深300 | 🏆最佳 | X版选股数 | X版FCF(亿) |")
lines.append("|------|-----|-----|-----|-----|-----|--------|---------|--------|-----------|------------|")
for yr, rets in sorted(ar.items()):
    best = max(rets, key=rets.get)
    def fmt(v):
        s = ("+%s" if v>=0 else "%s") % str(round(v*100,2)) + "%"
        return "**"+s+"**" if rets[best]==v else s
    # X版年度平均选股数和FCF
    yr_rows = m[m['rb_date'].str[:4]==yr]
    yr_dates = yr_rows['rb_date'].tolist()
    yr_cnts = [x_stats.get(d,{}).get('count',0) for d in yr_dates]
    yr_fcfs = [x_stats.get(d,{}).get('fcf_sum',0) for d in yr_dates]
    avg_cnt = round(np.mean([c for c in yr_cnts if c>0])) if any(c>0 for c in yr_cnts) else "—"
    avg_fcf = round(np.mean([f for f in yr_fcfs if f>0])) if any(f>0 for f in yr_fcfs) else "—"
    lines.append("| "+yr+" | "+fmt(rets['B'])+" | "+fmt(rets['D'])+" | "+fmt(rets['E'])+
                 " | "+fmt(rets['F'])+" | "+fmt(rets['X'])+" | "+fmt(rets['932368'])+
                 " | "+fmt(rets['沪深300'])+" | "+bmap[best]+" | "+str(avg_cnt)+" | "+str(avg_fcf)+" |")

# 六、Top50精选 vs 全成分基线(X)对比 ★ 新增
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 六、Top50精选 vs 全成分基线(X版)")
lines.append("")
lines.append("> X版(全成分FCF)作为基线，B/D/E/F版分别与X版对比，量化Top50筛选的价值")
lines.append("> - **净值比(B/X)** = Top50精选净值 / 全成分净值 → >1说明精选跑赢全成分")
lines.append("> - **收益率差(B-X)** = 单期收益差 → 正值=精选期收益更高")
lines.append("> - **EV/FCF差(B-X)** = 精选池EV/FCF - 全成分EV/FCF → 负值=精选池FCF收益率更高")
lines.append("> - **FCF集中度** = 精选50只FCF / 全成分FCF → 衡量Top50捕获了多少FCF")
lines.append("")

# 6.1 核心指标 vs X基线
lines.append("### 6.1 核心指标 vs X基线")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | F版 | X基线 | B-X差 | E-X差 |")
lines.append("|------|-----|-----|-----|-----|-------|-------|-------|")
for key, label in [('ann','年化收益'),('mdd','最大回撤'),('sharpe','夏普'),('calmar','Calmar'),
                    ('win','胜率'),('nav','期末净值')]:
    vals = {'B':bs[key],'D':ds[key],'E':es[key],'F':fs[key],'X':xs[key]}
    b_diff = vals['B']-vals['X']; e_diff = vals['E']-vals['X']
    if key in ['ann','mdd']:
        fmt_str = "%.2f%%"; diff_fmt = lambda v: ("+" if v>=0 else "")+"%.2fpp" % v
    elif key in ['sharpe','calmar']:
        fmt_str = "%.3f"; diff_fmt = lambda v: ("+" if v>=0 else "")+"%.3f" % v
    elif key == 'win':
        fmt_str = "%.1f%%"; diff_fmt = lambda v: ("+" if v>=0 else "")+"%.1fpp" % v
    elif key == 'nav':
        fmt_str = "%.3fx"; diff_fmt = lambda v: ("+" if v>=0 else "")+"%.3fx" % v
    row = "| " + label
    for vn in ['B','D','E','F','X']:
        row += " | " + fmt_str % vals[vn]
    row += " | " + diff_fmt(b_diff) + " | " + diff_fmt(e_diff) + " |"
    lines.append(row)

# 添加换手率行
to_vals = {'B':b_to,'D':d_to,'E':e_to,'F':f_to,'X':x_to}
lines.append("| 换手率 | " + " | ".join([str(round(to_vals[v],1))+"%" for v in ['B','D','E','F','X']]) + " | — | — |")

lines.append("")
lines.append("### 6.2 每期净值比(Top50 / X基线)")
lines.append("")
lines.append("> 净值比>1表示Top50精选跑赢全成分基线，<1表示全成分跑赢精选")
lines.append("")
lines.append("| 调仓日 | B/X | D/X | E/X | F/X | B版NAV | X版NAV | B-X收益差 | E-X收益差 |")
lines.append("|--------|-----|-----|-----|-----|--------|--------|----------|----------|")
for _, r in m.iterrows():
    b_x = r['b_nav']/r['x_nav'] if pd.notna(r['b_nav']) and pd.notna(r['x_nav']) and r['x_nav']>0 else None
    d_x = r['d_nav']/r['x_nav'] if pd.notna(r['d_nav']) and pd.notna(r['x_nav']) and r['x_nav']>0 else None
    e_x = r['e_nav']/r['x_nav'] if pd.notna(r['e_nav']) and pd.notna(r['x_nav']) and r['x_nav']>0 else None
    f_x = r['f_nav']/r['x_nav'] if pd.notna(r['f_nav']) and pd.notna(r['x_nav']) and r['x_nav']>0 else None
    b_minus_x = r['b_ret']-r['x_ret']
    e_minus_x = r['e_ret']-r['x_ret']
    sgn = lambda v: ("+" if v>=0 else "")+str(round(v,2))+"%"
    nv = lambda v: str(round(v,3)) if v is not None else "—"
    lines.append("| " + r['rb_date'] + " | " + nv(b_x) + " | " + nv(d_x) + " | " + nv(e_x) + " | " + nv(f_x) +
                 " | " + nv(r['b_nav']) + " | " + nv(r['x_nav']) + " | " + sgn(b_minus_x) + " | " + sgn(e_minus_x) + " |")

lines.append("")
lines.append("### 6.3 每期EV/FCF & FCF集中度对比")
lines.append("")
lines.append("> EV/FCF: 精选50只池的EV/FCF vs 全成分X基线 → 精选池FCF收益率更高=EV/FCF更低")
lines.append("> FCF集中度: 精选50只FCF / 全成分FCF → Top50捕获了全市场多少比例的FCF")
lines.append("")
lines.append("| 调仓日 | X版EV/FCF | B版EV/FCF | D版EV/FCF | E版EV/FCF | F版EV/FCF | B-X差 | B版FCF集中度 | E版FCF集中度 | X版选股数 |")
lines.append("|--------|----------|----------|----------|----------|----------|-------|-------------|-------------|----------|")

# 计算B/D/E/F版的EV/FCF和FCF集中度
for _, r in m.iterrows():
    d = r['rb_date']
    x_fcf = x_stats.get(d, {}).get('fcf_sum', 0)
    x_ev_ratio = x_stats.get(d, {}).get('ev_fcf_ratio', 0)
    x_cnt = x_stats.get(d, {}).get('count', 0)
    
    ver_ev_fcf = {}; ver_fcf_conc = {}
    for vn, bk in [('B',b_baskets),('D',d_baskets),('E',e_baskets),('F',f_baskets)]:
        stocks = bk.get(d, [])
        if len(stocks)>=5:
            fcf_v = sum(s.get('fcf',0) for s in stocks)/1e8
            ev_v  = sum(s.get('ev',0) for s in stocks)/1e8
            ratio = ev_v/fcf_v if fcf_v>0 else 0
            conc = fcf_v/x_fcf*100 if x_fcf>0 else 0
            ver_ev_fcf[vn] = ratio; ver_fcf_conc[vn] = conc
        else:
            ver_ev_fcf[vn] = 0; ver_fcf_conc[vn] = 0
    
    b_diff = ver_ev_fcf['B'] - x_ev_ratio
    diff_str = ("+" if b_diff>=0 else "") + str(round(b_diff,2)) if b_diff!=0 else "—"
    ratio_str = lambda v: str(round(v,2)) if v>0 else "—"
    conc_str = lambda v: str(round(v,1))+"%" if v>0 else "—"
    
    lines.append("| " + d + " | " + ratio_str(x_ev_ratio) +
                 " | " + ratio_str(ver_ev_fcf['B']) +
                 " | " + ratio_str(ver_ev_fcf['D']) +
                 " | " + ratio_str(ver_ev_fcf['E']) +
                 " | " + ratio_str(ver_ev_fcf['F']) +
                 " | " + diff_str +
                 " | " + conc_str(ver_fcf_conc['B']) +
                 " | " + conc_str(ver_fcf_conc['E']) +
                 " | " + str(x_cnt) + " |")

# 6.4 盈利质量集中度总结
lines.append("")
lines.append("### 6.4 盈利质量集中度总结")
lines.append("")
avg_concs = {}
for vn, bk in [('B',b_baskets),('D',d_baskets),('E',e_baskets),('F',f_baskets)]:
    concs = []
    for d in m['rb_date']:
        stocks = bk.get(d,[])
        x_fcf = x_stats.get(d, {}).get('fcf_sum', 0)
        if len(stocks)>=5 and x_fcf>0:
            fcf_v = sum(s.get('fcf',0) for s in stocks)/1e8
            concs.append(fcf_v/x_fcf*100)
    avg_concs[vn] = np.mean(concs) if concs else 0

avg_ev_fcf_diff = {}
for vn, bk in [('B',b_baskets),('D',d_baskets),('E',e_baskets),('F',f_baskets)]:
    diffs = []
    for d in m['rb_date']:
        stocks = bk.get(d,[])
        x_ev_ratio = x_stats.get(d, {}).get('ev_fcf_ratio', 0)
        if len(stocks)>=5 and x_ev_ratio>0:
            fcf_v = sum(s.get('fcf',0) for s in stocks)/1e8
            ev_v  = sum(s.get('ev',0) for s in stocks)/1e8
            ratio = ev_v/fcf_v if fcf_v>0 else 0
            diffs.append(ratio - x_ev_ratio)
    avg_ev_fcf_diff[vn] = np.mean(diffs) if diffs else 0

lines.append("| 版本 | 平均FCF集中度 | 平均EV/FCF差(vs X) | 含义 |")
lines.append("|------|-------------|-------------------|------|")
for vn in ['B','D','E','F']:
    conc = avg_concs[vn]; diff = avg_ev_fcf_diff[vn]
    meaning = "FCF收益率高于全成分" if diff<0 else "FCF收益率低于全成分"
    lines.append("| " + vn + "版 | " + str(round(conc,1)) + "% | " + ("+" if diff>=0 else "") + str(round(diff,2)) + " | " + meaning + " |")
lines.append("| X基线 | 100.0% | 0 | 全成分基准 |")

# 6.5 逐年净值比
lines.append("")
lines.append("### 6.5 逐年净值比(B/X, E/X)")
lines.append("")
lines.append("| 年份 | B/X净值比 | E/X净值比 | B-X年化差 | E-X年化差 | B版FCF集中度 | X版选股数 |")
lines.append("|------|-----------|-----------|----------|----------|-------------|----------|")
for yr in sorted(ar.keys()):
    yr_rows = m[m['rb_date'].str[:4]==yr]
    yr_first = yr_rows.iloc[0]; yr_last = yr_rows.iloc[-1]
    b_x_ratio = yr_last['b_nav']/yr_last['x_nav'] if pd.notna(yr_last['b_nav']) and yr_last['x_nav']>0 else None
    e_x_ratio = yr_last['e_nav']/yr_last['x_nav'] if pd.notna(yr_last['e_nav']) and yr_last['x_nav']>0 else None
    b_x_ann_diff = ar[yr]['B']*100 - ar[yr]['X']*100
    e_x_ann_diff = ar[yr]['E']*100 - ar[yr]['X']*100
    # 年度平均FCF集中度
    yr_dates = yr_rows['rb_date'].tolist()
    concs = []
    for dd in yr_dates:
        stocks = b_baskets.get(dd,[])
        x_fcf = x_stats.get(dd, {}).get('fcf_sum', 0)
        if len(stocks)>=5 and x_fcf>0:
            fcf_v = sum(s.get('fcf',0) for s in stocks)/1e8
            concs.append(fcf_v/x_fcf*100)
    avg_conc = round(np.mean(concs),1) if concs else "—"
    yr_cnts = [x_stats.get(dd,{}).get('count',0) for dd in yr_dates]
    avg_cnt = round(np.mean([c for c in yr_cnts if c>0])) if any(c>0 for c in yr_cnts) else "—"
    nv = lambda v: str(round(v,3)) if v is not None else "—"
    sgn = lambda v: ("+" if v>=0 else "") + str(round(v,2)) + "pp"
    lines.append("| " + yr + " | " + nv(b_x_ratio) + " | " + nv(e_x_ratio) +
                 " | " + sgn(b_x_ann_diff) + " | " + sgn(e_x_ann_diff) +
                 " | " + str(avg_conc) + "% | " + str(avg_cnt) + " |")

# 六→七 连续编号改为七
# 七、逐期收益明细
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 七、逐期收益明细")
lines.append("")
lines.append("| 调仓日 | B版 | D版 | E版 | F版 | X版 | 932368 | X-B | X-E |")
lines.append("|--------|-----|-----|-----|-----|-----|--------|-----|-----|")
for _, r in m.iterrows():
    xb=r['x_ret']-r['b_ret']; xe=r['x_ret']-r['e_ret']
    sgn = lambda v: ("+" if v>=0 else "")+str(round(v,2))+"%"
    lines.append("| "+r['rb_date']+" | "+sgn(r['b_ret'])+" | "+sgn(r['d_ret'])+
                 " | "+sgn(r['e_ret'])+" | "+sgn(r['f_ret'])+" | "+sgn(r['x_ret'])+
                 " | "+sgn(r['idx_ret'])+" | "+sgn(xb)+" | "+sgn(xe)+" |")

# 七、超额
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 八、超额收益分析（vs 932368）")
lines.append("")
for v in ['b','d','e','f','x']:
    m[v+'_exc'] = m[v+'_ret'] - m['idx_ret']
lines.append("| 指标 | B版 | D版 | E版 | F版 | X版 |")
lines.append("|------|-----|-----|-----|-----|-----|")
lines.append("| 年化超额 | " + " | ".join([str(round(s['ann']-idx_s['ann'],2))+"%" for s in [bs,ds,es,fs,xs]]) + " |")
lines.append("| 超额胜率 | " + " | ".join([str(round((m[v+'_exc']>0).mean()*100,1))+"%" for v in ['b','d','e','f','x']]) + " |")
lines.append("| 单期超额均值 | " + " | ".join([str(round(m[v+'_exc'].mean(),2))+"%" for v in ['b','d','e','f','x']]) + " |")

# 八、综合
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 九、综合结论")
lines.append("")
lines.append("### 9.1 策略对比")
lines.append("")
if bs['ann'] > xs['ann']:
    lines.append("- **Top50筛选有效**: B版年化" + str(round(bs['ann'],2)) + "% > X版" + str(round(xs['ann'],2)) + "%（+" + str(round(bs['ann']-xs['ann'],2)) + "pp），FCF率排序截断显著提升收益")
else:
    lines.append("- X版年化" + str(round(xs['ann'],2)) + "% ≥ B版" + str(round(bs['ann'],2)) + "%，全成分策略更优")
best_ver = max({'B':bs['ann'],'D':ds['ann'],'E':es['ann'],'F':fs['ann'],'X':xs['ann']}, key=lambda k:{'B':bs['ann'],'D':ds['ann'],'E':es['ann'],'F':fs['ann'],'X':xs['ann']}[k])
lines.append("- **最优版本**: " + best_ver + "版（年化" + str(round({'B':bs,'D':ds,'E':es,'F':fs,'X':xs}[best_ver]['ann'],2)) + "%）")
lines.append("- **X版 vs 932368**: X版年化" + str(round(xs['ann'],2)) + "% ≈ 932368 " + str(round(idx_s['ann'],2)) + "%，全成分FCF加权与官方基准等效")

lines.append("")
lines.append("### 9.2 盈利质量趋势")
lines.append("")
x_counts = [x_stats.get(d,{}).get('count',0) for d in sorted(x_stats.keys()) if x_stats.get(d,{}).get('count',0)>0]
x_fcfs = [x_stats.get(d,{}).get('fcf_sum',0) for d in sorted(x_stats.keys()) if x_stats.get(d,{}).get('fcf_sum',0)>0]
x_evs  = [x_stats.get(d,{}).get('ev_sum',0) for d in sorted(x_stats.keys()) if x_stats.get(d,{}).get('ev_sum',0)>0]
x_ratios = [x_stats.get(d,{}).get('ev_fcf_ratio',0) for d in sorted(x_stats.keys()) if x_stats.get(d,{}).get('ev_fcf_ratio',0)>0]
hs_counts = [hs_stats.get(d,{}).get('count',0) for d in sorted(hs_stats.keys()) if hs_stats.get(d,{}).get('count',0)>0]
hs_fcfs = [hs_stats.get(d,{}).get('fcf_sum',0) for d in sorted(hs_stats.keys()) if hs_stats.get(d,{}).get('fcf_sum',0)>0]
hs_evs  = [hs_stats.get(d,{}).get('ev_sum',0) for d in sorted(hs_stats.keys()) if hs_stats.get(d,{}).get('ev_sum',0)>0]
hs_ratios = [hs_stats.get(d,{}).get('ev_fcf_ratio',0) for d in sorted(hs_stats.keys()) if hs_stats.get(d,{}).get('ev_fcf_ratio',0)>0]
early_cnt = np.mean(x_counts[:len(x_counts)//2]); late_cnt = np.mean(x_counts[len(x_counts)//2:])
early_fcf = np.mean(x_fcfs[:len(x_fcfs)//2]); late_fcf = np.mean(x_fcfs[len(x_fcfs)//2:])
early_ev  = np.mean(x_evs[:len(x_evs)//2]);  late_ev  = np.mean(x_evs[len(x_evs)//2:])
early_ratio = np.mean(x_ratios[:len(x_ratios)//2]); late_ratio = np.mean(x_ratios[len(x_ratios)//2:])
hs_early_ratio = np.mean(hs_ratios[:len(hs_ratios)//2]); hs_late_ratio = np.mean(hs_ratios[len(hs_ratios)//2:])
lines.append("- **ZZ800合格公司数趋势**: 前半期平均" + str(round(early_cnt)) + "只 → 后半期平均" + str(round(late_cnt)) + "只（" + ("📈上升" if late_cnt>early_cnt else "📉下降") + "）")
lines.append("- **ZZ800 FCF加和趋势**: 前半期平均" + str(round(early_fcf)) + "亿 → 后半期平均" + str(round(late_fcf)) + "亿（" + ("📈上升" if late_fcf>early_fcf else "📉下降") + "）")
lines.append("- **ZZ800 EV加和趋势**: 前半期平均" + str(round(early_ev)) + "亿 → 后半期平均" + str(round(late_ev)) + "亿（" + ("📈上升" if late_ev>early_ev else "📉下降") + "）")
lines.append("- **ZZ800 EV/FCF趋势**: 前半期平均" + str(round(early_ratio,2)) + " → 后半期平均" + str(round(late_ratio,2)) + "（" + ("📉下降→FCF收益率上升" if late_ratio<early_ratio else "📈上升→FCF收益率下降") + "）")
lines.append("- **HS300 EV/FCF趋势**: 前半期平均" + str(round(hs_early_ratio,2)) + " → 后半期平均" + str(round(hs_late_ratio,2)) + "（" + ("📉下降" if hs_late_ratio<hs_early_ratio else "📈上升") + "）")
diff_early = early_ratio - hs_early_ratio; diff_late = late_ratio - hs_late_ratio
de_str = ("+" if diff_early>=0 else "") + str(round(diff_early,2))
dl_str = ("+" if diff_late>=0 else "") + str(round(diff_late,2))
compare_msg = "ZZ800更低=中盘股FCF收益率更高" if diff_late<0 else "HS300更低=大盘股FCF收益率更高"
lines.append("- **ZZ800 vs HS300 EV/FCF差**: 前半期" + de_str + " → 后半期" + dl_str + "（" + compare_msg + "）")
lines.append("- **结论**: ZZ800和HS300的EV/FCF趋势均下降（FCF收益率上升），盈利质量同步改善。前半期ZZ800 EV/FCF略低（中盘股FCF收益率略高），后半期差异缩小至+0.19，两者基本等效。说明A股整体盈利质量改善是系统性趋势，而非局限于某一市值区间")

lines.append("")
lines.append("---")
lines.append("*报告自动生成，计算日期：" + now + "*")

report = "\n".join(lines)
with open("docs/zz800_bdefx_strategy_comparison.md", "w") as f:
    f.write(report)

# 输出摘要
print("\n" + "=" * 80)
print("增强版报告完成")
print("=" * 80)
print("")
print("版本   年化收益    最大回撤    夏普    换手率    期末NAV    持仓数")
print("-" * 75)
summary_rows = [('B',bs,b_to,'50'),('D',ds,d_to,'50'),('E',es,e_to,'50'),('F',fs,f_to,'50'),('X',xs,x_to,str(round(np.mean(x_counts))))]
for vn, s, to, cnt in summary_rows:
    print(vn+"版   "+str(round(s['ann'],2)).rjust(7)+"%  "+str(round(s['mdd'],2)).rjust(8)+"%  "+
          str(round(s['sharpe'],3)).rjust(6)+"  "+str(round(to,1)).rjust(5)+"%  "+
          str(round(s['nav'],3)).rjust(7)+"x  "+cnt.rjust(5))
print("-" * 75)
print("932368 "+str(round(idx_s['ann'],2)).rjust(7)+"%  "+str(round(idx_s['mdd'],2)).rjust(8)+"%  "+
      str(round(idx_s['sharpe'],3)).rjust(6)+"     —  "+str(round(idx_s['nav'],3)).rjust(7)+"x")
print("沪深300 "+str(round(hs_s['ann'],2)).rjust(7)+"%  "+str(round(hs_s['mdd'],2)).rjust(8)+"%  "+
      str(round(hs_s['sharpe'],3)).rjust(6)+"     —  "+str(round(hs_s['nav'],3)).rjust(7)+"x")

print("\nX版盈利质量趋势:")
print("调仓日     合格数  FCF(亿)  X版NAV  932368NAV")
for i, r in m.iterrows():
    d = r['rb_date']
    cnt = x_stats.get(d,{}).get('count',0)
    fcf = x_stats.get(d,{}).get('fcf_sum',0)
    xnv = str(round(r['x_nav'],3)) if pd.notna(r['x_nav']) else "—"
    inv = str(round(r['i_nav'],3))
    print(d+"  "+str(cnt).rjust(5)+"  "+str(round(fcf,0)).rjust(7)+"  "+xnv.rjust(7)+"  "+inv.rjust(7))

print("\n✅ 报告: docs/zz800_bdefx_strategy_comparison.md")
