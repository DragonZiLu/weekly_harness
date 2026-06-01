"""回测: HS300+ZZ500 池 + 每季度动态 ROE≥8% & PE≤25 筛选"""
import json, time, os
from pathlib import Path
from collections import defaultdict

import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

import dividend_evaluator
from weekly_harness.backtest import BacktestEngine, StrategyParams

# ==================== 1. 加载 eligibility 数据 ====================
print('加载 eligibility...')
with open('/tmp/eligibility.json') as f:
    eligibility = json.load(f)  # {ts_code: {date_str: bool}}

with open('/tmp/q_dates.json') as f:
    all_q_dates = json.load(f)

print(f'  {len(eligibility)} 只股票, {len(all_q_dates)} 个交易日')

# ==================== 2. 构建 COMPANIES (800只) ====================
print('构建股票池...')
# 获取名称和行业
hs300 = list(set(pro.index_weight(index_code='000300.SH', trade_date='20260529')['con_code'].tolist()))
zz500 = list(set(pro.index_weight(index_code='000905.SH', trade_date='20260529')['con_code'].tolist()))
all_codes = list(set(hs300 + zz500))

sb = pro.stock_basic(ts_code=','.join(all_codes), fields='ts_code,name,industry')
name_map = {r['ts_code']: (r['name'], r['industry']) for _, r in sb.iterrows()}

# Group by industry
pool = defaultdict(dict)
for code in all_codes:
    name, industry = name_map.get(code, (code, '其他'))
    # Simplify industry name
    ind = industry[:8] if industry else '其他'
    pool[ind][name] = {
        'ts_code': code,
        'category': '周期龙头红利',  # generic
        'certainty': 'B',
        'moat': '',
        'comment': ''
    }

# Convert to regular dict
COMPANIES_FILTERED = dict(pool)
total = sum(len(v) for v in COMPANIES_FILTERED.values())
print(f'  股票池: {total} 只, {len(COMPANIES_FILTERED)} 个行业')

# ==================== 3. Monkey-patch 回测引擎 ====================
original_simulate = BacktestEngine._simulate_simple_scores

def filtered_simulate(self, date_str):
    """带 ROE+PE 过滤的评分模拟"""
    scores = {}
    bond_yield = self._get_bond_yield_at_date(date_str)
    
    for ts_code, meta in self._stock_meta.items():
        # ── ROE+PE 过滤 ──
        if ts_code in eligibility and date_str in eligibility[ts_code]:
            if not eligibility[ts_code][date_str]:
                continue  # 不满足条件，跳过
        else:
            # 无eligibility数据，保守跳过
            continue
        
        price = self._get_price_on_date(ts_code, date_str)
        category = meta["category"]
        sector = meta.get("sector", "")
        
        if not price or price <= 0:
            continue
        
        trailing_dps = self._get_trailing_dps_at_date(ts_code, date_str)
        if trailing_dps > 0 and price > 0:
            sim_div_yield = trailing_dps / price * 100
        else:
            # 无分红数据，跳过
            continue
        
        # 简化评分 (复用原引擎逻辑的核心)
        # 行业阈值
        th = {'watch': 2.0, 'buy': 3.0, 'full': 4.5}
        sector_name = sector
        for kw, t in [('银行', (3.5, 4.5, 6.0)), ('煤炭', (3.0, 4.0, 5.5)),
                        ('石油', (3.0, 4.0, 5.0)), ('海运', (3.0, 4.0, 5.5)),
                        ('中药', (2.5, 3.5, 5.0))]:
            if kw in sector_name:
                th = {'watch': t[0], 'buy': t[1], 'full': t[2]}
                break
        
        # 评分
        if sim_div_yield >= th['full']:
            s1 = 30
        elif sim_div_yield >= th['buy']:
            s1 = 20 + 10 * (sim_div_yield - th['buy']) / max(th['full'] - th['buy'], 0.01)
        elif sim_div_yield >= th['watch']:
            s1 = 10 + 10 * (sim_div_yield - th['watch']) / max(th['buy'] - th['watch'], 0.01)
        else:
            s1 = max(0, 10 * sim_div_yield / th['watch'])
        
        s2 = 12  # default div stability
        s3 = 12  # default financial health
        s4 = 8   # default valuation
        s5 = 8   # default growth
        
        total = s1 + s2 + s3 + s4 + s5
        total = min(total, 100)
        
        if total >= 75:
            verdict = '🔥 大胆攒股'
        elif total >= 65:
            verdict = '✅ 积极布局'
        elif total >= 50:
            verdict = '👀 观察等待'
        else:
            verdict = '🚫 回避'
        
        scores[ts_code] = {
            'name': meta.get('name', ts_code),
            'category': category,
            'sector': sector,
            'total_score': total,
            'verdict': verdict,
            'div_yield': sim_div_yield,
            'close': price,
            'pe_ttm': 0,
            'roe': 0,
            'bond_spread_bp': (sim_div_yield - bond_yield) * 100,
            'score_source': '模拟评分(ROE+PE筛选)',
        }
    
    return scores

# Apply monkey-patch
BacktestEngine._simulate_simple_scores = filtered_simulate

# ==================== 4. 运行回测 ====================
print('\n' + '='*60)
print('  回测: HS300+ZZ500 + ROE≥8% + PE≤25')
print('='*60)

original_companies = dividend_evaluator.COMPANIES
dividend_evaluator.COMPANIES = COMPANIES_FILTERED

try:
    t0 = time.time()
    params = StrategyParams()
    params.max_positions = 15
    
    engine = BacktestEngine(
        strategy_params=params,
        initial_cash=100_0000,
        commission_rate=0.001,
        slippage=0.001,
        rebalance_freq='quarterly',
        use_forward_yield=False,
    )
    
    results = engine.run(
        start_date='2015-01-01',
        end_date='2026-05-30',
        benchmark_code='000300.SH',
    )
    
    out_dir = Path('data/backtest_filtered')
    engine.generate_backtest_report(results, output_dir=out_dir)
    
    elapsed = time.time() - t0
    print(f'\n  ⏱️ 耗时: {elapsed:.0f}s')
    
finally:
    dividend_evaluator.COMPANIES = original_companies
    BacktestEngine._simulate_simple_scores = original_simulate

# ==================== 5. 对比当前策略池 ====================
print('\n' + '='*60)
print('  对比: 当前策略池(32只) vs ROE+PE筛选池(800只动态过滤)')
print('='*60)

import re

def extract_metrics(path):
    m = {}
    with open(path) as f:
        c = f.read()
    for key, pat in [
        ('total', r'总收益率.*?([+-]?[\d.]+)%'),
        ('annual', r'年化收益率.*?([+-]?[\d.]+)%'),
        ('dd', r'最大回撤.*?([+-]?[\d.]+)%'),
        ('sharpe', r'夏普比率.*?([\d.]+)'),
        ('excess', r'超额沪深300.*?([+-]?[\d.]+)%'),
        ('trades', r'交易次数.*?(\d+)'),
    ]:
        m2 = re.search(pat, c)
        if m2:
            m[key] = float(m2.group(1))
    return m

old_m = extract_metrics('data/backtest/backtest_report.md')
new_m = extract_metrics('data/backtest_filtered/backtest_report.md')

print(f"\n{'指标':16s} {'当前池(32只)':>12s} {'ROE+PE筛选':>12s} {'变化':>10s}")
print('-' * 55)
for key, label in [
    ('total', '总收益率(%)'), ('annual', '年化收益(%)'),
    ('dd', '最大回撤(%)'), ('sharpe', '夏普比率'),
    ('excess', '超额沪深300(%)'), ('trades', '交易次数'),
]:
    v1 = old_m.get(key, 0)
    v2 = new_m.get(key, 0)
    print(f'{label:16s} {v1:12.2f} {v2:12.2f} {v2-v1:+10.2f}')

print(f"\n报告: {out_dir}/backtest_report.md")
