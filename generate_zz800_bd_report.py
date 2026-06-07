#!/usr/bin/env python3
"""generate_zz800_bd_report.py — ZZ800 FCF B版 vs D版 回测报告"""
import json, os, sys
import pandas as pd, numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load_nav(path):
    df = pd.read_csv(path)
    # rename period_ret → ret if needed
    if 'period_ret' in df.columns and 'ret' not in df.columns:
        df['ret'] = df['period_ret']
    return df

def annual(years, nav): return (nav ** (1/years) - 1) * 100

# 加载 NAV
nav_b = load_nav(ROOT / 'output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv')
nav_d = load_nav(ROOT / 'output/zz800_fcf_lenient_buffer/backtest_nav_tr.csv')

# 加载指数（确保升序）
idx_932 = pd.read_csv(ROOT / 'data/932366_daily.csv', parse_dates=['trade_date'], index_col='trade_date').sort_index()
idx_hs  = pd.read_csv(ROOT / 'data/hs300_total_return.csv', parse_dates=['trade_date'], index_col='trade_date').sort_index()

# 篮子
baskets_b = json.load(open(ROOT / 'output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json'))
baskets_d = json.load(open(ROOT / 'output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json'))

dates = sorted(baskets_b.keys())
n_periods = len(nav_b)

# 计算统计
b_ret = nav_b['ret'].values
d_ret = nav_d['ret'].values

years = n_periods / 4
nav_b_final = nav_b['nav'].iloc[-1]
nav_d_final = nav_d['nav'].iloc[-1]
b_cagr = annual(years, nav_b_final)
d_cagr = annual(years, nav_d_final)

# 932366 同期计算 (from 2015-01-05)
if 'close' in idx_932.columns:
    idx_932_start = idx_932.index[0]
    p1 = idx_932['close'].iloc[0]
    p2 = idx_932['close'].iloc[-1]
    idx_932_ret = p2 / p1
    idx_932_years = (idx_932.index[-1] - idx_932_start).days / 365.25
    idx_932_cagr = annual(idx_932_years, idx_932_ret)
else:
    idx_932_ret = 1.0
    idx_932_cagr = 0

# HS300TR (from 2016-01-04)
idx_hs_start = idx_hs.index[0]
p1 = idx_hs['close'].iloc[0]
p2 = idx_hs['close'].iloc[-1]
hs300_ret = p2 / p1
hs300_years = (idx_hs.index[-1] - idx_hs_start).days / 365.25
hs300_cagr = annual(hs300_years, hs300_ret)

# 换手率
def calc_turnover(baskets):
    prev_codes = set()
    turnovers = []
    for d in dates:
        codes = set(item['ts_code'] for item in baskets.get(d, []) if item.get('ts_code'))
        if prev_codes:
            turnover = len(codes - prev_codes) / max(len(codes), 1)
            turnovers.append(turnover)
        prev_codes = codes
    return np.mean(turnovers) * 100 if turnovers else 0

b_turnover = calc_turnover(baskets_b)
d_turnover = calc_turnover(baskets_d)

# 逐年统计
year_returns = {}
for _, row in nav_b.iterrows():
    y = str(row['rb_date'])[:4]
    if y not in year_returns:
        year_returns[y] = {'b': 1.0, 'd': 1.0, 'hs300': 1.0, 'n': 0}
    year_returns[y]['b'] *= (1 + row['ret'])
    year_returns[y]['n'] += 1
for _, row in nav_d.iterrows():
    y = str(row['rb_date'])[:4]
    if y not in year_returns: year_returns[y] = {'b':1,'d':1,'hs300':1,'n':0}
    year_returns[y]['d'] *= (1 + row['ret'])

# HS300TR yearly (use actual data range)
for y in sorted(year_returns):
    try:
        yr_data = idx_hs.loc[f"{y}-01-01":f"{y}-12-31"]
        if len(yr_data) >= 2:
            p1 = yr_data['close'].iloc[0]
            p2 = yr_data['close'].iloc[-1]
            year_returns[y]['hs300'] = p2 / p1
        else:
            year_returns[y]['hs300'] = 1.0
    except:
        year_returns[y]['hs300'] = 1.0

# 生成报告
report = []
report.append("# ZZ800 FCF B版 vs D版 回测报告\n")
report.append(f"> 回测期间：{dates[0]} ~ {dates[-1]} ({years:.1f}年)\n")
report.append(f"> 调仓频次：每年 4 次 | 共 {n_periods} 期\n\n")

report.append("## 核心指标\n\n")
report.append(f"| 指标 | B版 | D版 | HS300TR | 932366 |\n")
report.append(f"|------|:---:|:---:|:--:|:--:|\n")
report.append(f"| 累计净值 | {nav_b_final:.2f}x | {nav_d_final:.2f}x | {hs300_ret:.2f}x | {idx_932_ret:.2f}x |\n")
report.append(f"| 年化收益 | {b_cagr:.2f}% | {d_cagr:.2f}% | {hs300_cagr:.2f}% | {idx_932_cagr:.2f}% |\n")
report.append(f"| 平均换手率 | {b_turnover:.1f}% | {d_turnover:.1f}% | — | — |\n")
report.append(f"| 超额(vs HS300TR) | {b_cagr-hs300_cagr:+.2f}pp | {d_cagr-hs300_cagr:+.2f}pp | — | {idx_932_cagr-hs300_cagr:+.2f}pp |\n\n")

report.append("## 逐年收益\n\n")
report.append(f"| 年份 | B版 | D版 | HS300TR | B版超额 | D版超额 |\n")
report.append(f"|------|:---:|:---:|:--:|:--:|:--:|\n")
for y in sorted(year_returns):
    yr = year_returns[y]
    b_r = (yr['b'] - 1) * 100
    d_r = (yr['d'] - 1) * 100
    h_r = (yr['hs300'] - 1) * 100
    report.append(f"| {y} | {b_r:+.2f}% | {d_r:+.2f}% | {h_r:+.2f}% | {b_r-h_r:+.2f}pp | {d_r-h_r:+.2f}pp |\n")

# B-D 差异统计
diffs = (d_ret - b_ret) * 100
report.append(f"\n## B-D 差异统计\n\n")
report.append(f"| 指标 | 数值 |\n")
report.append(f"|------|------|\n")
report.append(f"| 平均差异 | {np.mean(diffs):.2f}pp/期 |\n")
report.append(f"| D优于B的期数 | {np.sum(diffs > 0)} / {len(diffs)} 期 |\n")
report.append(f"| D劣于B的期数 | {np.sum(diffs < 0)} / {len(diffs)} 期 |\n")
report.append(f"| 最大正差异 | {np.max(diffs):+.2f}pp |\n")
report.append(f"| 最大负差异 | {np.min(diffs):+.2f}pp |\n")

path = ROOT / 'docs/zz800_fcf_d_vs_b_report.md'
with open(path, 'w') as f:
    f.writelines(report)

print(f"✅ 报告已保存: {path}")
print(f"   B版: NAV={nav_b_final:.2f}x, 年化={b_cagr:.2f}%, 换手={b_turnover:.1f}%")
print(f"   D版: NAV={nav_d_final:.2f}x, 年化={d_cagr:.2f}%, 换手={d_turnover:.1f}%")
print(f"   HS300TR: 累计={hs300_ret:.2f}x, 年化={hs300_cagr:.2f}%")
print(f"   932366: 累计={idx_932_ret:.2f}x, 年化={idx_932_cagr:.2f}%")
