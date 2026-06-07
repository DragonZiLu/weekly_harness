#!/usr/bin/env python3
"""
generate_zz800_baskets.py — 生成ZZ800 B版和D版篮子
复用FcfUniverse, Phase1预计算排名+Phase2应用缓冲区规则
"""
import sys, json, os
from pathlib import Path
import pandas as pd, numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from weekly_harness.fcf_universe import FcfUniverse, INDUSTRY_TO_SECTOR

ROOT = Path(__file__).parent

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
    low_bound = int(top_n * (1 - buffer_ratio))
    high_bound = int(top_n * (1 + buffer_ratio))
    must_include = set(c for c, _ in ranked_candidates[:low_bound])
    buffer_zone = [c for c, _ in ranked_candidates[low_bound:high_bound]]
    buffer_old = [c for c in buffer_zone if c in prev_codes]
    buffer_new = [c for c in buffer_zone if c not in prev_codes]
    selected = list(must_include) + buffer_old
    remaining = top_n - len(selected)
    if remaining > 0:
        selected.extend(buffer_new[:remaining])
    return selected[:top_n]


def build_basket_from_ranking(selected_codes, all_rankings, rb_date, fcf):
    """从排名数据构建篮子(含权重)"""
    ranked = all_rankings[rb_date]
    info_lookup = {code: (fcf_val, ev_val, info) for code, _, fcf_val, ev_val, info in ranked}
    selected_basket = []
    total_fcf = 0
    
    for code in selected_codes:
        if code in info_lookup:
            fcf_val, ev_val, info = info_lookup[code]
            selected_basket.append({
                'ts_code': code, 'name': info['name'], 'industry': info['industry'],
                'sector': INDUSTRY_TO_SECTOR.get(info['industry'], '其他'),
                'fcf': fcf_val, 'ev': ev_val, 'fcf_yield': info['fcf_yield'],
                'profit_quality': info.get('profit_quality'), 'total_mv': info.get('total_mv'),
                'category': 'FCF精选', 'certainty': 'B+', 'is_etf': False, 'weight': 0,
            })
            total_fcf += fcf_val
    
    if total_fcf > 0 and len(selected_basket) > 0:
        raw_weights = {s['ts_code']: s['fcf'] / total_fcf for s in selected_basket}
        final_weights = fcf._apply_capped_redistribution(
            raw_weights, cap=0.10, allow_cash=len(selected_basket)*0.10 < 1.0)
        for s in selected_basket:
            s['weight'] = round(final_weights.get(s['ts_code'], 0), 4)
    
    return selected_basket


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', choices=['B', 'D', 'both'], default='both')
    args = parser.parse_args()
    
    print("=" * 60)
    print("ZZ800 篮子生成 (B版 + D版缓冲区)")
    print("=" * 60)
    
    fcf = FcfUniverse(index_code='000906.SH', strict_ocf=False)
    fcf.preload_all(download=False)
    
    # Phase 1: 批量预计算排名(一次性)
    print("\nPhase 1: 预计算FCF率排名...")
    all_rankings = {}
    for i, rb_date in enumerate(REBALANCE_DATES):
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {rb_date}...", end=" ", flush=True)
        basket_raw = fcf.get_fcf_basket(date_str=rb_date, top_n=70, verbose=False, use_ttm=True)
        stocks = [(k, v) for k, v in basket_raw.items() 
                   if k != '__quality_warnings__' and isinstance(v, dict)]
        stocks_sorted = sorted(stocks, key=lambda x: x[1]['fcf_yield'], reverse=True)
        all_rankings[rb_date] = [(k, v['fcf_yield'], v['fcf'], v['ev'], v) for k, v in stocks_sorted]
        print(f"{len(stocks_sorted)}只")
    
    print(f"  ✅ Phase 1 完成")
    
    # Phase 2: B版(纯Top50)
    if args.version in ('B', 'both'):
        print("\nPhase 2a: 生成B版篮子(纯Top50)...")
        b_baskets = {}
        for rb_date in REBALANCE_DATES:
            ranked_candidates = [(code, fcf_yield) for code, fcf_yield, _, _, _ in all_rankings[rb_date]]
            selected_codes = [c for c, _ in ranked_candidates[:50]]
            b_baskets[rb_date] = build_basket_from_ranking(selected_codes, all_rankings, rb_date, fcf)
            print(f"  {rb_date}: {len(selected_codes)}只")
        
        b_dir = ROOT / "output" / "zz800_fcf_fixed_lenient"
        b_dir.mkdir(parents=True, exist_ok=True)
        with open(b_dir / "all_baskets_2015_2026.json", 'w') as f:
            json.dump(b_baskets, f, ensure_ascii=False, indent=2)
        print(f"  ✅ B版保存: {b_dir / 'all_baskets_2015_2026.json'}")
    
    # Phase 2b: D版(缓冲区)
    if args.version in ('D', 'both'):
        print("\nPhase 2b: 生成D版篮子(缓冲区±20%)...")
        d_baskets = {}
        prev_codes = set()
        
        for i, rb_date in enumerate(REBALANCE_DATES):
            ranked_candidates = [(code, fcf_yield) for code, fcf_yield, _, _, _ in all_rankings[rb_date]]
            
            if i == 0 or len(prev_codes) == 0:
                selected_codes = [c for c, _ in ranked_candidates[:50]]
            else:
                selected_codes = apply_buffer_zone(ranked_candidates, prev_codes, 50, 0.20)
            
            d_baskets[rb_date] = build_basket_from_ranking(selected_codes, all_rankings, rb_date, fcf)
            prev_codes = set(selected_codes)
            
            # 与B版对比
            diff = 0
            if args.version == 'both' and rb_date in b_baskets:
                b_codes = set(s['ts_code'] for s in b_baskets[rb_date])
                d_codes = set(selected_codes)
                diff = len(d_codes - b_codes)
            print(f"  {rb_date}: {len(selected_codes)}只, 与B版差异={diff}只")
        
        d_dir = ROOT / "output" / "zz800_fcf_lenient_buffer"
        d_dir.mkdir(parents=True, exist_ok=True)
        with open(d_dir / "all_baskets_2015_2026.json", 'w') as f:
            json.dump(d_baskets, f, ensure_ascii=False, indent=2)
        print(f"  ✅ D版保存: {d_dir / 'all_baskets_2015_2026.json'}")
    
    print(f"\n{'='*60}")
    print("✅ ZZ800篮子生成完成!")


if __name__ == "__main__":
    main()