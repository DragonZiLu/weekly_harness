#!/usr/bin/env python3
"""
验证沪深300官方权重 vs 自算 circ_mv 权重 的一致性

CSI 300 加权规则：
- 使用"分级靠档"后的自由流通市值加权
- 分级靠档：实际自由流通比例 → 靠档比例
  ≤10% → 实际值, 10-20% → 20%, 20-30% → 30%, ... 80-100% → 100%
- 单股权重上限 10%

Tushare daily_basic.circ_mv = 流通市值（非自由流通市值！）
- 流通股包括大股东持有的可流通股（不一定在市场上交易）
- 自由流通市值 = 剔除控股股东、战略投资者、员工持股等后的真正可交易市值

预期结果：官方权重 ≠ circ_mv 直接加权（因为分级靠档 + free-float 口径不同）
"""
import sys, time
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
import os
import tushare as ts

ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

# ============================================================================
# 选几个检查日期
# ============================================================================
CHECK_DATES = [
    "2019-03-11",   # 分化最大的一年
    "2020-03-16",
    "2023-03-13",
    "2026-03-16",   # 最近一期
]

def get_official_weights(index_code: str, date_str: str) -> dict:
    """拉取 index_weight 表中的官方权重"""
    # 先拉取全量（因为tushare可能需要提供范围）
    snap_date = date_str.replace("-", "")
    
    # 向后回溯找最近的快照（一般月末有）
    base = datetime.strptime(snap_date, "%Y%m%d")
    for delta in range(40):
        d = (base - timedelta(days=delta)).strftime("%Y%m%d")
        try:
            df = pro.index_weight(
                index_code=index_code, 
                trade_date=d
            )
            time.sleep(0.3)
            if df is not None and not df.empty:
                weights = {}
                for _, row in df.iterrows():
                    weights[str(row["con_code"])] = float(row["weight"])
                return d, weights
        except Exception as e:
            time.sleep(1)
            continue
    return None, {}

def get_circ_mv_at(date_str: str, codes: list) -> dict:
    """获取流通市值"""
    base = datetime.strptime(date_str[:10].replace("-", ""), "%Y%m%d")
    result = {}
    for delta in range(7):
        d = (base - timedelta(days=delta)).strftime("%Y%m%d")
        try:
            df = pro.daily_basic(trade_date=d, fields="ts_code,circ_mv,total_mv")
            time.sleep(0.3)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row["ts_code"])
                    result[code] = {
                        "circ_mv": float(row["circ_mv"]) if pd.notna(row["circ_mv"]) else 0,
                        "total_mv": float(row["total_mv"]) if pd.notna(row["total_mv"]) else 0,
                    }
                break
        except Exception as e:
            time.sleep(1)
    return result

def compare_weights(date_str: str):
    """对比单日"""
    print(f"\n{'='*70}")
    print(f"📅 检查日期: {date_str}")
    print(f"{'='*70}")
    
    # 1) 拉取官方权重
    snap, official = get_official_weights("000300.SH", date_str)
    if not official:
        print("  ❌ 无法获取官方权重")
        return
    print(f"  官方快照日: {snap} | 成分股: {len(official)} 只")
    
    # 2) 拉取流通市值
    codes = list(official.keys())
    mv_data = get_circ_mv_at(date_str, codes)
    print(f"  流通市值数据覆盖: {len(mv_data)}/{len(official)} 只")
    
    # 3) 计算 circ_mv 权重
    codes_with_mv = [c for c in codes if c in mv_data and mv_data[c]["circ_mv"] > 0]
    total_circ = sum(mv_data[c]["circ_mv"] for c in codes_with_mv)
    
    calc_weights = {}
    for c in codes_with_mv:
        calc_weights[c] = mv_data[c]["circ_mv"] / total_circ
    
    # 4) 对比
    common = list(set(calc_weights.keys()) & set(official.keys()))
    common.sort(key=lambda c: -official.get(c, 0))
    
    print(f"\n  {'代码':<12s} {'名称':<10s} {'官方权重':>10s} {'circ_mv权重':>12s} {'差异(bp)':>10s} {'流通占比':>8s}")
    print(f"  {'-'*65}")
    
    diffs = []
    stock_info = pro.stock_basic(fields="ts_code,name")
    time.sleep(0.3)
    name_map = dict(zip(stock_info["ts_code"], stock_info["name"]))
    
    for c in common[:20]:  # 看前20大权重
        off = official.get(c, 0)  # 已是百分比
        calc = calc_weights.get(c, 0)  # 小数
        calc_pct = calc * 100  # 转为百分比
        diff_pp = calc_pct - off  # 百分点差异
        diffs.append(diff_pp)
        
        circ_ratio = mv_data[c]["circ_mv"] / mv_data[c]["total_mv"] if mv_data[c]["total_mv"] > 0 else 0
        name = name_map.get(c, "?")
        
        marker = ""
        if abs(diff_pp) > 2.0:
            marker = " ⚠️"
        elif abs(diff_pp) > 0.5:
            marker = " 🔸"
        
        print(f"  {c:<12s} {name:<10s} {off:>9.2f}%  {calc_pct:>11.2f}%  {diff_pp:>+9.2f}pp  {circ_ratio:>7.1%}{marker}")
    
    # 统计
    abs_diffs = [abs(d) for d in diffs]
    print(f"\n  前20大权重差异统计 (百分点):")
    print(f"    平均绝对差异: {np.mean(abs_diffs):.2f}pp")
    print(f"    最大绝对差异: {np.max(abs_diffs):.2f}pp")
    print(f"    >2pp差异: {sum(1 for d in abs_diffs if d > 2.0)} 只")
    print(f"    >0.5pp差异: {sum(1 for d in abs_diffs if d > 0.5)} 只")
    
    # 按行业看差异
    print(f"\n  行业维度差异 (前10大行业):")
    industry_diff = {}
    for c in common:
        off = official.get(c, 0)
        calc = calc_weights.get(c, 0)
        diff_bp = (calc - off) * 10000
        ind = name_map.get(c, "?")  # 简单用名字作为标识
        
        # 获取行业
        if c in name_map:
            ind = "行业"
        
        if ind not in industry_diff:
            industry_diff[ind] = {"total_diff": 0, "count": 0, "off_sum": 0, "calc_sum": 0}
        industry_diff[ind]["total_diff"] += abs(diff_bp)
        industry_diff[ind]["count"] += 1
        industry_diff[ind]["off_sum"] += off
        industry_diff[ind]["calc_sum"] += calc
    
    # 改用申万行业
    try:
        industry_data = pro.stock_basic(fields="ts_code,industry")
        time.sleep(0.3)
        ind_map = dict(zip(industry_data["ts_code"], industry_data["industry"]))
    except:
        ind_map = {}
    
    ind_stats = {}
    for c in common:
        off = official.get(c, 0)  # 已是百分比
        calc = calc_weights.get(c, 0) * 100  # 转百分比
        diff_pp = calc - off
        ind = ind_map.get(c, "其他")
        if ind not in ind_stats:
            ind_stats[ind] = {"count": 0, "off_sum": 0.0, "calc_sum": 0.0, "abs_diff_sum": 0.0}
        ind_stats[ind]["count"] += 1
        ind_stats[ind]["off_sum"] += off
        ind_stats[ind]["calc_sum"] += calc
        ind_stats[ind]["abs_diff_sum"] += abs(diff_pp)
    
    top_inds = sorted(ind_stats.items(), key=lambda x: -x[1]["off_sum"])[:10]
    print(f"  {'行业':<12s} {'数量':>5s} {'官方合计':>10s} {'自算合计':>10s} {'差异':>10s}")
    for ind, s in top_inds:
        diff_str = f"{s['calc_sum'] - s['off_sum']:+.2f}pp"
        print(f"  {ind:<12s} {s['count']:>5d} {s['off_sum']:>9.2f}%  {s['calc_sum']:>9.2f}%  {diff_str:>10s}")
    
    return diffs

# ============================================================================
# 执行
# ============================================================================
print("=" * 70)
print("沪深300 官方权重 vs circ_mv 自算权重 对比验证")
print("=" * 70)

all_results = []
for d in CHECK_DATES:
    r = compare_weights(d)
    all_results.append(r)

print(f"\n{'='*70}")
print(f"结论：")
print(f"  官方权重来自 Tushare index_weight API → 经分级靠档 + 自由流通市值计算")
print(f"  自算权重 = circ_mv / sum(circ_mv) → 未经分级靠档 + 流通市值口径")
print(f"{'='*70}")
