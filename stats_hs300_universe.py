#!/usr/bin/env python3
"""
stats_hs300_universe.py — HS300 B版条件（无Top50筛选）全量统计

对每个调仓日，统计：
  - 满足所有条件（剔除金融地产 + FCF>0 + EV>0 + 5年OCF>0 + 盈利质量前80%）的公司数
  - FCF 总和（亿元）
  - (OCF - 营业利润) 总和（亿元）

B版参数：total_mv + TTM + 宽松OCF
"""
import sys, json, time, types
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from fcf_universe import FcfUniverse, _is_financial_or_real_estate

# ═══════════════════════════════════════════════════════
# 调仓日期
# ═══════════════════════════════════════════════════════
REBALANCE_DATES = [
    "2016-06-13", "2016-09-12", "2016-12-12",
    "2017-03-13", "2017-06-12", "2017-09-11", "2017-12-11",
    "2018-03-12", "2018-06-11", "2018-09-17", "2018-12-17",
    "2019-03-11", "2019-06-17", "2019-09-16", "2019-12-16",
    "2020-03-16", "2020-06-15", "2020-09-14", "2020-12-14",
    "2021-03-15", "2021-06-14", "2021-09-13", "2021-12-13",
    "2022-03-14", "2022-06-13", "2022-09-12", "2022-12-12",
    "2023-03-13", "2023-06-12", "2023-09-11", "2023-12-11",
    "2024-03-11", "2024-06-17", "2024-09-16", "2024-12-16",
    "2025-03-17", "2025-06-16", "2025-09-15", "2025-12-15",
    "2026-03-16",
]

INDEX_CODE = "000300.SH"
CACHE_DIR = PROJECT_ROOT / "data" / "fcf_financials" / "daily_basic_cache"

# ═══════════════════════════════════════════════════════
# 预加载 daily_basic 内存缓存
# ═══════════════════════════════════════════════════════

def preload_daily_basic() -> Dict[str, Dict[str, Dict[str, float]]]:
    if not CACHE_DIR.exists():
        return {}
    preloaded = {}
    for f in sorted(CACHE_DIR.glob("daily_basic_*.csv")):
        date_str = f.stem.replace("daily_basic_", "")
        try:
            df = pd.read_csv(f, dtype={"ts_code": str})
        except Exception:
            continue
        day_data = {}
        for _, row in df.iterrows():
            code = str(row["ts_code"])
            entry = {}
            if pd.notna(row.get("total_mv")) and float(row["total_mv"]) > 0:
                entry["total_mv"] = float(row["total_mv"])
            if pd.notna(row.get("circ_mv")) and float(row["circ_mv"]) > 0:
                entry["circ_mv"] = float(row["circ_mv"])
            if entry:
                day_data[code] = entry
        preloaded[date_str] = day_data
    return preloaded


def make_fast_market_cap(preloaded):
    def fast_market_cap(self, pro, date_str, ts_codes):
        code_set = set(ts_codes)
        base = datetime.strptime(date_str.replace("-", ""), "%Y%m%d")
        result = {}
        for delta in range(6):
            d = (base - timedelta(days=delta)).strftime("%Y%m%d")
            day_data = preloaded.get(d)
            if day_data is None:
                continue
            for code in ts_codes:
                if code not in code_set:
                    continue
                sd = day_data.get(code)
                if sd is None:
                    continue
                if code not in result and "total_mv" in sd:
                    result[code] = sd["total_mv"]
                if f"{code}_circ" not in result and "circ_mv" in sd:
                    result[f"{code}_circ"] = sd["circ_mv"]
            if result:
                return result
        return result
    return fast_market_cap


# ═══════════════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════════════

print("📥 预加载 daily_basic...")
preloaded = preload_daily_basic()
print(f"   ✅ {len(preloaded)} 个交易日")

uni = FcfUniverse(index_code=INDEX_CODE, strict_ocf=False)
uni.preload_all(download=False)
uni._batch_fetch_market_cap = types.MethodType(make_fast_market_cap(preloaded), uni)

# 获取股票基本信息
stock_basic = uni._stock_basic
if stock_basic is not None and not stock_basic.empty:
    info_map = stock_basic.set_index("ts_code").to_dict("index")
else:
    info_map = {}

print(f"\n{'='*85}")
print(f"  HS300 B版条件 | 全量统计（不做Top50筛选）")
print(f"{'='*85}")

rows = []
for i, date_str in enumerate(REBALANCE_DATES):
    print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str} ...", end=" ")

    # Step 1: 获取成分股
    constituents = uni._idx_cache.get_constituents(date_str)
    if not constituents:
        print("无成分股")
        rows.append({"date": date_str, "n": 0, "fcf_sum_亿": 0, "ocf_op_sum_亿": 0})
        continue

    # Step 2: 确定参考报告期（TTM）
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    month = dt.month
    if month <= 3:
        ref_period = f"{dt.year - 1}0930"
        ref_year = dt.year - 1
    elif month <= 6:
        ref_period = f"{dt.year}0331"
        ref_year = dt.year
    elif month <= 9:
        ref_period = f"{dt.year}0630"
        ref_year = dt.year
    else:
        ref_period = f"{dt.year}0930"
        ref_year = dt.year

    # 拉取市值
    mv_map = {}
    codes_need = list(constituents)
    pro = None  # 不需要，fast_market_cap 不用

    # 构建全量候选
    passed = []
    for code in constituents:
        info = info_map.get(code, {})
        industry = str(info.get("industry", ""))

        # 行业过滤
        if _is_financial_or_real_estate(industry):
            continue

        # 财务数据
        fin = uni._fin_cache.get_ttm_financials(code, ref_period)
        if fin["oper_cf"] is None or fin["capex"] is None:
            rep_year = uni._get_available_report_year(date_str, code)
            fin = uni._fin_cache.get_annual_financials(code, rep_year)

        fcf = uni._calc_fcf(fin["oper_cf"], fin["capex"])
        pq = uni._calc_profit_quality(fin["oper_cf"], fin["oper_profit"], fin["total_assets"])

        passed.append({
            "ts_code": code,
            "oper_cf": fin["oper_cf"],
            "capex": fin["capex"],
            "oper_profit": fin["oper_profit"],
            "total_liab": fin["total_liab"],
            "money_cap": fin["money_cap"],
            "total_assets": fin["total_assets"],
            "fcf": fcf,
            "profit_quality": pq,
        })

    if not passed:
        print("0 只（行业过滤后无标的）")
        rows.append({"date": date_str, "n": 0, "fcf_sum_亿": 0, "ocf_op_sum_亿": 0})
        continue

    # PQ cutoff（前80%，含金融地产基数）
    pq_vals = [c["profit_quality"] for c in passed if c["profit_quality"] is not None]
    pq_cutoff = float(np.percentile(pq_vals, 20)) if pq_vals else float("-inf")

    # 构建市值映射
    remaining = []
    for c in passed:
        if c["fcf"] is None or c["fcf"] <= 0:
            continue
        if c["profit_quality"] is not None and c["profit_quality"] < pq_cutoff:
            continue
        # OCF 5年检查
        info_row = info_map.get(c["ts_code"], {})
        list_date_str = str(info_row.get("list_date", ""))
        rep_year_for_ocf = ref_year
        ocf_start = rep_year_for_ocf - 4
        try:
            if list_date_str and len(list_date_str) >= 4:
                list_year = int(list_date_str[:4])
                ocf_start = max(ocf_start, list_year)
        except (ValueError, TypeError):
            pass
        if not uni._fin_cache.check_5yr_positive_ocf(
            c["ts_code"], rep_year_for_ocf, start_year=ocf_start,
            ref_period=ref_period, strict=False
        ):
            continue
        remaining.append(c)

    if not remaining:
        print("0 只（全部条件过滤后）")
        rows.append({"date": date_str, "n": 0, "fcf_sum_亿": 0, "ocf_op_sum_亿": 0})
        continue

    # 拉取市值 → EV 筛选
    codes_need = [c["ts_code"] for c in remaining]
    mv_map_all = uni._batch_fetch_market_cap(None, date_str, codes_need)

    final = []
    for c in remaining:
        code = c["ts_code"]
        total_mv = mv_map_all.get(code) or mv_map_all.get(f"{code}_circ")
        if total_mv is None:
            continue
        ev = uni._calc_ev(total_mv, c["total_liab"], c["money_cap"])
        if ev is None or ev <= 0:
            continue
        final.append(c)

    # 统计
    n = len(final)
    fcf_sum = sum(c["fcf"] for c in final) / 1e8  # 元→亿
    ocf_op_sum = sum(
        (c["oper_cf"] - c["oper_profit"]) if (c["oper_cf"] is not None and c["oper_profit"] is not None) else 0
        for c in final
    ) / 1e8

    rows.append({
        "date": date_str,
        "n": n,
        "fcf_sum_亿": round(fcf_sum, 2),
        "ocf_op_sum_亿": round(ocf_op_sum, 2),
    })
    print(f"{n} 只 | FCF总和={fcf_sum:.0f}亿 | (OCF-OP)总和={ocf_op_sum:.0f}亿")

# ═══════════════════════════════════════════════════════
# 输出表格
# ═══════════════════════════════════════════════════════
print(f"\n{'='*85}")
print(f"  汇总表")
print(f"{'='*85}")
print(f"{'调仓日':<12} {'合格数':>6} {'FCF总和(亿)':>14} {'OCF-OP(亿)':>14}")
print("-" * 50)
for r in rows:
    print(f"{r['date']:<12} {r['n']:>6} {r['fcf_sum_亿']:>14.0f} {r['ocf_op_sum_亿']:>14.0f}")

# 统计
ns = [r["n"] for r in rows if r["n"] > 0]
fcf_sums = [r["fcf_sum_亿"] for r in rows if r["n"] > 0]
op_sums = [r["ocf_op_sum_亿"] for r in rows if r["n"] > 0]

print(f"\n  平均合格数: {np.mean(ns):.0f} 只 (范围 {min(ns)}-{max(ns)})")
print(f"  平均FCF总和: {np.mean(fcf_sums):.0f} 亿")
print(f"  平均(OCF-OP)总和: {np.mean(op_sums):.0f} 亿")
