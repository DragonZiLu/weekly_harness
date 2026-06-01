"""回测对比: 中证500 vs 沪深300 vs 当前策略池 (2015起)"""
import time, re
from pathlib import Path
from collections import defaultdict

import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

import dividend_evaluator
from weekly_harness.backtest import BacktestEngine, StrategyParams

def build_companies(index_code, index_name):
    """构建指数成分股池"""
    codes = list(set(pro.index_weight(index_code=index_code, trade_date='20260529')['con_code'].tolist()))
    
    # 获取名称
    name_map = {}
    for i in range(0, len(codes), 200):
        batch = codes[i:i+200]
        sb = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry')
        for _, r in sb.iterrows():
            name_map[r['ts_code']] = (r['name'], r['industry'])
    
    pool = defaultdict(dict)
    for code in codes:
        name, industry = name_map.get(code, (code[:6], '其他'))
        ind = (industry or '其他')[:12]
        pool[ind][name] = {
            'ts_code': code, 'category': '周期龙头红利',
            'certainty': 'B', 'moat': '', 'comment': ''
        }
    result = dict(pool)
    total = sum(len(v) for v in result.values())
    print(f'  {index_name}: {total} 只, {len(result)} 个行业')
    return result, total

def run_one(pool_dict, label, out_dir_path):
    """执行单次回测"""
    original_companies = dividend_evaluator.COMPANIES
    dividend_evaluator.COMPANIES = pool_dict
    
    try:
        params = StrategyParams()
        params.max_positions = 15
        
        engine = BacktestEngine(
            strategy_params=params, initial_cash=100_0000,
            commission_rate=0.001, slippage=0.001,
            rebalance_freq='quarterly', use_forward_yield=False,
        )
        results = engine.run(start_date='2015-01-01', end_date='2026-05-30',
                           benchmark_code='000300.SH', verbose=False)
        
        out_dir = Path(out_dir_path)
        engine.generate_backtest_report(results, output_dir=out_dir)
        print(f'    ✅ {label} 完成')
        return results
        
    finally:
        dividend_evaluator.COMPANIES = original_companies

def extract_metrics(path):
    m = {}
    if not Path(path).exists():
        return m
    with open(path) as f:
        c = f.read()
    for key, pat in [
        ('total', r'总收益率.*?([+-]?[\d.]+)%'), ('annual', r'年化收益率.*?([+-]?[\d.]+)%'),
        ('dd', r'最大回撤.*?([+-]?[\d.]+)%'), ('sharpe', r'夏普比率.*?([\d.]+)'),
        ('excess', r'超额沪深300.*?([+-]?[\d.]+)%'),
        ('dividend_ret', r'累计股息收益.*?([+-]?[\d.]+)%'),
    ]:
        m2 = re.search(pat, c)
        if m2: m[key] = float(m2.group(1))
    return m

# ==================== 主流程 ====================
print('=' * 60)
print('  三池对比: ZZ500 vs 精选32 vs HS300')
print('=' * 60)

# 1. 中证500
print('\n[1/2] 构建中证500池...')
pool_zz500, n_zz500 = build_companies('000905.SH', '中证500')
t0 = time.time()
print('  回测中...')
run_one(pool_zz500, '中证500', 'data/backtest_zz500_v2')
print(f'  ⏱️ {time.time()-t0:.0f}s')

# 2. 沪深300
print('\n[2/2] 构建沪深300池...')
pool_hs300, n_hs300 = build_companies('000300.SH', '沪深300')
t0 = time.time()
print('  回测中...')
run_one(pool_hs300, '沪深300', 'data/backtest_hs300_v2')
print(f'  ⏱️ {time.time()-t0:.0f}s')

# 3. 当前策略池 (已有数据)
print('\n[3] 当前策略池 (已有)')

# ==================== 比较 ====================
print('\n' + '=' * 60)
print('  三池对比结果')
print('=' * 60)

zz500_m = extract_metrics('data/backtest_zz500_v2/backtest_report.md')
cur_m = extract_metrics('data/backtest/backtest_report.md')
hs300_m = extract_metrics('data/backtest_hs300_v2/backtest_report.md')

print(f"\n{'指标':14s} {'中证500(500只)':>14s} {'精选池(32只)':>14s} {'沪深300(300只)':>14s}")
print('-' * 62)
best = lambda *vals: max((v for v in vals if v), default=0)
for key, label in [
    ('total', '总收益率(%)'), ('annual', '年化收益(%)'),
    ('dd', '最大回撤(%)'), ('sharpe', '夏普比率'),
    ('excess', '超额沪深300(%)'),
]:
    v1 = zz500_m.get(key, 0); v2 = cur_m.get(key, 0); v3 = hs300_m.get(key, 0)
    # Highlight best
    best_val = best(v1, v2, v3)
    s1 = f"**{v1:.2f}**" if v1 == best_val else f"{v1:.2f}"
    s2 = f"**{v2:.2f}**" if v2 == best_val else f"{v2:.2f}"
    s3 = f"**{v3:.2f}**" if v3 == best_val else f"{v3:.2f}"
    print(f'{label:14s} {s1:>14s} {s2:>14s} {s3:>14s}')
