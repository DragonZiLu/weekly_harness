"""
Deep diagnostic: trace each 932365 constituent through our filter pipeline
to understand why they pass or fail each step.
"""
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
import json
import tushare as ts

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg
from weekly_harness.fcf_universe import FcfUniverse, IndexWeightCache

def diagnose(date_str):
    """For a given date, trace each 932365 stock through all filter steps."""
    pro = ts.pro_api(tushare_cfg.token)
    uni = FcfUniverse()
    uni.preload_all(download=False)
    iwc = IndexWeightCache()
    iwc.download()

    # Get 932365 constituents for this date
    target_d = date_str.replace("-", "")
    w = pro.index_weight(index_code="932365.CSI")
    avail = pro.index_weight(index_code="932365.CSI", start_date="20241201", end_date="20260601")
    if avail is None or avail.empty:
        print("No 932365 data")
        return
    
    avail["trade_date"] = avail["trade_date"].astype(str)
    all_dates = sorted(avail["trade_date"].unique())
    closest = None
    for d in all_dates:
        if d >= target_d:
            closest = d
            break
    if closest is None:
        closest = all_dates[-1]
    
    print(f"\n{'='*80}")
    print(f"  诊断日期: {date_str} → 932365基准日期: {closest}")
    print(f"{'='*80}")
    
    actual = avail[avail["trade_date"] == closest]
    actual_codes = set(actual["con_code"].tolist())
    actual_weight = dict(zip(actual["con_code"], actual["weight"]))
    print(f"932365 成分股: {len(actual_codes)} 只")
    
    # Step 1: CSI All Share constituents
    csi_codes = set(iwc.get_constituents(date_str))
    in_csi = [c for c in actual_codes if c in csi_codes]
    not_in_csi = [c for c in actual_codes if c not in csi_codes]
    print(f"\nStep1 CSI全指: {len(in_csi)}/{len(actual_codes)} 在成分中")
    if not_in_csi:
        print(f"  不在CSI: {not_in_csi[:5]}")
    
    # Step 2: Industry exclusion
    EXCLUDE_KW = ["金融", "银行", "证券", "保险", "地产", "房产"]
    EXCLUDED = {"银行","证券","保险","多元金融","信托","期货","融资租赁","金融控股",
                "资产管理","房地产开发","房地产服务","全国地产","区域地产","房产服务","园区开发"}
    
    sl = pd.read_csv(_PROJECT_ROOT / "data" / "fcf_financials" / "stock_list.csv", dtype={"ts_code": str})
    ind_map = dict(zip(sl["ts_code"], sl["industry"]))
    
    def is_excl(ind):
        ind = str(ind).strip()
        if ind in EXCLUDED: return True
        for kw in EXCLUDE_KW:
            if kw in ind: return True
        return False
    
    excluded = [c for c in in_csi if is_excl(ind_map.get(c, ""))]
    remaining1 = [c for c in in_csi if not is_excl(ind_map.get(c, ""))]
    print(f"\nStep2 行业剔除: {len(excluded)} 只金融/地产被剔除")
    if excluded:
        print(f"  剔除: {[(c, ind_map.get(c,'?')) for c in excluded]}")
    
    # Step 3-5: FCF calculation and filters
    # Determine which year's annual report to use
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    # CSI FCF index uses the most recent annual report filed by the rebalance date
    # For March rebalance, use previous year's annual report
    # For June, use previous year (if available) 
    if dt.month <= 4:
        fy = dt.year - 1  # Use FY(t-1) for Q1
    else:
        fy = dt.year - 1  # Use FY(t-1) for Q2-Q4 (could use FY(t) later in year)
    
    print(f"\nStep3 FCF计算 (使用FY{fy}年报):")
    
    # Get our exact basket calculation to find cutoffs
    our_basket = uni.get_fcf_basket(date_str, verbose=False)
    our_codes = set(our_basket.keys())
    
    # Now trace each 932365 stock
    results = {
        "passed": [],
        "no_data": [],
        "neg_ocf": [],
        "neg_fcf": [],
        "bad_pq": [],
        "bad_5yr_ocf": [],
        "bad_ev": [],
        "not_in_csi": [],
        "financial": [],
        "ranked_out": [],
    }
    
    # Get PQ cutoff from our basket calculation
    pq_values = []
    for c in remaining1:
        fin = uni._fin_cache.get_annual_financials(c, fy)
        if fin["oper_cf"] is None or fin["oper_profit"] is None or fin["total_assets"] is None:
            continue
        if fin["total_assets"] <= 0:
            continue
        pq = (fin["oper_cf"] - fin["oper_profit"]) / fin["total_assets"]
        pq_values.append(pq)
    
    pq_values.sort()
    if pq_values:
        cutoff_idx = int(len(pq_values) * 0.2)
        pq_cutoff = pq_values[cutoff_idx] if cutoff_idx < len(pq_values) else pq_values[-1]
        print(f"  盈利质量 cutoff (最低20%): {pq_cutoff:.4f} (n={len(pq_values)})")
    else:
        pq_cutoff = -999
        print(f"  盈利质量: 无有效数据")
    
    # Also get our basket to compute FCF-related cutoffs
    our_candidates = []
    for c in remaining1:
        fin = uni._fin_cache.get_annual_financials(c, fy)
        ocf = fin["oper_cf"]
        capex = fin["capex"]
        
        if ocf is None:
            continue
            
        fcf = None
        if ocf is not None and capex is not None:
            fcf = ocf - capex
        
        # 5yr OCF check
        ocf_5yr = True
        for dy in range(fy-4, fy+1):
            f = uni._fin_cache.get_annual_financials(c, dy)
            if f["oper_cf"] is None or f["oper_cf"] <= 0:
                ocf_5yr = False
                break
        
        our_candidates.append({
            "code": c,
            "fy": fy,
            "ocf": ocf,
            "capex": capex,
            "fcf": fcf,
            "pq": (ocf - fin["oper_profit"]) / fin["total_assets"] if fin["oper_profit"] and fin["total_assets"] and fin["total_assets"] > 0 else None,
            "ocf_5yr": ocf_5yr,
            "total_assets": fin["total_assets"],
            "total_liab": fin["total_liab"],
            "money_cap": fin["money_cap"],
        })
    
    our_df = pd.DataFrame(our_candidates)
    
    # Trace each actual 932365 stock
    for code in sorted(actual_codes):
        if code not in csi_codes:
            results["not_in_csi"].append(code)
            continue
        if is_excl(ind_map.get(code, "")):
            results["financial"].append(code)
            continue
        
        fin = uni._fin_cache.get_annual_financials(code, fy)
        if fin["oper_cf"] is None:
            # Try previous year
            fin2 = uni._fin_cache.get_annual_financials(code, fy-1)
            if fin2["oper_cf"] is not None:
                fy_used = fy-1
                fin = fin2
            else:
                results["no_data"].append((code, "无年报数据"))
                continue
        else:
            fy_used = fy
        
        ocf = fin["oper_cf"]
        capex = fin["capex"]
        
        if ocf is not None and ocf <= 0:
            results["neg_ocf"].append((code, f"经营CF={ocf/1e8:.1f}亿≤0"))
            continue
        
        # FCF
        if ocf is not None and capex is not None:
            fcf = ocf - capex
            if fcf <= 0:
                results["neg_fcf"].append((code, f"FCF={fcf/1e8:.1f}亿≤0"))
                continue
        else:
            results["no_data"].append((code, f"数据不完整 OCF={ocf}, capex={capex}"))
            continue
        
        # Profit quality
        op = fin["oper_profit"]
        ta = fin["total_assets"]
        if op is None or ta is None or ta <= 0:
            results["no_data"].append((code, f"PQ无数据 op={op}, ta={ta}"))
            continue
        pq = (ocf - op) / ta
        if pq < pq_cutoff:
            results["bad_pq"].append((code, f"PQ={pq:.4f}<cutoff={pq_cutoff:.4f} (经营CF={ocf/1e8:.0f}亿, 营业利润={op/1e8:.0f}亿, 总资产={ta/1e8:.0f}亿)"))
            continue
        
        # 5yr OCF
        ocf_5yr = True
        for dy in range(fy_used-4, fy_used+1):
            f = uni._fin_cache.get_annual_financials(code, dy)
            if f["oper_cf"] is None or f["oper_cf"] <= 0:
                ocf_5yr = False
                break
        if not ocf_5yr:
            results["bad_5yr_ocf"].append((code, f"5年OCF不满足"))
            continue
        
        # EV check
        tl = fin["total_liab"]
        mc = fin["money_cap"]
        if tl is not None and mc is not None and ta is not None:
            # EV = market_value + total_liab - money_cap
            # We need market value here - simplified check
            pass  # Skip EV for now since we need daily MV
        
        # This stock should pass
        if code in our_codes:
            # Check ranking
            our_w = our_basket[code].get("weight", 0)
            actual_w = actual_weight.get(code, 0)
            results["passed"].append((code, f"✅ 匹配! 我们权重{our_w*100:.1f}% vs 指数{actual_w:.1f}%"))
        else:
            # Passed filters but not in top N or filtered by other criteria
            results["ranked_out"].append((code, f"通过筛选但未入选TopN (PQ={pq:.4f}, FCF={fcf/1e8:.1f}亿, ocf_5yr={ocf_5yr})"))
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"  诊断汇总: {len(actual_codes)}只932365成分股")
    print(f"{'='*80}")
    
    total_accounted = 0
    for category, stocks in results.items():
        n = len(stocks)
        if n == 0:
            continue
        total_accounted += n
        pct = n / len(actual_codes) * 100
        print(f"\n  [{category}] {n}只 ({pct:.1f}%):")
        if category == "passed":
            for s in stocks[:10]:
                print(f"    {s[0]}: {s[1]}")
        elif category in ("not_in_csi", "financial", "no_data"):
            for s in stocks[:5]:
                print(f"    {s}")
        else:
            for s in stocks[:5]:
                print(f"    {s[0]}: {s[1]}")
        if len(stocks) > 10:
            print(f"    ... 还有 {len(stocks)-10} 只")
    
    print(f"\n  总计: {total_accounted}/{len(actual_codes)}")

if __name__ == "__main__":
    diagnose("2026-03-20")
    diagnose("2025-03-21")
