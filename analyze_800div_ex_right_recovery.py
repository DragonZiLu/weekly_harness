#!/usr/bin/env python3
"""
analyze_800div_ex_right_recovery.py
===================================
统计中证红利800持仓期间的分红除权事件中，纯价格填权的比例。

定义：
  - 填权成功：除权后10个交易日内，不复权收盘价 ≥ 除权前一日收盘价
  - 统计口径：仅统计股票在800红利篮子内持仓期间发生的「实施」状态除权事件
  - 时间范围：2015-06 → 2026-06（23期半年度调仓）

数据来源（全部本地缓存，零API调用）：
  - 持仓篮: output/800div/all_baskets_2015_2026.json
  - 分红除权: data/dividend_history/{ts_code}.csv
  - 日线价格: data/adj_close_cache/{ts_code}.csv (close = 不复权收盘价)
  - 交易日历: data/trade_cal.csv
  - 股票信息: data/stock_basic.csv

输出:
  - 控制台打印总体和分年度汇总表
  - docs/2026-07-03_800红利填权统计.md
"""

import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output" / "800div"
DIV_DIR = DATA_DIR / "dividend_history"
ADJ_CLOSE_DIR = DATA_DIR / "adj_close_cache"
DOCS_DIR = PROJECT_ROOT / "docs"

# ═══════════════════════════════════════════════════════════
# 调仓日列表（与 run_800div_full.py 一致，半年度）
# ═══════════════════════════════════════════════════════════
REBALANCE_DATES = [
    "2015-06-15", "2015-12-14",
    "2016-06-13", "2016-12-12",
    "2017-06-12", "2017-12-11",
    "2018-06-11", "2018-12-17",
    "2019-06-17", "2019-12-16",
    "2020-06-15", "2020-12-14",
    "2021-06-14", "2021-12-13",
    "2022-06-13", "2022-12-12",
    "2023-06-12", "2023-12-11",
    "2024-06-17", "2024-12-16",
    "2025-06-16", "2025-12-15",
    "2026-06-15",
]


# ═══════════════════════════════════════════════════════════
# Step 1: 数据加载 & 持仓区间索引
# ═══════════════════════════════════════════════════════════

def load_baskets() -> Dict[str, List[Dict]]:
    """加载 800红利 Top100 持仓篮"""
    baskets_path = OUTPUT_DIR / "all_baskets_2015_2026.json"
    if not baskets_path.exists():
        print(f"❌ 持仓篮文件不存在: {baskets_path}")
        print("   请先运行 run_800div_full.py 生成篮子数据")
        sys.exit(1)
    with open(baskets_path) as f:
        baskets = json.load(f)
    print(f"✅ 加载持仓篮: {len(baskets)} 期")
    return baskets


def build_holding_index(baskets: Dict[str, List[Dict]]) -> Dict[str, List[Tuple[str, str]]]:
    """
    构建持仓区间索引。
    返回: {ts_code: [(start_date8, end_date8), ...]}
    start = 本期调仓日, end = 下期调仓日（不含）
    最后一期 end 用 start+180天近似。
    """
    dates = sorted(baskets.keys())
    index = defaultdict(list)

    for i, start_d in enumerate(dates):
        # 确定持仓结束日
        if i + 1 < len(dates):
            end_d = dates[i + 1]
        else:
            # 最后一期：用 start+180 天
            from datetime import datetime as dt, timedelta
            end_d = (dt.strptime(start_d, "%Y-%m-%d") + timedelta(days=180)).strftime("%Y-%m-%d")

        start8 = start_d.replace("-", "")
        end8 = end_d.replace("-", "")

        stocks = baskets.get(start_d, [])
        for s in stocks:
            ts_code = s.get("ts_code", "")
            if ts_code:
                index[ts_code].append((start8, end8))

    # 合并连续/重叠区间（减少冗余检查）
    for ts_code in index:
        intervals = sorted(index[ts_code])
        merged = []
        for (s, e) in intervals:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        index[ts_code] = merged

    return index


def load_trade_cal() -> List[str]:
    """加载交易日历，返回排序后的交易日列表 (YYYYMMDD)"""
    cal_path = DATA_DIR / "trade_cal.csv"
    if not cal_path.exists():
        print("❌ 交易日历文件不存在: trade_cal.csv")
        sys.exit(1)
    cal = pd.read_csv(cal_path, dtype={"cal_date": str})
    trading_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    print(f"✅ 交易日历: {len(trading_days)} 个交易日 "
          f"({trading_days[0]} ~ {trading_days[-1]})")
    return trading_days


def load_stock_names() -> Dict[str, str]:
    """加载股票名称映射 {ts_code: name}"""
    sb_path = DATA_DIR / "stock_basic.csv"
    if not sb_path.exists():
        return {}
    df = pd.read_csv(sb_path, dtype={"ts_code": str, "name": str})
    return dict(zip(df["ts_code"], df["name"]))


# ═══════════════════════════════════════════════════════════
# Step 2: 核心计算函数
# ═══════════════════════════════════════════════════════════

def _is_in_holding(ex_date8: str, intervals: List[Tuple[str, str]]) -> bool:
    """判断 ex_date 是否在任意持仓区间内"""
    for s, e in intervals:
        if s <= ex_date8 < e:
            return True
    return False


def get_trading_days_after(
    ex_date8: str,
    trade_days: List[str],
    n: int = 10,
) -> List[str]:
    """获取 ex_date 之后的 n 个交易日"""
    import bisect
    # ex_date 本身是除权日，当天不开盘可交易，但也有成交价
    # 实际上 ex_date 是交易日（除权除息日当天正常交易）
    # 我们需要 ex_date 之后的下一个交易日开始的 n 个交易日
    idx = bisect.bisect_right(trade_days, ex_date8)
    return trade_days[idx: idx + n]


def get_pre_ex_close(ts_code: str, ex_date8: str, price_map: Dict[str, float],
                     trade_days: List[str]) -> Optional[float]:
    """获取除权前一交易日的收盘价（除权参考价用前一日收盘−现金分红）"""
    import bisect
    idx = bisect.bisect_left(trade_days, ex_date8)
    if idx <= 0:
        return None
    prev_day = trade_days[idx - 1]
    return price_map.get(prev_day)


def compute_recovery_via_cash_adj(
    ts_code: str,
    ex_date8: str,
    cash_div_tax: float,
    trade_days: List[str],
    adj_close_cache: Optional[Dict[str, float]] = None,
) -> bool:
    """
    纯价格填权判断：除权后10个交易日，不复权收盘价 ≥ 除权前一日收盘价。

    注意：除权日当天股价会因为分红自动下调（前一日收盘价 − 每股现金分红）。
    所以我们直接用不复权收盘价对比：如果后10日中任一日的 close ≥ 前一日 close，
    则填权成功。不需要做任何人工除权调整。

    Args:
        adj_close_cache: 如果已缓存该股票的价格映射，直接传入；否则从磁盘加载
    """
    # 加载价格映射（不复权 close）
    if adj_close_cache is None:
        price_map = _load_price_map(ts_code)
        if price_map is None:
            return False
    else:
        price_map = adj_close_cache

    # 除权前一日收盘价 P0
    p0 = get_pre_ex_close(ts_code, ex_date8, price_map, trade_days)
    if p0 is None or p0 <= 0:
        return False

    # 除权后 10 个交易日
    post_dates = get_trading_days_after(ex_date8, trade_days, 10)

    if not post_dates:
        return False

    for d in post_dates:
        close = price_map.get(d)
        if close is not None and close >= p0:
            return True

    return False


def compute_recovery_detail(
    ts_code: str,
    ex_date8: str,
    cash_div_tax: float,
    trade_days: List[str],
    max_days: int = 10,
    target_pct: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    详细填权计算，返回丰富诊断信息。

    Args:
        max_days: 最大观察交易日数（默认10 = 2周，约126 = 半年）
        target_pct: 目标超额百分比（0=刚好填权，1=超过除权前股价1%）
    """
    price_map = _load_price_map(ts_code)
    if price_map is None:
        return None

    p0 = get_pre_ex_close(ts_code, ex_date8, price_map, trade_days)
    if p0 is None or p0 <= 0:
        return None

    target_price = p0 * (1 + target_pct / 100)

    post_dates = get_trading_days_after(ex_date8, trade_days, max_days)
    if not post_dates:
        return {
            "price_before": p0,
            "recovered": False,
            "recovery_day": -1,
            "recovery_price": None,
            "available_days": 0,
            "reason": "no_post_dates",
        }

    recovered = False
    recovery_day = -1
    recovery_price = None

    for i, d in enumerate(post_dates):
        close = price_map.get(d)
        if close is not None and close >= target_price:
            recovered = True
            recovery_day = i + 1  # 第几个交易日
            recovery_price = close
            break

    return {
        "price_before": round(p0, 3),
        "recovered": recovered,
        "recovery_day": recovery_day,
        "recovery_price": round(recovery_price, 3) if recovery_price else None,
        "available_days": len(post_dates),
    }


def _load_price_map(ts_code: str) -> Optional[Dict[str, float]]:
    """加载某只股票的不复权收盘价映射 {trade_date8: close}"""
    # 兼容不同后缀格式
    for suffix in ["", ".SH", ".SZ"]:
        code = ts_code
        if suffix:
            code = ts_code.replace(".SZ", "").replace(".SH", "") + suffix
        cache_file = ADJ_CLOSE_DIR / f"{code}.csv"
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file, dtype={"trade_date": str})
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                return dict(zip(df["trade_date"], df["close"]))
            except Exception:
                continue
        # 也尝试原始 ts_code 格式
        if not suffix:
            continue

    # 最后用原始 ts_code 再试一次
    cache_file = ADJ_CLOSE_DIR / f"{ts_code}.csv"
    if cache_file.exists():
        try:
            df = pd.read_csv(cache_file, dtype={"trade_date": str})
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            return dict(zip(df["trade_date"], df["close"]))
        except Exception:
            pass

    return None


# ═══════════════════════════════════════════════════════════
# Step 3: 主处理流程
# ═══════════════════════════════════════════════════════════

def process_all_events(
    holding_index: Dict[str, List[Tuple[str, str]]],
    trade_days: List[str],
    stock_names: Dict[str, str],
    max_days: int = 10,
    target_pct: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    遍历所有持仓过的股票，筛选持仓期间的除权事件，判断填权。

    Args:
        max_days: 最大观察交易日数（默认10日 = 2周）
            - 10 = 2 周
            - 126 ≈ 半年（约6个月）
        target_pct: 目标超额百分比（0=刚好填权，1=超过1%）

    Returns:
        events: List[dict], 每条包含 ts_code, name, ex_date, cash_div_tax,
                price_before, recovered, recovery_day, recovery_price, available_days
    """
    trade_days_set = set(trade_days)
    all_events = []

    # 按 ts_code 预购排序（便于进度输出）
    codes = sorted(holding_index.keys())
    total = len(codes)
    n_checked = 0
    n_with_div = 0
    n_events = 0
    n_missing_price = 0
    n_skip_no_ex = 0

    t_start = time.time()

    for i, ts_code in enumerate(codes):
        n_checked += 1

        # 每50只或每5秒打印进度
        if i % 50 == 0 or (time.time() - t_start > 5 and n_checked > 0):
            t_start = time.time()
            print(f"  ⏳ [{n_checked}/{total}] 处理中... "
                  f"已找到 {n_events} 个除权事件 "
                  f"(无除权数据:{n_skip_no_ex} 缺日线:{n_missing_price})", end="\r")

        intervals = holding_index[ts_code]

        # 读取分红历史
        div_file = DIV_DIR / f"{ts_code}.csv"
        if not div_file.exists():
            n_skip_no_ex += 1
            continue

        try:
            div_df = pd.read_csv(div_file, dtype={
                "ts_code": str, "ex_date": str, "div_proc": str,
                "cash_div_tax": str, "end_date": str, "ann_date": str,
            })
        except Exception:
            n_skip_no_ex += 1
            continue

        if div_df.empty:
            n_skip_no_ex += 1
            continue

        # 仅保留「实施」状态且有有效 ex_date 的事件
        div_df = div_df[
            (div_df["div_proc"] == "实施") &
            (div_df["ex_date"].notna()) &
            (div_df["ex_date"] != "") &
            (div_df["ex_date"] != "nan")
        ].copy()

        if div_df.empty:
            n_skip_no_ex += 1
            continue

        # 转换 cash_div_tax 为数值
        div_df["cash_div_tax"] = pd.to_numeric(div_df["cash_div_tax"], errors="coerce").fillna(0)

        # 筛选 ex_date 在持仓区间内的事件
        in_holding_events = []
        for _, row in div_df.iterrows():
            ex_date8 = str(row["ex_date"])
            if len(ex_date8) == 8 and _is_in_holding(ex_date8, intervals):
                # 检查 ex_date 是否为交易日（有数据可处理）
                if ex_date8 not in trade_days_set:
                    continue
                in_holding_events.append(row)

        if not in_holding_events:
            continue

        n_with_div += 1

        # 预加载日线价格（所有事件共用）
        price_map = _load_price_map(ts_code)
        if price_map is None:
            stock_name = stock_names.get(ts_code, ts_code)
            for row in in_holding_events:
                all_events.append({
                    "ts_code": ts_code,
                    "name": stock_name,
                    "ex_date": str(row["ex_date"]),
                    "cash_div_tax": float(row["cash_div_tax"]),
                    "price_before": None,
                    "recovered": None,  # 缺 adj_close_cache
                    "recovery_day": -1,
                    "recovery_price": None,
                    "available_days": 0,
                    "reason": "no_adj_cache",
                })
            n_missing_price += len(in_holding_events)
            continue

        stock_name = stock_names.get(ts_code, ts_code)

        for row in in_holding_events:
            ex_date8 = str(row["ex_date"])
            cash_div = float(row["cash_div_tax"])

            detail = compute_recovery_detail(
                ts_code, ex_date8, cash_div, trade_days, max_days, target_pct,
            )

            if detail is None:
                # 价格数据不足（无除权前后日线）
                all_events.append({
                    "ts_code": ts_code,
                    "name": stock_name,
                    "ex_date": ex_date8,
                    "cash_div_tax": cash_div,
                    "price_before": None,
                    "recovered": None,  # 数据不足
                    "recovery_day": -1,
                    "recovery_price": None,
                    "available_days": 0,
                })
                n_missing_price += 1
                continue

            all_events.append({
                "ts_code": ts_code,
                "name": stock_name,
                "ex_date": ex_date8,
                "cash_div_tax": cash_div,
                **detail,
            })
            n_events += 1

    print(f"\n  ✅ 处理完成: 遍历 {n_checked} 只持仓股")
    print(f"     有分红数据: {n_with_div} 只")
    print(f"     持仓期内除权事件: {n_events} 条")
    print(f"     缺日线数据: {n_missing_price} 条")

    return all_events


# ═══════════════════════════════════════════════════════════
# Step 4: 统计汇总 & 报告生成
# ═══════════════════════════════════════════════════════════

def generate_stats(events: List[Dict]) -> Tuple[Dict, Dict[int, Dict]]:
    """
    生成统计：总体 + 分年度。
    Returns: (overall_stats, yearly_stats)
    """
    # 仅统计有有效判断结果的事件
    valid = [e for e in events if e["recovered"] is not None]

    total = len(valid)
    recovered = sum(1 for e in valid if e["recovered"])
    not_recovered = total - recovered
    rate = recovered / total * 100 if total > 0 else 0

    # 恢复天数统计（仅限已恢复的）
    recovery_days = [e["recovery_day"] for e in valid if e["recovered"] and e["recovery_day"] > 0]
    avg_days = np.mean(recovery_days) if recovery_days else 0
    median_days = np.median(recovery_days) if recovery_days else 0

    overall = {
        "total": total,
        "recovered": recovered,
        "not_recovered": not_recovered,
        "rate": rate,
        "avg_days": avg_days,
        "median_days": median_days,
        "missing": len(events) - total,
    }

    # 分年度统计（按 ex_date 年份）
    yearly = defaultdict(lambda: {"total": 0, "recovered": 0, "not_recovered": 0})
    for e in valid:
        yr = int(e["ex_date"][:4])
        yearly[yr]["total"] += 1
        if e["recovered"]:
            yearly[yr]["recovered"] += 1
        else:
            yearly[yr]["not_recovered"] += 1

    for yr, d in yearly.items():
        d["rate"] = d["recovered"] / d["total"] * 100 if d["total"] > 0 else 0

    return overall, dict(sorted(yearly.items()))


def print_results(overall: Dict, yearly: Dict, events: List[Dict], horizon_label: str = "10个交易日"):
    """打印控制台汇总表"""
    max_show_days = 10 if "10" in horizon_label else 20
    print("\n" + "=" * 65)
    print(f"  中证红利800 — 填权统计（{horizon_label}）")
    print("=" * 65)
    print(f"  定义：除权后{horizon_label}内，不复权收盘价 ≥ 除权前一日收盘价")
    print(f"  有效期：2015-06 ~ 2026-06")
    print()

    # 总体统计
    print("─" * 65)
    print("  📊 总体统计")
    print("─" * 65)
    print(f"  有效事件总数:  {overall['total']}")
    print(f"  成功填权:      {overall['recovered']}")
    print(f"  未填权:        {overall['not_recovered']}")
    print(f"  数据缺失:      {overall['missing']}")
    print(f"  ────────────────────────")
    print(f"  填权成功率:    {overall['rate']:.1f}%")
    if overall['avg_days'] > 0:
        print(f"  平均恢复天数:  {overall['avg_days']:.1f} 个交易日")
        print(f"  中位恢复天数:  {overall['median_days']:.1f} 个交易日")
    print()

    # 分年度
    print("─" * 65)
    print("  📊 分年度统计")
    print("─" * 65)
    header = f"  {'年份':<6} {'事件数':>6} {'填权':>6} {'未填权':>7} {'填权率':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for yr in sorted(yearly.keys()):
        d = yearly[yr]
        print(f"  {yr:<6} {d['total']:>6} {d['recovered']:>6} "
              f"{d['not_recovered']:>7} {d['rate']:>7.1f}%")
    print()

    # 恢复天数分布
    valid = [e for e in events if e["recovered"] is not None]
    recovered_events = [e for e in valid if e["recovered"]]
    if recovered_events:
        print("─" * 65)
        print("  📊 恢复天数分布（仅限已填权事件）")
        print("─" * 65)
        day_dist = defaultdict(int)
        for e in recovered_events:
            day_dist[e["recovery_day"]] += 1
        for d in range(1, max_show_days + 1):
            cnt = day_dist.get(d, 0)
            if cnt > 0:
                bar = "█" * max(1, cnt // max(1, max(day_dist.values()) // 30))
                print(f"  第{d:>2}天: {cnt:>4} 只 {bar}")
        print()


def generate_report(overall: Dict, yearly: Dict, events: List[Dict],
                    holding_index: Dict, horizon_label: str = "10个交易日"):
    """生成 Markdown 报告 """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    valid = [e for e in events if e["recovered"] is not None]
    recovered_events = [e for e in valid if e["recovered"]]
    day_dist = defaultdict(int)
    for e in recovered_events:
        day_dist[e["recovery_day"]] += 1

    max_show_days = 10 if "10" in horizon_label else 20

    lines = []
    lines.append(f"# 中证红利800 — 持仓期间分红除权填权统计（{horizon_label}）")
    lines.append("")
    lines.append(f"> 生成时间：{now}")
    lines.append(f"> 回测区间：2015-06 → 2026-06（23期半年度调仓）")
    lines.append(f"> 定义：除权后 **{horizon_label}** 内，**不复权收盘价 ≥ 除权前一日收盘价**（纯价格填权）")
    lines.append("> 统计口径：仅统计股票在 **800红利篮子内持仓期间** 发生的「实施」状态除权事件")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、总体统计")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 有效事件总数 | {overall['total']} |")
    lines.append(f"| **成功填权** | **{overall['recovered']}** |")
    lines.append(f"| 未填权 | {overall['not_recovered']} |")
    lines.append(f"| 数据缺失 | {overall['missing']} |")
    lines.append(f"| **填权成功率** | **{overall['rate']:.1f}%** |")
    lines.append(f"| 平均恢复天数 | {overall['avg_days']:.1f} 个交易日 |")
    lines.append(f"| 中位恢复天数 | {overall['median_days']:.1f} 个交易日 |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 二、分年度统计")
    lines.append("")
    lines.append("| 年份 | 事件数 | 填权成功 | 未填权 | 填权率 |")
    lines.append("|------|--------|----------|--------|--------|")
    for yr in sorted(yearly.keys()):
        d = yearly[yr]
        lines.append(f"| {yr} | {d['total']} | {d['recovered']} | "
                     f"{d['not_recovered']} | **{d['rate']:.1f}%** |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 三、恢复天数分布")
    lines.append("")
    lines.append("| 交易日 | 填权只数 | 占比 |")
    lines.append("|--------|----------|------|")
    for d in range(1, max_show_days + 1):
        cnt = day_dist.get(d, 0)
        pct = cnt / len(recovered_events) * 100 if recovered_events else 0
        lines.append(f"| 第{d}天 | {cnt} | {pct:.1f}% |")
    lines.append(f"| 未恢复 | {overall['not_recovered']} | "
                 f"{overall['not_recovered']/max(overall['total'],1)*100:.1f}% |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 四、未填权典型样本（Top 10）")
    lines.append("")
    not_recovered = [e for e in valid if not e["recovered"]]
    # 按除权日排序展示最近10条
    not_recovered_sorted = sorted(not_recovered, key=lambda x: x["ex_date"], reverse=True)[:10]
    if not_recovered_sorted:
        lines.append("| 股票 | 名称 | 除权日 | 除权前收盘 | 现金分红 |")
        lines.append("|------|------|--------|------------|----------|")
        for e in not_recovered_sorted:
            lines.append(f"| {e['ts_code']} | {e['name']} | {e['ex_date']} | "
                         f"{e.get('price_before','N/A')} | {e.get('cash_div_tax','N/A'):.3f} |")
    else:
        lines.append("（全部填权成功）")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 五、结论")
    lines.append("")
    lines.append(f"- 800红利持仓期间，总计 **{overall['total']}** 次除权事件中，")
    lines.append(f"  **{overall['rate']:.1f}%** 在 {horizon_label} 内完成纯价格填权。")
    lines.append(f"- 已填权事件平均恢复天数 **{overall['avg_days']:.1f}** 个交易日，"
                 f"中位数 **{overall['median_days']:.1f}** 个交易日。")
    if overall["rate"] >= 50:
        lines.append("- 高股息股票的除权缺口具有较好的填权效应，对红利策略的持有体验有正面影响。")
    else:
        lines.append("- 纯价格填权率较低，分红造成的股价缺口在观察期内不易被完全填补。")
    lines.append("")
    lines.append("*报告自动生成*")

    report = "\n".join(lines)
    return report


def generate_comparison_report(results_2w: tuple, results_6m: tuple, events_2w: List[Dict],
                               events_6m: List[Dict]):
    """生成 2周 vs 半年 对比报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    o2, y2 = results_2w
    o6, y6 = results_6m

    lines = []
    lines.append("# 中证红利800 — 填权统计：2周 vs 半年 对比")
    lines.append("")
    lines.append(f"> 生成时间：{now}")
    lines.append(f"> 回测区间：2015-06 → 2026-06（23期半年度调仓）")
    lines.append("> 定义：纯价格填权 = 不复权收盘价 ≥ 除权前一日收盘价")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、总体对比")
    lines.append("")
    lines.append("| 指标 | 2周(10交易日) | 半年(≈126交易日) | 差异 |")
    lines.append("|------|---------------|-------------------|------|")
    lines.append(f"| 有效事件总数 | {o2['total']} | {o6['total']} | — |")
    lines.append(f"| **成功填权** | **{o2['recovered']}** | **{o6['recovered']}** | "
                 f"+{o6['recovered'] - o2['recovered']} |")
    lines.append(f"| 未填权 | {o2['not_recovered']} | {o6['not_recovered']} | "
                 f"{o6['not_recovered'] - o2['not_recovered']} |")
    lines.append(f"| **填权成功率** | **{o2['rate']:.1f}%** | **{o6['rate']:.1f}%** | "
                 f"+{o6['rate'] - o2['rate']:.1f}pp |")
    lines.append(f"| 平均恢复天数 | {o2['avg_days']:.1f}日 | {o6['avg_days']:.1f}日 | — |")
    lines.append(f"| 中位恢复天数 | {o2['median_days']:.1f}日 | {o6['median_days']:.1f}日 | — |")
    lines.append("")

    # 2周已填权 + 半年新增填权的交集分析
    valid_2w = {e["ts_code"] + "|" + e["ex_date"]: e for e in events_2w if e["recovered"] is not None}
    valid_6m = {e["ts_code"] + "|" + e["ex_date"]: e for e in events_6m if e["recovered"] is not None}
    common_keys = set(valid_2w.keys()) & set(valid_6m.keys())

    # 2周内填权 vs 2周后半年内填权
    recovered_2w = {k for k in common_keys if valid_2w[k]["recovered"]}
    recovered_6m = {k for k in common_keys if valid_6m[k]["recovered"]}
    only_6m = recovered_6m - recovered_2w  # 2周未填但半年填了的

    lines.append("---")
    lines.append("")
    lines.append("## 二、时间窗口递进分析")
    lines.append("")
    lines.append(f"- 2周内填权：**{len(recovered_2w)}** 次（{len(recovered_2w)/max(len(common_keys),1)*100:.1f}%）")
    lines.append(f"- 2周后→半年内新填权：**{len(only_6m)}** 次（{len(only_6m)/max(len(common_keys),1)*100:.1f}%）")
    lines.append(f"- 半年内仍未填权：**{len(common_keys) - len(recovered_6m)}** 次")
    lines.append("")
    if only_6m:
        # 这些事件的恢复天数分布
        late_days = []
        for k in only_6m:
            e = valid_6m[k]
            if e["recovery_day"] > 0:
                late_days.append(e["recovery_day"])
        if late_days:
            import numpy as np
            lines.append(f"- 2周后填权事件的平均恢复天数：**{np.mean(late_days):.1f}** 个交易日（中位 **{np.median(late_days):.1f}**）")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 三、分年度对比")
    lines.append("")
    lines.append("| 年份 | 事件数 | 2周填权率 | 半年填权率 | 提升 |")
    lines.append("|------|--------|-----------|------------|------|")
    for yr in sorted(set(list(y2.keys()) + list(y6.keys()))):
        d2 = y2.get(yr, {"total": 0, "rate": 0})
        d6 = y6.get(yr, {"total": 0, "rate": 0})
        total = d2["total"] or d6["total"]
        lift = d6["rate"] - d2["rate"]
        lines.append(f"| {yr} | {total} | {d2['rate']:.1f}% | {d6['rate']:.1f}% | "
                     f"+{lift:.1f}pp |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 四、结论")
    lines.append("")
    lines.append(f"- 2周填权率：**{o2['rate']:.1f}%**，半年后升至 **{o6['rate']:.1f}%**（+{o6['rate']-o2['rate']:.1f}pp）")
    lines.append(f"- 2周后→半年内新填权 {len(only_6m)} 次，占有效事件的 {len(only_6m)/max(len(common_keys),1)*100:.1f}%")
    lines.append(f"- 即使拉长到半年，仍有 **{o6['not_recovered']}** 次（{100-o6['rate']:.1f}%）未填权")
    if o6["rate"] >= 50:
        lines.append("- 半年维度上多数除权缺口被填补，红利策略中长期持有体验优于短期")
    else:
        lines.append("- 即使半年维度，仍有超过一半的除权事件未填权，红利股的价格恢复能力有限")
    lines.append("")
    lines.append("*报告自动生成*")

    report = "\n".join(lines)
    report_path = DOCS_DIR / "2026-07-03_800红利填权统计_2周vs半年.md"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"✅ 对比报告已输出: {report_path}")


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def run_single_horizon(
    holding_index: Dict,
    trade_days: List[str],
    stock_names: Dict[str, str],
    max_days: int,
    horizon_label: str,
    report_filename: str,
    target_pct: float = 0.0,
):
    """执行单窗口统计并生成报告"""
    target_desc = f"（超过除权前 +{target_pct:.0f}%）" if target_pct > 0 else ""
    print(f"\n{'='*65}")
    print(f"  窗口: {horizon_label}（{max_days} 个交易日）{target_desc}")
    print(f"{'='*65}")
    events = process_all_events(holding_index, trade_days, stock_names, max_days, target_pct)
    print(f"  📊 总事件: {len(events)}")

    overall, yearly = generate_stats(events)
    label_full = horizon_label + (f"，目标>除权前{target_pct:.0f}%" if target_pct > 0 else "")
    print_results(overall, yearly, events, label_full)

    report = generate_report(overall, yearly, events, holding_index, label_full)
    report_path = DOCS_DIR / report_filename
    with open(report_path, "w") as f:
        f.write(report)
    print(f"✅ 报告已输出: {report_path}")

    return events, overall, yearly


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="800红利持仓期间分红除权填权统计"
    )
    parser.add_argument(
        "--horizon",
        choices=["2w", "6m", "both"],
        default="both",
        help="观察窗口: 2w=2周(10个交易日), 6m=半年(~126个交易日), both=两者对比 (默认: both)",
    )
    args = parser.parse_args()

    print("=" * 65)
    print("  中证红利800 — 持仓期间除权填权统计")
    print("=" * 65)
    print()

    # 1. 加载数据
    print("📂 Step 1: 加载数据...")
    baskets = load_baskets()
    trade_days = load_trade_cal()
    stock_names = load_stock_names()
    print(f"✅ 股票名称映射: {len(stock_names)} 只")

    # 2. 构建持仓区间索引
    print("\n📂 Step 2: 构建持仓区间索引...")
    holding_index = build_holding_index(baskets)
    n_stocks = len(holding_index)
    total_intervals = sum(len(v) for v in holding_index.values())
    print(f"✅ 持仓股票: {n_stocks} 只, 总持仓区间: {total_intervals}")

    if args.horizon == "2w":
        run_single_horizon(
            holding_index, trade_days, stock_names,
            max_days=10, horizon_label="10个交易日(2周)",
            report_filename="2026-07-03_800红利填权统计.md",
        )
    elif args.horizon == "6m":
        run_single_horizon(
            holding_index, trade_days, stock_names,
            max_days=126, horizon_label="~126个交易日(半年)",
            report_filename="2026-07-03_800红利填权统计_半年.md",
        )
    else:
        # both: 先跑2周，再跑半年，输出对比
        events_2w, overall_2w, yearly_2w = run_single_horizon(
            holding_index, trade_days, stock_names,
            max_days=10, horizon_label="10个交易日(2周)",
            report_filename="2026-07-03_800红利填权统计.md",
        )
        events_6m, overall_6m, yearly_6m = run_single_horizon(
            holding_index, trade_days, stock_names,
            max_days=126, horizon_label="~126个交易日(半年)",
            report_filename="2026-07-03_800红利填权统计_半年.md",
        )

        # 生成对比报告
        print(f"\n{'='*65}")
        print("  📊 生成 2周 vs 半年 对比报告")
        print(f"{'='*65}")
        generate_comparison_report(
            (overall_2w, yearly_2w),
            (overall_6m, yearly_6m),
            events_2w, events_6m,
        )

    print("\n✅ 全部完成!")


if __name__ == "__main__":
    main()
