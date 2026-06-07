"""逐步骤追踪 CSI 300 缺失标的在 pipeline 中的排除点"""
import sys
sys.path.insert(0, '.')
from weekly_harness.fcf_universe import FcfUniverse, _is_financial_or_real_estate
import tushare as ts
from config.settings import tushare_cfg
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

DATE = '2026-03-20'
INDEX = '000300.SH'          # CSI 300
TARGET = '932366.CSI'         # 300 现金流指数实际成分
TOP_N = 50

# ── 加载 ──
uni = FcfUniverse(index_code=INDEX)
uni.preload_all(download=False)

# ── 获取实际 932366 成分 ──
pro = ts.pro_api(tushare_cfg.token)
avail = pro.index_weight(index_code=TARGET, start_date='20260301', end_date='20260401')
avail['trade_date'] = avail['trade_date'].astype(str)
closest = sorted(avail['trade_date'].unique())[0]
actual_df = avail[avail['trade_date'] == closest]
actual_codes = set(actual_df['con_code'].tolist())
aw = dict(zip(actual_df['con_code'], actual_df['weight']))

# ── 获取我们的篮子 ──
our_basket = uni.get_fcf_basket(DATE, top_n=TOP_N, verbose=False)
our_codes = {k for k in our_basket if k != '__quality_warnings__'}

missing = actual_codes - our_codes
print(f'932366 实际={len(actual_codes)}只  我们={len(our_codes)}只  重叠={len(actual_codes & our_codes)}只')
print(f'缺失={len(missing)}只')

# ── 准备基础数据 ──
csi = set(uni._idx_cache.get_constituents(DATE))
info_map = uni._stock_basic.set_index('ts_code').to_dict('index') if uni._stock_basic is not None else {}

# ── 计算成交额排名（复现 _apply_turnover_filter） ──
dt = datetime.strptime(DATE, '%Y-%m-%d')
lookback = dt - timedelta(days=400)
daily_dfs = []
cur = lookback
while cur < dt:
    end = min(cur + timedelta(days=92), dt)
    try:
        df = pro.daily(start_date=cur.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d'),
                       fields='ts_code,trade_date,amount')
        if df is not None and not df.empty:
            daily_dfs.append(df)
    except:
        pass
    cur = end + timedelta(days=1)

daily_all = pd.concat(daily_dfs, ignore_index=True)
daily_all['ts_code'] = daily_all['ts_code'].astype(str)
daily_all['amount'] = pd.to_numeric(daily_all['amount'], errors='coerce')
in_csi = daily_all[daily_all['ts_code'].isin(csi)]
avg_amt = in_csi.groupby('ts_code')['amount'].mean().sort_values(ascending=False)
cutoff_idx = max(1, int(len(avg_amt) * 0.80))
turnover_passed = set(avg_amt.iloc[:cutoff_idx].index.tolist())
cutoff_amt = avg_amt.iloc[cutoff_idx - 1]

# ── 计算 PQ cutoff（全样本空间，含金融地产）──
pq_all = []
for code in csi:
    ry = uni._get_available_report_year(DATE, code)
    fin = uni._fin_cache.get_annual_financials(code, ry)
    o, p, t = fin['oper_cf'], fin['oper_profit'], fin['total_assets']
    if o is not None and p is not None and t is not None and t > 0:
        pq_all.append((o - p) / t)
pq_cutoff = np.percentile(pq_all, 20) if pq_all else float('-inf')

# ── 获取流通市值 ──
dk = DATE.replace('-', '')
b = datetime.strptime(dk, '%Y%m%d')
mv_map = {}
circ_map = {}
for delta in range(6):
    d = (b - timedelta(days=delta)).strftime('%Y%m%d')
    df = pro.daily_basic(trade_date=d, fields='ts_code,total_mv,circ_mv')
    if df is not None and not df.empty:
        mv_map.update(dict(zip(df['ts_code'].astype(str), df['total_mv'])))
        circ_map.update(dict(zip(df['ts_code'].astype(str), df['circ_mv'])))
        break

# ── 逐只诊断 ──
results = {}
for code in sorted(missing, key=lambda x: -float(aw.get(x, 0))):
    w = float(aw.get(code, 0))
    info = info_map.get(code, {})
    name = info.get('name', code)
    industry = str(info.get('industry', ''))

    # Step 1: CSI 成分
    step = 'in_csi'
    if code not in csi:
        results[code] = {'name': name, 'w': w, 'fail_step': 'CSI成分', 'detail': '不在CSI300'}
        continue

    # Step 2: 成交额
    if code not in turnover_passed:
        amt_rank = list(avg_amt.index).index(code) + 1 if code in avg_amt.index else 'N/A'
        results[code] = {'name': name, 'w': w, 'fail_step': '成交额',
                         'detail': f'排名{amt_rank}/{len(avg_amt)}, cutoff={cutoff_amt/1e4:.0f}万'}
        continue

    # Step 3: 行业
    if _is_financial_or_real_estate(industry):
        results[code] = {'name': name, 'w': w, 'fail_step': '金融地产',
                         'detail': f'industry={industry}'}
        continue

    # Step 4: 财务数据 + FCF
    ry = uni._get_available_report_year(DATE, code)
    fin = uni._fin_cache.get_annual_financials(code, ry)
    ocf = fin['oper_cf']
    capex = fin['capex']
    fcf = (ocf or 0) - (capex or 0)
    if fcf is None or fcf <= 0:
        results[code] = {'name': name, 'w': w, 'fail_step': 'FCF≤0',
                         'detail': f'FCF={fcf/1e8:.0f}亿' if fcf else 'FCF=None'}
        continue

    # Step 5: PQ
    op = fin['oper_profit']
    ta = fin['total_assets']
    pq = (ocf - op) / ta if op and ta and ta > 0 else None
    if pq is not None and pq < pq_cutoff:
        results[code] = {'name': name, 'w': w, 'fail_step': 'PQ不足',
                         'detail': f'PQ={pq:.4f} < cutoff={pq_cutoff:.4f}'}
        continue

    # Step 6: 5年OCF
    list_date_str = str(info.get('list_date', ''))
    ocf_start = ry - 4
    try:
        if list_date_str and len(list_date_str) >= 4:
            ocf_start = max(ocf_start, int(list_date_str[:4]))
    except:
        pass
    if not uni._fin_cache.check_5yr_positive_ocf(code, ry, start_year=ocf_start):
        bad = []
        for yr in range(ocf_start, ry + 1):
            f = uni._fin_cache.get_annual_financials(code, yr)
            if f['oper_cf'] is None or f['oper_cf'] <= 0:
                bad.append(str(yr))
        results[code] = {'name': name, 'w': w, 'fail_step': '5年OCF',
                         'detail': f'失败年份:{bad}'}
        continue

    # Step 7: EV
    circ_mv = circ_map.get(code) or mv_map.get(code)
    if circ_mv is None:
        results[code] = {'name': name, 'w': w, 'fail_step': 'EV',
                         'detail': '无市值数据'}
        continue
    ev = circ_mv * 10000 + (fin['total_liab'] or 0) - (fin['money_cap'] or 0)
    if ev <= 0:
        results[code] = {'name': name, 'w': w, 'fail_step': 'EV',
                         'detail': f'EV={ev/1e8:.1f}亿 ≤ 0'}
        continue

    # Step 8: FCF率 vs Top 50 cutoff
    fy = fcf / ev * 100
    # 需要知道 Top 50 cutoff —— 从 wexternal 获取
    our_fy = [v['fcf_yield'] * 100 for k, v in our_basket.items() if k != '__quality_warnings__']
    top50_cutoff = min(our_fy) if our_fy else 0

    if fy < top50_cutoff:
        results[code] = {'name': name, 'w': w, 'fail_step': 'FCF率不足',
                         'detail': f'FCF率={fy:.2f}% < cutoff={top50_cutoff:.2f}%'}
    else:
        results[code] = {'name': name, 'w': w, 'fail_step': '???BUG???',
                         'detail': f'FCF率={fy:.2f}% ≥ cutoff={top50_cutoff:.2f}%, PQ={pq:.4f}, '
                                   f'FCF={fcf/1e8:.0f}亿 EV={ev/1e8:.0f}亿 circMV={circ_mv/10000:.0f}亿'}

# ── 输出 ──
print(f'\n成交额过滤: {len(avg_amt)}只→保留{len(turnover_passed)}只, cutoff={cutoff_amt/1e4:.0f}万/天')
print(f'PQ cutoff={pq_cutoff:.4f} (全样本{len(pq_all)}只)')
print(f'Top50 FCF率 cutoff={min(our_fy) if our_fy else 0:.2f}%')
print()

by_step = {}
for code, r in sorted(results.items(), key=lambda x: -x[1]['w']):
    step = r['fail_step']
    if step not in by_step:
        by_step[step] = []
    by_step[step].append((code, r['name'], r['w'], r['detail']))

for step, items in by_step.items():
    total_w = sum(x[2] for x in items)
    print(f'\n{"="*60}')
    print(f'📌 {step}: {len(items)}只, 丢失权重={total_w:.1f}%')
    print(f'{"="*60}')
    for code, name, w, detail in items:
        print(f'  {code} {name} w={w:.1f}%')
        print(f'    → {detail}')
