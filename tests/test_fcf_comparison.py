"""
Comprehensive FCF comparison: our simulation vs 932365 actual constituents.
Compares selected stocks, weights, and intermediate filter results.
"""
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.fcf_universe import FcfUniverse, IndexWeightCache
import tushare as ts
from config.settings import tushare_cfg


def fetch_932365(date_str):
    """Fetch 932365 index weights for a given date (YYYY-MM-DD)."""
    pro = ts.pro_api(tushare_cfg.token)
    d = date_str.replace("-", "")
    df = pro.index_weight(index_code="932365.CSI", trade_date=d)
    if df is None or df.empty:
        return pd.DataFrame(columns=["con_code", "weight"])
    return df[["con_code", "weight"]].copy()


def compare_single(date_str, verbose=True):
    """Compare our basket vs 932365 for a single date."""
    uni = FcfUniverse()
    uni.preload_all(download=False)

    # Our basket
    our_basket = uni.get_fcf_basket(date_str, verbose=False)
    our_codes = set(our_basket.keys())
    our_weight = {c: d.get("weight", 0) for c, d in our_basket.items()}
    print(f"\n{'='*70}")
    print(f"  比对日期: {date_str}")
    print(f"{'='*70}")
    print(f"  我们选股: {len(our_basket)} 只")

    # 932365 actual
    # Find nearest trade_date
    target_d = date_str.replace("-", "")
    pro = ts.pro_api(tushare_cfg.token)
    avail = pro.index_weight(index_code="932365.CSI", start_date="20241201", end_date="20260601")
    if avail is not None and not avail.empty:
        avail["trade_date"] = avail["trade_date"].astype(str)
        all_dates = sorted(avail["trade_date"].unique())
        # Find closest date >= target
        closest = None
        for d in all_dates:
            if d >= target_d:
                closest = d
                break
        if closest is None:
            closest = all_dates[-1]
        print(f"  932365 对应日期: {closest} (目标 {target_d})")

        actual = avail[avail["trade_date"] == closest][["con_code", "weight"]].copy()
        actual_codes = set(actual["con_code"].tolist())
        actual_weight = dict(zip(actual["con_code"], actual["weight"]))
        print(f"  932365 成分股: {len(actual_codes)} 只")

        # Comparison
        intersection = our_codes & actual_codes
        only_ours = our_codes - actual_codes
        only_theirs = actual_codes - our_codes

        recall = len(intersection) / len(actual_codes) * 100 if actual_codes else 0
        precision = len(intersection) / len(our_codes) * 100 if our_codes else 0
        f1 = 2 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0

        print(f"\n  ┌─ 选股对比 ─────────────────────────────────")
        print(f"  │ Recall:     {recall:.1f}% ({len(intersection)}/{len(actual_codes)})")
        print(f"  │ Precision:  {precision:.1f}% ({len(intersection)}/{len(our_codes)})")
        print(f"  │ F1-score:   {f1:.1f}%")

        # Weight correlation for overlapping stocks
        if len(intersection) >= 3:
            ow = [our_weight.get(c, 0) for c in intersection]
            aw = [actual_weight.get(c, 0) for c in intersection]
            try:
                from scipy.stats import spearmanr, pearsonr
            except ImportError:
                spearmanr = lambda x, y: (np.nan, np.nan)
                pearsonr = lambda x, y: (np.nan, np.nan)
            sp, _ = spearmanr(ow, aw)
            pr, _ = pearsonr(ow, aw)
            print(f"  │ Spearman:   {sp:.4f}")
            print(f"  │ Pearson:    {pr:.4f}")

            # Weight differences
            diffs = []
            for c in intersection:
                o_w = our_weight.get(c, 0) * 100
                a_w = actual_weight.get(c, 0)
                if abs(o_w - a_w) > 1.5:
                    # Find name
                    name = our_basket.get(c, {}).get("name", "")
                    diffs.append((c, name, o_w, a_w, abs(o_w - a_w)))
            diffs.sort(key=lambda x: x[4], reverse=True)
            if diffs:
                print(f"  │ 权重大差异 (>1.5%): {len(diffs)} 只")
                for c, n, ow_pct, aw_pct, diff in diffs[:10]:
                    mark = "←偏高" if ow_pct > aw_pct else "←偏低"
                    print(f"  │   {c} {n}: 我们{ow_pct:.1f}% vs 指数{aw_pct:.1f}% ({diff:.1f}%) {mark}")

        # Missing stocks analysis
        if only_theirs:
            print(f"\n  ┌─ 缺失标的 ({len(only_theirs)}只, 在932365但不在我们选股):")
            # Attempt to diagnose why each is missing
            iwc = IndexWeightCache()
            for code in sorted(only_theirs)[:15]:
                # Check which step failed
                reasons = []
                fin_2024 = uni._fin_cache.get_annual_financials(code, 2024)
                fin_2025 = uni._fin_cache.get_annual_financials(code, 2025)
                
                # Check available years
                avail_years = []
                for y in range(2015, 2026):
                    f = uni._fin_cache.get_annual_financials(code, y)
                    if f["oper_cf"] is not None:
                        avail_years.append(y)
                
                reasons.append(f"年报={avail_years[-1] if avail_years else '无'}")
                
                # Use 2024 or most recent
                use_fin = fin_2024 if fin_2024["oper_cf"] is not None else fin_2025
                if use_fin["oper_cf"] is None:
                    reasons.append("无现金流数据")
                else:
                    ocf = use_fin.get("oper_cf", 0)
                    cap = use_fin.get("capex")
                    op = use_fin.get("oper_profit")
                    ta = use_fin.get("total_assets")
                    
                    if ocf and ocf <= 0:
                        reasons.append(f"经营CF≤0 ({ocf/1e8:.1f}亿)")
                    elif ocf and op and ta:
                        pq = (ocf - op) / ta if ta > 0 else None
                        reasons.append(f"PQ={pq:.4f}" if pq is not None else "PQ无数据")
                    else:
                        reasons.append("数据不完整")
                
                print(f"  │ {code}: {'; '.join(reasons)}")

        # Extra stocks
        if only_ours:
            print(f"\n  ┌─ 多余标的 ({len(only_ours)}只, 在我们选股但不在932365):")
            for code in sorted(only_ours)[:10]:
                name = our_weight.get(code, None)
                w = our_weight.get(code, 0)
                print(f"  │ {code} (权重{w*100:.1f}%)")

        print(f"  └{'─'*50}")

        return {
            "date": date_str,
            "actual_date": closest,
            "our_n": len(our_codes),
            "actual_n": len(actual_codes),
            "intersection": len(intersection),
            "recall": round(recall, 2),
            "precision": round(precision, 2),
            "f1": round(f1, 2),
            "only_theirs": sorted(only_theirs),
            "only_ours": sorted(only_ours),
        }
    else:
        print("  932365 无数据可用")
        return None


def main():
    pro = ts.pro_api(tushare_cfg.token)
    
    # Get all available 932365 dates
    avail = pro.index_weight(index_code="932365.CSI", start_date="20241201", end_date="20260601")
    if avail is None or avail.empty:
        print("无法获取 932365 成分股数据！")
        return
    
    avail["trade_date"] = avail["trade_date"].astype(str)
    all_dates = sorted(avail["trade_date"].unique())
    print(f"932365 可用日期: {len(all_dates)} 个, 范围 {all_dates[0]}~{all_dates[-1]}")
    
    # Compare key dates that align with our rebalance schedule
    # CSI FCF rebalance: March, June, September, December (3rd Friday)
    rebalance_dates = [
        "2025-03-21",   # Q1 2025
        "2025-06-20",   # Q2 2025
        "2025-09-19",   # Q3 2025
        "2025-12-19",   # Q4 2025
        "2026-03-20",   # Q1 2026
    ]
    
    results = []
    for d in rebalance_dates:
        r = compare_single(d)
        if r:
            results.append(r)
    
    # Summary table
    if results:
        print(f"\n{'='*70}")
        print(f"  综合比对汇总")
        print(f"{'='*70}")
        print(f"  {'日期':<12} {'我们':>5} {'指数':>5} {'交集':>5} {'Recall':>8} {'Precision':>10} {'F1':>8}")
        print(f"  {'-'*60}")
        for r in results:
            print(f"  {r['date']:<12} {r['our_n']:>5} {r['actual_n']:>5} {r['intersection']:>5} "
                  f"{r['recall']:>7.1f}% {r['precision']:>9.1f}% {r['f1']:>7.1f}%")
        
        # Save results
        out_path = _PROJECT_ROOT / "data" / "fcf_financials" / "comparison_results.json"
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n结果已保存: {out_path}")
    
    print("\n✅ 比对完成！")


if __name__ == "__main__":
    main()
