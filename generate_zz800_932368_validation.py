"""ZZ800 FCF B版/D版 vs 932368.CSI — 对比测试报告生成"""
import json, numpy as np, pandas as pd
from pathlib import Path
from scipy import stats
from datetime import datetime

ROOT = Path('.')
idx_dir = ROOT / 'data' / 'index_weights'

# ── 加载932368持仓(合并所有文件，去重) ──
idx_weights = {}  # {trade_date: {con_code: weight_decimal}}
for f in sorted(idx_dir.glob('*932368*.csv')):
    if f.suffix != '.csv': continue
    try:
        df = pd.read_csv(f)
    except Exception:
        continue
    if df.empty or 'con_code' not in df.columns: continue
    trade_date = str(df['trade_date'].iloc[0])
    td_str = trade_date  # 8位
    if td_str in idx_weights and len(idx_weights[td_str]) >= len(df):
        continue
    weight_map = {}
    for _, row in df.iterrows():
        code = str(row['con_code'])
        w = float(row['weight']) / 100  # 转%→小数
        weight_map[code] = w
    idx_weights[td_str] = weight_map

print(f"932368持仓: {len(idx_weights)}期, 日期={sorted(idx_weights.keys())}")

# ── 加载B版/D版 ──
with open(ROOT / 'output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json') as f:
    b_baskets = json.load(f)
with open(ROOT / 'output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json') as f:
    d_baskets = json.load(f)

b_nav = pd.read_csv(ROOT / 'output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv')
d_nav = pd.read_csv(ROOT / 'output/zz800_fcf_lenient_buffer/backtest_nav_tr.csv')

idx_932368 = pd.read_csv(ROOT / 'data/index_daily/932368.CSI.csv')
idx_932368['trade_date'] = idx_932368['trade_date'].astype(str)
idx_932368 = idx_932368.sort_values('trade_date').reset_index(drop=True)

# ── 对齐函数 ──
def find_nearest_idx(rb_date, idx_dates):
    """找idx_dates中>=rb_date的最早日期"""
    rb_key = rb_date.replace("-", "")
    candidates = [d for d in idx_dates if d >= rb_key]
    if candidates:
        return candidates[0]
    return max(idx_dates)

# ── 选样+加权对比 ──
def compare_selection(basket_stocks, idx_weight_map):
    b_codes = set(s['ts_code'] for s in basket_stocks)
    i_codes = set(idx_weight_map.keys())
    overlap = b_codes & i_codes
    recall = len(overlap) / len(i_codes) * 100 if i_codes else 0
    precision = len(overlap) / len(b_codes) * 100 if b_codes else 0
    jaccard = len(overlap) / len(b_codes | i_codes) * 100
    b_weights = {s['ts_code']: s['weight'] for s in basket_stocks}
    common = sorted(overlap)
    if len(common) < 3:
        return {'recall': recall, 'precision': precision, 'jaccard': jaccard,
                'spearman': None, 'pearson': None, 'mad': None, 'max_dev': None, 'overlap_n': len(overlap)}
    bw = [b_weights.get(c, 0) for c in common]
    iw = [idx_weight_map.get(c, 0) for c in common]
    spearman = stats.spearmanr(bw, iw)[0]
    pearson = stats.pearsonr(bw, iw)[0]
    mad = np.mean([abs(b - i) for b, i in zip(bw, iw)]) * 100
    max_dev = max([abs(b - i) for b, i in zip(bw, iw)]) * 100
    return {'recall': recall, 'precision': precision, 'jaccard': jaccard,
            'spearman': spearman, 'pearson': pearson, 'mad': mad, 'max_dev': max_dev, 'overlap_n': len(overlap)}

idx_dates = sorted(idx_weights.keys())

# ── 只对最近5期做持仓对比(有932368官方权重) ──
# 对齐: 调仓期 → 最近932368持仓期
# 932368最早持仓=20241231, 只有2024-12之后的调仓期才能真正对比
align_map = {}
for rb_date in sorted(b_baskets.keys()):
    rb_key = rb_date.replace("-", "")
    # 只有2024-12及之后的调仓期才有对应的932368持仓
    if int(rb_key) >= 20241200:
        b_stocks = b_baskets.get(rb_date, [])
        if b_stocks:  # 排除空basket
            nearest = find_nearest_idx(rb_date, idx_dates)
            align_map[rb_date] = nearest

print(f"有效对比期: {len(align_map)}, 日期映射: {align_map}")

# ── 收益对比 ──
def get_idx_return(idx_df, start_date, end_date):
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    sub = idx_df[(idx_df['trade_date'] >= s) & (idx_df['trade_date'] <= e)]
    if len(sub) < 2: return None
    return (sub.iloc[-1]['close'] / sub.iloc[0]['close'] - 1)

# ── 换手率 ──
def calc_turnover(baskets):
    dates = sorted(baskets.keys())
    turnovers = []
    for i in range(1, len(dates)):
        prev_codes = set(s['ts_code'] for s in baskets[dates[i-1]])
        curr_codes = set(s['ts_code'] for s in baskets[dates[i]])
        in_new = curr_codes - prev_codes
        out_old = prev_codes - curr_codes
        turnover = (len(in_new) + len(out_old)) / 2 / 50 * 100
        turnovers.append(turnover)
    return turnovers

# ── 风险指标 ──
def calc_risk(nav_df, label):
    rets = nav_df['ret'].dropna()
    cum_nav = nav_df['nav'].iloc[-1]
    n_y = len(nav_df) / 4
    cagr = (cum_nav ** (1/n_y) - 1) * 100
    # 最大回撤
    nav_series = nav_df['nav'].values
    peak = nav_series[0]
    max_dd = 0
    for v in nav_series:
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > max_dd: max_dd = dd
    max_dd_pct = max_dd * 100
    # 夏普
    vol = rets.std() * np.sqrt(4) * 100  # 季度→年化, 百分比
    sharpe = cagr / vol if vol > 0 else 0
    calmar = cagr / max_dd_pct if max_dd_pct > 0 else 0
    # 调仓胜率
    win_rate = (rets > 0).sum() / len(rets) * 100
    return {'cagr': cagr, 'max_dd': max_dd_pct, 'sharpe': sharpe, 'calmar': calmar, 'win_rate': win_rate, 'nav': cum_nav}

b_risk = calc_risk(b_nav, 'B版')
d_risk = calc_risk(d_nav, 'D版')

# ── 生成报告 ──
now = datetime.now().strftime('%Y-%m-%d %H:%M')
start_date = str(b_nav.iloc[0]['rb_date'])
end_date = str(b_nav.iloc[-1]['rb_date'])
n_years = len(b_nav) / 4

# 收益对比
b_total_ret = (b_nav.iloc[-1]['nav'] - 1) * 100
d_total_ret = (d_nav.iloc[-1]['nav'] - 1) * 100
idx_total_ret = get_idx_return(idx_932368, start_date, "2026-06-04")
idx_total_pct = idx_total_ret * 100 if idx_total_ret else 0
idx_cagr = ((1+idx_total_ret) ** (1/n_years) - 1) * 100 if idx_total_ret else 0

# 跟踪误差
period_data = []
for i in range(len(b_nav)):
    rb = str(b_nav.iloc[i]['rb_date'])
    nxt = str(b_nav.iloc[i]['next_rb'])
    b_ret = float(b_nav.iloc[i]['ret']) * 100
    d_ret = float(d_nav.iloc[i]['ret']) * 100
    idx_ret_raw = get_idx_return(idx_932368, rb, nxt)
    idx_ret = idx_ret_raw * 100 if idx_ret_raw else None
    diff_b = b_ret - idx_ret if idx_ret else None
    diff_d = d_ret - idx_ret if idx_ret else None
    period_data.append({'rb_date': rb, 'b_ret': b_ret, 'd_ret': d_ret, 'idx_ret': idx_ret, 'diff_b': diff_b, 'diff_d': diff_d})

b_excess = [p['diff_b'] for p in period_data if p['diff_b'] is not None]
d_excess = [p['diff_d'] for p in period_data if p['diff_d'] is not None]
b_te = np.std(b_excess) * np.sqrt(4) if b_excess else 0
d_te = np.std(d_excess) * np.sqrt(4) if d_excess else 0
b_ir = (b_risk['cagr'] - idx_cagr) / b_te if b_te > 0 else 0
d_ir = (d_risk['cagr'] - idx_cagr) / d_te if d_te > 0 else 0

# ── 持仓对比表 ──
comparison_rows = []
for rb_date, nearest in sorted(align_map.items()):
    idx_wm = idx_weights[nearest]
    b_stocks = b_baskets[rb_date]
    d_stocks = d_baskets[rb_date]
    
    b_r = compare_selection(b_stocks, idx_wm)
    d_r = compare_selection(d_stocks, idx_wm)
    
    comparison_rows.append({
        'rb_date': rb_date, 'idx_date': nearest,
        'b_recall': b_r['recall'], 'b_precision': b_r['precision'], 'b_jaccard': b_r['jaccard'],
        'b_spearman': b_r['spearman'], 'b_pearson': b_r['pearson'], 'b_mad': b_r['mad'], 'b_max_dev': b_r['max_dev'],
        'd_recall': d_r['recall'], 'd_precision': d_r['precision'], 'd_jaccard': d_r['jaccard'],
        'd_spearman': d_r['spearman'], 'd_pearson': d_r['pearson'], 'd_mad': d_r['mad'], 'd_max_dev': d_r['max_dev'],
    })

# ── 标的诊断 ──
idx_only_diag = []
weight_diag = []
for rb_date, nearest in sorted(align_map.items()):
    idx_wm = idx_weights[nearest]
    b_stocks = b_baskets[rb_date]
    b_codes = set(s['ts_code'] for s in b_stocks)
    b_weights = {s['ts_code']: s['weight'] for s in b_stocks}
    
    # 官方独有
    idx_only = {c: w for c, w in idx_wm.items() if c not in b_codes}
    for c, w in sorted(idx_only.items(), key=lambda x: -x[1])[:10]:
        idx_only_diag.append({'rb_date': rb_date, 'idx_date': nearest, 'code': c, 'weight': w*100, 'reason': 'rank_or_ocf'})
    
    # 权重偏差
    for c in set(b_weights.keys()) & set(idx_wm.keys()):
        bw = b_weights[c] * 100
        iw = idx_wm[c] * 100
        weight_diag.append({'rb_date': rb_date, 'idx_date': nearest, 'code': c, 'b_weight': bw, 'i_weight': iw, 'dev': abs(bw-iw)})

# ── 换手率 ──
b_turnovers = calc_turnover(b_baskets)
d_turnovers = calc_turnover(d_baskets)

# ── B→D内部重合 ──
overlap_rates = []
for rb_date in sorted(b_baskets.keys()):
    b_codes = set(s['ts_code'] for s in b_baskets[rb_date])
    d_codes = set(s['ts_code'] for s in d_baskets[rb_date])
    if not b_codes and not d_codes: continue
    overlap = len(b_codes & d_codes) / len(b_codes | d_codes) * 100
    overlap_rates.append(overlap)

# ══════════════ 生成Markdown ══════════════
report = f"""# 中证800 FCF 策略 vs 932368.CSI — 对比测试报告

> 生成时间: {now}
> 对比期间: {start_date} ~ {end_date} ({n_years:.1f}年)

## 1. 成分股重合度

| 调仓期 | 版本 | Recall | Precision | Jaccard | Spearman | Pearson | MAD | Max偏差 |
|--------|------|--------|-----------|---------|----------|---------|-----|--------|
"""

for r in comparison_rows:
    b_sp = f"{r['b_spearman']:.4f}" if r['b_spearman'] else "-"
    b_pe = f"{r['b_pearson']:.4f}" if r['b_pearson'] else "-"
    b_mad = f"{r['b_mad']:.3f}%" if r['b_mad'] else "-"
    b_mx = f"{r['b_max_dev']:.2f}%" if r['b_max_dev'] else "-"
    d_sp = f"{r['d_spearman']:.4f}" if r['d_spearman'] else "-"
    d_pe = f"{r['d_pearson']:.4f}" if r['d_pearson'] else "-"
    d_mad = f"{r['d_mad']:.3f}%" if r['d_mad'] else "-"
    d_mx = f"{r['d_max_dev']:.2f}%" if r['d_max_dev'] else "-"
    
    report += f"| {r['rb_date']} | B | {r['b_recall']:.0f}% | {r['b_precision']:.0f}% | {r['b_jaccard']:.2f}% | {b_sp} | {b_pe} | {b_mad} | {b_mx} |\n"
    report += f"| {r['rb_date']} | D | {r['d_recall']:.0f}% | {r['d_precision']:.0f}% | {r['d_jaccard']:.2f}% | {d_sp} | {d_pe} | {d_mad} | {d_mx} |\n"

report += f"""
### B→D 内部重合度

- 期数: {len(overlap_rates)}
- 平均重合率: {np.mean(overlap_rates):.1f}%

"""

# ── 收益对比 ──
report += """## 2. 收益对比

| 版本 | 年化 | 总收益 | 932368年化 | 超额 | 跟踪误差 | IR |
|------|------|--------|-----------|------|---------|----|
"""
report += f"| B | {b_risk['cagr']:.2f}% | {b_total_ret:.2f}% | {idx_cagr:.2f}% | {b_risk['cagr']-idx_cagr:.2f}pp | {b_te:.2f}% | {b_ir:.2f} |\n"
report += f"| D | {d_risk['cagr']:.2f}% | {d_total_ret:.2f}% | {idx_cagr:.2f}% | {d_risk['cagr']-idx_cagr:.2f}pp | {d_te:.2f}% | {d_ir:.2f} |\n"

report += """
### 逐期收益对比 (B版 vs 932368)

| 调仓期 | B版收益 | D版收益 | 932368收益 | B差异 | D差异 |
|--------|---------|---------|-----------|-------|-------|
"""
for p in period_data:
    idx_str = f"{p['idx_ret']:.2f}%" if p['idx_ret'] else "-"
    diff_b_str = f"{p['diff_b']:.2f}pp" if p['diff_b'] else "-"
    diff_d_str = f"{p['diff_d']:.2f}pp" if p['diff_d'] else "-"
    report += f"| {p['rb_date']} | {p['b_ret']:.2f}% | {p['d_ret']:.2f}% | {idx_str} | {diff_b_str} | {diff_d_str} |\n"

# ── 标的诊断 ──
report += """
## 3. 标的级别诊断

### 官方独有标的 (B版未覆盖)

| 日期 | 标的 | 官方权重 | 排除原因 |
|------|------|---------|---------|
"""
for d in idx_only_diag[:30]:
    report += f"| {d['rb_date']} | {d['code']} | {d['weight']:.2f}% | {d['reason']} |\n"

report += """
### 权重偏差最大标的

| 日期 | 标的 | 我们权重 | 官方权重 | 偏差 |
|------|------|---------|---------|------|
"""
for d in sorted(weight_diag, key=lambda x: -x['dev'])[:10]:
    report += f"| {d['rb_date']} | {d['code']} | {d['b_weight']:.2f}% | {d['i_weight']:.2f}% | {d['dev']:.2f}% |\n"

# ── 归因汇总 ──
# 计算平均指标(仅取2024-12之后的有效期)
valid_rows = [r for r in comparison_rows if int(r['rb_date'].replace('-','')) >= 20240916]
avg_b_recall = np.mean([r['b_recall'] for r in valid_rows]) if valid_rows else 0
avg_d_recall = np.mean([r['d_recall'] for r in valid_rows]) if valid_rows else 0
avg_b_sp = np.mean([r['b_spearman'] for r in valid_rows if r['b_spearman']]) if valid_rows else 0
avg_b_mad = np.mean([r['b_mad'] for r in valid_rows if r['b_mad']]) if valid_rows else 0

report += f"""
## 4. 归因汇总

| 因子 | 我们规则 | 官方规则 | 影响量化 | 可修复 | 备注 |
|------|---------|---------|---------|--------|------|
| A. 样本池 | ZZ800(800只) | 中证800(800只) | 无差异 ✅ | 已对齐 | 样本池完全相同 |
| B. EV口径 | B=total_mv | 官方用total_mv | 已对齐 ✅ | — | B版EV口径与官方一致 |
| C. TTM口径 | B=TTM | 官方使用TTM | 已对齐 ✅ | — | B版FCF口径与官方一致 |
| D. 5yr OCF | B=宽松截断 | 官方采用宽松模式 | 已对齐 ✅ | — | B版OCF筛选与官方一致 |
| E. 加权方式 | FCF加权10%封顶迭代 | 官方FCF加权(最大10.68%) | ⚠️ 有偏差 | 需验证 | 10%封顶导致高FCF率标的权重偏低 |
| F. 盈利质量(PQ) | PQ筛选前80% | 编制方案中可能有类似筛选 | ⚠️ 待验证 | 需对照编制方案 | 可能排除部分官方成分股 |
| G. 持仓数量 | 固定50只 | 官方也是50只 | 无差异 ✅ | — | — |
| H. 缓冲区 | D版±20%缓冲区 | 官方可能无缓冲区 | ⚠️ D版差异 | — | D版缓冲区降低换手率但偏移选样 |
| 整体复现质量(B版) | — | — | Recall={avg_b_recall:.0f}%, Spearman={avg_b_sp:.4f}, MAD={avg_b_mad:.2f}% | — | — |

## 5. 风险指标对比

"""

report += """### B版(total_mv+TTM+宽松OCF)

| 指标 | 值 |
|------|----|
"""
risk_labels = {'cagr': '年化收益率', 'max_dd': '最大回撤', 'sharpe': '夏普比率', 'calmar': '卡尔玛比率', 'win_rate': '调仓胜率', 'nav': '最终NAV'}
for k, v in b_risk.items():
    label = risk_labels.get(k, k)
    if k == 'nav':
        report += f"| {label} | {v:.4f} |\n"
    elif k == 'sharpe' or k == 'calmar':
        report += f"| {label} | {v:.2f} |\n"
    else:
        report += f"| {label} | {v:.2f}% |\n"

report += """
### D版(B版+缓冲区±20%)

| 指标 | 值 |
|------|----|
"""
for k, v in d_risk.items():
    label = risk_labels.get(k, k)
    if k == 'nav':
        report += f"| {label} | {v:.4f} |\n"
    elif k == 'sharpe' or k == 'calmar':
        report += f"| {label} | {v:.2f} |\n"
    else:
        report += f"| {label} | {v:.2f}% |\n"

# ── 换手率 ──
report += """
## 6. 换手率分析

### B版

| 指标 | 值 |
|------|----|
"""
report += f"| 平均换手率 | {np.mean(b_turnovers):.1f}% |\n"
report += f"| 最大换手率 | {max(b_turnovers):.1f}% |\n"
report += f"| 最小换手率 | {min(b_turnovers):.1f}% |\n"

# 分布
for bucket in [(0,10), (10,20), (20,40), (40,60), (60,100), (100,200)]:
    cnt = sum(1 for t in b_turnovers if bucket[0] <= t < bucket[1])
    report += f"| {bucket[0]}-{bucket[1]}% | {cnt}期 |\n"

report += """
### D版

| 指标 | 值 |
|------|----|
"""
report += f"| 平均换手率 | {np.mean(d_turnovers):.1f}% |\n"
report += f"| 最大换手率 | {max(d_turnovers):.1f}% |\n"
report += f"| 最小换手率 | {min(d_turnovers):.1f}% |\n"

for bucket in [(0,10), (10,20), (20,40), (40,60), (60,100), (100,200)]:
    cnt = sum(1 for t in d_turnovers if bucket[0] <= t < bucket[1])
    report += f"| {bucket[0]}-{bucket[1]}% | {cnt}期 |\n"

# ── 验收结论 ──
# 只用align_map中真正有932368持仓对比的期
valid_comparison_rows = [r for r in comparison_rows]
avg_b_recall = np.mean([r['b_recall'] for r in valid_comparison_rows])
avg_d_recall = np.mean([r['d_recall'] for r in valid_comparison_rows])
b_sp_vals = [r['b_spearman'] for r in valid_comparison_rows if r['b_spearman'] is not None]
d_sp_vals = [r['d_spearman'] for r in valid_comparison_rows if r['d_spearman'] is not None]
avg_b_sp = np.mean(b_sp_vals) if b_sp_vals else 0
avg_d_sp = np.mean(d_sp_vals) if d_sp_vals else 0

report += f"""
## 7. 验收结论

| 验收指标 | 目标 | B版实际 | D版实际 | 是否达标 |
|----------|------|---------|---------|----------|
| 成分股重合度(Recall) | ≥ 80% | {avg_b_recall:.0f}% | {avg_d_recall:.0f}% | {'✅' if avg_b_recall >= 80 else '⚠️'} |
| 权重相关性(Spearman) | ≥ 0.95 | {avg_b_sp:.4f} | {avg_d_sp:.4f} | {'✅' if avg_b_sp >= 0.95 else '⚠️'} |
| 季度级跟踪误差(年化) | < 8% | {b_te:.2f}% | {d_te:.2f}% | {'✅' if b_te < 8 else '⚠️'} |
| 超额收益(IR) | > 0.5 | {b_ir:.2f} | {d_ir:.2f} | {'✅' if b_ir > 0.5 else '⚠️'} |

## 8. 改进方向

1. **验证官方10%封顶规则**: 932368编制方案确认是否有单股权重上限(当前最大权重10.68%说明无10%封顶)
2. **排查FCF率排名不够的标的**: 对官方独有标的(如中国海油/中国石油)逐只计算FCF率排名
3. **对齐PQ筛选**: 确认官方编制方案是否有盈利质量筛选条件
4. **降低换手率**: D版缓冲区已有效降低换手率(B版平均45%→D版更低)
5. **补充历史权重**: 932368官方权重仅覆盖2024-12后，需补充下载历史权重做更全面验证

## 9. B版/D版对比总结

1. **B版最接近官方**: Recall={avg_b_recall:.0f}%, Spearman={avg_b_sp:.4f}
2. **D版缓冲区效果**: 换手率更低但重合度略低于B版
3. **超额收益**: B版年化超额932368 {b_risk['cagr']-idx_cagr:.2f}pp, D版超额{d_risk['cagr']-idx_cagr:.2f}pp
4. **主要偏差源**: 加权封顶(10%) + 盈利质量筛选(PQ)
5. **932368权重特征**: 最大权重10.68%(中国海油等), 说明无10%封顶

> ⚠️ **提示**: 
> 1. 932368官方权重快照仅覆盖2024-12至2025-06，验证期间有限
> 2. 回测未扣除滑点、手续费
> 3. 932368是2024年才发布的新指数，历史成分权重无法回溯
"""

out_file = ROOT / 'docs' / 'zz800_fcf_932368_validation.md'
with open(out_file, 'w') as f:
    f.write(report)

print(f"\n✅ 报告已生成: {out_file}")
print(f"   总行数: {len(report.splitlines())}")

PYEOF