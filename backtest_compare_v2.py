"""回测: ZZ500 + HS300 正确分类 vs 当前策略池 (2015起)"""
import time, re
from pathlib import Path
from collections import defaultdict

import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

import dividend_evaluator
from weekly_harness.backtest import BacktestEngine, StrategyParams

# ==================== 1. 行业→策略分类映射 ====================
# 基于 tushare industry 映射到策略的 5 个分类
def classify_stock(industry_name):
    """根据 tushare 行业名称 → 策略分类"""
    if not industry_name:
        return '周期龙头红利'
    
    ind = industry_name
    
    # 金融红利
    if any(kw in ind for kw in ['银行', '证券', '保险', '多元金融', '信托', '租赁']):
        return '金融红利'
    
    # 消费红利
    if any(kw in ind for kw in ['白酒', '饮料', '食品', '调味品', '乳制品', '啤酒',
                                   '黄酒', '酒',
                                   '家用电器', '家电',
                                   '中药', '中成药', '医药', '医疗', '生物', '制药',
                                   '纺织', '服装', '服饰', '家纺',
                                   '出版', '传媒', '广告', '影视', '游戏',
                                   '汽车', '乘用车', '商用车', '摩托车',
                                   '零售', '超市', '百货', '连锁',
                                   '旅游', '酒店', '餐饮',
                                   '文教', '休闲',
                                   '家居',
                                   '烟草']):
        return '消费红利'
    
    # 基础设施红利
    if any(kw in ind for kw in ['路桥', '高速', '公路', '铁路', '港口', '机场', '航运',
                                   '水运',
                                   '电力', '发电', '电网', '水务', '燃气', '供热',
                                   '环保', '环卫',
                                   '建筑', '工程', '施工', '基建',
                                   '地产',
                                   '物流', '运输', '仓储',
                                   '通信', '电信', '运营']):
        return '基础设施红利'
    
    # 周期资源红利
    if any(kw in ind for kw in ['煤炭', '石油', '石化', '化工', '化纤', '塑料',
                                   '钢铁', '普钢', '特钢', '钢', '有色', '金属', '黄金',
                                   '铅锌', '铝', '铜', '采矿',
                                   '矿物',
                                   '水泥', '玻璃', '造纸',
                                   '农业', '种植', '养殖', '饲料', '化肥', '农药']):
        return '周期资源红利'
    
    # 防御红利
    if any(kw in ind for kw in ['软件', '计算机', '互联网', 'IT',
                                   '半导体', '芯片', '电子',
                                   '建材', '涂料', '染料',
                                   '机械', '设备', '仪器', '仪表',
                                   '电气', '自动化',
                                   '军工', '航天', '航空',
                                   '新材料']):
        return '防御红利'
    
    return '周期龙头红利'

# ==================== 2. 构建分类股票池 ====================
def build_classified_pool(index_code, index_name):
    codes = list(set(pro.index_weight(index_code=index_code, trade_date='20260529')['con_code'].tolist()))
    
    # 获取行业
    name_map = {}
    for i in range(0, len(codes), 200):
        batch = codes[i:i+200]
        sb = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry')
        for _, r in sb.iterrows():
            name_map[r['ts_code']] = (r['name'], r.get('industry', '') or '')
    
    pool = defaultdict(dict)
    cat_counts = defaultdict(int)
    for code in codes:
        name, industry = name_map.get(code, (code[:6], ''))
        cat = classify_stock(industry)
        pool[cat][name] = {
            'ts_code': code, 'category': cat,
            'certainty': 'B', 'moat': '', 'comment': f'[{index_name}] {industry}'
        }
        cat_counts[cat] += 1
    
    result = dict(pool)
    print(f'  {index_name}:')
    for cat, cnt in sorted(cat_counts.items()):
        print(f'    {cat}: {cnt}')
    return result

def run_one(pool_dict, label, out_dir_path):
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
        print(f'    ✅ {label}')
        return results
    finally:
        dividend_evaluator.COMPANIES = original_companies

def extract_metrics(path):
    m = {}
    if not Path(path).exists(): return m
    with open(path) as f:
        c = f.read()
    for key, pat in [
        ('total', r'总收益率.*?\*\*([+-]?[\d.]+)%\*\*'),
        ('annual', r'年化收益率.*?\*\*([+-]?[\d.]+)%\*\*'),
        ('dd', r'最大回撤.*?\|\s+\*\*([+-]?[\d.]+)%\*\*'),
        ('dd2', r'最大回撤.*?([+-]?[\d.]+)%'),
        ('sharpe', r'夏普比率.*?\*\*([\d.]+)\*\*'),
    ]:
        m2 = re.search(pat, c)
        if m2: m[key] = float(m2.group(1))
    return m

# ==================== 主流程 ====================
print('=' * 60)
print('  三池对比: ZZ500 vs 精选32 vs HS300 (行业正确分类)')
print('=' * 60)

# 1. 中证500
print('\n[1/2] 构建中证500池(5类分类)...')
pool_zz500 = build_classified_pool('000905.SH', 'ZZ500')
t0 = time.time()
print('  回测中...')
run_one(pool_zz500, 'ZZ500', 'data/backtest_zz500_v3')
print(f'  ⏱️ {time.time()-t0:.0f}s')

# 2. 沪深300
print('\n[2/2] 构建沪深300池(5类分类)...')
pool_hs300 = build_classified_pool('000300.SH', 'HS300')
t0 = time.time()
print('  回测中...')
run_one(pool_hs300, 'HS300', 'data/backtest_hs300_v3')
print(f'  ⏱️ {time.time()-t0:.0f}s')

# ==================== 对比 ====================
print('\n' + '=' * 60)
print('  三池对比结果')
print('=' * 60)

zz500_m = extract_metrics('data/backtest_zz500_v3/backtest_report.md')
cur_m = extract_metrics('data/backtest/backtest_report.md')
hs300_m = extract_metrics('data/backtest_hs300_v3/backtest_report.md')

# dd fallback
for m in [zz500_m, cur_m, hs300_m]:
    if 'dd2' in m and 'dd' not in m:
        m['dd'] = m['dd2']

print(f"\n{'指标':14s} {'中证500(500只)':>14s} {'精选池(32只)':>14s} {'沪深300(300只)':>14s}")
print('-' * 62)
for key, label in [
    ('total', '总收益率(%)'), ('annual', '年化收益(%)'),
    ('dd', '最大回撤(%)'), ('sharpe', '夏普比率'),
]:
    v1 = zz500_m.get(key, 0); v2 = cur_m.get(key, 0); v3 = hs300_m.get(key, 0)
    best_val = max(v for v in (v1, v2, v3) if v != 0)
    s1 = f"**{v1:.2f}**" if v1 == best_val else f"{v1:.2f}"
    s2 = f"**{v2:.2f}**" if v2 == best_val else f"{v2:.2f}"
    s3 = f"**{v3:.2f}**" if v3 == best_val else f"{v3:.2f}"
    print(f'{label:14s} {s1:>14s} {s2:>14s} {s3:>14s}')
