#!/usr/bin/env python3
"""E100 版回测：Top100 ±40%缓冲区（必选60+缓冲40），vs E版对比"""
import sys, json, time
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
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

# ──── E100 配置 ────
TOP_N = 100
BUFFER_RATIO = 0.40
LOW  = int(TOP_N * (1 - BUFFER_RATIO))   # 60 必选
HIGH = int(TOP_N * (1 + BUFFER_RATIO))   # 140 缓冲池上限
CAP  = 0.10
OUT_DIR = "output/zz800_fcf_lenient_buffer_e100"

# 加载已有E版数据对比
E_OUT_DIR = "output/zz800_fcf_lenient_buffer_e40"

# ──── 权重计算 ────
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

def apply_buffer(ranked, prev_codes, low, high, top_n):
    must = ranked[:low]
    buffer_pool = ranked[low:high]
    buffer_old = [s for s in buffer_pool if s['ts_code'] in prev_codes]
    buffer_new = [s for s in buffer_pool if s['ts_code'] not in prev_codes]
    selected = must + buffer_old
    remaining = top_n - len(selected)
    if remaining > 0: selected.extend(buffer_new[:remaining])
    return selected[:top_n]

# ──── 第一步：从 X 版排名池导出 E100 ────
print("=" * 70)
print("E100 版回测：Top100 ±40% 缓冲区")
print(f"  参数: TOP_N={TOP_N}, buffer={BUFFER_RATIO*100:.0f}%, low={LOW}, high={HIGH}")
print("=" * 70)

# 加载 X 版排名
x_rankings_path = PROJECT_ROOT / "output/zz800_fcf_full_universe/rankings_2015_2026.json"
print("\n加载 X 版排名池...")
with open(x_rankings_path) as f:
    x_rankings = json.load(f)
print(f"  ✅ 加载 {len(x_rankings)} 期排名")

# 生成 E100 baskets
out_path = PROJECT_ROOT / OUT_DIR
out_path.mkdir(parents=True, exist_ok=True)

e100_baskets = {}
prev_codes = set()

print(f"\n从 X 版排名池导出 E100（零 FCF 计算）...")
for i, date_str in enumerate(REBALANCE_DATES):
    ranked = x_rankings.get(date_str, [])
    if not ranked:
        e100_baskets[date_str] = []
        continue
    
    if i == 0 or not prev_codes:
        stocks = [dict(s) for s in ranked[:TOP_N]]
    else:
        stocks = [dict(s) for s in apply_buffer(ranked, prev_codes, LOW, HIGH, TOP_N)]
    
    fcf_weights(stocks)
    e100_baskets[date_str] = stocks
    prev_codes = {s['ts_code'] for s in stocks}

with open(out_path / "all_baskets_2015_2026.json", "w") as f:
    json.dump(e100_baskets, f, ensure_ascii=False, indent=2)
valid = sum(1 for d in e100_baskets if len(e100_baskets[d]) >= 10)
print(f"  ✅ E100: {valid}/{len(e100_baskets)} 期有效 → {OUT_DIR}/")

# ──── 第二步：计算 NAV ────
print("\n" + "=" * 70)
print("第二步：计算 NAV")
print("=" * 70)

nav_df = pd.DataFrame([
    {'rb_date': REBALANCE_DATES[i], 'next_rb': REBALANCE_DATES[i+1]}
    for i in range(len(REBALANCE_DATES)-1)
])

df_idx = pd.read_csv("data/index_daily/932368.CSI.csv")
df_idx['trade_date'] = df_idx['trade_date'].astype(str); df_idx = df_idx.sort_values('trade_date')
df_hs = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str); df_hs = df_hs.sort_values('trade_date')

def idx_ret(df, s, e):
    sk, ek = s.replace('-',''), e.replace('-','')
    try:
        p0 = float(df[df['trade_date']<=sk]['close'].iloc[-1])
        p1 = float(df[df['trade_date']<=ek]['close'].iloc[-1])
        return (p1/p0-1)*100 if p0 > 0 else 0.0
    except IndexError:
        return 0.0

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

# E100 NAV
print("  计算 E100 版 NAV...")
nav_e100 = calc_nav(e100_baskets)
nav_e100.to_csv(out_path / "backtest_nav_tr.csv", index=False)
e100_final = nav_e100['nav'].iloc[-1] if len(nav_e100) > 0 else 0
print(f"    E100: {len(nav_e100)}期, 期末NAV={e100_final:.3f}x")

# 加载 E 版 NAV + baskets 用于对比
print("  加载 E 版 NAV + baskets（对比基准）...")
e_nav_path = PROJECT_ROOT / E_OUT_DIR / "backtest_nav_tr.csv"
nav_e = pd.read_csv(e_nav_path)
with open(PROJECT_ROOT / E_OUT_DIR / "all_baskets_2015_2026.json") as f:
    e_baskets = json.load(f)

# ──── 第三步：统计指标 ────
print("\n" + "=" * 70)
print("第三步：计算统计指标")
print("=" * 70)

def stats(rc, nc, data):
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

# 合并 E100 和 E 的数据
m = nav_e100[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','e100_ret','e100_nav']
m = m.merge(nav_e[['rb_date','period_ret','nav']].rename(
    columns={'period_ret':'e_ret','nav':'e_nav'}), on='rb_date')
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)
i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_idx_nav'] = h_navs

e100_s = stats('e100_ret','e100_nav', m)
e_s    = stats('e_ret','e_nav', m)
idx_s  = stats('idx_ret','i_nav', m)
hs_s   = stats('hs_ret','h_idx_nav', m)
e100_to = turnover(e100_baskets)
e_to    = turnover(e_baskets)

# 年度收益
ar = {}
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    ar[yr] = {}
    ar[yr]['E100'] = (1+rows['e100_ret']/100).prod()-1
    ar[yr]['E']    = (1+rows['e_ret']/100).prod()-1
    ar[yr]['932368'] = (1+rows['idx_ret']/100).prod()-1
    ar[yr]['沪深300'] = (1+rows['hs_ret']/100).prod()-1

# ──── 第四步：生成报告 ────
now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)

lines = []
lines.append("# ZZ800 FCF E100 版回测报告（Top100 ±40%缓冲区，vs E版）")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + str(m['rb_date'].iloc[0]) + " → " + str(m['next_rb'].iloc[-1]) + "（共 " + str(N) + " 期）")
lines.append("> 全收益模式（含分红再投资，复权价计算）")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 一、策略说明")
lines.append("")
lines.append("| 版本 | TopN | 缓冲区 | 必选 | 缓冲池 | 换手率 |")
lines.append("|------|:---:|:------:|:---:|:-----:|:------:|")
lines.append("| **E版**（基准） | 50 | ±40% | 30 | 31-70 | " + str(round(e_to,1)) + "% |")
lines.append("| **E100版**（实验） | 100 | ±40% | 60 | 61-140 | " + str(round(e100_to,1)) + "% |")
lines.append("")
lines.append("> **加权方式**：FCF 绝对值加权 + 单股 10% 封顶迭代重分配")
lines.append("> **数据来源**：X 版排名池（全成分 FCF 率排名），E100 从同一排名池导出，仅改变截断位置和缓冲区规模")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")
lines.append("| 指标 | E100版 | E版（基准） | 932368 | 沪深300 |")
lines.append("|------|:------:|:----------:|:------:|:-------:|")
for metric, key, fmt_str, unit in [
    ('**年化收益**','ann','%.2f','%'),
    ('**最大回撤**','mdd','%.2f','%'),
    ('年化波动率','vol','%.2f','%'),
    ('夏普比率','sharpe','%.3f',''),
    ('Calmar比率','calmar','%.3f',''),
    ('单期胜率','win','%.1f','%'),
    ('期末净值','nav','%.3f','x'),
]:
    row_parts = ["| " + metric]
    for s_ref in [e100_s, e_s, idx_s, hs_s]:
        if key == 'nav':
            row_parts.append(str(round(s_ref[key],3))+"x")
        else:
            row_parts.append(fmt_str % s_ref[key] + unit)
    lines.append(" | ".join(row_parts) + " |")
lines.append("")
lines.append("> **E100 vs E 年化差异**: " + ("+" if e100_s['ann']>=e_s['ann'] else "") + str(round(e100_s['ann']-e_s['ann'],2)) + "pp")
lines.append("")

# 三、逐年
lines.append("---")
lines.append("")
lines.append("## 三、逐年收益对比")
lines.append("")
lines.append("| 年份 | E100版 | E版 | 932368 | 沪深300 | E100-E | 最优 |")
lines.append("|------|:------:|:---:|:------:|:-------:|:------:|:----:|")
for yr, rets in sorted(ar.items()):
    best = max(rets, key=rets.get)
    diff = rets['E100'] - rets['E']
    def fmt(v):
        s = ("+%s" if v>=0 else "%s") % str(round(v*100,2)) + "%"
        return "**"+s+"**" if rets[best]==v and best != 'E100' else s
    e100_f = ("+%s" if rets['E100']>=0 else "%s") % str(round(rets['E100']*100,2)) + "%"
    if best == 'E100': e100_f = "**" + e100_f + "**"
    diff_f = ("+%s" if diff>=0 else "%s") % str(round(diff*100,2)) + "pp"
    lines.append("| "+yr+" | "+e100_f+" | "+fmt(rets['E'])+" | "+fmt(rets['932368'])+" | "+fmt(rets['沪深300'])+" | "+diff_f+" | "+best+" |")
lines.append("")

# E100 年胜率
e100_wins = sum(1 for yr in ar if ar[yr]['E100'] > ar[yr]['E'])
e_wins = len(ar) - e100_wins
lines.append("> **年度 E100 vs E**: E100 赢 **" + str(e100_wins) + "年**，E 赢 **" + str(e_wins) + "年**")
lines.append("")

# 四、逐期
lines.append("---")
lines.append("")
lines.append("## 四、逐期收益明细")
lines.append("")
lines.append("| 调仓日 | E100版 | E版 | 932368 | E100-E | E100-932368 |")
lines.append("|--------|:------:|:---:|:------:|:------:|:-----------:|")
for _, r in m.iterrows():
    e100_e = r['e100_ret'] - r['e_ret']
    e100_i = r['e100_ret'] - r['idx_ret']
    sgn = lambda v: ("+" if v>=0 else "")+str(round(v,2))+"%"
    lines.append("| "+r['rb_date']+" | "+sgn(r['e100_ret'])+" | "+sgn(r['e_ret'])+" | "+sgn(r['idx_ret'])+" | "+sgn(e100_e)+" | "+sgn(e100_i)+" |")
lines.append("")

# 五、综合结论
lines.append("---")
lines.append("")
lines.append("## 五、综合结论")
lines.append("")
e100_exc_e = e100_s['ann'] - e_s['ann']
if e100_exc_e > 0.5:
    verdict = "✅ **建议采纳** — E100 在年化收益上有显著提升，分散化大幅发挥威力"
elif e100_exc_e > -0.5:
    verdict = "➡️ **基本持平** — E100 与 E 版差异不大，可根据容量需求选用"
else:
    verdict = "❌ **建议存档** — E100 年化低于 E 版，分散化稀释了 alpha"

lines.append("1. **年化收益**: E100 版 " + str(round(e100_s['ann'],2)) + "% vs E 版 " + str(round(e_s['ann'],2)) + "%，差异 " + ("+" if e100_exc_e>=0 else "") + str(round(e100_exc_e,2)) + "pp")
lines.append("2. **最大回撤**: E100 版 " + str(round(e100_s['mdd'],2)) + "% vs E 版 " + str(round(e_s['mdd'],2)) + "%")
lines.append("3. **换手率**: E100 版 " + str(round(e100_to,1)) + "% vs E 版 " + str(round(e_to,1)) + "%")
lines.append("4. **持仓规模**: E100 版平均 100 只 vs E 版 50 只，容量翻倍")
lines.append("5. **结论**: " + verdict)
lines.append("")
lines.append("---")
lines.append("*报告自动生成，计算日期：" + now + "*")

report = "\n".join(lines)
report_path = "docs/zz800_e100_vs_e_comparison.md"
with open(report_path, "w") as f:
    f.write(report)

# ──── 终端输出 ────
print("\n" + "=" * 80)
print("E100 版回测完成！")
print("=" * 80)
print("")
print("版本     年化收益    最大回撤    夏普    换手率    期末NAV    持仓数")
print("-" * 75)
print("E100    " + str(round(e100_s['ann'],2)).rjust(7)+"%  "+str(round(e100_s['mdd'],2)).rjust(8)+"%  "+
      str(round(e100_s['sharpe'],3)).rjust(6)+"  "+str(round(e100_to,1)).rjust(5)+"%  "+
      str(round(e100_s['nav'],3)).rjust(7)+"x  100")
print("E版     " + str(round(e_s['ann'],2)).rjust(7)+"%  "+str(round(e_s['mdd'],2)).rjust(8)+"%  "+
      str(round(e_s['sharpe'],3)).rjust(6)+"  "+str(round(e_to,1)).rjust(5)+"%  "+
      str(round(e_s['nav'],3)).rjust(7)+"x   50")
print("932368  " + str(round(idx_s['ann'],2)).rjust(7)+"%  "+str(round(idx_s['mdd'],2)).rjust(8)+"%  "+
      str(round(idx_s['sharpe'],3)).rjust(6)+"     —  "+str(round(idx_s['nav'],3)).rjust(7)+"x")
print("沪深300  " + str(round(hs_s['ann'],2)).rjust(7)+"%  "+str(round(hs_s['mdd'],2)).rjust(8)+"%  "+
      str(round(hs_s['sharpe'],3)).rjust(6)+"     —  "+str(round(hs_s['nav'],3)).rjust(7)+"x")
print("")
print("逐年收益：")
print("年份      E100        E      932368    沪深300")
for yr, rets in sorted(ar.items()):
    row = yr+"  "
    for v in ['E100','E','932368','沪深300']:
        r = rets[v]*100
        row += ("+" if r>=0 else "")+str(round(r,2)).rjust(8)+"%  "
    print(row)
print("")
print("✅ 报告: " + report_path)
print("✅ 持仓: " + OUT_DIR + "/")
