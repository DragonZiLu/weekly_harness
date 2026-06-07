#!/usr/bin/env python3
"""fix_ocf_op_stats.py — 修正(OCF-营业利润)加和统计
从财务数据直接计算 OCF - oper_profit，不依赖 profit_quality * total_assets
"""
import sys, json, time
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from fcf_universe import FcfUniverse

REBALANCE_DATES = [
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

# 加载篮子
zz_full = json.load(open("output/zz800_fcf_full_universe/all_baskets_2015_2026.json"))
hs_full = json.load(open("output/hs300_fcf_full_universe/all_baskets_2015_2026.json"))
b_baskets = json.load(open("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json"))

# 初始化 Universe 获取财务数据
uni_zz = FcfUniverse(index_code="000906.SH", strict_ocf=False)
uni_zz.preload_all(download=False)
uni_hs = FcfUniverse(index_code="000300.SH", strict_ocf=False)
uni_hs.preload_all(download=False)

def get_financial_data(uni, ts_code, date_str):
    """获取单只股票的 oper_cf 和 oper_profit（TTM）"""
    # 使用 _get_ttm_financial_data 方法
    try:
        fin = uni._get_ttm_financial_data(ts_code, date_str)
        if fin and fin.get('oper_cf') is not None and fin.get('oper_profit') is not None:
            ocf = fin['oper_cf']
            op_profit = fin['oper_profit']
            return dict(ocf=ocf, op_profit=op_profit, diff=ocf-op_profit)
    except Exception as ex:
        pass
    return None

# 计算每期的 (OCF-营业利润) 加和
t0 = time.time()
zz_ocf_op = {}
hs_ocf_op = {}
b_ocf_op = {}

print("计算 (OCF-营业利润) 加和...")
for i, date_str in enumerate(REBALANCE_DATES):
    # ZZ800全成分
    zz_stocks = zz_full.get(date_str, [])
    zz_diff_sum = 0
    zz_count = 0
    for s in zz_stocks:
        fin = get_financial_data(uni_zz, s['ts_code'], date_str)
        if fin and fin.get('diff') is not None:
            zz_diff_sum += fin['diff'] / 1e8  # 亿元
            zz_count += 1
    zz_ocf_op[date_str] = dict(diff_sum=zz_diff_sum, count=zz_count)
    
    # HS300全成分
    hs_stocks = hs_full.get(date_str, [])
    hs_diff_sum = 0
    hs_count = 0
    for s in hs_stocks:
        fin = get_financial_data(uni_hs, s['ts_code'], date_str)
        if fin and fin.get('diff') is not None:
            hs_diff_sum += fin['diff'] / 1e8
            hs_count += 1
    hs_ocf_op[date_str] = dict(diff_sum=hs_diff_sum, count=hs_count)
    
    # B版Top50
    b_stocks = b_baskets.get(date_str, [])
    b_diff_sum = 0
    b_count = 0
    for s in b_stocks:
        fin = get_financial_data(uni_zz, s['ts_code'], date_str)
        if fin and fin.get('diff') is not None:
            b_diff_sum += fin['diff'] / 1e8
            b_count += 1
    b_ocf_op[date_str] = dict(diff_sum=b_diff_sum, count=b_count)
    
    elapsed = time.time() - t0
    print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ZZ800 zz_diff={zz_diff_sum:.0f}亿({zz_count}只)  "
          f"HS300 hs_diff={hs_diff_sum:.0f}亿({hs_count}只)  "
          f"B50 b_diff={b_diff_sum:.0f}亿({b_count}只)  ({elapsed:.0f}s)")

# 保存结果
results = {
    'zz800_ocf_op': zz_ocf_op,
    'hs300_ocf_op': hs_ocf_op,
    'b50_ocf_op': b_ocf_op,
}
with open("output/ocf_op_stats.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n✅ (OCF-营业利润)统计已保存: output/ocf_op_stats.json")
print(f"   总耗时: {time.time()-t0:.0f}s")

# 快速输出最近5期
print("\n最近5期统计:")
print("调仓日     ZZ800(OCF-OP)(亿)  HS300(OCF-OP)(亿)  B50(OCF-OP)(亿)")
for d in REBALANCE_DATES[-6:-1]:
    zz = zz_ocf_op.get(d, {})
    hs = hs_ocf_op.get(d, {})
    b50 = b_ocf_op.get(d, {})
    print(f"{d}  {zz.get('diff_sum',0):>+10.0f}亿({zz.get('count',0)}只)  "
          f"{hs.get('diff_sum',0):>+10.0f}亿({hs.get('count',0)}只)  "
          f"{b50.get('diff_sum',0):>+10.0f}亿({b50.get('count',0)}只)")