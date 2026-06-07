#!/usr/bin/env python3
"""B版持仓复盘分析 — 每次调仓的个股收益/亏损深度复盘"""
import json, sys, time
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime
from compute_nav_cached import get_adj_close_cached

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ═══════════════ 加载数据 ═══════════════
b_baskets = json.load(open("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json"))
nav_df = pd.read_csv("output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv")

# ═══════════════ 计算每只股票的持仓收益 ═══════════════
print("计算B版每只股票的持仓收益...")
all_holdings = []  # 每条记录: {rb_date, ts_code, weight, fcf_yield, fcf, ev, period_ret, is_new, is_kept, ...}

for _, row in nav_df.iterrows():
    rb, nrb = row['rb_date'], row['next_rb']
    stocks = b_baskets.get(rb, [])
    if len(stocks) < 5: continue
    
    prev_codes = set()
    # 找上一期持仓
    prev_dates = [r['rb_date'] for _, r in nav_df.iterrows() if r['rb_date'] < rb]
    if prev_dates:
        prev_rb = prev_dates[-1]
        prev_codes = {s['ts_code'] for s in b_baskets.get(prev_rb, [])}
    
    for s in stocks:
        r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
        if r:
            period_ret = (r[1]/r[0]-1)*100
            w_ret = s['weight'] * (r[1]/r[0]-1)
        else:
            period_ret = None
            w_ret = None
        
        all_holdings.append({
            'rb_date': rb,
            'next_rb': nrb,
            'ts_code': s['ts_code'],
            'weight': s.get('weight',0),
            'fcf_yield': s.get('fcf_yield',0),
            'fcf': s.get('fcf',0)/1e8,  # 亿
            'ev': s.get('ev',0)/1e8,    # 亿
            'profit_quality': s.get('profit_quality',0),
            'period_ret': period_ret,
            'w_ret': w_ret,
            'is_new': s['ts_code'] not in prev_codes,
            'is_kept': s['ts_code'] in prev_codes,
        })

df = pd.DataFrame(all_holdings)
print(f"  总持仓记录: {len(df)}条, 有效收益记录: {df['period_ret'].notna().sum()}条")

# ═══════════════ 统计分析 ═══════════════

# 1. 每期盈亏统计
period_stats = []
for rb in sorted(df['rb_date'].unique()):
    sub = df[(df['rb_date']==rb) & df['period_ret'].notna()]
    if len(sub) == 0: continue
    
    winners = sub[sub['period_ret']>0]
    losers = sub[sub['period_ret']<=0]
    new_stocks = sub[sub['is_new']]
    kept_stocks = sub[sub['is_kept']]
    
    period_stats.append({
        'rb_date': rb,
        'total': len(sub),
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': len(winners)/len(sub)*100,
        'avg_ret': sub['period_ret'].mean(),
        'avg_w_ret': sub['w_ret'].mean()*100,
        'max_ret': sub['period_ret'].max(),
        'min_ret': sub['period_ret'].min(),
        'avg_winner_ret': winners['period_ret'].mean() if len(winners)>0 else 0,
        'avg_loser_ret': losers['period_ret'].mean() if len(losers)>0 else 0,
        'new_count': len(new_stocks),
        'kept_count': len(kept_stocks),
        'new_avg_ret': new_stocks['period_ret'].mean() if len(new_stocks)>0 else 0,
        'kept_avg_ret': kept_stocks['period_ret'].mean() if len(kept_stocks)>0 else 0,
        'top3_winners': winners.nlargest(3, 'period_ret')[['ts_code','period_ret','fcf_yield']].values.tolist(),
        'top3_losers': losers.nsmallest(3, 'period_ret')[['ts_code','period_ret','fcf_yield']].values.tolist(),
    })

ps = pd.DataFrame(period_stats)

# 2. 个股出现频次和平均收益
stock_stats = df[df['period_ret'].notna()].groupby('ts_code').agg(
    count=('period_ret','count'),
    avg_ret=('period_ret','mean'),
    total_w_ret=('w_ret','sum'),
    win_count=('period_ret', lambda x: (x>0).sum()),
    avg_fcf_yield=('fcf_yield','mean'),
    avg_fcf=('fcf','mean'),
    avg_ev=('ev','mean'),
).reset_index()
stock_stats['win_rate'] = stock_stats['win_count'] / stock_stats['count'] * 100
stock_stats['total_contribution'] = stock_stats['total_w_ret'] * 100  # 对总收益的贡献(%)

# 3. 新进 vs 保留表现对比
new_vs_kept = df[df['period_ret'].notna()].groupby('is_new').agg(
    count=('period_ret','count'),
    avg_ret=('period_ret','mean'),
    win_rate=('period_ret', lambda x: (x>0).mean()*100),
    avg_w_ret=('w_ret','mean'),
).reset_index()
new_vs_kept['type'] = new_vs_kept['is_new'].map({True:'新进', False:'保留'})

# 4. FCF收益率分档表现
df_valid = df[df['period_ret'].notna()].copy()
df_valid['fy_bucket'] = pd.cut(df_valid['fcf_yield']*100, 
    bins=[0,5,8,10,15,100], labels=['0-5%','5-8%','8-10%','10-15%','>15%'])
fy_performance = df_valid.groupby('fy_bucket').agg(
    count=('period_ret','count'),
    avg_ret=('period_ret','mean'),
    win_rate=('period_ret', lambda x: (x>0).mean()*100),
    avg_w_ret=('w_ret','mean'),
).reset_index()

# 5. EV分档表现
df_valid['ev_bucket'] = pd.cut(df_valid['ev'], 
    bins=[0,200,500,1000,5000,100000], labels=['<200亿','200-500亿','500-1000亿','1000-5000亿','>5000亿'])
ev_performance = df_valid.groupby('ev_bucket').agg(
    count=('period_ret','count'),
    avg_ret=('period_ret','mean'),
    win_rate=('period_ret', lambda x: (x>0).mean()*100),
    avg_w_ret=('w_ret','mean'),
).reset_index()

# ═══════════════ 最赚钱和最亏钱的股票 ═══════════════
top_contributors = stock_stats.nlargest(10, 'total_contribution')
top_detractors = stock_stats.nsmallest(10, 'total_contribution')
most_frequent = stock_stats.nlargest(10, 'count')
highest_winrate = stock_stats[stock_stats['count']>=5].nlargest(10, 'win_rate')

# ═══════════════ 生成报告 ═══════════════
lines = []
lines.append("# B版策略持仓复盘分析")
lines.append(f"\n> 生成日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append("> 深度复盘B版每期50只持仓的个股收益/亏损，挖掘盈亏结构、持仓稳定性和选股效率")
lines.append("\n---")

# ═══ 每期盈亏概览 ═══
lines.append("\n## 一、每期盈亏概览")
lines.append("> 每个调仓周期的胜率、平均收益、最大赢家/输家")
lines.append("\n| 调仓日 | 持仓数 | 赢家 | 输家 | 胜率 | 平均收益 | 最大赢家 | 最大输家 | 新进数 | 新进收益 | 保留收益 |")
lines.append("|--------|--------|------|------|------|----------|----------|----------|--------|----------|----------|")
for _, r in ps.iterrows():
    top3w = r['top3_winners']
    top3l = r['top3_losers']
    best_name = top3w[0][0] if len(top3w)>0 else "—"
    best_ret = round(top3w[0][1],1) if len(top3w)>0 else 0
    worst_name = top3l[0][0] if len(top3l)>0 else "—"
    worst_ret = round(top3l[0][1],1) if len(top3l)>0 else 0
    lines.append(f"| {r['rb_date']} | {r['total']} | {r['winners']} | {r['losers']} | {round(r['win_rate'],1)}% | {round(r['avg_ret'],2)}% | {best_name} {best_ret}% | {worst_name} {worst_ret}% | {r['new_count']} | {round(r['new_avg_ret'],2)}% | {round(r['kept_avg_ret'],2)}% |")

# ═══ 盈亏结构 ═══
lines.append("\n## 二、盈亏结构分析")
lines.append("\n### 2.1 整体盈亏分布")
valid_rets = df_valid['period_ret']
lines.append(f"- **总持仓记录**: {len(df_valid)}条")
lines.append(f"- **平均个股收益**: {round(valid_rets.mean(),2)}%")
lines.append(f"- **收益中位数**: {round(valid_rets.median(),2)}%")
lines.append(f"- **收益标准差**: {round(valid_rets.std(),2)}%")
lines.append(f"- **胜率**: {round((valid_rets>0).mean()*100,1)}%")
lines.append(f"- **赢家平均收益**: {round(valid_rets[valid_rets>0].mean(),2)}%")
lines.append(f"- **输家平均亏损**: {round(valid_rets[valid_rets<=0].mean(),2)}%")
lines.append(f"- **盈亏比(赢家avg/输家avg)**: {round(abs(valid_rets[valid_rets>0].mean()/valid_rets[valid_rets<=0].mean()),2)}")

# 收益分布
ret_bins = pd.cut(valid_rets, bins=[-100,-20,-10,-5,0,5,10,20,50,100])
ret_dist = valid_rets.groupby(ret_bins).count()
lines.append("\n### 2.2 收益分布")
lines.append("| 收益区间 | 次数 | 占比 |")
lines.append("|----------|------|------|")
for bucket, cnt in ret_dist.items():
    pct = cnt/len(valid_rets)*100
    lines.append(f"| {bucket} | {cnt} | {round(pct,1)}% |")

# ═══ 新进 vs 保留 ═══
lines.append("\n### 2.3 新进 vs 保留表现")
lines.append("| 类型 | 次数 | 平均收益 | 胜率 | 平均加权贡献 |")
lines.append("|------|------|----------|------|-------------|")
for _, r in new_vs_kept.iterrows():
    lines.append(f"| {r['type']} | {r['count']} | {round(r['avg_ret'],2)}% | {round(r['win_rate'],1)}% | {round(r['avg_w_ret']*100,4)}% |")

# ═══ FCF收益率分档 ═══
lines.append("\n## 三、选股质量分析")
lines.append("\n### 3.1 FCF收益率分档表现")
lines.append("> 验证核心假设：FCF收益率越高的公司，收益是否越好？")
lines.append("\n| FCF率档位 | 次数 | 平均收益 | 胜率 | 加权贡献 |")
lines.append("|-----------|------|----------|------|---------|")
for _, r in fy_performance.iterrows():
    lines.append(f"| {r['fy_bucket']} | {r['count']} | {round(r['avg_ret'],2)}% | {round(r['win_rate'],1)}% | {round(r['avg_w_ret']*100,4)}% |")

# ═══ EV分档 ═══
lines.append("\n### 3.2 企业价值(EV)分档表现")
lines.append("> 大盘股 vs 中小盘股的收益差异")
lines.append("\n| EV档位 | 次数 | 平均收益 | 胜率 | 加权贡献 |")
lines.append("|--------|------|----------|------|---------|")
for _, r in ev_performance.iterrows():
    lines.append(f"| {r['ev_bucket']} | {r['count']} | {round(r['avg_ret'],2)}% | {round(r['win_rate'],1)}% | {round(r['avg_w_ret']*100,4)}% |")

# ═══ 最赚钱股票 ═══
lines.append("\n## 四、核心个股分析")
lines.append("\n### 4.1 对组合收益贡献最大的10只股票")
lines.append("> 累计加权贡献 = 该股票所有持仓期的加权收益之和")
lines.append("\n| 公司 | 出现次数 | 平均收益 | 胜率 | 累计贡献 | 平均FCF率 | 平均FCF(亿) |")
lines.append("|------|----------|----------|------|----------|-----------|-------------|")
for _, r in top_contributors.iterrows():
    lines.append(f"| {r['ts_code']} | {r['count']} | {round(r['avg_ret'],2)}% | {round(r['win_rate'],1)}% | {round(r['total_contribution'],3)}% | {round(r['avg_fcf_yield']*100,2)}% | {round(r['avg_fcf'],1)} |")

# ═══ 最亏钱股票 ═══
lines.append("\n### 4.2 对组合收益拖累最大的10只股票")
lines.append("\n| 公司 | 出现次数 | 平均收益 | 胜率 | 累计拖累 | 平均FCF率 | 平均FCF(亿) |")
lines.append("|------|----------|----------|------|----------|-----------|-------------|")
for _, r in top_detractors.iterrows():
    lines.append(f"| {r['ts_code']} | {r['count']} | {round(r['avg_ret'],2)}% | {round(r['win_rate'],1)}% | {round(r['total_contribution'],3)}% | {round(r['avg_fcf_yield']*100,2)}% | {round(r['avg_fcf'],1)} |")

# ═══ 最高胜率 ═══
lines.append("\n### 4.3 胜率最高的10只股票(>=5次持仓)")
lines.append("\n| 公司 | 出现次数 | 平均收益 | 胜率 | 累计贡献 | 平均FCF率 |")
lines.append("|------|----------|----------|------|----------|-----------|")
for _, r in highest_winrate.iterrows():
    lines.append(f"| {r['ts_code']} | {r['count']} | {round(r['avg_ret'],2)}% | {round(r['win_rate'],1)}% | {round(r['total_contribution'],3)}% | {round(r['avg_fcf_yield']*100,2)}% |")

# ═══ 最频繁 ═══
lines.append("\n### 4.4 持仓最频繁的10只股票")
lines.append("\n| 公司 | 出现次数 | 平均收益 | 胜率 | 累计贡献 | 平均FCF率 | 平均EV(亿) |")
lines.append("|------|----------|----------|------|----------|-----------|------------|")
for _, r in most_frequent.iterrows():
    lines.append(f"| {r['ts_code']} | {r['count']} | {round(r['avg_ret'],2)}% | {round(r['win_rate'],1)}% | {round(r['total_contribution'],3)}% | {round(r['avg_fcf_yield']*100,2)}% | {round(r['avg_ev'],0)} |")

# ═══ 持仓稳定性 ═══
lines.append("\n## 五、持仓稳定性分析")
# 计算连续持仓长度
df_valid_sorted = df_valid.sort_values(['ts_code','rb_date'])
consecutive_runs = []
for code, group in df_valid_sorted.groupby('ts_code'):
    dates = sorted(group['rb_date'].tolist())
    # 找最长连续持仓
    max_run = 1; current_run = 1
    for i in range(1, len(dates)):
        # 检查是否连续（下一个日期在nav_df中是上一期的next_rb）
        if dates[i] in set(nav_df['rb_date'].tolist()):
            # 简单判断：如果日期差约3个月（季度调仓）
            prev_idx = nav_df[nav_df['rb_date']==dates[i-1]].index
            if len(prev_idx) > 0:
                prev_nrb = nav_df.loc[prev_idx[0], 'next_rb']
                if prev_nrb == dates[i]:
                    current_run += 1
                else:
                    max_run = max(max_run, current_run)
                    current_run = 1
            else:
                max_run = max(max_run, current_run)
                current_run = 1
        else:
            max_run = max(max_run, current_run)
            current_run = 1
    max_run = max(max_run, current_run)
    avg_ret = group['period_ret'].mean()
    consecutive_runs.append({'ts_code': code, 'total_count': len(dates), 'max_consecutive': max_run, 'avg_ret': avg_ret})

cr = pd.DataFrame(consecutive_runs)
lines.append(f"- **平均持仓次数**: {round(stock_stats['count'].mean(),1)}次")
lines.append(f"- **平均最长连续持仓**: {round(cr['max_consecutive'].mean(),1)}期")
lines.append(f"- **持仓>=10次的股票**: {len(stock_stats[stock_stats['count']>=10])}只")
lines.append(f"- **持仓>=20次的股票**: {len(stock_stats[stock_stats['count']>=20])}只")
lines.append(f"- **仅持仓1次的股票**: {len(stock_stats[stock_stats['count']==1])}只")

# 连续持仓最长的股票
longest_held = cr.nlargest(10, 'max_consecutive')
lines.append("\n### 5.1 连续持仓最长的10只股票")
lines.append("\n| 公司 | 总持仓次数 | 最长连续期数 | 平均收益 |")
lines.append("|------|-----------|-------------|----------|")
for _, r in longest_held.iterrows():
    lines.append(f"| {r['ts_code']} | {r['total_count']} | {r['max_consecutive']} | {round(r['avg_ret'],2)}% |")

# ═══ 亏损期深度分析 ═══
lines.append("\n## 六、亏损期深度分析")
losing_periods = ps[ps['avg_ret']<0].sort_values('avg_ret')
lines.append(f"- **亏损期数**: {len(losing_periods)}/{len(ps)}期 ({round(len(losing_periods)/len(ps)*100,1)}%)")
lines.append(f"- **亏损期平均亏损**: {round(losing_periods['avg_ret'].mean(),2)}%")
lines.append(f"- **最大亏损期**: {losing_periods.iloc[0]['rb_date']} ({round(losing_periods.iloc[0]['avg_ret'],2)}%)")

lines.append("\n### 6.1 亏损期个股亏损分布")
lines.append("\n| 调仓日 | 平均收益 | 赢家数 | 输家数 | 赢家avg | 输家avg | 输家>10% | 输家>20% |")
lines.append("|--------|----------|--------|--------|---------|---------|----------|----------|")
for _, r in losing_periods.iterrows():
    sub = df_valid[df_valid['rb_date']==r['rb_date']]
    losers_sub = sub[sub['period_ret']<=0]
    big_losers10 = len(losers_sub[losers_sub['period_ret']<-10])
    big_losers20 = len(losers_sub[losers_sub['period_ret']<-20])
    lines.append(f"| {r['rb_date']} | {round(r['avg_ret'],2)}% | {r['winners']} | {r['losers']} | {round(r['avg_winner_ret'],2)}% | {round(r['avg_loser_ret'],2)}% | {big_losers10} | {big_losers20} |")

# ═══ 反思与结论 ═══
lines.append("\n## 七、复盘反思与核心结论")

# 关键数字
total_win = (valid_rets>0).sum()
total_loss = (valid_rets<=0).sum()
avg_win_ret = valid_rets[valid_rets>0].mean()
avg_loss_ret = valid_rets[valid_rets<=0].mean()
profit_loss_ratio = abs(avg_win_ret / avg_loss_ret) if avg_loss_ret != 0 else 0

lines.append(f"\n### 7.1 盈亏基本盘")
lines.append(f"- **总持仓**: {len(df_valid)}条记录, {stock_stats['ts_code'].nunique()}只不同公司")
lines.append(f"- **胜率**: {round(total_win/len(df_valid)*100,1)}% ({total_win}赢 / {total_loss}亏)")
lines.append(f"- **盈亏比**: {round(profit_loss_ratio,2)} (赢家avg {round(avg_win_ret,2)}% / 输家avg {round(avg_loss_ret,2)}%)")
lines.append(f"- **策略盈利核心**: 赢家收益的绝对值 > 输家亏损的绝对值，盈亏比>{round(profit_loss_ratio,1)}驱动正期望")

# 新进vs保留
new_avg = new_vs_kept[new_vs_kept['type']=='新进']['avg_ret'].values[0]
kept_avg = new_vs_kept[new_vs_kept['type']=='保留']['avg_ret'].values[0]
lines.append(f"\n### 7.2 持仓稳定性反思")
lines.append(f"- **新进股平均收益**: {round(new_avg,2)}% vs 保留股{round(kept_avg,2)}%")
if new_avg > kept_avg:
    lines.append(f"- **新进股表现更好**: +{round(new_avg-kept_avg,2)}pp → 换仓有效，淘汰旧股引入更强标的")
else:
    lines.append(f"- **保留股表现更好**: +{round(kept_avg-new_avg,2)}pp → 持仓粘性有效，频繁换仓可能引入弱标的")

# FCF率与收益关系
fy_high = fy_performance[fy_performance['fy_bucket'].isin(['10-15%','>15%'])]['avg_ret'].mean()
fy_low = fy_performance[fy_performance['fy_bucket'].isin(['0-5%','5-8%'])]['avg_ret'].mean()
lines.append(f"\n### 7.3 FCF收益率验证")
lines.append(f"- **高FCF率档(>10%)平均收益**: {round(fy_high,2)}%")
lines.append(f"- **低FCF率档(<8%)平均收益**: {round(fy_low,2)}%")
if fy_high > fy_low:
    lines.append(f"- **高FCF率股表现更好**: +{round(fy_high-fy_low,2)}pp → FCF收益率排序策略的核心假设成立")
else:
    lines.append(f"- **低FCF率股反而更好**: +{round(fy_low-fy_high,2)}pp → FCF率排序需进一步优化")

# EV大小与收益关系
ev_small = ev_performance[ev_performance['ev_bucket'].isin(['<200亿','200-500亿'])]['avg_ret'].mean()
ev_big = ev_performance[ev_performance['ev_bucket'].isin(['1000-5000亿','>5000亿'])]['avg_ret'].mean()
lines.append(f"\n### 7.4 大盘vs中小盘反思")
lines.append(f"- **中小盘(EV<500亿)平均收益**: {round(ev_small,2)}%")
lines.append(f"- **大盘(EV>1000亿)平均收益**: {round(ev_big,2)}%")
if ev_small > ev_big:
    lines.append(f"- **中小盘表现更好**: +{round(ev_small-ev_big,2)}pp → FCF率策略天然偏向中小盘高FCF率公司，这是策略优势")
else:
    lines.append(f"- **大盘表现更好**: +{round(ev_big-ev_small,2)}% → 可能大盘股稳定性更强")

# 最大贡献者特征
lines.append(f"\n### 7.5 最大贡献者特征")
top3 = top_contributors.head(3)
for _, r in top3.iterrows():
    lines.append(f"- {r['ts_code']}: 累计贡献{round(r['total_contribution'],3)}%, 胜率{round(r['win_rate'],1)}%, FCF率{round(r['avg_fcf_yield']*100,2)}%, FCF{round(r['avg_fcf'],1)}亿")

# 最大拖累者特征
lines.append(f"\n### 7.6 最大拖累者特征")
bottom3 = top_detractors.head(3)
for _, r in bottom3.iterrows():
    lines.append(f"- {r['ts_code']}: 累计拖累{round(r['total_contribution'],3)}%, 胜率{round(r['win_rate'],1)}%, FCF率{round(r['avg_fcf_yield']*100,2)}%")

lines.append(f"\n---\n*报告自动生成，计算日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}*")

report = "\n".join(lines)
Path("docs/zz800_b_version_review.md").write_text(report, encoding='utf-8')
print("✅ 报告: docs/zz800_b_version_review.md")
print(f"总持仓记录: {len(df_valid)}, 公司数: {stock_stats['ts_code'].nunique()}")
print(f"胜率: {round(total_win/len(df_valid)*100,1)}%, 盈亏比: {round(profit_loss_ratio,2)}")