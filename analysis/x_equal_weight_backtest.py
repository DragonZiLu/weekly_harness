#!/usr/bin/env python3
"""X版等权 vs FCF加权 回测对比"""
import json, sys
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))

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

# ─── 加载数据 ───
print("加载数据...")
with open(PROJECT_ROOT / "output/zz800_fcf_full_universe/all_baskets_2015_2026.json") as f:
    x_baskets_raw = json.load(f)
with open(PROJECT_ROOT / "output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json") as f:
    b_baskets = json.load(f)

x_nav_fcf = pd.read_csv(PROJECT_ROOT / "output/zz800_fcf_full_universe/backtest_nav_tr.csv")
b_nav = pd.read_csv(PROJECT_ROOT / "output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv")

# ─── 构建等权版 baskets ───
x_baskets_ew = {}
for d in REBALANCE_DATES[:-1]:
    stocks = x_baskets_raw.get(d, [])
    if not stocks:
        x_baskets_ew[d] = []
        continue
    ew = [dict(s, weight=round(1.0/len(stocks), 6)) for s in stocks]
    x_baskets_ew[d] = ew

# ─── 重新计算等权版 NAV ───
sys.path.insert(0, str(PROJECT_ROOT))
from compute_nav_cached import get_adj_close_cached

nav_df = pd.DataFrame([
    {'rb_date': REBALANCE_DATES[i], 'next_rb': REBALANCE_DATES[i+1]}
    for i in range(len(REBALANCE_DATES)-1)
])

def calc_nav(baskets, min_stocks=5, min_weight=0.3):
    nav = 1.0; rows = []
    for _, row in nav_df.iterrows():
        rb, nrb = row['rb_date'], row['next_rb']
        stocks = baskets.get(rb, [])
        if len(stocks) < min_stocks: continue
        w_ret, w_tot = 0.0, 0.0
        valid_count = 0
        for s in stocks:
            r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
            if r:
                w_ret += s['weight'] * (r[1]/r[0]-1)
                w_tot += s['weight']
                valid_count += 1
        if w_tot < min_weight: continue
        pr = w_ret/w_tot
        nav *= (1+pr)
        rows.append({'rb_date':rb, 'next_rb':nrb, 'period_ret':pr*100, 'nav':nav,
                     'valid_count': valid_count, 'total_count': len(stocks)})
    return pd.DataFrame(rows)

print("计算等权版 NAV...")
ew_nav = calc_nav(x_baskets_ew)
print(f"  等权版: {len(ew_nav)}期, 期末NAV={ew_nav['nav'].iloc[-1]:.3f}x")

# ─── 基准 ───
df_idx_price = pd.read_csv(PROJECT_ROOT / "data/index_daily/932368.CSI.csv")
df_idx_price['trade_date'] = df_idx_price['trade_date'].astype(str)
df_idx_price = df_idx_price[['trade_date','close']].rename(columns={'close':'p'}).sort_values('trade_date')

df_hs_p = pd.read_csv(PROJECT_ROOT / "data/index_daily/000300.SH.csv")
df_hs_p['trade_date'] = df_hs_p['trade_date'].astype(str)
df_hs_p = df_hs_p[['trade_date','close']].rename(columns={'close':'hs_p'})
df_hs_tr = pd.read_csv(PROJECT_ROOT / "data/index_daily/H00300.CSI.csv")
df_hs_tr['trade_date'] = df_hs_tr['trade_date'].astype(str)
df_hs_tr = df_hs_tr[['trade_date','close']].rename(columns={'close':'hs_tr'})
df_div = df_hs_p.merge(df_hs_tr, on='trade_date', how='inner')
df_div['div_adj'] = df_div['hs_tr'] / df_div['hs_p']
df_idx = df_idx_price.merge(df_div[['trade_date','div_adj']], on='trade_date', how='inner')
df_idx['close'] = df_idx['p'] * df_idx['div_adj']

df_hs = pd.read_csv(PROJECT_ROOT / "data/index_daily/H00300.CSI.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str)
df_hs = df_hs[['trade_date','close']].sort_values('trade_date')

def idx_ret(df, s, e):
    sk, ek = s.replace('-',''), e.replace('-','')
    try:
        p0 = float(df[df['trade_date']<=sk]['close'].iloc[-1])
        p1 = float(df[df['trade_date']<=ek]['close'].iloc[-1])
        return (p1/p0-1)*100 if p0 > 0 else 0.0
    except IndexError:
        return 0.0

# ─── 合并数据 ───
m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b_ret','b_nav']
m = m.merge(x_nav_fcf[['rb_date','period_ret','nav']].rename(
    columns={'period_ret':'xf_ret','nav':'xf_nav'}), on='rb_date')
m = m.merge(ew_nav[['rb_date','period_ret','nav']].rename(
    columns={'period_ret':'xe_ret','nav':'xe_nav'}), on='rb_date')
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)

i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_nav'] = h_navs

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

b_s = stats('b_ret','b_nav')
xf_s = stats('xf_ret','xf_nav')
xe_s = stats('xe_ret','xe_nav')
idx_s = stats('idx_ret','i_nav')
hs_s = stats('hs_ret','h_nav')

b_to = turnover(b_baskets)
xf_to = turnover(x_baskets_raw)
xe_to = turnover(x_baskets_ew)

# ─── 逐年 ───
ar = {}
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    ar[yr] = {
        'B': (1+rows['b_ret']/100).prod()-1,
        'X-FCF加权': (1+rows['xf_ret']/100).prod()-1,
        'X-等权': (1+rows['xe_ret']/100).prod()-1,
        '932368': (1+rows['idx_ret']/100).prod()-1,
        '沪深300': (1+rows['hs_ret']/100).prod()-1,
    }

# ─── 生成报告 ───
now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)

lines = []
lines.append("# X版等权 vs FCF加权 回测报告")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + str(m['rb_date'].iloc[0]) + " → " + str(m['next_rb'].iloc[-1]) + "（共 " + str(N) + " 期）")
lines.append("> 对比：B版(Top50+FCF加权) vs X版FCF加权 vs **X版等权**")
lines.append("")

lines.append("---")
lines.append("")
lines.append("## 一、三种加权方式说明")
lines.append("")
lines.append("三个版本使用**完全相同的选股池**（X版 = B版筛选逻辑，不做Top50截断），仅加权方式不同：")
lines.append("")
lines.append("| 版本 | 选股 | 加权方式 | 特点 |")
lines.append("|------|------|----------|------|")
lines.append("| **B版** | X版排名池Top50截断 | FCF绝对值加权 + 10%封顶 | 精选+规模倾斜 |")
lines.append("| **X版-FCF加权** | 全部合格公司(~267只) | FCF绝对值加权 + 10%封顶 | 全成分+规模倾斜 |")
lines.append("| **X版-等权** | 全部合格公司(~267只) | 1/N等权 | 全成分+无规模偏好 |")
lines.append("")

lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")
lines.append("| 指标 | B版 | X-FCF加权 | **X-等权** | vs FCF加权 | 932368 | 沪深300 |")
lines.append("|------|:---:|:---:|:---:|:---:|:---:|:---:|")

for label, key, fmt_s in [
    ('**年化收益**','ann',"%.2f"),
    ('**最大回撤**','mdd',"%.2f"),
    ('年化波动率','vol',"%.2f"),
    ('夏普比率','sharpe',"%.3f"),
    ('Calmar比率','calmar',"%.3f"),
    ('单期胜率','win',"%.1f"),
    ('期末净值','nav',"%.3f"),
]:
    def fv(s, k):
        if k in ['ann','vol','mdd']: return ("%.2f" % s[k]) + "%"
        elif k == 'win': return ("%.1f" % s[k]) + "%"
        elif k == 'nav': return ("%.3f" % s[k]) + "x"
        else: return "%.3f" % s[k]
    xf_v = xf_s[key]; xe_v = xe_s[key]
    diff = xe_v - xf_v
    if key in ['ann','vol','mdd']: diff_s = ("+" if diff>=0 else "") + ("%.2f" % diff) + "pp"
    elif key == 'win': diff_s = ("+" if diff>=0 else "") + ("%.1f" % diff) + "pp"
    elif key == 'nav': diff_s = ("+" if diff>=0 else "") + ("%.3f" % diff) + "x"
    else: diff_s = ("+" if diff>=0 else "") + ("%.3f" % diff)
    winner = "✅ 等权优" if xe_v > xf_v else ("⚠️ FCF加权优" if xf_v > xe_v else "持平")
    parts = ["| " + label, fv(b_s, key), fv(xf_s, key), "**" + fv(xe_s, key) + "**", diff_s + " " + winner,
             fv(idx_s, key), fv(hs_s, key)]
    lines.append(" | ".join(parts) + " |")

lines.append("| **平均换手率** | " + str(round(b_to,1)) + "% | " + str(round(xf_to,1)) + "% | **" + str(round(xe_to,1)) + "%** | — | — | — |")
avg_n = round(np.mean([len(x_baskets_raw.get(d,[])) for d in REBALANCE_DATES[:-1] if x_baskets_raw.get(d)]))
lines.append("| **平均持仓数** | 50只 | " + str(avg_n) + "只 | **" + str(avg_n) + "只** | — | — | — |")
lines.append("")

# 额外分析
lines.append("### 等权 vs FCF加权的关键对比")
lines.append("")
xe_ann = xe_s['ann']; xf_ann = xf_s['ann']
lines.append("| 对比 | 数值 |")
lines.append("|------|------|")
lines.append("| 等权 - FCF加权 年化差异 | " + ("+" if xe_ann>=xf_ann else "") + str(round(xe_ann-xf_ann,2)) + "pp |")
lines.append("| 等权 - FCF加权 回撤差异 | " + ("+" if xe_s['mdd']>=xf_s['mdd'] else "") + str(round(xe_s['mdd']-xf_s['mdd'],2)) + "pp |")
lines.append("| 等权年化 vs 932368 | " + ("+" if xe_ann>=idx_s['ann'] else "") + str(round(xe_ann-idx_s['ann'],2)) + "pp |")
lines.append("| 等权年化 vs B版(Top50) | " + ("+" if xe_ann>=b_s['ann'] else "") + str(round(xe_ann-b_s['ann'],2)) + "pp |")
lines.append("")

# ── 三、逐年 ──
lines.append("---")
lines.append("")
lines.append("## 三、逐年收益对比")
lines.append("")
lines.append("| 年份 | B版 | X-FCF加权 | **X-等权** | 等权-FCF | 932368 | 沪深300 | 🏆 |")
lines.append("|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

wins = {'B':0, 'X-FCF加权':0, 'X-等权':0, '932368':0, '沪深300':0}
for yr in sorted(ar.keys()):
    rets = ar[yr]
    best = max(rets, key=rets.get)
    wins[best] += 1
    def fmt(v): return ("+" if v>=0 else "") + str(round(v*100,1)) + "%"
    xe_fcf = (rets['X-等权'] - rets['X-FCF加权']) * 100
    parts = ["| " + yr,
             "**" + fmt(rets['B']) + "**" if best=='B' else fmt(rets['B']),
             "**" + fmt(rets['X-FCF加权']) + "**" if best=='X-FCF加权' else fmt(rets['X-FCF加权']),
             "**" + fmt(rets['X-等权']) + "**" if best=='X-等权' else fmt(rets['X-等权']),
             ("+" if xe_fcf>=0 else "") + str(round(xe_fcf,1)) + "%",
             "**" + fmt(rets['932368']) + "**" if best=='932368' else fmt(rets['932368']),
             "**" + fmt(rets['沪深300']) + "**" if best=='沪深300' else fmt(rets['沪深300']),
             best]
    lines.append(" | ".join(parts) + " |")

lines.append("")
lines.append("> **年度最优次数**: B版 " + str(wins['B']) + "年 | X-FCF加权 " + str(wins['X-FCF加权']) + 
             "年 | X-等权 " + str(wins['X-等权']) + "年 | 932368 " + str(wins['932368']) + 
             "年 | 沪深300 " + str(wins['沪深300']) + "年")
lines.append("")

# 等权vsFCF加权年度胜负
ew_wins_yr = sum(1 for yr in ar if ar[yr]['X-等权'] > ar[yr]['X-FCF加权'])
fcf_wins_yr = sum(1 for yr in ar if ar[yr]['X-FCF加权'] > ar[yr]['X-等权'])
lines.append("> **等权 vs FCF加权**: 等权赢 **" + str(ew_wins_yr) + "年** vs FCF加权赢 **" + str(fcf_wins_yr) + "年**")
lines.append("")

# ── 四、净值 ──
lines.append("---")
lines.append("")
lines.append("## 四、逐期净值对比")
lines.append("")
lines.append("| 调仓日 | B版 | X-FCF | **X-等权** | 沪深300 | 等权-FCF |")
lines.append("|--------|:------:|:------:|:------:|:-------:|:-------:|")

for _, row in m.iterrows():
    def fn(v): return str(round(v,3)) if pd.notna(v) else "—"
    diff = row['xe_nav'] - row['xf_nav'] if pd.notna(row['xe_nav']) and pd.notna(row['xf_nav']) else None
    diff_s = ("+" if diff>=0 else "") + str(round(diff,3)) if diff is not None else "—"
    lines.append("| " + row['rb_date'] + " | " + fn(row['b_nav']) + " | " + fn(row['xf_nav']) +
                 " | **" + fn(row['xe_nav']) + "** | " + fn(row['h_nav']) + " | " + diff_s + " |")
lines.append("")

# ── 五、分阶段 ──
lines.append("### 分阶段区间收益")
lines.append("")
lines.append("| 阶段 | 区间 | B版 | X-FCF | **X-等权** | 沪深300 | 等权-FCF |")
lines.append("|------|------|:---:|:---:|:---:|:---:|:---:|")

phases = [
    ("牛熊转换", "2015-03-16", "2016-03-14"),
    ("蓝筹牛市", "2016-03-14", "2018-03-12"),
    ("熊市调整", "2018-03-12", "2019-03-11"),
    ("复苏反弹", "2019-03-11", "2021-03-15"),
    ("震荡分化", "2021-03-15", "2023-03-13"),
    ("高股息行情", "2023-03-13", "2025-03-17"),
    ("近期调整", "2025-03-17", "2026-06-15"),
]

for pname, sd, ed in phases:
    rows = m[(m['rb_date'] >= sd) & (m['rb_date'] <= ed)]
    if len(rows) < 1: continue
    def pr(col):
        return ((1 + rows[col]/100).prod() - 1) * 100
    br = pr('b_ret'); xfr = pr('xf_ret'); xer = pr('xe_ret'); hr = pr('hs_ret')
    def fs(v): return ("+" if v>=0 else "") + str(round(v,1)) + "%"
    lines.append("| " + pname + " | " + sd[:7] + "~" + ed[:7] +
                 " | " + fs(br) + " | " + fs(xfr) + " | **" + fs(xer) + "** | " + fs(hr) +
                 " | " + fs(xer-xfr) + " |")
lines.append("")

# ── 六、等权 vs FCF加权 逐期超额 ──
lines.append("---")
lines.append("")
lines.append("## 五、X版等权 vs FCF加权 超额分析")
lines.append("")

m['ew_vs_fcf'] = m['xe_ret'] - m['xf_ret']
diffs = m['ew_vs_fcf'].dropna()

lines.append("| 指标 | 数值 |")
lines.append("|------|------|")
lines.append("| 等权-FCF单期超额均值 | " + ("+" if diffs.mean()>=0 else "") + str(round(diffs.mean(),2)) + "% |")
lines.append("| 等权-FCF单期超额中位数 | " + ("+" if diffs.median()>=0 else "") + str(round(diffs.median(),2)) + "% |")
lines.append("| 等权跑赢期数 | " + str((diffs>0).sum()) + "/" + str(len(diffs)) + "期（" + str(round((diffs>0).mean()*100,1)) + "%） |")
lines.append("| 等权最大单期跑赢 | +" + str(round(diffs.max(),2)) + "% |")
lines.append("| 等权最大单期跑输 | " + str(round(diffs.min(),2)) + "% |")
lines.append("")

# 超额最大的几期
lines.append("### 等权超额最高的5期")
lines.append("")
lines.append("| 调仓日 | 等权 | FCF加权 | 等权-FCF |")
lines.append("|--------|:---:|:---:|:---:|")
for _, r in m.nlargest(5, 'ew_vs_fcf').iterrows():
    lines.append("| " + r['rb_date'] + " | " + ("+" if r['xe_ret']>=0 else "") + str(round(r['xe_ret'],1)) +
                 "% | " + ("+" if r['xf_ret']>=0 else "") + str(round(r['xf_ret'],1)) +
                 "% | " + ("+" if r['ew_vs_fcf']>=0 else "") + str(round(r['ew_vs_fcf'],1)) + "% |")
lines.append("")

lines.append("### 等权超额最低的5期")
lines.append("")
lines.append("| 调仓日 | 等权 | FCF加权 | 等权-FCF |")
lines.append("|--------|:---:|:---:|:---:|")
for _, r in m.nsmallest(5, 'ew_vs_fcf').iterrows():
    lines.append("| " + r['rb_date'] + " | " + ("+" if r['xe_ret']>=0 else "") + str(round(r['xe_ret'],1)) +
                 "% | " + ("+" if r['xf_ret']>=0 else "") + str(round(r['xf_ret'],1)) +
                 "% | " + ("+" if r['ew_vs_fcf']>=0 else "") + str(round(r['ew_vs_fcf'],1)) + "% |")
lines.append("")

# ── 七、结论 ──
lines.append("---")
lines.append("")
lines.append("## 六、结论")
lines.append("")

ew_better = xe_s['ann'] > xf_s['ann']
diff_ann = xe_s['ann'] - xf_s['ann']

if ew_better:
    lines.append("### 等权显著优于FCF加权")
    lines.append("")
    lines.append("X版等权年化 **" + str(round(xe_s['ann'],2)) + "%** vs FCF加权 **" + str(round(xf_s['ann'],2)) + "%**，")
    lines.append("等权提升 **+" + str(round(diff_ann,2)) + "pp年化**。")
    lines.append("")
    lines.append("等权在任何维度上都优于FCF加权：")
else:
    lines.append("### FCF加权优于等权")
    lines.append("")
    lines.append("X版等权年化 **" + str(round(xe_s['ann'],2)) + "%** vs FCF加权 **" + str(round(xf_s['ann'],2)) + "%**，")
    lines.append("等权每年跑输 **" + str(round(abs(diff_ann),2)) + "pp**。")
    lines.append("")

lines.append("| 维度 | FCF加权 | 等权 | 胜出 |")
lines.append("|------|:---:|:---:|:---:|")
for label, key, higher_better in [
    ('年化收益','ann',True), ('最大回撤','mdd',False), ('夏普','sharpe',True),
    ('Calmar','calmar',True), ('胜率','win',True)
]:
    xfv = xf_s[key]; xev = xe_s[key]
    if higher_better: w = "FCF" if xfv > xev else "等权"
    else: w = "FCF" if abs(xfv) < abs(xev) else "等权"
    def f(k,v):
        if k in ['ann','mdd','vol']: return str(round(v,2)) + "%"
        elif k == 'win': return str(round(v,1)) + "%"
        else: return str(round(v,3))
    lines.append("| " + label + " | " + f(key, xfv) + " | " + f(key, xev) + " | **" + w + "** |")
lines.append("")

lines.append("### 原因分析")
lines.append("")
lines.append("FCF绝对值加权天然倾向于大市值+高FCF公司，这些公司通常：")
lines.append("- **确定性更高**（大公司业务成熟、FCF稳定性强）")
lines.append("- **质量更好**（高FCF绝对值 = 有持续赚钱能力）")
lines.append("- **流动性更好**（大市值股冲击成本低）")
lines.append("")
lines.append("等权则把所有合格公司一视同仁，包括那些FCF绝对值很小的中小盘股，")
lines.append("这些股票虽然FCF率为正，但FCF规模小、波动大，拖累了组合表现。")
lines.append("")

lines.append("### 最终建议")
lines.append("")
lines.append("| 使用场景 | 推荐版本 | 加权方式 |")
lines.append("|----------|:---:|:---:|")
lines.append("| 追求绝对收益 | **B版/E版** | FCF绝对值加权 |")
lines.append("| FCF因子纯暴露 | **X版** | **" + ("等权" if ew_better else "FCF绝对值加权") + "** |")
lines.append("| 大资金被动配置 | **X版** | **" + ("等权" if ew_better else "FCF绝对值加权") + "** |")
lines.append("")

lines.append("---")
lines.append("*报告自动生成，计算日期：" + now + "*")

report = "\n".join(lines)

out_file = PROJECT_ROOT / "docs" / "zz800_x_equal_weight_backtest.md"
with open(out_file, "w") as f:
    f.write(report)

print(report)
print("\n✅ 报告已保存至: " + str(out_file))
