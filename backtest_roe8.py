"""回测: HS300+ZZ500 + 每季度动态 3Y ROE≥8% 筛选"""
import json, time, re
from pathlib import Path
from collections import defaultdict

import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

import dividend_evaluator
from weekly_harness.backtest import BacktestEngine, StrategyParams

# ==================== 1. 加载 ROE eligibility (季度对齐版) ====================
print('加载 ROE≥8% eligibility...')
with open('/tmp/eligibility_roe8_q.json') as f:
    eligibility = json.load(f)  # {ts_code: {date_str(YYYY-MM-DD): bool}}

with open('/tmp/q_dates_engine.json') as f:
    q_dates = json.load(f)

# Count eligible quarters per stock
quarter_counts = {c: sum(1 for v in ds.values() if v) for c, ds in eligibility.items()}
print(f'  {len(eligibility)} 只股票, {len(q_dates)} 个季度')

# Only include stocks eligible in ≥10 quarters (less noisy, faster)
MIN_QUARTERS = 10
active_codes = [c for c, cnt in quarter_counts.items() if cnt >= MIN_QUARTERS]
print(f'  活跃 (≥{MIN_QUARTERS}季度): {len(active_codes)} 只')

# ==================== 2. 构建 HS300+ZZ500 COMPANIES ====================
print('\n构建 HS300+ZZ500 股票池...')
hs300 = list(set(pro.index_weight(index_code='000300.SH', trade_date='20260529')['con_code'].tolist()))
zz500 = list(set(pro.index_weight(index_code='000905.SH', trade_date='20260529')['con_code'].tolist()))
all_codes = list(set(hs300 + zz500))
print(f'  HS300: {len(hs300)}, ZZ500: {len(zz500)}')

# Intersect with active ROE-eligible codes
codes_with_roe = [c for c in all_codes if c in active_codes]
print(f'  HS300+ZZ500 且 ROE活跃: {len(codes_with_roe)}')

# 获取名称(分批)
name_map = {}
for i in range(0, len(codes_with_roe), 200):
    batch = codes_with_roe[i:i+200]
    sb = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry')
    for _, r in sb.iterrows():
        name_map[r['ts_code']] = (r['name'], r['industry'])

# 按行业分组
pool = defaultdict(dict)
for code in codes_with_roe:
    name, industry = name_map.get(code, (code[:6], '其他'))
    ind = (industry or '其他')[:12]
    pool[ind][name] = {
        'ts_code': code,
        'category': '周期龙头红利',
        'certainty': 'B',
        'moat': '',
        'comment': ''
    }

COMPANIES_ROE8 = dict(pool)
total_stocks = sum(len(v) for v in COMPANIES_ROE8.values())
print(f'  股票池: {total_stocks} 只, {len(COMPANIES_ROE8)} 个行业')

# ==================== 3. Monkey-patch 评分方法 ====================
original_simulate = BacktestEngine._simulate_simple_scores

def filtered_simulate(self, date_str):
    """带 ROE≥8% 过滤的评分模拟"""
    scores = {}
    bond_yield = self._get_bond_yield_at_date(date_str)

    for ts_code, meta in self._stock_meta.items():
        # ── ROE≥8% 过滤 ──
        if ts_code in eligibility:
            el = eligibility[ts_code].get(date_str)
            if el is None or not el:
                continue  # ROE 不达标或无数据，跳过
        else:
            continue  # 不在 eligibility 中，跳过

        price = self._get_price_on_date(ts_code, date_str)
        if not price or price <= 0:
            continue

        trailing_dps = self._get_trailing_dps_at_date(ts_code, date_str)
        if trailing_dps > 0 and price > 0:
            sim_div_yield = trailing_dps / price * 100
        else:
            continue

        # 行业阈值
        sector = meta.get("sector", "")
        category = meta["category"]

        th = {'watch': 2.0, 'buy': 3.0, 'full': 4.5}
        for kw, t in [
            ('银行', (3.5, 4.5, 6.0)),
            ('煤炭', (3.0, 4.0, 5.5)),
            ('石油', (3.0, 4.0, 5.0)),
            ('海运', (3.0, 4.0, 5.5)),
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

        s2 = 12  # default div stability
        s3 = 12  # default financial health
        s4 = 8   # default valuation
        s5 = 8   # default growth

        total = min(s1 + s2 + s3 + s4 + s5, 100)

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
            'score_source': '模拟评分(ROE≥8%)',
        }

    return scores

BacktestEngine._simulate_simple_scores = filtered_simulate

# ==================== 4. 运行回测 ====================
print('\n' + '=' * 60)
print('  回测: HS300+ZZ500 + 3年ROE≥8% 动态筛选')
print('  每季度 eligible: ~430 只')
print('=' * 60)

original_companies = dividend_evaluator.COMPANIES
dividend_evaluator.COMPANIES = COMPANIES_ROE8

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

    out_dir = Path('data/backtest_roe8')
    engine.generate_backtest_report(results, output_dir=out_dir)

    elapsed = time.time() - t0
    print(f'\n  ⏱️ 耗时: {elapsed:.0f}s')

finally:
    dividend_evaluator.COMPANIES = original_companies
    BacktestEngine._simulate_simple_scores = original_simulate

# ==================== 5. 对比 ====================
print('\n' + '=' * 60)
print('  对比: 当前策略池(32只) vs ROE≥8%筛选池')
print('=' * 60)

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
    ]:
        m2 = re.search(pat, c)
        if m2:
            m[key] = float(m2.group(1))
    return m

old_m = extract_metrics('data/backtest/backtest_report.md')
new_m = extract_metrics('data/backtest_roe8/backtest_report.md')

print(f"\n{'指标':16s} {'当前池(32只)':>12s} {'ROE≥8%池':>12s} {'变化':>10s}")
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
