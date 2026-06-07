#!/usr/bin/env python3
"""run_bdef_full.py — 一键重跑 ZZ800 B/D/E/F 全流程：选股→篮子→回测→报告"""
import sys, json, time, os
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime
from compute_nav_cached import get_adj_close_cached

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from fcf_universe import FcfUniverse

# ═══════════════ 调仓日 ═══════════════
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

TOP_N = 50
CAP = 0.10

# 四版配置: (name, buffer_ratio, low_bound, high_bound, output_dir)
VERSIONS = [
    ("B", 0.00, 50, 50, "output/zz800_fcf_fixed_lenient"),
    ("D", 0.20, 40, 60, "output/zz800_fcf_lenient_buffer"),
    ("E", 0.40, 30, 70, "output/zz800_fcf_lenient_buffer_e40"),
    ("F", 0.50, 25, 75, "output/zz800_fcf_lenient_buffer_f50"),
]

def fcf_weights(stocks, cap=CAP, max_iter=100):
    if not stocks: return stocks
    fcf_vals = [max(s.get('fcf', 0), 0) for s in stocks]
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

def apply_buffer(ranked, prev_codes, low, high, top_n):
    must = ranked[:low]
    buffer = ranked[low:high]
    buffer_old = [s for s in buffer if s['ts_code'] in prev_codes]
    buffer_new = [s for s in buffer if s['ts_code'] not in prev_codes]
    selected = must + buffer_old
    remaining = top_n - len(selected)
    if remaining > 0: selected.extend(buffer_new[:remaining])
    return selected[:top_n]

# ═══════════════ 第一步：选股生成篮子 ═══════════════
print("=" * 70)
print("第一步：选股生成 B/D/E/F 四版篮子")
print("=" * 70)

uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
uni.preload_all(download=False)

all_baskets = {}  # {ver_name: {date: [stocks]}}

for ver_name, buf_ratio, low, high, out_dir in VERSIONS:
    print(f"\n--- {ver_name}版 (buffer=±{int(buf_ratio*100)}%, low={low}, high={high}) ---")
    out_path = PROJECT_ROOT / out_dir
    out_path.mkdir(parents=True, exist_ok=True)

    baskets = {}
    prev_codes = set()
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        try:
            raw = uni.get_fcf_basket(date_str, top_n=high, verbose=False, use_ttm=True)
            ranked = [dict(v, ts_code=k) for k,v in raw.items()
                      if k != "__quality_warnings__" and isinstance(v, dict)]
            ranked.sort(key=lambda x: x.get('fcf_yield',0), reverse=True)

            if i == 0 or not prev_codes:
                stocks = [dict(s) for s in ranked[:TOP_N]]
            else:
                stocks = [dict(s) for s in apply_buffer(ranked, prev_codes, low, high, TOP_N)]

            fcf_weights(stocks)
            baskets[date_str] = stocks
            prev_codes = {s['ts_code'] for s in stocks}
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: {len(stocks)}只 ({time.time()-t0:.0f}s)")
        except Exception as ex:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
            baskets[date_str] = []

    with open(out_path / "all_baskets_2015_2026.json", "w") as f:
        json.dump(baskets, f, ensure_ascii=False, indent=2)

    valid = sum(1 for d in baskets if len(baskets[d])>=10)
    print(f"  ✅ {ver_name}版: {valid}/{len(baskets)}期有效, 保存至 {out_dir}/")
    all_baskets[ver_name] = baskets

# ═══════════════ 第二步：计算NAV ═══════════════
print("\n" + "=" * 70)
print("第二步：计算 NAV")
print("=" * 70)

nav_df = pd.read_csv("output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv")
df_idx = pd.read_csv("data/index_daily/932368.CSI.csv")
df_idx['trade_date'] = df_idx['trade_date'].astype(str)
df_idx = df_idx.sort_values('trade_date')
df_hs = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str)
df_hs = df_hs.sort_values('trade_date')

def idx_ret(df, s, e):
    sk, ek = s.replace('-',''), e.replace('-','')
    p0 = float(df[df['trade_date']<=sk]['close'].iloc[-1])
    p1 = float(df[df['trade_date']<=ek]['close'].iloc[-1])
    return (p1/p0-1)*100

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
        pr = w_ret/w_tot
        nav *= (1+pr)
        rows.append({'rb_date':rb, 'next_rb':nrb, 'period_ret':pr*100, 'nav':nav})
    return pd.DataFrame(rows)

nav_results = {}
for ver_name, _, _, _, out_dir in VERSIONS:
    baskets = all_baskets[ver_name]
    print(f"  计算 {ver_name}版 NAV...")
    nav_df_ver = calc_nav(baskets)
    out_path = PROJECT_ROOT / out_dir
    nav_df_ver.to_csv(out_path / "backtest_nav_tr.csv", index=False)
    nav_results[ver_name] = nav_df_ver
    print(f"    {ver_name}版: {len(nav_df_ver)}期, 期末NAV={nav_df_ver['nav'].iloc[-1]:.3f}x")

# ═══════════════ 第三步：合并对比 + 生成报告 ═══════════════
print("\n" + "=" * 70)
print("第三步：生成对比报告")
print("=" * 70)

# 合并
b_nav = nav_results['B']
m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b_ret','b_nav']
for ver_name in ['D','E','F']:
    df = nav_results[ver_name]
    m = m.merge(df[['rb_date','period_ret','nav']].rename(
        columns={'period_ret':ver_name.lower()+'_ret','nav':ver_name.lower()+'_nav'}), on='rb_date')

m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs,  r['rb_date'], r['next_rb']), axis=1)

i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_idx_nav'] = h_navs

# 超额
for v in ['b','d','e','f']:
    m[v+'_exc'] = m[v+'_ret'] - m['idx_ret']

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

ver_stats = {}
ver_to = {}
for ver_name, _, _, _, _ in VERSIONS:
    rc = ver_name.lower()+'_ret'; nc = ver_name.lower()+'_nav'
    ver_stats[ver_name] = stats(rc, nc)
    ver_to[ver_name] = turnover(all_baskets[ver_name])

idx_s = stats('idx_ret','i_nav')
hs_s = stats('hs_ret','h_idx_nav')

# 年度收益
ar = {}
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    ar[yr] = {
        'B': (1+rows['b_ret']/100).prod()-1,
        'D': (1+rows['d_ret']/100).prod()-1,
        'E': (1+rows['e_ret']/100).prod()-1,
        'F': (1+rows['f_ret']/100).prod()-1,
        '932368': (1+rows['idx_ret']/100).prod()-1,
        '沪深300': (1+rows['hs_ret']/100).prod()-1,
    }

now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)
bmap = {'B':'B版','D':'D版','E':'E版','F':'F版','932368':'932368','沪深300':'沪深300'}
buf_map = {'B':'±0%','D':'±20%','E':'±40%','F':'±50%'}

best_ver = max(ver_stats, key=lambda k: ver_stats[k]['ann'])

# ──── 生成报告 ────
lines = []
lines.append("# ZZ800 FCF 策略全版本回测报告（B/D/E/F 四版对比）")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + str(m['rb_date'].iloc[0]) + " → " + str(m['next_rb'].iloc[-1]) + "（共 " + str(N) + " 期）")
lines.append("> 全收益模式（含分红再投资，复权价计算）")
lines.append("")
lines.append("---")
lines.append("")

# 一、策略版本
lines.append("## 一、策略版本说明")
lines.append("")
lines.append("| 版本 | 缓冲区 | 必选区 | 候选池 | 换手率 | 核心差异 |")
lines.append("|------|--------|--------|--------|--------|----------|")
for vn, _, _, _, _ in VERSIONS:
    s = ver_stats[vn]; to = ver_to[vn]
    lines.append("| **" + vn + "版** | " + buf_map[vn] + " | Top" + str(int(TOP_N*(1-float(buf_map[vn].replace('±','').replace('%',''))/100))) + " | Top" + str(int(TOP_N*(1+float(buf_map[vn].replace('±','').replace('%',''))/100))) + " | " + str(round(to,1)) + "% | 前" + str(int(TOP_N*(1-float(buf_map[vn].replace('±','').replace('%',''))/100))) + "必选，粘性缓冲 |")
lines.append("| 932368 | — | — | — | — | 官方中证800现金流TR基准 |")
lines.append("| 沪深300 | — | — | — | — | 大盘基准 |")
lines.append("")
lines.append("> **加权方式（四版一致）**：FCF绝对值加权 + 单股10%封顶迭代重分配")
lines.append("")

# 二、核心指标
lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | F版 | 932368 | 沪深300 |")
lines.append("|------|-----|-----|-----|-----|--------|---------|")
lines.append("| **年化收益** | " + str(round(ver_stats['B']['ann'],2)) + "% | " + str(round(ver_stats['D']['ann'],2)) + "% | " + str(round(ver_stats['E']['ann'],2)) + "% | **" + str(round(ver_stats['F']['ann'],2)) + "%** | " + str(round(idx_s['ann'],2)) + "% | " + str(round(hs_s['ann'],2)) + "% |")
lines.append("| **最大回撤** | " + str(round(ver_stats['B']['mdd'],2)) + "% | " + str(round(ver_stats['D']['mdd'],2)) + "% | " + str(round(ver_stats['E']['mdd'],2)) + "% | **" + str(round(ver_stats['F']['mdd'],2)) + "%** | " + str(round(idx_s['mdd'],2)) + "% | " + str(round(hs_s['mdd'],2)) + "% |")
lines.append("| 年化波动率 | " + str(round(ver_stats['B']['vol'],2)) + "% | " + str(round(ver_stats['D']['vol'],2)) + "% | " + str(round(ver_stats['E']['vol'],2)) + "% | " + str(round(ver_stats['F']['vol'],2)) + "% | " + str(round(idx_s['vol'],2)) + "% | " + str(round(hs_s['vol'],2)) + "% |")
lines.append("| 夏普比率 | " + str(round(ver_stats['B']['sharpe'],3)) + " | " + str(round(ver_stats['D']['sharpe'],3)) + " | " + str(round(ver_stats['E']['sharpe'],3)) + " | **" + str(round(ver_stats['F']['sharpe'],3)) + "** | " + str(round(idx_s['sharpe'],3)) + " | " + str(round(hs_s['sharpe'],3)) + " |")
lines.append("| Calmar比率 | " + str(round(ver_stats['B']['calmar'],3)) + " | " + str(round(ver_stats['D']['calmar'],3)) + " | " + str(round(ver_stats['E']['calmar'],3)) + " | **" + str(round(ver_stats['F']['calmar'],3)) + "** | " + str(round(idx_s['calmar'],3)) + " | " + str(round(hs_s['calmar'],3)) + " |")
lines.append("| 单期胜率 | " + str(round(ver_stats['B']['win'],1)) + "% | " + str(round(ver_stats['D']['win'],1)) + "% | " + str(round(ver_stats['E']['win'],1)) + "% | " + str(round(ver_stats['F']['win'],1)) + "% | " + str(round(idx_s['win'],1)) + "% | " + str(round(hs_s['win'],1)) + "% |")
lines.append("| 平均换手率 | " + str(round(ver_to['B'],1)) + "% | " + str(round(ver_to['D'],1)) + "% | " + str(round(ver_to['E'],1)) + "% | **" + str(round(ver_to['F'],1)) + "%** | — | — |")
lines.append("| 期末净值 | " + str(round(ver_stats['B']['nav'],3)) + "x | " + str(round(ver_stats['D']['nav'],3)) + "x | " + str(round(ver_stats['E']['nav'],3)) + "x | **" + str(round(ver_stats['F']['nav'],3)) + "x** | " + str(round(idx_s['nav'],3)) + "x | " + str(round(hs_s['nav'],3)) + "x |")

# 三、逐年
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、逐年收益对比")
lines.append("")
lines.append("| 年份 | B版 | D版 | E版 | F版 | 932368 | 沪深300 | 🏆最佳 |")
lines.append("|------|-----|-----|-----|-----|--------|---------|--------|")
for yr, rets in sorted(ar.items()):
    best = max(rets, key=rets.get)
    def fmt(v):
        s = ("+%s" if v>=0 else "%s") % str(round(v*100,2)) + "%"
        return "**"+s+"**" if rets[best]==v else s
    lines.append("| "+yr+" | "+fmt(rets['B'])+" | "+fmt(rets['D'])+" | "+fmt(rets['E'])+" | "+fmt(rets['F'])+" | "+fmt(rets['932368'])+" | "+fmt(rets['沪深300'])+" | "+bmap[best]+" |")

all_c = {'B':(1+m['b_ret']/100).prod()-1,'D':(1+m['d_ret']/100).prod()-1,'E':(1+m['e_ret']/100).prod()-1,'F':(1+m['f_ret']/100).prod()-1,'932368':(1+m['idx_ret']/100).prod()-1,'沪深300':(1+m['hs_ret']/100).prod()-1}
best_all = max(all_c, key=all_c.get)
def fmtc(v):
    s = ("+%s" if v>=0 else "%s") % str(round(v*100,1))
    return s + "%"
lines.append("| **全期** | **"+fmtc(all_c['B'])+"** | **"+fmtc(all_c['D'])+"** | **"+fmtc(all_c['E'])+"** | **"+fmtc(all_c['F'])+"** | **"+fmtc(all_c['932368'])+"** | **"+fmtc(all_c['沪深300'])+"** | "+bmap[best_all]+" |")

# 四、逐期明细
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、逐期收益明细")
lines.append("")
lines.append("| 调仓日 | B版 | D版 | E版 | F版 | 932368 | F-B | F-E |")
lines.append("|--------|-----|-----|-----|-----|--------|-----|-----|")
for _, r in m.iterrows():
    fb=r['f_ret']-r['b_ret']; fe=r['f_ret']-r['e_ret']
    sgn = lambda v: ("+" if v>=0 else "")+str(round(v,2))+"%"
    lines.append("| "+r['rb_date']+" | "+sgn(r['b_ret'])+" | "+sgn(r['d_ret'])+" | "+sgn(r['e_ret'])+" | "+sgn(r['f_ret'])+" | "+sgn(r['idx_ret'])+" | "+sgn(fb)+" | "+sgn(fe)+" |")

# 五、超额
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 五、超额收益分析（vs 932368）")
lines.append("")
lines.append("| 指标 | B版 | D版 | E版 | F版 |")
lines.append("|------|-----|-----|-----|-----|")
for vn in ['B','D','E','F']:
    s = ver_stats[vn]
    lines[-1]  # header already
lines.append("| 年化超额 | "+str(round(ver_stats['B']['ann']-idx_s['ann'],2))+"% | "+str(round(ver_stats['D']['ann']-idx_s['ann'],2))+"% | "+str(round(ver_stats['E']['ann']-idx_s['ann'],2))+"% | "+str(round(ver_stats['F']['ann']-idx_s['ann'],2))+"% |")
lines.append("| 超额胜率 | "+str(round((m['b_exc']>0).mean()*100,1))+"% | "+str(round((m['d_exc']>0).mean()*100,1))+"% | "+str(round((m['e_exc']>0).mean()*100,1))+"% | "+str(round((m['f_exc']>0).mean()*100,1))+"% |")
lines.append("| 单期超额均值 | "+str(round(m['b_exc'].mean(),2))+"% | "+str(round(m['d_exc'].mean(),2))+"% | "+str(round(m['e_exc'].mean(),2))+"% | "+str(round(m['f_exc'].mean(),2))+"% |")
lines.append("| 最大单期超额 | "+str(round(m['b_exc'].max(),2))+"% | "+str(round(m['d_exc'].max(),2))+"% | "+str(round(m['e_exc'].max(),2))+"% | "+str(round(m['f_exc'].max(),2))+"% |")
lines.append("| 最小单期超额 | "+str(round(m['b_exc'].min(),2))+"% | "+str(round(m['d_exc'].min(),2))+"% | "+str(round(m['e_exc'].min(),2))+"% | "+str(round(m['f_exc'].min(),2))+"% |")

# 六、缓冲区效果
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 六、缓冲区效果分析")
lines.append("")
lines.append("### 6.1 换手率 vs 年化收益")
lines.append("")
lines.append("| 版本 | 缓冲区 | 换手率 | 年化收益 | 夏普 | Calmar |")
lines.append("|------|--------|--------|----------|------|--------|")
for vn in ['B','D','E','F']:
    s = ver_stats[vn]; to = ver_to[vn]
    lines.append("| "+vn+"版 | "+buf_map[vn]+" | "+str(round(to,1))+"% | "+str(round(s['ann'],2))+"% | "+str(round(s['sharpe'],3))+" | "+str(round(s['calmar'],3))+" |")

lines.append("")
lines.append("### 6.2 F版 vs E版 差异最大季度")
lines.append("")
m['abs_fe'] = (m['f_ret']-m['e_ret']).abs()
top_diff = m.nlargest(10, 'abs_fe')
lines.append("| 调仓日 | E版 | F版 | F-E | 说明 |")
lines.append("|--------|-----|-----|-----|------|")
for _, r in top_diff.iterrows():
    fe = r['f_ret']-r['e_ret']
    note = "F版粘性更强" if fe>0 else "F版追涨滞后"
    lines.append("| "+r['rb_date']+" | "+str(round(r['e_ret'],2))+"% | "+str(round(r['f_ret'],2))+"% | "+("+" if fe>=0 else "")+str(round(fe,2))+"% | "+note+" |")

# 七、综合结论
f_better = "进一步提升" if ver_stats['F']['ann']>ver_stats['E']['ann'] else "趋于平缓"
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 七、综合结论")
lines.append("")
lines.append("### 关键发现")
lines.append("")
lines.append("1. **缓冲区规律**：B("+str(round(ver_stats['B']['ann'],2))+"%) → D("+str(round(ver_stats['D']['ann'],2))+"%) → E("+str(round(ver_stats['E']['ann'],2))+"%) → F("+str(round(ver_stats['F']['ann'],2))+"%)，F版年化收益"+f_better)
lines.append("2. **换手率梯度**：B("+str(round(ver_to['B'],1))+"%) → D("+str(round(ver_to['D'],1))+"%) → E("+str(round(ver_to['E'],1))+"%) → F("+str(round(ver_to['F'],1))+"%)")
lines.append("3. **最大回撤改善**：B("+str(round(ver_stats['B']['mdd'],2))+"%) → D("+str(round(ver_stats['D']['mdd'],2))+"%) → E("+str(round(ver_stats['E']['mdd'],2))+"%) → F("+str(round(ver_stats['F']['mdd'],2))+"%)")
lines.append("4. **vs 932368**：F版年化超额 "+str(round(ver_stats['F']['ann']-idx_s['ann'],2))+"%，超额胜率 "+str(round((m['f_exc']>0).mean()*100,1))+"%")
lines.append("5. **F vs E 边际效益**：年化差 "+("+" if ver_stats['F']['ann']>ver_stats['E']['ann'] else "")+str(round(ver_stats['F']['ann']-ver_stats['E']['ann'],2))+"pp，夏普差 "+("+" if ver_stats['F']['sharpe']>ver_stats['E']['sharpe'] else "")+str(round(ver_stats['F']['sharpe']-ver_stats['E']['sharpe'],3)))
lines.append("")
lines.append("### 建议")
lines.append("")
lines.append("> **推荐 "+best_ver+"版**（年化"+str(round(ver_stats[best_ver]['ann'],2))+"%，回撤"+str(round(ver_stats[best_ver]['mdd'],2))+"%，换手率"+str(round(ver_to[best_ver],1))+"%）")
lines.append("")
lines.append("---")
lines.append("")
lines.append("*报告自动生成，计算日期："+now+"*")

report = "\n".join(lines)
with open("docs/zz800_bdef_strategy_comparison.md", "w") as f:
    f.write(report)

# ═══════════════ 输出摘要 ═══════════════
print("\n" + "=" * 70)
print("四版回测完成！摘要：")
print("=" * 70)
print("")
print("版本   年化收益    最大回撤    夏普    换手率    期末NAV")
print("-" * 62)
for vn in ['B','D','E','F']:
    s = ver_stats[vn]; to = ver_to[vn]
    print(vn+"版   "+str(round(s['ann'],2)).rjust(7)+"%  "+str(round(s['mdd'],2)).rjust(8)+"%  "+str(round(s['sharpe'],3)).rjust(6)+"  "+str(round(to,1)).rjust(5)+"%  "+str(round(s['nav'],3)).rjust(7)+"x")
print("-" * 62)
print("932368 "+str(round(idx_s['ann'],2)).rjust(7)+"%  "+str(round(idx_s['mdd'],2)).rjust(8)+"%  "+str(round(idx_s['sharpe'],3)).rjust(6)+"     —  "+str(round(idx_s['nav'],3)).rjust(7)+"x")
print("沪深300 "+str(round(hs_s['ann'],2)).rjust(7)+"%  "+str(round(hs_s['mdd'],2)).rjust(8)+"%  "+str(round(hs_s['sharpe'],3)).rjust(6)+"     —  "+str(round(hs_s['nav'],3)).rjust(7)+"x")
print("")
print("逐年收益：")
print("年份    B       D       E       F     932368")
for yr, rets in sorted(ar.items()):
    print(yr+"  "+("+" if rets['B']>=0 else "")+str(round(rets['B']*100,2)).rjust(6)+"%  "+("+" if rets['D']>=0 else "")+str(round(rets['D']*100,2)).rjust(6)+"%  "+("+" if rets['E']>=0 else "")+str(round(rets['E']*100,2)).rjust(6)+"%  "+("+" if rets['F']>=0 else "")+str(round(rets['F']*100,2)).rjust(6)+"%  "+("+" if rets['932368']>=0 else "")+str(round(rets['932368']*100,2)).rjust(6)+"%")
print("")
print("✅ 报告: docs/zz800_bdef_strategy_comparison.md")
print("✅ 篮子: output/zz800_fcf_fixed_lenient/, output/zz800_fcf_lenient_buffer/, output/zz800_fcf_lenient_buffer_e40/, output/zz800_fcf_lenient_buffer_f50/")
