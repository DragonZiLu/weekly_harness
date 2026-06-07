#!/usr/bin/env python3
"""
generate_zz800_d_baskets_buffer.py — ZZ800 D版(B版基线+缓冲区±20%)篮子生成

缓冲区规则同HS300 D版: 排名≤40必选入, 41~60缓冲区老成分股优先, >60必剔除
"""
import sys, json, os
from pathlib import Path
import pandas as pd, numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from weekly_harness.fcf_universe import FcfUniverse, INDUSTRY_TO_SECTOR

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "output" / "zz800_fcf_lenient_buffer"
OUT_DIR.mkdir(parents=True, exist_ok=True)

INDEX_CODE = "000906.SH"
TOP_N = 50
BUFFER_RATIO = 0.20

REBALANCE_DATES = [
    "2015-06-12", "2015-09-14", "2015-12-14",
    "2016-03-14", "2016-06-13", "2016-09-12", "2016-12-12",
    "2017-03-13", "2017-06-12", "2017-09-11", "2017-12-11",
    "2018-03-12", "2018-06-11", "2018-09-14", "2018-12-17",
    "2019-03-11", "2019-06-14", "2019-09-16", "2019-12-16",
    "2020-03-13", "2020-06-15", "2020-09-14", "2020-12-14",
    "2021-03-15", "2021-06-11", "2021-09-13", "2021-12-13",
    "2022-03-14", "2022-06-13", "2022-09-12", "2022-12-12",
    "2023-03-13", "2023-06-12", "2023-09-11", "2023-12-11",
    "2024-03-11", "2024-06-17", "2024-09-16", "2024-12-16",
    "2025-03-17", "2025-06-16", "2025-09-15", "2025-12-15",
    "2026-03-16", "2026-06-15",
]


def apply_buffer_zone(ranked_candidates, prev_codes, top_n=50, buffer_ratio=0.20):
    """缓冲区选样: 排名≤40必选入, 41~60缓冲区老成分股优先保留, >60必剔除"""
    low_bound = int(top_n * (1 - buffer_ratio))   # 40
    high_bound = int(top_n * (1 + buffer_ratio))   # 60
    
    must_include = set(c for c, _ in ranked_candidates[:low_bound])
    buffer_zone = [c for c, _ in ranked_candidates[low_bound:high_bound]]
    
    buffer_old = [c for c in buffer_zone if c in prev_codes]
    buffer_new = [c for c in buffer_zone if c not in prev_codes]
    
    selected = list(must_include) + buffer_old
    remaining = top_n - len(selected)
    
    if remaining > 0:
        selected.extend(buffer_new[:remaining])
    
    return selected[:top_n]


def generate_d_baskets():
    print("=" * 60)
    print("ZZ800 D版: B版基线 + 缓冲区选样 (±20%)")
    print("=" * 60)
    
    fcf = FcfUniverse(index_code=INDEX_CODE, strict_ocf=False)
    fcf.preload_all(download=False)
    
    # Phase 1: 批量预计算排名
    print("\nPhase 1: 批量预计算FCF率排名...")
    all_rankings = {}
    for i, rb_date in enumerate(REBALANCE_DATES):
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {rb_date} - 计算排名...")
        basket_raw = fcf.get_fcf_basket(date_str=rb_date, top_n=70, verbose=False, use_ttm=True)
        stocks = [(k, v) for k, v in basket_raw.items() 
                   if k != '__quality_warnings__' and isinstance(v, dict)]
        stocks_sorted = sorted(stocks, key=lambda x: x[1]['fcf_yield'], reverse=True)
        all_rankings[rb_date] = [(k, v['fcf_yield'], v['fcf'], v['ev'], v) for k, v in stocks_sorted]
    
    print(f"  ✅ Phase 1 完成: {len(all_rankings)}期排名数据已缓存")
    
    # Phase 2: 应用缓冲区规则
    print("\nPhase 2: 应用缓冲区规则...")
    all_baskets = {}
    prev_codes = set()
    
    # 加载B版篮子用于对比
    b_data_file = ROOT / "output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json"
    b_all = None
    if b_data_file.exists():
        with open(b_data_file) as f:
            b_all = json.load(f)
    
    for i, rb_date in enumerate(REBALANCE_DATES):
        ranked = all_rankings[rb_date]
        ranked_candidates = [(code, fcf_yield) for code, fcf_yield, _, _, _ in ranked]
        
        if i == 0 or len(prev_codes) == 0:
            selected_codes = [c for c, _ in ranked_candidates[:TOP_N]]
        else:
            selected_codes = apply_buffer_zone(ranked_candidates, prev_codes, TOP_N, BUFFER_RATIO)
        
        info_lookup = {code: (fcf_val, ev_val, info) for code, _, fcf_val, ev_val, info in ranked}
        selected_basket = []
        total_fcf = 0
        
        for code in selected_codes:
            if code in info_lookup:
                fcf_val, ev_val, info = info_lookup[code]
                selected_basket.append({
                    'ts_code': code,
                    'name': info['name'],
                    'industry': info['industry'],
                    'sector': INDUSTRY_TO_SECTOR.get(info['industry'], '其他'),
                    'fcf': fcf_val,
                    'ev': ev_val,
                    'fcf_yield': info['fcf_yield'],
                    'profit_quality': info.get('profit_quality'),
                    'total_mv': info.get('total_mv'),
                    'category': 'FCF精选',
                    'certainty': 'B+',
                    'is_etf': False,
                    'weight': 0,
                })
                total_fcf += fcf_val
        
        if total_fcf > 0 and len(selected_basket) > 0:
            raw_weights = {s['ts_code']: s['fcf'] / total_fcf for s in selected_basket}
            final_weights = fcf._apply_capped_redistribution(raw_weights, cap=0.10, allow_cash=len(selected_basket)*0.10 < 1.0)
            for s in selected_basket:
                s['weight'] = round(final_weights.get(s['ts_code'], 0), 4)
        
        prev_codes = set(selected_codes)
        all_baskets[rb_date] = selected_basket
        
        if b_all and rb_date in b_all:
            b_codes = set(s['ts_code'] for s in b_all[rb_date])
            d_codes = set(selected_codes)
            diff = len(d_codes - b_codes)
            print(f"  {rb_date}: {len(selected_codes)}只, 与B版差异={diff}只")
        else:
            print(f"  {rb_date}: {len(selected_codes)}只")
    
    out_file = OUT_DIR / "all_baskets_2015_2026.json"
    with open(out_file, 'w') as f:
        json.dump(all_baskets, f, ensure_ascii=False, indent=2)
    print(f"\n✅ ZZ800 D版篮子已保存: {out_file}")
    print(f"   期数: {len(all_baskets)}")
    return all_baskets


if __name__ == "__main__":
    generate_d_baskets()