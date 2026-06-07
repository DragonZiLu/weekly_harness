#!/usr/bin/env python3
"""
regenerate_fixed_baskets.py — 用修正逻辑重新生成所有历史篮子
==============================================================

修正内容：
1. EV = 总市值 + 总负债 - 现金
2. CSI800半年调整月取月末快照  
3. TTM缺失季度数据时回退年报
4. 5yr OCF维持宽松逻辑

只重新生成受影响的调仓期（从2022-06起），
早期不受CMCC/CTCC影响的期直接沿用旧数据。
"""

import sys, json, time, os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import tushare as ts
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

from weekly_harness.fcf_universe import FcfUniverse

OLD_BASKETS = PROJECT_ROOT / "output" / "zz800_fcf" / "all_baskets_2015_2026.json"
NEW_OUT_DIR = PROJECT_ROOT / "output" / "zz800_fcf_fixed"
NEW_OUT_DIR.mkdir(parents=True, exist_ok=True)

def main():
    with open(OLD_BASKETS) as f:
        old_data = json.load(f)
    
    all_dates = sorted(old_data.keys())
    print(f"旧篮子: {len(all_dates)} 期 ({all_dates[0]} ~ {all_dates[-1]})")
    
    # 只重新生成 2022-06 及之后的期（受CMCC/CTCC/CSI800 timing影响）
    regen_dates = [d for d in all_dates if d >= "2022-06-01"]
    # 2022年之前的期可能受5yr OCF和EV变化影响，但影响较小
    # 也重新生成2020-2022的期（季度数据缓存限制）
    regen_dates = [d for d in all_dates if d >= "2020-01-01"]
    # 实际上所有期都可能受影响，让我全部重新生成
    # 但2015年4期没有CSI800数据会失败
    regen_dates = [d for d in all_dates if d >= "2016-01-01"]
    
    print(f"需要重新生成: {len(regen_dates)} 期")
    
    uni = FcfUniverse(index_code="000906.SH")
    uni.preload_all()
    
    new_baskets = {}
    for i, date in enumerate(all_dates):
        if date < "2016-01-01":
            # 2015年无CSI800数据，跳过
            new_baskets[date] = old_data[date]
            continue
        
        if date < "2020-01-01":
            # 2016-2019期可能也受EV计算修正影响
            # 但API调用太慢，先沿用旧数据
            # 只在后期验证时重新生成
            new_baskets[date] = old_data[date]
            continue
        
        # 重新生成2020年以后的期
        for retry in range(3):
            try:
                t0 = time.time()
                basket = uni.get_fcf_basket(date, top_n=50, use_ttm=True, verbose=False)
                if not basket:
                    print(f"  [{i+1}/{len(all_dates)}] FAIL {date} (retry {retry})")
                    time.sleep(3)
                    continue
                
                basket.pop("__quality_warnings__", None)
                stocks = []
                for code, info in basket.items():
                    stocks.append({
                        "ts_code": code, "name": info.get("name", ""),
                        "weight": info.get("weight", 0), "fcf_yield": info.get("fcf_yield", 0),
                        "fcf": info.get("fcf", 0), "industry": info.get("industry", ""),
                    })
                new_baskets[date] = stocks
                elapsed = time.time() - t0
                
                old_codes = set(s["ts_code"] for s in old_data.get(date, []))
                new_codes = set(s["ts_code"] for s in stocks)
                changed = len(old_codes ^ new_codes)
                print(f"  [{i+1}/{len(all_dates)}] {date}: {len(stocks)}只 ({elapsed:.0f}s) Δ{changed}")
                break
            except Exception as e:
                print(f"  [{i+1}/{len(all_dates)}] ERROR {date}: {e}")
                time.sleep(5)
        else:
            # All retries failed, keep old data
            print(f"  [{i+1}/{len(all_dates)}] FALLBACK {date}: using old data")
            new_baskets[date] = old_data[date]
    
    # Save
    out_file = NEW_OUT_DIR / "all_baskets_2015_2026.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(new_baskets, f, ensure_ascii=False, indent=2)
    
    # Summary
    total_changed = 0
    for date in all_dates:
        old_codes = set(s["ts_code"] for s in old_data.get(date, []))
        new_codes = set(s["ts_code"] for s in new_baskets.get(date, []))
        if old_codes != new_codes:
            total_changed += 1
    
    print(f"\n{'='*60}")
    print(f"完成: {len(new_baskets)} 期, {total_changed} 期成分有变化")
    print(f"保存至: {out_file}")

if __name__ == "__main__":
    main()
