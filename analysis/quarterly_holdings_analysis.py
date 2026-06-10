#!/usr/bin/env python3
"""
季度持仓分析模板
用法:
  python3 analysis/quarterly_holdings_analysis.py E 2026-03-16
  python3 analysis/quarterly_holdings_analysis.py X 2025-12-15
  python3 analysis/quarterly_holdings_analysis.py B 2024-06-17

输出:
  1. 板块权重分布 vs HS300
  2. 本期板块级收益 & 贡献
  3. HS300 对比 & 归因
  4. 历史分位 & 背景
"""

import sys, json, pandas as pd, numpy as np
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(str(ROOT / '.env'))
import tushare as ts
from compute_nav_cached import get_adj_close_cached

pro = ts.pro_api()

# ═══════════════ 配置 ═══════════════
VER_DIRS = {
    'B': 'output/zz800_fcf_fixed_lenient',
    'D': 'output/zz800_fcf_lenient_buffer',
    'E': 'output/zz800_fcf_lenient_buffer_e40',
    'F': 'output/zz800_fcf_lenient_buffer_f50',
    'X': 'output/zz800_fcf_full_universe',
}

# 申万行业 → 一级板块归并
SECTOR_MAP = {
    '铝':'有色','铜':'有色','黄金':'有色','铅锌':'有色','小金属':'有色','稀土':'有色',
    '普钢':'钢铁','特钢':'钢铁','特种钢':'钢铁',
    '煤炭开采':'煤炭','石油开采':'石油石化','石油加工':'石油石化','石油贸易':'石油石化',
    '电气设备':'电力设备','专用机械':'机械','工程机械':'机械','机械基件':'机械','电器仪表':'电力设备',
    '汽车整车':'汽车','汽车配件':'汽车','摩托车':'汽车',
    '元器件':'电子','半导体':'电子','通信设备':'通信','电信运营':'通信',
    '软件服务':'计算机','IT设备':'计算机',
    '白酒':'食品饮料','啤酒':'食品饮料','乳制品':'食品饮料','食品':'食品饮料','软饮料':'食品饮料',
    '家用电器':'家电',
    '化学制药':'医药','生物制药':'医药','中成药':'医药','医药商业':'医药','医疗保健':'医药',
    '银行':'银行','证券':'非银金融','保险':'非银金融','多元金融':'非银金融',
    '全国地产':'房地产','区域地产':'房地产','房产服务':'房地产',
    '建筑施工':'建筑装饰','装修装饰':'建筑装饰','建筑工程':'建筑装饰',
    '水泥':'建材','玻璃':'建材','其他建材':'建材',
    '水运':'交通运输','港口':'交通运输','路桥':'交通运输','空运':'交通运输','铁路':'交通运输','仓储物流':'交通运输','机场':'交通运输',
    '火力发电':'公用事业','水力发电':'公用事业','供气供热':'公用事业','新型电力':'公用事业','环保':'公用事业',
    '农药化肥':'基础化工','化工原料':'基础化工','染料涂料':'基础化工','塑料':'基础化工','化纤':'基础化工',
    '造纸':'轻工制造','家居用品':'轻工制造','文教休闲':'轻工制造','日用化工':'轻工制造',
    '服饰':'纺织服装','纺织':'纺织服装',
    '种植业':'农林牧渔','渔业':'农林牧渔','饲料':'农林牧渔','农业综合':'农林牧渔',
    '船舶':'国防军工','航空':'国防军工',
    '影视音像':'传媒','出版业':'传媒','广告包装':'传媒','互联网':'传媒',
    '旅游景点':'社会服务','酒店餐饮':'社会服务','旅游服务':'社会服务',
    '商贸代理':'商贸零售','超市连锁':'商贸零售','百货':'商贸零售','商品城':'商贸零售','批发业':'商贸零售','其他商业':'商贸零售',
    '运输设备':'运输设备','综合类':'综合',
}
HS300_KEEP_OUT = {'电子','通信','银行','非银金融'}  # 策略天然不持有的板块

def get_sector(ind):
    return SECTOR_MAP.get(str(ind), str(ind)) if ind and str(ind) != 'nan' else '其他'

# ═══════════════ 1. 加载持仓 ═══════════════
def load_holdings(version, rb_date):
    """加载策略某期持仓，返回 {code: {weight, fcf_yield, rank}}"""
    ver_dir = VER_DIRS.get(version.upper())
    if not ver_dir:
        print(f"未知版本: {version}，可选: {list(VER_DIRS.keys())}")
        sys.exit(1)
    
    basket_path = ROOT / ver_dir / 'all_baskets_2015_2026.json'
    if not basket_path.exists():
        print(f"篮子文件不存在: {basket_path}")
        sys.exit(1)
    
    with open(basket_path) as f:
        baskets = json.load(f)
    
    if rb_date not in baskets:
        dates = sorted(baskets.keys())
        print(f"日期 {rb_date} 不在篮子中，可用日期:")
        for d in dates[-10:]:
            n = len(baskets[d])
            print(f"  {d}: {n}只")
        sys.exit(1)
    
    holdings = {}
    for i, s in enumerate(baskets[rb_date]):
        holdings[s['ts_code']] = {
            'weight': s.get('weight', 0),
            'fcf_yield': s.get('fcf_yield', 0),
            'rank': i + 1,
        }
    return holdings, baskets

def load_hs300():
    """加载最新沪深300成分股及权重"""
    hs300_raw = pd.read_csv(ROOT / 'data/index_weights/index_weight_000300.SH.csv', 
                            dtype={'con_code': str})
    latest_date = hs300_raw['trade_date'].max()
    hs300_latest = hs300_raw[hs300_raw['trade_date'] == latest_date]
    weights = {}
    for _, r in hs300_latest.iterrows():
        weights[r['con_code']] = float(r['weight']) / 100
    tw = sum(weights.values())
    if tw > 0:
        for c in weights: weights[c] /= tw
    return weights, latest_date

# ═══════════════ 2. 行业分类 ═══════════════
def get_industries(codes):
    """批量获取行业分类"""
    info_list = []
    for i in range(0, len(codes), 500):
        batch = codes[i:i+500]
        try:
            df = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry')
            if df is not None and len(df) > 0:
                info_list.append(df)
        except:
            pass
    if info_list:
        df_info = pd.concat(info_list, ignore_index=True)
        return dict(zip(df_info['ts_code'], df_info['industry']))
    return {}

# ═══════════════ 3. 获取期间收益 ═══════════════
def get_period_returns(codes, weights, rb_date, next_rb):
    """获取每只股票在期间内的收益"""
    records = []
    missing = 0
    for code in codes:
        r = get_adj_close_cached(code, rb_date, next_rb, auto_fetch=False)
        if r:
            records.append({
                'code': code,
                'weight': weights.get(code, 0),
                'ret': r[1]/r[0] - 1,
            })
        else:
            missing += 1
    return records, missing

# ═══════════════ 4. 板块聚合 ═══════════════
def aggregate_sectors(records, industry_map):
    """按板块聚合收益和权重"""
    sectors = defaultdict(lambda: {'weight': 0, 'w_ret': 0, 'pos': 0, 'neg': 0, 'stocks': []})
    total_w = sum(r['weight'] for r in records)
    
    for r in records:
        s = get_sector(industry_map.get(r['code'], '其他'))
        sectors[s]['weight'] += r['weight']
        sectors[s]['w_ret'] += r['weight'] * r['ret']
        sectors[s]['stocks'].append(r)
        if r['ret'] > 0:
            sectors[s]['pos'] += 1
        else:
            sectors[s]['neg'] += 1
    
    result = []
    total_ret = 0
    for s, v in sectors.items():
        w = v['weight'] / total_w
        avg_ret = v['w_ret'] / v['weight'] if v['weight'] > 0 else 0
        contrib = v['w_ret'] / total_w
        result.append({
            'sector': s, 'weight': w, 'count': len(v['stocks']),
            'avg_ret': avg_ret, 'contrib': contrib,
            'pos': v['pos'], 'neg': v['neg'],
        })
        total_ret += contrib
    
    return sorted(result, key=lambda x: -x['weight']), total_ret

# ═══════════════ 5. 历史分位 ═══════════════
def historical_context(ret_value):
    """计算给定收益在 HS300 历史中的分位"""
    tr = pd.read_csv(ROOT / 'data/hs300_total_return.csv', 
                     parse_dates=['trade_date']).sort_values('trade_date').set_index('trade_date')
    
    rebalance_dates = [
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
    
    q_returns = []
    for i in range(len(rebalance_dates)-1):
        s, e = rebalance_dates[i], rebalance_dates[i+1]
        try:
            p0 = tr.loc[:s].iloc[-1]['close']
            p1 = tr.loc[:e].iloc[-1]['close']
            q_returns.append((s, (p1/p0-1)*100))
        except:
            pass
    
    q_returns = [(s, r) for s, r in q_returns if s >= '2016-01-01']
    q_returns.sort(key=lambda x: x[1])
    
    worse = sum(1 for _, r in q_returns if r < ret_value)
    pct = worse / len(q_returns) * 100
    
    # Find comparable large drawdowns
    drawdowns = [(s, r) for s, r in q_returns if r < -5]
    
    return pct, worse, len(q_returns), drawdowns

# ═══════════════ MAIN ═══════════════
def main():
    if len(sys.argv) < 3:
        print("用法: python3 analysis/quarterly_holdings_analysis.py <版本> <调仓日>")
        print("示例: python3 analysis/quarterly_holdings_analysis.py E 2026-03-16")
        print("版本: B D E F X")
        sys.exit(1)
    
    version = sys.argv[1].upper()
    rb_date = sys.argv[2]
    
    # --- Load ---
    print(f"\n{'='*70}")
    print(f" {version}版 季度持仓分析 — {rb_date}")
    print(f"{'='*70}")
    
    holdings, all_baskets = load_holdings(version, rb_date)
    dates = sorted(all_baskets.keys())
    idx = dates.index(rb_date)
    next_rb = dates[idx+1] if idx+1 < len(dates) else rb_date
    
    hs300_weights, hs300_date = load_hs300()
    
    print(f"调仓日: {rb_date} → {next_rb}")
    print(f"持仓: {len(holdings)}只 | HS300成分: {len(hs300_weights)}只 ({hs300_date})")
    
    # --- Industries ---
    all_codes = list(set(list(holdings.keys()) + list(hs300_weights.keys())))
    print(f"获取行业分类 ({len(all_codes)}只)...")
    industry_map = get_industries(all_codes)
    print(f"  匹配: {len(industry_map)}/{len(all_codes)}")
    
    # --- Returns ---
    print(f"计算期间收益...")
    e_records, e_miss = get_period_returns(
        list(holdings.keys()),
        {c: holdings[c]['weight'] for c in holdings},
        rb_date, next_rb
    )
    h_records, h_miss = get_period_returns(
        list(hs300_weights.keys()), hs300_weights,
        rb_date, next_rb
    )
    print(f"  {version}版: {len(e_records)}有效(缺{e_miss}) | HS300: {len(h_records)}有效(缺{h_miss})")
    
    # --- Aggregate ---
    e_sec, e_total = aggregate_sectors(e_records, industry_map)
    h_sec, h_total = aggregate_sectors(h_records, industry_map)
    
    # --- Part 1: Sector Weights ---
    print(f"\n{'─'*60}")
    print(f" 一、板块权重：{version}版 vs HS300")
    print(f"{'─'*60}")
    
    all_secs = sorted(set([x['sector'] for x in e_sec] + [x['sector'] for x in h_sec]),
                      key=lambda s: -((sum(x['weight'] for x in e_sec if x['sector']==s) +
                                       sum(x['weight'] for x in h_sec if x['sector']==s))))
    
    print(f"{'板块':<10} {version}版 {'HS300':>7} {'偏差':>7}")
    print("-" * 40)
    for s in all_secs:
        ew = sum(x['weight'] for x in e_sec if x['sector'] == s) * 100
        hw = sum(x['weight'] for x in h_sec if x['sector'] == s) * 100
        d = ew - hw
        tag = '🔥🔥' if d>5 else '🔥' if d>2 else '❄️❄️' if d<-5 else '❄️' if d<-2 else '  '
        if abs(d) >= 1:
            print(f"{s:<10} {ew:>5.1f}% {hw:>6.1f}% {d:>+6.1f}pp {tag}")
    
    # Overlap
    e_codes = set(holdings.keys())
    h_codes = set(hs300_weights.keys())
    overlap = e_codes & h_codes
    e_only = e_codes - h_codes
    print(f"\n在HS300内: {len(overlap)}只 ({sum(holdings[c]['weight'] for c in overlap)*100:.0f}%权重)")
    print(f"在HS300外: {len(e_only)}只 ({sum(holdings[c]['weight'] for c in e_only)*100:.0f}%权重)")
    
    # --- Part 2: Period Returns ---
    print(f"\n{'─'*60}")
    print(f" 二、本期收益 & 贡献：{rb_date} → {next_rb}")
    print(f"{'─'*60}")
    
    print(f"\n{version}版总收益: {e_total*100:+.2f}%  |  HS300总收益: {h_total*100:+.2f}%")
    print(f"\n{'板块':<10} {version}版权重 {'收益':>7} {'贡献':>7} | HS300权重 {'收益':>7} {'贡献':>7} | {'收益差':>7}")
    print("-" * 75)
    
    for s in all_secs:
        e = next((x for x in e_sec if x['sector']==s), None)
        h = next((x for x in h_sec if x['sector']==s), None)
        
        ew = e['weight']*100 if e else 0
        er = e['avg_ret']*100 if e else 0
        ec = e['contrib']*100 if e else 0
        hw = h['weight']*100 if h else 0
        hr = h['avg_ret']*100 if h else 0
        hc = h['contrib']*100 if h else 0
        
        # Only show sectors with meaningful weight or contribution
        if abs(ec) < 0.05 and abs(hc) < 0.05 and ew < 1 and hw < 1:
            continue
        
        bar_e = '█' * min(10, int(abs(ec)*2)) if ec < -0.3 else ''
        bar_h = '█' * min(10, int(abs(hc)*2)) if hc < -0.3 else ''
        
        print(f"{s:<10} {ew:>5.1f}% {er:>+6.1f}% {ec:>+6.2f}% {bar_e}| "
              f"{hw:>5.1f}% {hr:>+6.1f}% {hc:>+6.2f}% {bar_h}| {er-hr:>+6.1f}pp")
    
    # --- Part 3: Attribution ---
    print(f"\n{'─'*60}")
    print(f" 三、差异归因")
    print(f"{'─'*60}")
    
    full_diff = e_total - h_total
    
    # E版不持有但HS300持有的板块
    e_sec_names = {x['sector'] for x in e_sec}
    h_missed = [(x['sector'], x['contrib']) for x in h_sec if x['sector'] not in e_sec_names]
    h_missed = sorted(h_missed, key=lambda x: -abs(x[1]))
    
    if h_missed:
        print(f"\nE版完全不持有的板块（HS300收益贡献）:")
        for s, c in h_missed:
            print(f"  {s:<10}: {c*100:+.2f}pp")
    
    # HS300剔除策略不持有板块后的收益
    keeep_out = HS300_KEEP_OUT & e_sec_names
    keep_out_contrib = sum(x['contrib'] for x in h_sec if x['sector'] in HS300_KEEP_OUT)
    
    excl = [r for r in h_records if get_sector(industry_map.get(r['code'], '其他')) not in HS300_KEEP_OUT]
    if excl:
        excl_weight = sum(r['weight'] for r in excl) / sum(r['weight'] for r in h_records)
        excl_ret = sum(r['weight'] * r['ret'] for r in excl) / sum(r['weight'] for r in excl)
        print(f"\nHS300去掉{HS300_KEEP_OUT}后:")
        print(f"  收益: {excl_ret*100:+.2f}% (剩余{excl_weight*100:.0f}%权重)")
        print(f"  vs {version}版({e_total*100:+.2f}%): 差距 {e_total*100 - excl_ret*100:+.1f}pp")
    
    # Overweight drag
    both = [(x['sector'], 
             (next((y for y in e_sec if y['sector']==x['sector']), None)),
             x)
            for x in h_sec if x['sector'] in e_sec_names]
    
    if both:
        print(f"\n共有板块内 {version}版额外拖累:")
        drags = []
        for s, e, h in both:
            if e['weight'] > 0.01:
                edrag = (e['avg_ret'] - h['avg_ret']) * e['weight']
                drags.append((s, edrag*100, e['avg_ret']*100, h['avg_ret']*100, e['weight']*100))
        drags.sort(key=lambda x: x[1])
        for s, d, er, hr, w in drags[:10]:
            if abs(d) > 0.1:
                print(f"  {s:<10}: {version}版{er:+.1f}% vs HS300{hr:+.1f}% → 额外拖累{d:+.1f}pp (权重{w:.0f}%)")
    
    # --- Part 4: Historical Context ---
    print(f"\n{'─'*60}")
    print(f" 四、历史分位")
    print(f"{'─'*60}")
    
    if excl:
        pct, worse, total, drawdowns = historical_context(excl_ret)
        better = total - worse
        if excl_ret < 0:
            print(f"\n可比口径 (HS300去掉{HS300_KEEP_OUT}): {excl_ret*100:+.2f}%")
            print(f"  处于最差的 {pct:.0f}% (仅 {better} 期更好/{total} 期)")
        else:
            print(f"\n可比口径 (HS300去掉{HS300_KEEP_OUT}): {excl_ret*100:+.2f}%")
            print(f"  超过 {better}/{total} 期")
        if excl_ret < -3 and drawdowns:
            print(f"\n  历史上可比的大跌:")
            for s, r in drawdowns[:8]:
                marker = ' ← 本轮' if abs(r - excl_ret*100) < 0.5 else ''
                print(f"    {s}: {r:+.1f}%{marker}")
    
    pct2, worse2, total2, dd2 = historical_context(e_total)
    better2 = total2 - worse2
    label = f"处于最差的 {pct2:.0f}%" if e_total < 0 else f"超过 {better2}/{total2} 期"
    print(f"\n{version}版实际: {e_total*100:+.2f}% → {label}")
    
    print(f"\n{'='*70}")
    print(f" 分析完成")
    print(f"{'='*70}\n")

if __name__ == '__main__':
    main()
