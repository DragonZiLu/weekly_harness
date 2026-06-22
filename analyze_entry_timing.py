"""
analyze_entry_timing.py — 300万买入515180：一次性 vs 分批 vs 等回调
=================================================================

场景：持有300万现金，计划全部投入515180。
问题：是一次性买入、分批定投、还是等回调再买？

分析维度：
  1. 任意时点一次性买入，滚动1Y/2Y/3Y收益分布
  2. 一次性 vs 分12/24个月定投（同一入场时点）
  3. 等回调N%再买 vs 直接买（历史胜率）
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import tushare as ts

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))


def init_tushare():
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


def fetch_nav(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    df = pro.fund_nav(
        ts_code=ts_code,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    df = df.drop_duplicates(subset="nav_date", keep="last")
    df = df.sort_values("nav_date").reset_index(drop=True)
    df["nav_date"] = df["nav_date"].astype(str)
    df["dt"] = pd.to_datetime(df["nav_date"])
    df["adj_nav"] = pd.to_numeric(df["adj_nav"], errors="coerce")
    return df.dropna(subset=["adj_nav"])


def find_nearest_date(df: pd.DataFrame, target_date: str) -> int:
    """找到最近交易日索引"""
    target = pd.Timestamp(target_date)
    diffs = abs(df["dt"] - target)
    return int(diffs.idxmin())


def forward_days_to_idx(df: pd.DataFrame, start_idx: int, days: int) -> int:
    """从start_idx开始，找days个自然日后的最近交易日索引"""
    target = df.iloc[start_idx]["dt"] + pd.Timedelta(days=days)
    diffs = abs(df["dt"] - target)
    candidates = df.index[df.index >= start_idx]
    if len(candidates) == 0:
        return start_idx
    subset = diffs.loc[candidates]
    return int(subset.idxmin())


def calc_lump_sum_return(df, start_idx: int, hold_months: int) -> dict:
    """一次性买入，持有N个月的收益"""
    end_target = df.iloc[start_idx]["dt"] + pd.DateOffset(months=hold_months)
    # 找最近交易日
    diffs = abs(df["dt"] - end_target)
    candidates = df.index[df.index > start_idx]
    if len(candidates) == 0:
        return None
    subset = diffs.loc[candidates]
    end_idx = int(subset.idxmin())
    
    start_nav = df.iloc[start_idx]["adj_nav"]
    end_nav = df.iloc[end_idx]["adj_nav"]
    total_return = (end_nav / start_nav - 1) * 100
    years = hold_months / 12
    annual_return = ((end_nav / start_nav) ** (1 / years) - 1) * 100
    
    return {
        "start_date": df.iloc[start_idx]["nav_date"],
        "end_date": df.iloc[end_idx]["nav_date"],
        "start_nav": start_nav,
        "end_nav": end_nav,
        "total_return": total_return,
        "annual_return": annual_return,
        "hold_months": hold_months,
    }


def calc_dca_return(df, start_idx: int, total_capital: float, dca_months: int, 
                     hold_after_months: int = 0) -> dict:
    """
    分批定投：total_capital分dca_months个月投入，之后持有hold_after_months月
    """
    monthly_amount = total_capital / dca_months
    shares = 0.0
    cash_invested = 0.0
    remaining_cash = total_capital
    
    # 找每月第一个交易日
    monthly_dates = {}
    for idx in range(start_idx, len(df)):
        if idx - start_idx > dca_months * 31:  # 最多跨越 dca_months+1 个自然月
            break
        dt = df.iloc[idx]["dt"]
        ym = str(dt.to_period("M"))
        if ym not in monthly_dates:
            monthly_dates[ym] = idx
    
    # 取前 dca_months 个月
    month_keys = sorted(monthly_dates.keys())[:dca_months]
    
    for i, ym in enumerate(month_keys):
        idx = monthly_dates[ym]
        nav = float(df.iloc[idx]["adj_nav"])
        buy_shares = monthly_amount / nav
        shares += buy_shares
        cash_invested += monthly_amount
        remaining_cash -= monthly_amount
        # 最后买入日期
        last_buy_idx = idx
    
    # 定投结束后持有hold_after_months
    if hold_after_months > 0:
        end_target = df.iloc[last_buy_idx]["dt"] + pd.DateOffset(months=hold_after_months)
        diffs = abs(df["dt"] - end_target)
        candidates = df.index[df.index > last_buy_idx]
        if len(candidates) == 0:
            end_idx = last_buy_idx
        else:
            subset = diffs.loc[candidates]
            end_idx = int(subset.idxmin())
    else:
        end_idx = last_buy_idx
    
    final_nav = float(df.iloc[end_idx]["adj_nav"])
    final_value = shares * final_nav + remaining_cash
    total_return = (final_value / total_capital - 1) * 100
    
    return {
        "start_date": df.iloc[start_idx]["nav_date"],
        "end_date": df.iloc[end_idx]["nav_date"],
        "dca_months": dca_months,
        "hold_after": hold_after_months,
        "total_invested": cash_invested,
        "final_value": final_value,
        "total_return": total_return,
    }


def calc_wait_for_dip(df, start_idx: int, dip_pct: float, max_wait_days: int = 365,
                       hold_months: int = 12) -> dict:
    """
    等回调：从start_idx开始等，直到价格跌dip_pct%或超过max_wait_days天，
    然后一次性买入，持有hold_months
    """
    start_nav = float(df.iloc[start_idx]["adj_nav"])
    target_nav = start_nav * (1 - dip_pct / 100)
    
    buy_idx = None
    waited_days = 0
    
    for idx in range(start_idx, min(start_idx + max_wait_days, len(df))):
        nav = float(df.iloc[idx]["adj_nav"])
        waited_days = (df.iloc[idx]["dt"] - df.iloc[start_idx]["dt"]).days
        if nav <= target_nav:
            buy_idx = idx
            break
    
    if buy_idx is None:
        # 超过等待期没等到，按最后一天买入
        buy_idx = min(start_idx + max_wait_days, len(df) - 1)
        dip_triggered = False
    else:
        dip_triggered = True
    
    buy_nav = float(df.iloc[buy_idx]["adj_nav"])
    
    # 持有hold_months
    end_target = df.iloc[buy_idx]["dt"] + pd.DateOffset(months=hold_months)
    diffs = abs(df["dt"] - end_target)
    candidates = df.index[df.index > buy_idx]
    if len(candidates) == 0:
        return None
    subset = diffs.loc[candidates]
    end_idx = int(subset.idxmin())
    end_nav = float(df.iloc[end_idx]["adj_nav"])
    
    buy_return = (end_nav / buy_nav - 1) * 100
    
    # 对比：如果直接买
    direct_end_target = df.iloc[start_idx]["dt"] + pd.DateOffset(months=hold_months)
    diffs2 = abs(df["dt"] - direct_end_target)
    candidates2 = df.index[df.index > start_idx]
    if len(candidates2) == 0:
        direct_return = 0
    else:
        subset2 = diffs2.loc[candidates2]
        direct_end_idx = int(subset2.idxmin())
        direct_end_nav = float(df.iloc[direct_end_idx]["adj_nav"])
        direct_return = (direct_end_nav / start_nav - 1) * 100
    
    return {
        "start_date": df.iloc[start_idx]["nav_date"],
        "start_nav": start_nav,
        "dip_pct": dip_pct,
        "dip_triggered": dip_triggered,
        "buy_date": df.iloc[buy_idx]["nav_date"],
        "buy_nav": buy_nav,
        "waited_days": waited_days,
        "end_date": df.iloc[end_idx]["nav_date"],
        "buy_return": buy_return,
        "direct_return": direct_return,
        "advantage": buy_return - direct_return,
    }


def run_analysis(ts_code: str = "515180.SH", start: str = "2020-01-01", end: str = "2026-06-14"):
    """主分析流程"""
    print("=" * 70)
    print("  300万买入515180：一次性 vs 分批 vs 等回调")
    print(f"  数据区间: {start} → {end}")
    print("=" * 70)
    
    pro = init_tushare()
    df = fetch_nav(pro, ts_code, start, end)
    print(f"\n  净值数据: {len(df)} 行")
    
    total_capital = 3_000_000  # 300万
    
    # ═══════════════════════════════════════════════════
    # 分析1: 滚动买入收益分布
    # ═══════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("  分析1: 任意时点一次性买入，滚动持有1Y/2Y/3Y收益分布")
    print("-" * 70)
    
    for hold_months in [12, 24, 36]:
        results = []
        step = 20  # 每隔20个交易日取样
        for start_idx in range(0, len(df) - hold_months * 25, step):
            r = calc_lump_sum_return(df, start_idx, hold_months)
            if r:
                results.append(r)
        
        if results:
            returns = [r["total_return"] for r in results]
            annuals = [r["annual_return"] for r in results]
            
            win_rate = sum(1 for x in returns if x > 0) / len(returns) * 100
            
            print(f"\n  📊 持{hold_months}个月（{len(results)}个样本）:")
            print(f"     平均总收益: {np.mean(returns):+.1f}%  "
                  f"中位数: {np.median(returns):+.1f}%")
            print(f"     平均年化:   {np.mean(annuals):+.1f}%  "
                  f"中位数: {np.median(annuals):+.1f}%")
            print(f"     最差: {np.min(returns):+.1f}%  "
                  f"最佳: {np.max(returns):+.1f}%")
            print(f"     盈利概率: {win_rate:.0f}%")
            print(f"     标准差: {np.std(returns):.1f}%")
    
    # ═══════════════════════════════════════════════════
    # 分析2: 一次性 vs 分12/24月定投
    # ═══════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("  分析2: 同一时点入场，一次性 vs 分12/24个月定投")
    print("         （定投+持有至与一次性相同终点）")
    print("-" * 70)
    
    for hold_months in [12, 24, 36]:
        lump_results = []
        dca12_results = []
        dca24_results = []
        dca_wins = 0
        step = 30
        
        for start_idx in range(0, len(df) - hold_months * 30, step):
            # 一次性买入
            lump = calc_lump_sum_return(df, start_idx, hold_months)
            if not lump:
                continue
            
            # DCA: 先定投，剩余时间持有（总时间=一次性持有时间）
            dca_duration = min(12, hold_months)
            hold_after = hold_months - dca_duration
            dca12 = calc_dca_return(df, start_idx, total_capital, dca_duration, max(0, hold_after))
            
            dca_duration2 = min(24, hold_months)
            hold_after2 = hold_months - dca_duration2
            dca24 = calc_dca_return(df, start_idx, total_capital, dca_duration2, max(0, hold_after2))
            
            if dca12 and dca12["total_return"] > lump["total_return"]:
                dca_wins += 1
            
            lump_results.append(lump)
            dca12_results.append(dca12)
            dca24_results.append(dca24)
        
        if lump_results and dca12_results:
            lump_rets = [r["total_return"] for r in lump_results]
            dca12_rets = [r["total_return"] for r in dca12_results if r]
            dca24_rets = [r["total_return"] for r in dca24_results if r]
            
            dca12_win_rate = sum(1 for a, b in zip(dca12_rets, lump_rets) if a > b) / len(lump_rets) * 100
            
            print(f"\n  📊 持有{hold_months}个月（{len(lump_results)}个样本）:")
            print(f"     一次性买入 平均收益: {np.mean(lump_rets):+.1f}%  "
                  f"中位数: {np.median(lump_rets):+.1f}%")
            print(f"     分12月定投   平均收益: {np.mean(dca12_rets):+.1f}%  "
                  f"中位数: {np.median(dca12_rets):+.1f}%")
            if dca24_rets:
                print(f"     分24月定投   平均收益: {np.mean(dca24_rets):+.1f}%  "
                      f"中位数: {np.median(dca24_rets):+.1f}%")
            print(f"     分12月定投跑赢一次性的比例: {dca12_win_rate:.0f}%")
            
            # DCA与lump的差异
            diffs = [a - b for a, b in zip(dca12_rets, lump_rets)]
            print(f"     DCA-lump差异: 均值{np.mean(diffs):+.1f}%, "
                  f"最差{np.min(diffs):+.1f}%, 最佳{np.max(diffs):+.1f}%")
    
    # ═══════════════════════════════════════════════════
    # 分析3: 等回调 vs 直接买
    # ═══════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("  分析3: 等跌N%再买 vs 现价直接买（持有一年）")
    print("-" * 70)
    
    for dip_pct in [3, 5, 8, 10, 15]:
        results = []
        step = 10
        for start_idx in range(0, len(df) - 400, step):
            r = calc_wait_for_dip(df, start_idx, dip_pct, max_wait_days=365, hold_months=12)
            if r:
                results.append(r)
        
        if results:
            advantages = [r["advantage"] for r in results]
            triggered = [r for r in results if r["dip_triggered"]]
            not_triggered = [r for r in results if not r["dip_triggered"]]
            
            trigger_rate = len(triggered) / len(results) * 100
            
            print(f"\n  📊 等跌{dip_pct}%（{len(results)}个样本）:")
            print(f"     回调触发率: {trigger_rate:.0f}%")
            print(f"     平均等待天数: {np.mean([r['waited_days'] for r in results]):.0f}天")
            print(f"     等回调 vs 直接买 优势: 均值{np.mean(advantages):+.1f}%  "
                  f"中位数{np.median(advantages):+.1f}%")
            
            if triggered:
                trig_adv = [r["advantage"] for r in triggered]
                print(f"     回调触发时 优势: 均值{np.mean(trig_adv):+.1f}%  "
                      f"最佳{np.max(trig_adv):+.1f}% 最差{np.min(trig_adv):+.1f}%")
            if not_triggered:
                notrig_adv = [r["advantage"] for r in not_triggered]
                print(f"     未触发时 优势:   均值{np.mean(notrig_adv):+.1f}%  "
                      f"（被迫买入或踏空）")
            
            win_rate = sum(1 for a in advantages if a > 0) / len(advantages) * 100
            print(f"     等回调胜率: {win_rate:.0f}%")
    
    # ═══════════════════════════════════════════════════
    # 分析4: 当前时点情景分析
    # ═══════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("  分析4: 当前估值位置与历史情景")
    print("-" * 70)
    
    current_nav = float(df.iloc[-1]["adj_nav"])
    
    # 各分位数
    nav_series = df["adj_nav"].values
    pct_90 = np.percentile(nav_series, 90)
    pct_70 = np.percentile(nav_series, 70)
    pct_50 = np.percentile(nav_series, 50)
    pct_30 = np.percentile(nav_series, 30)
    pct_10 = np.percentile(nav_series, 10)
    
    current_pct = sum(nav_series < current_nav) / len(nav_series) * 100
    
    # 历史高位以来的最大回撤
    hist_high = np.max(nav_series[:len(nav_series)])
    high_idx = np.argmax(nav_series[:len(nav_series)])
    high_date = df.iloc[high_idx]["nav_date"]
    dd_from_high = (current_nav / hist_high - 1) * 100
    
    print(f"\n  当前复权净值: {current_nav:.4f}")
    print(f"  历史最高: {hist_high:.4f}（{high_date}）")
    print(f"  距历史最高: {dd_from_high:+.1f}%")
    print(f"  当前估值分位: {current_pct:.0f}%（高于历史{current_pct:.0f}%的时间）")
    
    print(f"\n  历史分位数:")
    print(f"    P90: {pct_90:.4f}  P70: {pct_70:.4f}  P50: {pct_50:.4f}")
    print(f"    P30: {pct_30:.4f}  P10: {pct_10:.4f}")
    
    # 找出历史上当前位置附近（±3%）的时点，看后续收益
    nav_lower = current_nav * 0.97
    nav_upper = current_nav * 1.03
    similar_idxs = [i for i, nav in enumerate(nav_series) 
                    if nav_lower <= nav <= nav_upper and i < len(nav_series) - 250]
    
    if similar_idxs:
        fwd_returns = []
        for idx in similar_idxs:
            r = calc_lump_sum_return(df, idx, 12)
            if r:
                fwd_returns.append(r["total_return"])
        
        if fwd_returns:
            print(f"\n  历史上类似估值水平（±3%）买入后1年收益（{len(fwd_returns)}个样本）:")
            print(f"    平均: {np.mean(fwd_returns):+.1f}%  中位数: {np.median(fwd_returns):+.1f}%")
            print(f"    最佳: {np.max(fwd_returns):+.1f}%  最差: {np.min(fwd_returns):+.1f}%")
            print(f"    盈利概率: {sum(1 for x in fwd_returns if x>0)/len(fwd_returns)*100:.0f}%")
    
    print("\n" + "=" * 70)
    print("  分析完成")
    print("=" * 70)


if __name__ == "__main__":
    run_analysis()
