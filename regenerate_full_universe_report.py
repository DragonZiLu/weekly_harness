#!/usr/bin/env python3
"""regenerate_full_universe_report.py — 全成分统计+NAV对比（修正版）
修正(OCF-营业利润)加和的计算：使用正确的ref_period获取TTM财务数据
"""
import sys, json, time
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

def date_to_ref_period(date_str):
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    m = dt.month
    if 1 <= m <= 3:   return f"{dt.year-1}0930"
    elif 4 <= m <= 6: return f"{dt.year}0331"
    elif 7 <= m <= 9: return f"{dt.year}0630"
    else:             return f"{dt.year}0930"

def calc_ocf_op(fc, ts_codes, ref_period):
    diff_sum, count = 0, 0
    for tc in ts_codes:
        try:
            fin = fc.get_ttm_financials(tc, ref_period)
            if fin and fin.get('oper_cf') is not None and fin.get('oper_profit') is not None:
                diff_sum += (fin['oper_cf'] - fin['oper_profit']) / 1e8
                count += 1
        except: pass
    return diff_sum, count

# 加载已有篮子
print("加载篮子...")
zz_full = json.load(open("output/zz800_fcf_full_universe/all_baskets_2015_2026.json"))
hs_full = json.load(open("output/hs300_fcf_full_universe/all_baskets_2015_2026.json"))
b_baskets = json.load(open("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json"))
nav_df = pd.read_csv("output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv")

df_idx = pd.read_csv("data/index_daily/932368.CSI.csv")
df_idx['trade_date'] = df_idx['trade_date'].astype(str); df_idx = df_idx.sort_values('trade_date')
df_hs = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str); df_hs = df_hs.sort_values('trade_date')

def idx_ret(df, s, e):
    s_k, e_k = s.replace('-',''), e.replace('-','')
    p0 = float(df[df['trade_date'] <= s_k]['close'].iloc[-1])
    p1 = float(df[df['trade_date'] <= e_k]['close'].iloc[-1])
    return (p1/p0 - 1)*100

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
        pr = w_ret / w_tot
        nav *= (1 + pr)
        rows.append({'rb_date': rb, 'next_rb': nrb, 'period_ret': pr*100, 'nav': nav})
    return pd.DataFrame(rows)

print("计算NAV...")
b_nav = calc_nav(b_baskets)
zz_nav = calc_nav(zz_full)
hs_nav = calc_nav(hs_full)

# 合并NAV
m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b50_ret','b50_nav']
m = m.merge(zz_nav[['rb_date','period_ret','nav']].rename(columns={'period_ret':'zz_ret','nav':'zz_nav'}), on='rb_date', how='left')
m = m.merge(hs_nav[['rb_date','period_ret','nav']].rename(columns={'period_ret':'hs_ret','nav':'hs_nav'}), on='rb_date', how='left')
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_idx_ret'] = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)
i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_idx_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_idx_nav'] = h_navs

# 初始化财务缓存
print("初始化财务缓存...")
uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)
uni.preload_all(download=False)
fc = uni._fin_cache
uni_hs = FcfUniverse(index_code="000300.SH", strict_ocf=False)
uni_hs.preload_all(download=False)
fc_hs = uni_hs._fin_cache

# 计算每期统计
print("\n计算每期统计...")
t0 = time.time()
all_stats = []
for i, date_str in enumerate(REBALANCE_DATES):
    ref_period = date_to_ref_period(date_str)
    zz_stocks = zz_full.get(date_str, [])
    hs_stocks = hs_full.get(date_str, [])
    b_stocks  = b_baskets.get(date_str, [])
    
    zz_fcf = sum(s.get('fcf',0) for s in zz_stocks) / 1e8
    hs_fcf = sum(s.get('fcf',0) for s in hs_stocks) / 1e8
    b_fcf  = sum(s.get('fcf',0) for s in b_stocks) / 1e8
    
    zz_diff, zz_pq = calc_ocf_op(fc, [s['ts_code'] for s in zz_stocks], ref_period)
    hs_diff, hs_pq = calc_ocf_op(fc_hs, [s['ts_code'] for s in hs_stocks], ref_period)
    b_diff, b_pq   = calc_ocf_op(fc, [s['ts_code'] for s in b_stocks], ref_period)
    
    stat = dict(date=date_str, ref_period=ref_period,
                zz_count=len(zz_stocks), zz_fcf=zz_fcf, zz_ocf_op=zz_diff,
                hs_count=len(hs_stocks), hs_fcf=hs_fcf, hs_ocf_op=hs_diff,
                b_count=len(b_stocks), b_fcf=b_fcf, b_ocf_op=b_diff)
    all_stats.append(stat)
    elapsed = time.time() - t0
    print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str} ref={ref_period}: "
          f"ZZ={len(zz_stocks)}只 FCF={zz_fcf:.0f}亿 (OCF-OP)={zz_diff:+.0f}亿  "
          f"HS={len(hs_stocks)}只 FCF={hs_fcf:.0f}亿 (OCF-OP)={hs_diff:+.0f}亿  "
          f"B50={len(b_stocks)}只 FCF={b_fcf:.0f}亿 (OCF-OP)={b_diff:+.0f}亿  ({elapsed:.0f}s)")

stat_map = {s['date']: s for s in all_stats}
for col in ['zz_count','zz_fcf','zz_ocf_op','hs_count','hs_fcf','hs_ocf_op','b_count','b_fcf','b_ocf_op']:
    m[col] = m['rb_date'].map(lambda d: stat_map.get(d, {}).get(col, 0))

# 统计函数
def stats(rc, nc, data=m):
    rets = data[rc].dropna(); navs = data[nc].dropna()
    n = len(rets)
    ann = (navs.iloc[-1]**(4/n)-1)*100
    vol = rets.std()*2
    peak = navs.cummax()
    mdd = ((peak-navs)/peak).max()*100
    sharpe = (ann-2.0)/vol if vol>0 else 0
    calmar = ann/mdd if mdd>0 else 0
    win = (rets>0).mean()*100
    return dict(ann=ann, vol=vol, mdd=-mdd, sharpe=sharpe, calmar=calmar, win=win, nav=navs.iloc[-1])

bs  = stats('b50_ret','b50_nav')
zs  = stats('zz_ret','zz_nav')
hss = stats('hs_ret','hs_nav')
is_ = stats('idx_ret','i_nav')
hsi = stats('hs_idx_ret','h_idx_nav')

now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)

# 生成报告
lines = []
lines.append("# ZZ800 & HS300 全成分FCF统计报告（不做Top50筛选）")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + str(m['rb_date'].iloc[0]) + " → " + str(m['next_rb'].iloc[-1]) + "（共 " + str(N) + " 期）")
lines.append("> 全收益模式（含分红再投资，复权价计算）")
lines.append("> 筛选条件：5年期OCF>0 + 盈利质量PQ前80% + 行业排除（金融地产），但**不做Top50截断**")
lines.append("> 加权方式：FCF绝对值加权 + 单股10%封顶迭代重分配")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 一、核心指标对比")
lines.append("")
lines.append("| 指标 | B版Top50 | ZZ800全成分 | HS300全成分 | 932368 | 沪深300指数 |")
lines.append("|------|----------|-------------|-------------|--------|-------------|")
lines.append("| **年化收益** | " + str(round(bs['ann'],2)) + "% | " + str(round(zs['ann'],2)) + "% | " + str(round(hss['ann'],2)) + "% | " + str(round(is_['ann'],2)) + "% | " + str(round(hsi['ann'],2)) + "% |")
lines.append("| **最大回撤** | " + str(round(bs['mdd'],2)) + "% | " + str(round(zs['mdd'],2)) + "% | " + str(round(hss['mdd'],2)) + "% | " + str(round(is_['mdd'],2)) + "% | " + str(round(hsi['mdd'],2)) + "% |")
lines.append("| 夏普比率 | " + str(round(bs['sharpe'],3)) + " | " + str(round(zs['sharpe'],3)) + " | " + str(round(hss['sharpe'],3)) + " | " + str(round(is_['sharpe'],3)) + " | " + str(round(hsi['sharpe'],3)) + " |")
lines.append("| Calmar | " + str(round(bs['calmar'],3)) + " | " + str(round(zs['calmar'],3)) + " | " + str(round(hss['calmar'],3)) + " | " + str(round(is_['calmar'],3)) + " | " + str(round(hsi['calmar'],3)) + " |")
lines.append("| 期末净值 | " + str(round(bs['nav'],3)) + "x | " + str(round(zs['nav'],3)) + "x | " + str(round(hss['nav'],3)) + "x | " + str(round(is_['nav'],3)) + "x | " + str(round(hsi['nav'],3)) + "x |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、每期调仓统计明细")
lines.append("")
lines.append("### 2.1 中证800 全成分 vs B版Top50")
lines.append("")
lines.append("| 调仓日 | 报告期 | 合格数 | FCF(亿) | (OCF-OP)(亿) | B50数 | B50 FCF(亿) | B50(OCF-OP)(亿) | ZZ全成分NAV | B50 NAV | ZZ收益 | B50收益 | 差异 |")
lines.append("|--------|--------|--------|---------|--------------|-------|-------------|-----------------|------------|---------|--------|---------|------|")
for _, r in m.iterrows():
    diff = r.get('zz_ret',0) - r.get('b50_ret',0)
    sign_d = ("+" if diff>=0 else "") + str(round(diff,2)) + "%"
    zz_nav_s = str(round(r['zz_nav'],3)) if pd.notna(r.get('zz_nav')) else "—"
    zz_ret_s = ("+" if r.get('zz_ret',0)>=0 else "") + str(round(r.get('zz_ret',2),2)) + "%" if pd.notna(r.get('zz_ret')) else "—"
    b50_ret_s = ("+" if r.get('b50_ret',0)>=0 else "") + str(round(r.get('b50_ret',2),2)) + "%"
    rp = stat_map.get(r['rb_date'], {}).get('ref_period', '')
    zz_ocf = r.get('zz_ocf_op',0); b_ocf = r.get('b_ocf_op',0)
    lines.append("| " + str(r['rb_date']) + " | " + str(rp) +
                 " | " + str(int(r.get('zz_count',0))) +
                 " | " + str(round(r.get('zz_fcf',0),0)) +
                 " | " + ("+" if zz_ocf>=0 else "") + str(round(zz_ocf,0)) +
                 " | " + str(int(r.get('b_count',0))) +
                 " | " + str(round(r.get('b_fcf',0),0)) +
                 " | " + ("+" if b_ocf>=0 else "") + str(round(b_ocf,0)) +
                 " | " + zz_nav_s + " | " + str(round(r['b50_nav'],3)) +
                 " | " + zz_ret_s + " | " + b50_ret_s + " | " + sign_d + " |")

lines.append("")
lines.append("### 2.2 沪深300 全成分 vs 沪深300指数")
lines.append("")
lines.append("| 调仓日 | 合格数 | FCF(亿) | (OCF-OP)(亿) | HS全成分NAV | 沪深300NAV | HS收益 | 指数收益 | 超额 |")
lines.append("|--------|--------|---------|--------------|-------------|------------|--------|---------|------|")
for _, r in m.iterrows():
    exc = r.get('hs_ret',0) - r.get('hs_idx_ret',0)
    exc_s = ("+" if exc>=0 else "") + str(round(exc,2)) + "%"
    hs_nav_s = str(round(r['hs_nav'],3)) if pd.notna(r.get('hs_nav')) else "—"
    hs_ret_s = ("+" if r.get('hs_ret',0)>=0 else "") + str(round(r.get('hs_ret',2),2)) + "%" if pd.notna(r.get('hs_ret')) else "—"
    hs_idx_ret_s = ("+" if r.get('hs_idx_ret',0)>=0 else "") + str(round(r.get('hs_idx_ret',2),2)) + "%"
    hs_ocf = r.get('hs_ocf_op',0)
    lines.append("| " + str(r['rb_date']) + " | " + str(int(r.get('hs_count',0))) +
                 " | " + str(round(r.get('hs_fcf',0),0)) +
                 " | " + ("+" if hs_ocf>=0 else "") + str(round(hs_ocf,0)) +
                 " | " + hs_nav_s + " | " + str(round(r['h_idx_nav'],3)) +
                 " | " + hs_ret_s + " | " + hs_idx_ret_s + " | " + exc_s + " |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、逐年汇总")
lines.append("")
lines.append("| 年份 | ZZ合格数 | ZZ FCF(亿) | ZZ(OCF-OP)(亿) | HS合格数 | HS FCF(亿) | HS(OCF-OP)(亿) | B50NAV | ZZ全NAV | HS全NAV | 932368 | HS300 |")
lines.append("|------|----------|-----------|-----------------|----------|-----------|----------------|--------|---------|---------|--------|-------|")
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    zc=rows['zz_count'].mean(); zf=rows['zz_fcf'].mean(); zo=rows['zz_ocf_op'].mean()
    hc=rows['hs_count'].mean(); hf=rows['hs_fcf'].mean(); ho=rows['hs_ocf_op'].mean()
    bn=rows['b50_nav'].iloc[-1]
    zn=rows['zz_nav'].iloc[-1] if pd.notna(rows['zz_nav'].iloc[-1]) else 0
    hn=rows['hs_nav'].iloc[-1] if pd.notna(rows['hs_nav'].iloc[-1]) else 0
    in_=rows['i_nav'].iloc[-1]; hn2=rows['h_idx_nav'].iloc[-1]
    lines.append("| "+yr+" | "+str(round(zc))+" | "+str(round(zf))+
                 " | "+("+" if zo>=0 else "")+str(round(zo))+
                 " | "+str(round(hc))+" | "+str(round(hf))+
                 " | "+("+" if ho>=0 else "")+str(round(ho))+
                 " | "+str(round(bn,2))+" | "+(str(round(zn,2)) if zn else "—")+
                 " | "+(str(round(hn,2)) if hn else "—")+
                 " | "+str(round(in_,2))+" | "+str(round(hn2,2))+" |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、FCF集中度 & (OCF-营业利润)分析")
lines.append("")
zz_counts=[int(m.iloc[i]['zz_count']) for i in range(len(m))]
hs_counts=[int(m.iloc[i]['hs_count']) for i in range(len(m))]
zz_fcfs=[m.iloc[i]['zz_fcf'] for i in range(len(m))]
hs_fcfs=[m.iloc[i]['hs_fcf'] for i in range(len(m))]
b_fcfs=[m.iloc[i]['b_fcf'] for i in range(len(m))]
zz_ops=[m.iloc[i]['zz_ocf_op'] for i in range(len(m))]
hs_ops=[m.iloc[i]['hs_ocf_op'] for i in range(len(m))]
b_ops=[m.iloc[i]['b_ocf_op'] for i in range(len(m))]

lines.append("### 4.1 公司数量趋势")
lines.append("")
lines.append("- ZZ800平均合格公司数: **" + str(round(np.mean(zz_counts))) + "**（范围 " + str(min(zz_counts)) + "~" + str(max(zz_counts)) + "）")
lines.append("- HS300平均合格公司数: **" + str(round(np.mean(hs_counts))) + "**（范围 " + str(min(hs_counts)) + "~" + str(max(hs_counts)) + "）")
lines.append("- B版Top50仅选取约" + str(round(50/np.mean(zz_counts)*100,1)) + "%的合格公司")

lines.append("")
lines.append("### 4.2 FCF集中度")
lines.append("")
lines.append("- ZZ800全成分平均FCF加和: **" + str(round(np.mean(zz_fcfs))) + " 亿元**")
lines.append("- HS300全成分平均FCF加和: **" + str(round(np.mean(hs_fcfs))) + " 亿元**")
lines.append("- B版Top50平均FCF加和: **" + str(round(np.mean(b_fcfs))) + " 亿元**")
b_conc = np.mean(b_fcfs) / np.mean(zz_fcfs) * 100
lines.append("- Top50 FCF集中度: 占全成分FCF的 **" + str(round(b_conc,1)) + "%**")

lines.append("")
lines.append("### 4.3 (OCF-营业利润)含义与趋势")
lines.append("")
lines.append("> **(OCF-营业利润)** = 经营活动现金流净额 − 营业利润。")
lines.append("> 正值=盈利质量好（现金>利润），负值=利润含水分（利润>现金）")
lines.append("")
lines.append("- ZZ800全成分平均(OCF-OP): **" + ("+" if np.mean(zz_ops)>=0 else "") + str(round(np.mean(zz_ops))) + " 亿元**")
lines.append("- HS300全成分平均(OCF-OP): **" + ("+" if np.mean(hs_ops)>=0 else "") + str(round(np.mean(hs_ops))) + " 亿元**")
lines.append("- B版Top50平均(OCF-OP): **" + ("+" if np.mean(b_ops)>=0 else "") + str(round(np.mean(b_ops))) + " 亿元**")

if np.mean(zz_fcfs) > 0:
    lines.append("- ZZ800 (OCF-OP)/FCF比率: **" + str(round(np.mean(zz_ops)/np.mean(zz_fcfs)*100,1)) + "%**")
if np.mean(b_fcfs) > 0:
    lines.append("- B版Top50 (OCF-OP)/FCF比率: **" + str(round(np.mean(b_ops)/np.mean(b_fcfs)*100,1)) + "%**")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 五、综合结论")
lines.append("")
if zs['ann'] > bs['ann']:
    lines.append("1. ZZ800全成分年化 " + str(round(zs['ann'],2)) + "% > Top50 " + str(round(bs['ann'],2)) + "%，全成分策略更优")
else:
    lines.append("1. ZZ800全成分年化 " + str(round(zs['ann'],2)) + "% < Top50 " + str(round(bs['ann'],2)) + "%，**Top50 FCF率筛选有效集中了高回报标的**")

if hss['ann'] > hsi['ann']:
    lines.append("2. HS300全成分年化 " + str(round(hss['ann'],2)) + "% > 沪深300 " + str(round(hsi['ann'],2)) + "%，FCF加权显著跑赢市值加权")
else:
    lines.append("2. HS300全成分年化 " + str(round(hss['ann'],2)) + "% vs 沪深300 " + str(round(hsi['ann'],2)) + "%")

lines.append("3. Top50仅选约" + str(round(50/np.mean(zz_counts)*100,0)) + "%合格公司，但贡献FCF的" + str(round(b_conc,0)) + "% → 高FCF率=高FCF绝对值，双重集中")

early_ops = np.mean(zz_ops[:len(zz_ops)//2]); late_ops = np.mean(zz_ops[len(zz_ops)//2:])
if late_ops > early_ops:
    lines.append("4. (OCF-OP)趋势上升（前半期" + str(round(early_ops)) + "亿 → 后半期" + str(round(late_ops)) + "亿），A股盈利质量改善")
else:
    lines.append("4. (OCF-OP)趋势（前半期" + str(round(early_ops)) + "亿 → 后半期" + str(round(late_ops)) + "亿）")

lines.append("")
lines.append("---")
lines.append("*报告自动生成，计算日期：" + now + "*")

report = "\n".join(lines)
with open("docs/full_universe_fcf_stats.md", "w") as f:
    f.write(report)

# 输出摘要
print("\n" + "="*70)
print("全成分FCF统计 & NAV对比摘要")
print("="*70)
print("")
print("策略             年化收益    最大回撤    夏普    期末NAV")
print("-"*70)
print("B版Top50(ZZ800)  " + str(round(bs['ann'],2)).rjust(7) + "%  " + str(round(bs['mdd'],2)).rjust(8) + "%  " + str(round(bs['sharpe'],3)).rjust(6) + "  " + str(round(bs['nav'],3)).rjust(7) + "x")
print("ZZ800全成分      " + str(round(zs['ann'],2)).rjust(7) + "%  " + str(round(zs['mdd'],2)).rjust(8) + "%  " + str(round(zs['sharpe'],3)).rjust(6) + "  " + str(round(zs['nav'],3)).rjust(7) + "x")
print("HS300全成分      " + str(round(hss['ann'],2)).rjust(7) + "%  " + str(round(hss['mdd'],2)).rjust(8) + "%  " + str(round(hss['sharpe'],3)).rjust(6) + "  " + str(round(hss['nav'],3)).rjust(7) + "x")
print("932368           " + str(round(is_['ann'],2)).rjust(7) + "%  " + str(round(is_['mdd'],2)).rjust(8) + "%  " + str(round(is_['sharpe'],3)).rjust(6) + "  " + str(round(is_['nav'],3)).rjust(7) + "x")
print("沪深300指数      " + str(round(hsi['ann'],2)).rjust(7) + "%  " + str(round(hsi['mdd'],2)).rjust(8) + "%  " + str(round(hsi['sharpe'],3)).rjust(6) + "  " + str(round(hsi['nav'],3)).rjust(7) + "x")
print("")
print("最近5期统计:")
print("-"*100)
print("调仓日     ZZ数  FCF(亿) (OCF-OP)(亿)  HS数  FCF(亿) (OCF-OP)(亿)  B50数 FCF(亿) (OCF-OP)(亿)")
print("-"*100)
for i in range(min(5, len(m))):
    r = m.iloc[-(5-i)]
    d = r['rb_date']
    zz_o=r.get('zz_ocf_op',0); hs_o=r.get('hs_ocf_op',0); b_o=r.get('b_ocf_op',0)
    print(d + "  " + str(int(r['zz_count'])).rjust(4) + "  " + str(round(r['zz_fcf'],0)).rjust(6) +
          "  " + ("+" if zz_o>=0 else "") + str(round(zz_o,0)).rjust(9) +
          "  " + str(int(r['hs_count'])).rjust(4) + "  " + str(round(r['hs_fcf'],0)).rjust(6) +
          "  " + ("+" if hs_o>=0 else "") + str(round(hs_o,0)).rjust(9) +
          "  " + str(int(r['b_count'])).rjust(4) + "  " + str(round(r['b_fcf'],0)).rjust(6) +
          "  " + ("+" if b_o>=0 else "") + str(round(b_o,0)).rjust(9))
print("")
print("✅ 报告已保存: docs/full_universe_fcf_stats.md")
