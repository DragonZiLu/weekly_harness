"""回测: HS300+ZZ500 全部800只 vs 当前策略池 无任何约束"""
import time, re
from pathlib import Path
from collections import defaultdict

import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

import dividend_evaluator
from weekly_harness.backtest import BacktestEngine, StrategyParams

# ==================== 1. 构建 HS300+ZZ500 全部 COMPANIES ====================
print('构建 HS300+ZZ500 全量股票池...')
hs300 = list(set(pro.index_weight(index_code='000300.SH', trade_date='20260529')['con_code'].tolist()))
zz500 = list(set(pro.index_weight(index_code='000905.SH', trade_date='20260529')['con_code'].tolist()))
all_codes = list(set(hs300 + zz500))
print(f'  HS300: {len(hs300)}, ZZ500: {len(zz500)}, 去重: {len(all_codes)}')

# 分批获取名称
name_map = {}
for i in range(0, len(all_codes), 200):
    batch = all_codes[i:i+200]
    sb = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry')
    for _, r in sb.iterrows():
        name_map[r['ts_code']] = (r['name'], r['industry'])

pool = defaultdict(dict)
for code in all_codes:
    name, industry = name_map.get(code, (code[:6], '其他'))
    ind = (industry or '其他')[:12]
    pool[ind][name] = {
        'ts_code': code,
        'category': '周期龙头红利',
        'certainty': 'B',
        'moat': '',
        'comment': ''
    }

COMPANIES_ALL = dict(pool)
total = sum(len(v) for v in COMPANIES_ALL.values())
print(f'  股票池: {total} 只, {len(COMPANIES_ALL)} 个行业')

# ==================== 2. 简化评分（纯股息率，无ROE约束）====================
original_simulate = BacktestEngine._simulate_simple_scores

def simple_scores(self, date_str):
    """纯股息率评分，无任何约束"""
    scores = {}
    bond_yield = self._get_bond_yield_at_date(date_str)

    for ts_code, meta in self._stock_meta.items():
        price = self._get_price_on_date(ts_code, date_str)
        if not price or price <= 0:
            continue

        trailing_dps = self._get_trailing_dps_at_date(ts_code, date_str)
        if trailing_dps > 0 and price > 0:
            sim_div_yield = trailing_dps / price * 100
        else:
            continue

        sector = meta.get("sector", "")
        category = meta["category"]

        th = {'watch': 2.0, 'buy': 3.0, 'full': 4.5}
        for kw, t in [
            ('银行', (3.5, 4.5, 6.0)), ('煤炭', (3.0, 4.0, 5.5)),
            ('石油', (3.0, 4.0, 5.0)), ('海运', (3.0, 4.0, 5.5)),
            ('中药', (2.5, 3.5, 5.0)),
        ]:
            if kw in sector:
                th = {'watch': t[0], 'buy': t[1], 'full': t[2]}
                break

        if sim_div_yield >= th['full']:
            s1 = 30
        elif sim_div_yield >= th['buy']:
            s1 = 20 + 10 * (sim_div_yield - th['buy']) / max(th['full'] - th['buy'], 0.01)
        elif sim_div_yield >= th['watch']:
            s1 = 10 + 10 * (sim_div_yield - th['watch']) / max(th['buy'] - th['watch'], 0.01)
        else:
            s1 = max(0, 10 * sim_div_yield / th['watch'])

        total = min(s1 + 12 + 12 + 8 + 8, 100)

        scores[ts_code] = {
            'name': meta.get('name', ts_code),
            'category': category, 'sector': sector,
            'total_score': total,
            'verdict': '🔥 大胆攒股' if total >= 75 else ('✅ 积极布局' if total >= 65 else ('👀 观察等待' if total >= 50 else '🚫 回避')),
            'div_yield': sim_div_yield, 'close': price,
            'pe_ttm': 0, 'roe': 0,
            'bond_spread_bp': (sim_div_yield - bond_yield) * 100,
            'score_source': '纯股息率评分(无约束)',
        }
    return scores

BacktestEngine._simulate_simple_scores = simple_scores

# ==================== 3. 运行回测 ====================
print('\n' + '=' * 60)
print('  回测: HS300+ZZ500 全池(800只) 无ROE约束')
print('=' * 60)

original_companies = dividend_evaluator.COMPANIES
dividend_evaluator.COMPANIES = COMPANIES_ALL

try:
    t0 = time.time()
    params = StrategyParams()
    params.max_positions = 15

    engine = BacktestEngine(
        strategy_params=params, initial_cash=100_0000,
        commission_rate=0.001, slippage=0.001,
        rebalance_freq='quarterly', use_forward_yield=False,
    )
    results = engine.run(start_date='2015-01-01', end_date='2026-05-30', benchmark_code='000300.SH')

    out_dir = Path('data/backtest_all800')
    engine.generate_backtest_report(results, output_dir=out_dir)
    print(f'\n  ⏱️ 耗时: {time.time()-t0:.0f}s')

finally:
    dividend_evaluator.COMPANIES = original_companies
    BacktestEngine._simulate_simple_scores = original_simulate

# ==================== 4. 对比 ====================
print('\n' + '=' * 60)
print('  对比: 当前策略池(32只) vs 全池无约束(800只)')
print('=' * 60)

def extract_metrics(path):
    m = {}
    with open(path) as f:
        c = f.read()
    for key, pat in [
        ('total', r'总收益率.*?([+-]?[\d.]+)%'), ('annual', r'年化收益率.*?([+-]?[\d.]+)%'),
        ('dd', r'最大回撤.*?([+-]?[\d.]+)%'), ('sharpe', r'夏普比率.*?([\d.]+)'),
        ('excess', r'超额沪深300.*?([+-]?[\d.]+)%'),
    ]:
        m2 = re.search(pat, c)
        if m2: m[key] = float(m2.group(1))
    return m

old_m = extract_metrics('data/backtest/backtest_report.md')
new_m = extract_metrics('data/backtest_all800/backtest_report.md')

print(f"\n{'指标':16s} {'当前池(32只)':>12s} {'全池(800只)':>12s} {'变化':>10s}")
print('-' * 55)
for key, label in [
    ('total', '总收益率(%)'), ('annual', '年化收益(%)'),
    ('dd', '最大回撤(%)'), ('sharpe', '夏普比率'),
    ('excess', '超额沪深300(%)'),
]:
    v1 = old_m.get(key, 0)
    v2 = new_m.get(key, 0)
    print(f'{label:16s} {v1:12.2f} {v2:12.2f} {v2-v1:+10.2f}')

print(f"\n报告: {out_dir}/backtest_report.md")
