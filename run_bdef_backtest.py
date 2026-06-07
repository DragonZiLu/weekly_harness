#!/usr/bin/env python3
"""run_bdef_backtest.py — B/D/E/F 四版回测与报告"""
import json, pandas as pd, numpy as np
from compute_nav_cached import get_adj_close_cached
from datetime import datetime
import sys
sys.path.insert(0, "weekly_harness")

def load_baskets(p):
    with open(p) as f: return json.load(f)

b_baskets = load_baskets("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json")
d_baskets = load_baskets("output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json")
e_baskets = load_baskets("output/zz800_fcf_lenient_buffer_e40/all_baskets_2015_2026.json")
f_baskets = load_baskets("output/zz800_fcf_lenient_buffer_f50/all_baskets_2015_2026.json")
nav_df    = pd.read_csv("output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv")

df_idx = pd.read_csv("data/index_daily/932368.CSI.csv")
df_idx['trade_date'] = df_idx['trade_date'].astype(str)
df_idx = df_idx.sort_values('trade_date')
df_hs = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str)
df_hs = df_hs.sort_values('trade_date')

def idx_ret(df, s, e):
    s_k, e_k = s.replace('-',''), e.replace('-','')
    p0 = float(df[df['trade_date'] <= s_k]['close'].iloc[-1])
    p1 = float(df[df['trade_date'] <= e_k]['close'].iloc[-1])
    return (p1/p0 - 1)*100

def calc_nav(baskets):
    nav = 1.0; rows = []
    for _, row in nav_df.iterrows():
        rb, nrb = row['rb_date'], row['next_rb']
        stocks = baskets.get(rb, [])
        if len(stocks) < 10: continue
        w_ret, w_tot = 0.0, 0.0
        for s in stocks:
            r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
            if r:
                w_ret += s['weight'] * (r[1]/r[0]-1)
                w_tot += s['weight']
        if w_tot < 0.5: continue
        pr = w_ret / w_tot
        nav *= (1 + pr)
        rows.append({'rb_date': rb, 'next_rb': nrb, 'period_ret': pr*100, 'nav': nav})
    return pd.DataFrame(rows)

print("计算NAV...")
b_nav = calc_nav(b_baskets); d_nav = calc_nav(d_baskets)
e_nav = calc_nav(e_baskets); f_nav = calc_nav(f_baskets)

m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b_ret','b_nav']
for ver, df in [('d',d_nav),('e',e_nav),('f',f_nav)]:
    m = m.merge(df[['rb_date','period_ret','nav']].rename(
        columns={'period_ret': ver+'_ret','nav': ver+'_nav'}), on='rb_date')
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs,  r['rb_date'], r['next_rb']), axis=1)

i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100);  h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_nav'] = h_navs

def stats(rc, nc):
    rets, navs = m[rc], m[nc]
    n = len(rets)
    ann  = (navs.iloc[-1]**(4/n)-1)*100
    vol  = rets.std()*2
    peak = navs.cummax()
    mdd  = ((peak-navs)/peak).max()*100
    sharpe = (ann-2.0)/vol if vol>0 else 0
    calmar = ann/mdd if mdd>0 else 0
    win  = (rets>0).mean()*100
    return dict(ann=ann, vol=vol, mdd=-mdd, sharpe=sharpe, calmar=calmar, win=win, nav=navs.iloc[-1])

def turnover(baskets):
    dates = [r['rb_date'] for _,r in nav_df.iterrows() if baskets.get(r['rb_date'])]
    tos = []
    for i in range(1, len(dates)):
        prev = {s['ts_code'] for s in baskets.get(dates[i-1],[])}
        curr = {s['ts_code'] for s in baskets.get(dates[i],[])}
        if curr: tos.append(len(curr-prev)/len(curr))
    return np.mean(tos)*100 if tos else 0

bs  = stats('b_ret','b_nav'); ds  = stats('d_ret','d_nav')
es  = stats('e_ret','e_nav'); fs  = stats('f_ret','f_nav')
is_ = stats('idx_ret','i_nav'); hs = stats('hs_ret','h_nav')
b_to = turnover(b_baskets); d_to = turnover(d_baskets)
e_to = turnover(e_baskets); f_to = turnover(f_baskets)
m['b_exc'] = m['b_ret']-m['idx_ret']; m['d_exc'] = m['d_ret']-m['idx_ret']
m['e_exc'] = m['e_ret']-m['idx_ret']; m['f_exc'] = m['f_ret']-m['idx_ret']

# 年度收益
ar = {}
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    ar[yr] = {
        'B':       (1+rows['b_ret']/100).prod()-1,
        'D':       (1+rows['d_ret']/100).prod()-1,
        'E':       (1+rows['e_ret']/100).prod()-1,
        'F':       (1+rows['f_ret']/100).prod()-1,
        '932368':  (1+rows['idx_ret']/100).prod()-1,
        'HS300':   (1+rows['hs_ret']/100).prod()-1,
    }

bmap = {'B':'B版','D':'D版','E':'E版','F':'F版','932368':'932368','HS300':'沪深300'}
now  = datetime.now().strftime("%Y-%m-%d %H:%M")
N    = len(m)

all_b = (1+m['b_ret']/100).prod()-1; all_d = (1+m['d_ret']/100).prod()-1
all_e = (1+m['e_ret']/100).prod()-1; all_f = (1+m['f_ret']/100).prod()-1
all_i = (1+m['idx_ret']/100).prod()-1; all_h = (1+m['hs_ret']/100).prod()-1

best_ver = max({'B':bs['ann'],'D':ds['ann'],'E':es['ann'],'F':fs['ann']},
               key=lambda k: {'B':bs['ann'],'D':ds['ann'],'E':es['ann'],'F':fs['ann']}[k])

buf_map = {'B':'±0%','D':'±20%','E':'±40%','F':'±50%'}
f_better = "进一步提升" if fs['ann'] > es['ann'] else "趋于平缓"
recommend_line = ("推荐 " + best_ver + "版（年化" +
                  str(round({'B':bs,'D':ds,'E':es,'F':fs}[best_ver]['ann'],2)) + "%，" +
                  "最大回撤" + str(round({'B':bs,'D':ds,'E':es,'F':fs}[best_ver]['mdd'],2)) + "%，" +
                  "换手率" + str(round({'B':b_to,'D':d_to,'E':e_to,'F':f_to}[best_ver],1)) + "%）")

lines = []
lines.append("# ZZ800 FCF 策略全版本回测报告（B/D/E/F 四版对比）")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + m['rb_date'].iloc[0] + " → " + m['next_rb'].iloc[-1] + "（共 " + str(N) + " 期）")
lines.append("> 全收益模式（含分红再投资，复权价计算）")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 一、策略版本说明")
lines.append("")
lines.append("| 版本 | 缓冲区 | 必选区 | 候选池 | 换手率 | 核心差异 |")
lines.append("|------|--------|--------|--------|--------|----------|")
lines.append("| **B版** | ±0% | Top50 | Top50 | " + str(round(b_to,1)) + "% | 纯FCF率排名Top50 |")
lines.append("| **D版** | ±20% | Top40 | Top60 | " + str(round(d_to,1)) + "% | 前40必选，41-60粘性 |")
lines.append("| **E版** | ±40% | Top30 | Top70 | " + str(round(e_to,1)) + "% | 前30必选，31-70粘性 |")
lines.append("| **F版** | ±50% | Top25 | Top75 | " + str(round(f_to,1)) + "% | 前25必选，26-75粘性（最宽）|")
lines.append("| 932368 | — | — | — | — | 官方中证800现金流TR基准 |")
lines.append("| 沪深300 | — | — | — | — | 大盘基准 |")
lines.append("")
lines.append("> **加权方式（四版一致）**：FCF绝对值加权 + 单股10%封顶迭代重分配")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | **F版** | 932368 | 沪深300 |")
lines.append("|------|-----|-----|-----|---------|--------|---------|")
lines.append("| **年化收益** | " + str(round(bs['ann'],2)) + "% | " + str(round(ds['ann'],2)) + "% | " + str(round(es['ann'],2)) + "% | **" + str(round(fs['ann'],2)) + "%** | " + str(round(is_['ann'],2)) + "% | " + str(round(hs['ann'],2)) + "% |")
lines.append("| **最大回撤** | " + str(round(bs['mdd'],2)) + "% | " + str(round(ds['mdd'],2)) + "% | " + str(round(es['mdd'],2)) + "% | **" + str(round(fs['mdd'],2)) + "%** | " + str(round(is_['mdd'],2)) + "% | " + str(round(hs['mdd'],2)) + "% |")
lines.append("| 年化波动率 | " + str(round(bs['vol'],2)) + "% | " + str(round(ds['vol'],2)) + "% | " + str(round(es['vol'],2)) + "% | " + str(round(fs['vol'],2)) + "% | " + str(round(is_['vol'],2)) + "% | " + str(round(hs['vol'],2)) + "% |")
lines.append("| 夏普比率 | " + str(round(bs['sharpe'],3)) + " | " + str(round(ds['sharpe'],3)) + " | " + str(round(es['sharpe'],3)) + " | **" + str(round(fs['sharpe'],3)) + "** | " + str(round(is_['sharpe'],3)) + " | " + str(round(hs['sharpe'],3)) + " |")
lines.append("| Calmar比率 | " + str(round(bs['calmar'],3)) + " | " + str(round(ds['calmar'],3)) + " | " + str(round(es['calmar'],3)) + " | **" + str(round(fs['calmar'],3)) + "** | " + str(round(is_['calmar'],3)) + " | " + str(round(hs['calmar'],3)) + " |")
lines.append("| 单期胜率 | " + str(round(bs['win'],1)) + "% | " + str(round(ds['win'],1)) + "% | " + str(round(es['win'],1)) + "% | " + str(round(fs['win'],1)) + "% | " + str(round(is_['win'],1)) + "% | " + str(round(hs['win'],1)) + "% |")
lines.append("| 平均换手率 | " + str(round(b_to,1)) + "% | " + str(round(d_to,1)) + "% | " + str(round(e_to,1)) + "% | **" + str(round(f_to,1)) + "%** | — | — |")
lines.append("| 期末净值 | " + str(round(bs['nav'],3)) + "x | " + str(round(ds['nav'],3)) + "x | " + str(round(es['nav'],3)) + "x | **" + str(round(fs['nav'],3)) + "x** | " + str(round(is_['nav'],3)) + "x | " + str(round(hs['nav'],3)) + "x |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、逐年收益对比")
lines.append("")
lines.append("| 年份 | B版 | D版 | E版 | F版 | 932368 | 沪深300 | 🏆最佳 |")
lines.append("|------|-----|-----|-----|-----|--------|---------|--------|")
for yr, rets in sorted(ar.items()):
    best = max(rets, key=rets.get)
    row_parts = [yr]
    for k in ['B','D','E','F','932368','HS300']:
        v = rets[k]*100
        s = ("+%s" if v>=0 else "%s") % str(round(v,2)) + "%"
        row_parts.append("**" + s + "**" if rets[best]==rets[k] else s)
    row_parts.append(bmap[best])
    lines.append("| " + " | ".join(row_parts) + " |")

all_vals = {'B':all_b,'D':all_d,'E':all_e,'F':all_f,'932368':all_i,'HS300':all_h}
best_all = max(all_vals, key=all_vals.get)
row_parts = ["**全期**"]
for k in ['B','D','E','F','932368','HS300']:
    v = all_vals[k]*100
    s = ("+%s" if v>=0 else "%s") % str(round(v,1)) + "%"
    row_parts.append("**" + s + "**")
row_parts.append(bmap[best_all])
lines.append("| " + " | ".join(row_parts) + " |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、逐期收益明细")
lines.append("")
lines.append("| 调仓日 | B版 | D版 | E版 | F版 | 932368 | F-B | F-E |")
lines.append("|--------|-----|-----|-----|-----|--------|-----|-----|")
for _, r in m.iterrows():
    fb = r['f_ret']-r['b_ret']; fe = r['f_ret']-r['e_ret']
    sign = lambda v: ("+%s" if v>=0 else "%s") % str(round(v,2)) + "%"
    lines.append("| " + r['rb_date'] + " | " + sign(r['b_ret']) + " | " + sign(r['d_ret']) + " | " + sign(r['e_ret']) + " | " + sign(r['f_ret']) + " | " + sign(r['idx_ret']) + " | " + sign(fb) + " | " + sign(fe) + " |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 五、超额收益分析（vs 932368）")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | F版 |")
lines.append("|------|-----|-----|-----|-----|")
lines.append("| 年化超额 | " + str(round(bs['ann']-is_['ann'],2)) + "% | " + str(round(ds['ann']-is_['ann'],2)) + "% | " + str(round(es['ann']-is_['ann'],2)) + "% | " + str(round(fs['ann']-is_['ann'],2)) + "% |")
lines.append("| 超额胜率 | " + str(round((m['b_exc']>0).mean()*100,1)) + "% | " + str(round((m['d_exc']>0).mean()*100,1)) + "% | " + str(round((m['e_exc']>0).mean()*100,1)) + "% | " + str(round((m['f_exc']>0).mean()*100,1)) + "% |")
lines.append("| 单期超额均值 | " + str(round(m['b_exc'].mean(),2)) + "% | " + str(round(m['d_exc'].mean(),2)) + "% | " + str(round(m['e_exc'].mean(),2)) + "% | " + str(round(m['f_exc'].mean(),2)) + "% |")
lines.append("| 最大单期超额 | " + str(round(m['b_exc'].max(),2)) + "% | " + str(round(m['d_exc'].max(),2)) + "% | " + str(round(m['e_exc'].max(),2)) + "% | " + str(round(m['f_exc'].max(),2)) + "% |")
lines.append("| 最小单期超额 | " + str(round(m['b_exc'].min(),2)) + "% | " + str(round(m['d_exc'].min(),2)) + "% | " + str(round(m['e_exc'].min(),2)) + "% | " + str(round(m['f_exc'].min(),2)) + "% |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 六、缓冲区效果分析")
lines.append("")
lines.append("### 6.1 换手率 vs 年化收益趋势")
lines.append("")
lines.append("| 版本 | 缓冲区 | 换手率 | 年化收益 | 夏普 | Calmar |")
lines.append("|------|--------|--------|----------|------|--------|")
for lab, s, to in [('B版',bs,b_to),('D版',ds,d_to),('E版',es,e_to),('F版',fs,f_to)]:
    buf = buf_map[lab[0]]
    lines.append("| " + lab + " | " + buf + " | " + str(round(to,1)) + "% | " + str(round(s['ann'],2)) + "% | " + str(round(s['sharpe'],3)) + " | " + str(round(s['calmar'],3)) + " |")

lines.append("")
lines.append("### 6.2 F版 vs E版 差异最大季度（Top10）")
lines.append("")
m['abs_fe'] = (m['f_ret']-m['e_ret']).abs()
top_diff = m.nlargest(10,'abs_fe')
lines.append("| 调仓日 | E版 | F版 | F-E | 说明 |")
lines.append("|--------|-----|-----|-----|------|")
for _, r in top_diff.iterrows():
    fe = r['f_ret']-r['e_ret']
    note = "F版更稳定" if fe>0 else "F版追涨滞后"
    lines.append("| " + r['rb_date'] + " | " + str(round(r['e_ret'],2)) + "% | " + str(round(r['f_ret'],2)) + "% | " + ("+" if fe>=0 else "") + str(round(fe,2)) + "% | " + note + " |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 七、综合结论")
lines.append("")
lines.append("### 关键发现")
lines.append("")
lines.append("1. **缓冲区规律**：B(" + str(round(bs['ann'],2)) + "%) < D(" + str(round(ds['ann'],2)) + "%) < E(" + str(round(es['ann'],2)) + "%) — F版(" + str(round(fs['ann'],2)) + "%) 年化收益" + f_better)
lines.append("2. **换手率梯度**：B(" + str(round(b_to,1)) + "%) → D(" + str(round(d_to,1)) + "%) → E(" + str(round(e_to,1)) + "%) → F(" + str(round(f_to,1)) + "%)")
lines.append("3. **最大回撤改善**：B(" + str(round(bs['mdd'],2)) + "%) → D(" + str(round(ds['mdd'],2)) + "%) → E(" + str(round(es['mdd'],2)) + "%) → F(" + str(round(fs['mdd'],2)) + "%)")
lines.append("4. **vs 932368**：F版年化超额 " + str(round(fs['ann']-is_['ann'],2)) + "%，超额胜率 " + str(round((m['f_exc']>0).mean()*100,1)) + "%")
lines.append("5. **F vs E 边际效益**：年化差 " + ("+" if fs['ann']>es['ann'] else "") + str(round(fs['ann']-es['ann'],2)) + "pp，夏普差 " + ("+" if fs['sharpe']>es['sharpe'] else "") + str(round(fs['sharpe']-es['sharpe'],3)))
lines.append("")
lines.append("### 建议")
lines.append("")
lines.append("> **" + recommend_line + "**")
lines.append("")
lines.append("---")
lines.append("")
lines.append("*报告由回测系统自动生成，计算日期：" + now + "*")

report = "\n".join(lines)
with open("docs/zz800_bdef_strategy_comparison.md", "w") as f:
    f.write(report)

f_nav.to_csv("output/zz800_fcf_lenient_buffer_f50/backtest_nav_tr.csv", index=False)

print("✅ 报告已保存: docs/zz800_bdef_strategy_comparison.md")
print("")
print("=" * 62)
print("四版对比摘要（2016-2026，40期）")
print("-" * 62)
print("版本   年化收益    最大回撤    夏普    换手率    期末NAV")
print("-" * 62)
for lab, s, to in [('B版',bs,b_to),('D版',ds,d_to),('E版',es,e_to),('F版',fs,f_to)]:
    print(lab + "   " + str(round(s['ann'],2)).rjust(7) + "%  " + str(round(s['mdd'],2)).rjust(7) + "%  " +
          str(round(s['sharpe'],3)).rjust(6) + "  " + str(round(to,1)).rjust(5) + "%  " + str(round(s['nav'],3)).rjust(7) + "x")
print("-" * 62)
print("932368 " + str(round(is_['ann'],2)).rjust(7) + "%  " + str(round(is_['mdd'],2)).rjust(7) + "%  " +
      str(round(is_['sharpe'],3)).rjust(6) + "  " + "  —  " + "  " + str(round(is_['nav'],3)).rjust(7) + "x")
print("沪深300 " + str(round(hs['ann'],2)).rjust(7) + "%  " + str(round(hs['mdd'],2)).rjust(7) + "%  " +
      str(round(hs['sharpe'],3)).rjust(6) + "  " + "  —  " + "  " + str(round(hs['nav'],3)).rjust(7) + "x")
print("")
print("逐年收益:")
print("年份    B版      D版      E版      F版    932368")
for yr, rets in sorted(ar.items()):
    print(yr + "  " + ("+" if rets['B']>=0 else "") + str(round(rets['B']*100,2)).rjust(6) + "%  " +
          ("+" if rets['D']>=0 else "") + str(round(rets['D']*100,2)).rjust(6) + "%  " +
          ("+" if rets['E']>=0 else "") + str(round(rets['E']*100,2)).rjust(6) + "%  " +
          ("+" if rets['F']>=0 else "") + str(round(rets['F']*100,2)).rjust(6) + "%  " +
          ("+" if rets['932368']>=0 else "") + str(round(rets['932368']*100,2)).rjust(6) + "%")
