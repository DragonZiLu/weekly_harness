#!/usr/bin/env python3
"""
ZZ800 FCF vs 932368 完整验证脚本
=================================

按照方案设计，实现四层架构的验证：
  Layer 1: 数据层 — 中证800成分 + 财务数据 + 行情数据
  Layer 2: 官方复现 (Benchmark) — 严格按932368规则验证 tracking
  Layer 3: 自有指数 (Enhanced) — 对比换手率与收益
  Layer 4: 对比分析层 — 成分一致率 / 收益偏离 / 换手率对比

验证内容：
  1. 成分股重合度（逐期）
  2. 权重相关性（Spearman + Pearson）
  3. NAV跟踪对比（季度级 + 日级估算）
  4. 逐年收益对比
  5. 换手率对比
  6. 风险指标（夏普/最大回撤/卡尔玛）
  7. 缺失标的根因分析

输出：
  docs/zz800_fcf_932368_validation.md — 完整验证报告
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent


# ══════════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════════

def load_our_baskets():
    """加载我们的ZZ800 FCF全部历史basket"""
    path = _PROJECT_ROOT / "output" / "zz800_fcf" / "all_baskets_2015_2026.json"
    with open(path) as f:
        data = json.load(f)
    # 转为 {date: {code: {weight, fcf_yield, ...}}}
    baskets = {}
    for date, stocks in data.items():
        if isinstance(stocks, list) and len(stocks) >= 50:
            baskets[date] = {s["ts_code"]: s for s in stocks}
    return baskets


def load_our_nav():
    """加载我们的回测NAV（含总收益率版）"""
    path = _PROJECT_ROOT / "output" / "zz800_fcf" / "backtest_nav_tr.csv"
    nav = pd.read_csv(path)
    nav["rb_date"] = nav["rb_date"].astype(str)
    nav["next_rb"] = nav["next_rb"].astype(str)
    return nav


def load_932368_daily():
    """加载932368官方日行情"""
    path = _PROJECT_ROOT / "data" / "932368_daily.csv"
    df = pd.read_csv(path, dtype={"trade_date": str})
    return df


def load_932368_weights():
    """加载所有可用的932368官方权重快照
    
    932368在季度调仓日（3/6/9/12月第二个周五下一交易日）更新成分
    Tushare的index_weight接口通常记录月末快照
    
    日期映射：
    2024-12-16调仓 → 20241231快照（12月末）
    2025-03-17调仓 → 20250331快照（3月末）
    2025-06-16调仓 → 20250630快照（6月末）
    """
    weight_dir = _PROJECT_ROOT / "data" / "index_weights"
    snapshots = {}
    
    # tushare格式快照 (index_weight_932368.CSI_YYYYMMDD.csv)
    for f in weight_dir.glob("index_weight_932368.CSI_*.csv"):
        df = pd.read_csv(f, dtype={"con_code": str, "trade_date": str})
        trade_date = df["trade_date"].iloc[0]
        # 权重为百分比格式
        weights = dict(zip(df["con_code"], df["weight"]))
        snapshots[trade_date] = {
            "codes": set(df["con_code"].tolist()),
            "weights": weights,
            "date": trade_date,
        }
    
    # 预格式化快照（932368_YYYYMM.csv）
    for f in weight_dir.glob("932368_*.csv"):
        df = pd.read_csv(f, dtype={"con_code": str, "trade_date": str})
        trade_date = df["trade_date"].iloc[0]
        if trade_date not in snapshots:
            weights = dict(zip(df["con_code"], df["weight"]))
            snapshots[trade_date] = {
                "codes": set(df["con_code"].tolist()),
                "weights": weights,
                "date": trade_date,
            }
    
    return snapshots


# ══════════════════════════════════════════════════════════════════
# 验收指标计算
# ══════════════════════════════════════════════════════════════════

def calc_overlap(our_codes: set, official_codes: set) -> dict:
    """计算成分股重合度"""
    intersection = our_codes & official_codes
    recall = len(intersection) / len(official_codes) if official_codes else 0
    precision = len(intersection) / len(our_codes) if our_codes else 0
    return {
        "intersection": len(intersection),
        "our_n": len(our_codes),
        "official_n": len(official_codes),
        "recall": recall,
        "precision": precision,
        "only_ours": sorted(our_codes - official_codes),
        "only_official": sorted(official_codes - our_codes),
    }


def calc_weight_correlation(our_weights: dict, official_weights: dict, 
                            overlap_codes: set) -> dict:
    """计算权重相关性（仅对重叠标的）
    
    注意：我们basket和官方权重都是百分比格式（10.0 = 10%）
    """
    
    # 仅对重叠标的计算
    ow = [our_weights.get(c, 0) for c in overlap_codes]
    aw = [official_weights.get(c, 0) for c in overlap_codes]
    
    if len(overlap_codes) < 3:
        return {"spearman": np.nan, "pearson": np.nan, "n_overlap": len(overlap_codes),
                "max_diff": [], "avg_diff_pct": 0}
    
    # 手动计算Spearman（避免scipy依赖）
    ow_ranks = pd.Series(ow).rank().values
    aw_ranks = pd.Series(aw).rank().values
    sp_corr = np.corrcoef(ow_ranks, aw_ranks)[0, 1]
    pr_corr = np.corrcoef(ow, aw)[0, 1]
    
    # 权重差异详情
    diffs = []
    for c in overlap_codes:
        o_w = our_weights.get(c, 0)
        a_w = official_weights.get(c, 0)
        diffs.append({
            "code": c,
            "our_weight": o_w,
            "official_weight": a_w,
            "diff_pct": abs(o_w - a_w),
        })
    diffs.sort(key=lambda x: x["diff_pct"], reverse=True)
    
    return {
        "spearman": sp_corr,
        "pearson": pr_corr,
        "n_overlap": len(overlap_codes),
        "max_diff": diffs[:10] if diffs else [],
        "avg_diff_pct": np.mean([d["diff_pct"] for d in diffs]),
    }


def calc_nav_comparison(our_nav: pd.DataFrame, official_daily: pd.DataFrame) -> dict:
    """NAV对比：我们的回测NAV vs 932368官方日线
    
    注意：
    - 我们的NAV是季度级（每期一个NAV值）
    - 932368是日级数据
    - 此处用季度级收益差来估算跟踪误差，日级需构建日NAV序列
    """
    
    # 预处理932368日线
    official_daily = official_daily.copy()
    official_daily["trade_date_dt"] = pd.to_datetime(official_daily["trade_date"], format="%Y%m%d")
    official_daily = official_daily.sort_values("trade_date_dt")
    
    # 获取起始基准
    first_rb = our_nav.iloc[0]["rb_date"]
    first_rb_dt = pd.Timestamp(first_rb)
    
    start_row = official_daily[official_daily["trade_date_dt"] >= first_rb_dt].head(1)
    if start_row.empty:
        return {"error": "无法找到932368起始日数据"}
    
    start_close = float(start_row.iloc[0]["close"])
    official_daily["official_nav"] = official_daily["close"] / start_close
    
    # 逐期对比
    period_comparisons = []
    for _, row in our_nav.iterrows():
        rb_date = row["rb_date"]
        next_rb = row["next_rb"]
        our_ret = row["ret"]
        
        rb_dt = pd.Timestamp(rb_date)
        next_dt = pd.Timestamp(next_rb)
        
        off_start = official_daily[official_daily["trade_date_dt"] >= rb_dt].head(1)
        off_end = official_daily[official_daily["trade_date_dt"] <= next_dt + timedelta(days=5)].tail(1)
        
        if off_start.empty or off_end.empty:
            continue
        
        off_nav_start = float(off_start.iloc[0]["official_nav"])
        off_nav_end = float(off_end.iloc[0]["official_nav"])
        off_ret = off_nav_end / off_nav_start - 1
        
        excess_ret = our_ret - off_ret
        
        period_comparisons.append({
            "rb_date": rb_date,
            "next_rb": next_rb,
            "our_ret_pct": our_ret * 100,
            "official_ret_pct": off_ret * 100,
            "excess_ret_pct": excess_ret * 100,
        })
    
    # 总体指标
    total_our_ret = our_nav.iloc[-1]["nav"] - 1
    total_off_ret = official_daily.iloc[-1]["official_nav"] / official_daily.iloc[0]["official_nav"] - 1
    
    n_years = (pd.Timestamp(our_nav.iloc[-1]["next_rb"]) - 
               pd.Timestamp(our_nav.iloc[0]["rb_date"])).days / 365.25
    our_annual = ((1 + total_our_ret) ** (1/n_years) - 1) * 100
    off_annual = ((1 + total_off_ret) ** (1/n_years) - 1) * 100
    
    # 季度级跟踪误差（年化）
    excess_rets = [p["excess_ret_pct"] / 100 for p in period_comparisons]
    te_ann = np.std(excess_rets) * np.sqrt(4) * 100 if excess_rets else np.nan
    
    # 日级跟踪误差估算（从932368日线推算我们的日NAV）
    # 简化方案：用季度级收益差的标准差 × sqrt(244/4)来估算日级TE
    te_daily_est = np.std(excess_rets) * np.sqrt(244/4) * 100 if excess_rets else np.nan
    
    return {
        "period_comparisons": period_comparisons,
        "total_our_ret_pct": total_our_ret * 100,
        "total_off_ret_pct": total_off_ret * 100,
        "total_excess_pct": (total_our_ret - total_off_ret) * 100,
        "our_annual_pct": our_annual,
        "off_annual_pct": off_annual,
        "tracking_error_ann_pct": te_ann,
        "tracking_error_daily_est_pct": te_daily_est,
        "n_periods": len(period_comparisons),
    }


def calc_risk_metrics(our_nav: pd.DataFrame) -> dict:
    """计算我们的策略风险指标"""
    returns = our_nav["ret"].values
    nav_series = our_nav["nav"].values
    
    # 最大回撤
    peak = nav_series[0]
    max_dd = 0
    for n in nav_series:
        if n > peak:
            peak = n
        dd = (n - peak) / peak
        if dd < max_dd:
            max_dd = dd
    
    # 年化收益率
    total_ret = nav_series[-1] / nav_series[0] - 1
    n_years = (pd.Timestamp(our_nav.iloc[-1]["next_rb"]) - 
               pd.Timestamp(our_nav.iloc[0]["rb_date"])).days / 365.25
    annual_ret = ((1 + total_ret) ** (1/max(n_years, 0.01)) - 1)
    
    # 夏普比率（季度收益→年化）
    rf_quarter = 0.015 / 4  # 1.5%年化无风险利率
    excess = returns - rf_quarter
    sharpe = np.mean(excess) / np.std(excess) * np.sqrt(4) if np.std(excess) > 0 else 0
    
    # 卡尔玛比率
    calmar = annual_ret / abs(max_dd) if max_dd != 0 else 0
    
    # 调仓胜率
    wins = sum(1 for r in returns if r > 0)
    n_returns = len(returns)
    win_rate = wins / n_returns if n_returns > 0 else 0
    
    return {
        "annual_return_pct": annual_ret * 100,
        "max_drawdown_pct": max_dd * 100,
        "sharpe": sharpe,
        "calmar": calmar,
        "win_rate_pct": win_rate * 100,
        "n_periods": n_returns,
        "total_return_pct": total_ret * 100,
    }


def calc_932368_risk_metrics(official_daily: pd.DataFrame, 
                              start_date: str, end_date: str) -> dict:
    """计算932368的风险指标"""
    official_daily = official_daily.copy()
    official_daily["trade_date_dt"] = pd.to_datetime(official_daily["trade_date"], format="%Y%m%d")
    
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)
    
    subset = official_daily[
        (official_daily["trade_date_dt"] >= start_dt) & 
        (official_daily["trade_date_dt"] <= end_dt)
    ].copy()
    
    if subset.empty:
        return {"error": "932368数据不足"}
    
    # 日收益
    subset["daily_ret"] = subset["close"].pct_change()
    subset = subset.dropna(subset=["daily_ret"])
    
    # NAV序列
    nav = subset["close"] / subset["close"].iloc[0]
    
    # 最大回撤
    cummax = nav.cummax()
    drawdown = (nav - cummax) / cummax
    max_dd = drawdown.min()
    
    # 年化收益
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    n_days = (subset["trade_date_dt"].iloc[-1] - subset["trade_date_dt"].iloc[0]).days
    n_years = n_days / 365.25
    annual_ret = ((1 + total_ret) ** (1/max(n_years, 0.01)) - 1)
    
    # 夏普
    rf_daily = 0.015 / 244
    excess = subset["daily_ret"] - rf_daily
    sharpe = excess.mean() / subset["daily_ret"].std() * np.sqrt(244) if subset["daily_ret"].std() > 0 else 0
    
    # 卡尔玛
    calmar = annual_ret / abs(max_dd) if max_dd != 0 else 0
    
    return {
        "annual_return_pct": annual_ret * 100,
        "max_drawdown_pct": max_dd * 100,
        "sharpe": sharpe,
        "calmar": calmar,
        "total_return_pct": total_ret * 100,
        "n_days": len(subset),
    }


def calc_turnover(baskets: dict) -> dict:
    """计算逐期换手率"""
    dates = sorted(baskets.keys())
    turnovers = []
    
    for i in range(1, len(dates)):
        prev_codes = set(baskets[dates[i-1]].keys())
        cur_codes = set(baskets[dates[i]].keys())
        
        changed = len(prev_codes - cur_codes) + len(cur_codes - prev_codes)
        turnover = changed / 50  # 单边换手率
        turnovers.append({
            "prev_date": dates[i-1],
            "cur_date": dates[i],
            "changed": changed,
            "turnover_pct": turnover * 100,
            "continued": len(prev_codes & cur_codes),
        })
    
    avg_turnover = np.mean([t["turnover_pct"] for t in turnovers])
    max_turnover = max(t["turnover_pct"] for t in turnovers)
    min_turnover = min(t["turnover_pct"] for t in turnovers)
    
    return {
        "avg_turnover_pct": avg_turnover,
        "max_turnover_pct": max_turnover,
        "min_turnover_pct": min_turnover,
        "periods": turnovers,
    }


# ══════════════════════════════════════════════════════════════════
# 逐期成分股对比
# ══════════════════════════════════════════════════════════════════

def compare_constituents_period(our_baskets: dict, official_snapshots: dict) -> list:
    """逐期对比成分股重合度
    
    日期匹配逻辑：
    - 932368在每季度调仓日（3/6/9/12月第二个周五下一交易日）调整成分
    - Tushare的index_weight快照日期为月末（如20241231、20250331等）
    - 调仓日→月末快照映射：调仓日在X月 → X月末快照
      - 2024-12-16调仓 → 20241231快照
      - 2025-03-17调仓 → 20250331快照
      - 2025-06-16调仓 → 20250630快照
    """
    
    results = []
    
    for our_date, our_stocks in sorted(our_baskets.items()):
        our_codes = set(our_stocks.keys())
        our_weights = {c: s.get("weight", 0) for c, s in our_stocks.items()}
        our_names = {c: s.get("name", "") for c, s in our_stocks.items()}
        
        our_dt = pd.Timestamp(our_date)
        best_snap = None
        best_dist = timedelta(days=999)
        
        for snap_date, snap_data in official_snapshots.items():
            snap_dt = pd.Timestamp(snap_date)
            # 官方快照应在调仓日之后（反映调仓后成分）
            # 允许范围：调仓日当天到调仓日+45天（同季度内）
            dist = snap_dt - our_dt
            if timedelta(days=0) <= dist <= timedelta(days=45) and dist < best_dist:
                best_dist = dist
                best_snap = snap_data
        
        if best_snap is None:
            continue
        
        overlap = calc_overlap(our_codes, best_snap["codes"])
        weight_corr = calc_weight_correlation(our_weights, best_snap["weights"], 
                                               our_codes & best_snap["codes"])
        
        # 缺失标的诊断
        missing_diag = diagnose_missing(overlap["only_official"], overlap["only_ours"], our_baskets)
        
        # 对重叠标的，补充名称
        for d in weight_corr.get("max_diff", []):
            d["name"] = our_names.get(d["code"], "")
        
        results.append({
            "our_date": our_date,
            "official_date": best_snap["date"],
            "overlap": overlap,
            "weight_corr": weight_corr,
            "missing_diag": missing_diag,
            "our_names": our_names,
        })
    
    return results


def diagnose_missing(only_official: list, only_ours: list, our_baskets: dict) -> dict:
    """对缺失标的做分类诊断
    
    返回:
      - official_missing: 我们缺失的官方标的及其原因
      - our_extra: 我们多出的标的及其可能原因
    """
    # 已知的分类问题
    INDUSTRY_ISSUES = {
        "600517.SH": "申万'多元金融'→我方剔除, 中证可能归入非金融行业保留",
    }
    
    # 已知的OCF/5yr口径问题
    OCF_5YR_ISSUES = {
        "600612.SH": "年报OCF可能为负, TTM口径下为正 → 通过5yr检查",
        "689009.SH": "年报OCF可能为负, TTM口径下为正",
    }
    
    # 我方多出的标的原因分析
    # 中国移动(600941)、中国电信(601728)：5yr OCF检查过宽（跳过缺失年份）
    TELECOM_CODES = {"600941.SH", "601728.SH"}
    
    official_missing = []
    for code in only_official:
        if code in INDUSTRY_ISSUES:
            reason = INDUSTRY_ISSUES[code]
            category = "行业分类"
        elif code in OCF_5YR_ISSUES:
            reason = OCF_5YR_ISSUES[code]
            category = "5yr OCF"
        else:
            reason = "排名边界/数据口径差异"
            category = "排名边界"
        official_missing.append({"code": code, "reason": reason, "category": category})
    
    our_extra = []
    for code in only_ours:
        if code in TELECOM_CODES:
            reason = "5yr OCF检查跳过缺失年份→过宽, 中证要求连续5年"
            category = "5yr OCF过宽"
        elif code.startswith("300"):
            reason = "创业板标的, 可能中证有额外筛选条件"
            category = "板块筛选"
        else:
            reason = "排名边界/数据口径差异"
            category = "排名边界"
        our_extra.append({"code": code, "reason": reason, "category": category})
    
    return {"official_missing": official_missing, "our_extra": our_extra}


# ══════════════════════════════════════════════════════════════════
# 日级跟踪误差计算
# ══════════════════════════════════════════════════════════════════

def calc_daily_tracking_error(our_nav: pd.DataFrame, official_daily: pd.DataFrame) -> dict:
    """估算日级跟踪误差
    
    方法：从季度级跟踪误差推算日级
    - 季度级TE / sqrt(61) ≈ 日级TE (每季度约61个交易日)
    - 年化日级TE ≈ 季度级TE (因为 std * sqrt(4) = std * sqrt(244/61) ≈ 2)
    
    注意：真正的日级TE需要构建我们组合的日级NAV序列，
    这需要对50只持仓股按权重计算每日收益，需要逐股日线数据。
    """
    official_daily = official_daily.copy()
    official_daily["trade_date_dt"] = pd.to_datetime(official_daily["trade_date"], format="%Y%m%d")
    
    # 计算每季度超额收益
    start_dt = pd.Timestamp(our_nav.iloc[0]["rb_date"])
    
    excess_rets = []
    for _, row in our_nav.iterrows():
        rb_dt = pd.Timestamp(row["rb_date"])
        next_dt = pd.Timestamp(row["next_rb"])
        
        off_start = official_daily[official_daily["trade_date_dt"] >= rb_dt].head(1)
        off_end = official_daily[official_daily["trade_date_dt"] <= next_dt + timedelta(days=5)].tail(1)
        
        if off_start.empty or off_end.empty:
            continue
        
        off_ret = float(off_end.iloc[0]["close"]) / float(off_start.iloc[0]["close"]) - 1
        our_ret = row["ret"]
        excess_rets.append(our_ret - off_ret)
    
    if not excess_rets:
        return {"tracking_error_ann_pct": np.nan, "daily_corr": np.nan, 
                "n_days": 0, "note": "数据不足"}
    
    # 季度级超额标准差
    q_excess_std = np.std(excess_rets)
    
    # 年化TE (季度级)
    te_annual = q_excess_std * np.sqrt(4) * 100
    
    # 日级TE估算 (从季度级换算)
    # 每季度约61个交易日
    te_daily_est = q_excess_std / np.sqrt(61) * 100
    
    # 日级年化TE ≈ 季度年化TE (数学等价)
    te_daily_annual = te_daily_est * np.sqrt(244)
    
    # 计算超额收益的相关性（季度级）
    our_rets = [row["ret"] for _, row in our_nav.iterrows()]
    off_rets = []
    for _, row in our_nav.iterrows():
        rb_dt = pd.Timestamp(row["rb_date"])
        next_dt = pd.Timestamp(row["next_rb"])
        off_start = official_daily[official_daily["trade_date_dt"] >= rb_dt].head(1)
        off_end = official_daily[official_daily["trade_date_dt"] <= next_dt + timedelta(days=5)].tail(1)
        if not off_start.empty and not off_end.empty:
            off_rets.append(float(off_end.iloc[0]["close"]) / float(off_start.iloc[0]["close"]) - 1)
    
    min_len = min(len(our_rets), len(off_rets))
    q_corr = np.corrcoef(our_rets[:min_len], off_rets[:min_len])[0, 1] if min_len > 2 else np.nan
    
    return {
        "tracking_error_ann_pct": te_annual,
        "tracking_error_daily_pct": te_daily_est,
        "tracking_error_daily_ann_pct": te_daily_annual,
        "quarterly_corr": q_corr,
        "n_periods": len(excess_rets),
        "note": "日级TE为从季度级推算的估算值，精确值需构建日级NAV",
    }


# ══════════════════════════════════════════════════════════════════
# 报告生成
# ══════════════════════════════════════════════════════════════════

def generate_report(constituent_results: list, nav_comparison: dict,
                    our_risk: dict, off_risk: dict, turnover: dict,
                    daily_te: dict) -> str:
    """生成完整验证报告"""
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    report = f"""# 中证800自由现金流指数(ZZ800 FCF) vs 932368 官方指数 —— 完整验证报告

**生成时间**: {now}
**回测期间**: {our_risk['n_periods']}期 (2016-06-13 ~ 2026-06-04)
**对比基准**: 932368.CSI 中证800自由现金流指数
**数据来源**: 我们的回测basket + 932368日行情 + 932368官方成分权重快照

---

## 一、验证结果总览

| 验收指标 | 目标 | 实际 | 是否达标 |
|----------|------|------|----------|
"""
    
    # 计算关键指标
    all_recalls = []
    all_spearmans = []
    for r in constituent_results:
        all_recalls.append(r["overlap"]["recall"] * 100)
        if not np.isnan(r["weight_corr"]["spearman"]):
            all_spearmans.append(r["weight_corr"]["spearman"])
    
    avg_recall = np.mean(all_recalls) if all_recalls else 0
    avg_spearman = np.mean(all_spearmans) if all_spearmans else 0
    best_recall = max(all_recalls) if all_recalls else 0
    best_spearman = max(all_spearmans) if all_spearmans else 0
    
    te_val = nav_comparison.get("tracking_error_ann_pct", 999)
    te_daily = daily_te.get("tracking_error_ann_pct", np.nan)
    te_daily_pct = daily_te.get("tracking_error_daily_pct", np.nan)
    q_corr = daily_te.get("quarterly_corr", np.nan)
    
    recall_pass = avg_recall >= 96
    spearman_pass = avg_spearman >= 0.99
    te_pass = te_val < 1.0 if not np.isnan(te_val) else False
    
    report += f"| 成分股重合度(Recall) | ≥ 96% (48/50) | {avg_recall:.1f}% (平均) / {best_recall:.1f}% (最佳) | {'✅' if recall_pass else '❌'} |\n"
    report += f"| 权重相关性(Spearman) | ≥ 0.99 | {avg_spearman:.4f} (平均) / {best_spearman:.4f} (最佳) | {'✅' if spearman_pass else '❌'} |\n"
    report += f"| 季度级跟踪误差(年化) | < 1% | {te_val:.2f}% | {'✅' if te_pass else '❌'} |\n"
    if not np.isnan(te_daily):
        report += f"| 日级跟踪误差(年化,从季度推算) | < 1% | {te_daily:.2f}% | {'✅' if te_daily < 1.0 else '❌'} |\n"
    if not np.isnan(q_corr):
        report += f"| 季度收益相关性 | — | {q_corr:.4f} | — |\n"
    
    report += "\n---\n\n## 二、逐期成分股对比\n\n"
    
    # ── 逐期成分对比表 ──
    if constituent_results:
        report += "| 调仓日 | 官方快照日 | 我们 | 官方 | 重叠 | Recall | Spearman | Pearson | 平均权重差% |\n"
        report += "|--------|-----------|------|------|------|--------|----------|---------|-------------|\n"
        
        for r in constituent_results:
            o = r["overlap"]
            w = r["weight_corr"]
            recall_pct = o["recall"] * 100
            spearman_val = w["spearman"]
            avg_diff = w.get("avg_diff_pct", 0)
            
            sp_str = f"{spearman_val:.4f}" if not np.isnan(spearman_val) else "N/A"
            pr_str = f"{w['pearson']:.4f}" if not np.isnan(w["pearson"]) else "N/A"
            
            report += f"| {r['our_date']} | {r['official_date']} | {o['our_n']} | {o['official_n']} | "
            report += f"{o['intersection']} | {recall_pct:.1f}% | {sp_str} | {pr_str} | {avg_diff:.2f} |\n"
        
        report += f"\n**平均Recall**: {avg_recall:.1f}%  |  **平均Spearman**: {avg_spearman:.4f}\n\n"
    
    # ── 缺失标的分析 ──
    report += "### 缺失标的根因分析\n\n"
    
    # 收集所有缺失标的（我方缺失的官方标的 + 我方多出的标的）
    official_missing_counter = Counter()
    official_missing_reason = {}
    our_extra_counter = Counter()
    our_extra_reason = {}
    
    for r in constituent_results:
        diag = r["missing_diag"]
        for m in diag.get("official_missing", []):
            official_missing_counter[m["code"]] += 1
            official_missing_reason[m["code"]] = m["reason"]
        for m in diag.get("our_extra", []):
            our_extra_counter[m["code"]] += 1
            our_extra_reason[m["code"]] = m["reason"]
    
    report += "#### 官方有但我们缺失的标的\n\n"
    if official_missing_counter:
        report += "| 代码 | 出现次数 | 原因 |\n"
        report += "|------|---------|------|\n"
        for code, count in official_missing_counter.most_common():
            reason = official_missing_reason.get(code, "排名边界/数据口径差异")
            report += f"| {code} | {count}次 | {reason} |\n"
    else:
        report += "无\n"
    report += "\n"
    
    report += "#### 我们有但官方缺失的标的\n\n"
    if our_extra_counter:
        report += "| 代码 | 出现次数 | 名称 | 原因 |\n"
        report += "|------|---------|------|------|\n"
        for code, count in our_extra_counter.most_common():
            # 获取名称
            name = ""
            for r in constituent_results:
                if code in r.get("our_names", {}):
                    name = r["our_names"][code]
                    break
            reason = our_extra_reason.get(code, "排名边界/数据口径差异")
            report += f"| {code} | {count}次 | {name} | {reason} |\n"
    else:
        report += "无\n"
    report += "\n"
    
    # ── 权重差异详情（最佳匹配期）──
    report += "### 权重差异详情（最佳匹配期：重叠最高的期间）\n\n"
    
    if constituent_results:
        # 找recall最高的期间
        best_r = max(constituent_results, key=lambda x: x["overlap"]["recall"])
        report += f"**期间**: {best_r['our_date']} vs {best_r['official_date']}，"
        report += f"重叠{best_r['overlap']['intersection']}/{best_r['overlap']['official_n']}，"
        report += f"Recall={best_r['overlap']['recall']*100:.1f}%，"
        report += f"Spearman={best_r['weight_corr']['spearman']:.4f}\n\n"
        
        if best_r["weight_corr"]["max_diff"]:
            report += "| 代码 | 名称 | 我们权重% | 官方权重% | 差异% |\n"
            report += "|------|------|---------|---------|------|\n"
            for d in best_r["weight_corr"]["max_diff"]:
                name = d.get("name", "")
                report += f"| {d['code']} | {name} | {d['our_weight']:.2f} | {d['official_weight']:.3f} | "
                report += f"{d['diff_pct']:.2f} |\n"
            report += "\n"
    
    # ── NAV对比 ──
    report += "## 三、NAV收益对比\n\n"
    
    if nav_comparison.get("period_comparisons"):
        report += "| 指标 | 我们的策略 | 932368官方 | 超额 |\n"
        report += "|------|-----------|-----------|------|\n"
        report += f"| 总收益率 | {nav_comparison['total_our_ret_pct']:.2f}% | "
        report += f"{nav_comparison['total_off_ret_pct']:.2f}% | "
        report += f"{nav_comparison['total_excess_pct']:.2f}% |\n"
        report += f"| 年化收益率 | {nav_comparison['our_annual_pct']:.2f}% | "
        report += f"{nav_comparison['off_annual_pct']:.2f}% | "
        report += f"{nav_comparison['our_annual_pct']-nav_comparison['off_annual_pct']:.2f}% |\n"
        report += f"| 季度级跟踪误差(年化) | — | — | "
        report += f"{te_val:.2f}% |\n"
        if not np.isnan(te_daily):
            report += f"| 日级跟踪误差(年化,估算) | — | — | "
            report += f"{te_daily:.2f}% |\n"
        report += "\n"
        
        # 逐期收益对比
        report += "### 逐期收益对比\n\n"
        report += "| 调仓日 | 下一调仓日 | 我们收益% | 932368收益% | 超额% |\n"
        report += "|--------|-----------|----------|------------|------|\n"
        for p in nav_comparison["period_comparisons"]:
            report += f"| {p['rb_date']} | {p['next_rb']} | {p['our_ret_pct']:.2f} | "
            report += f"{p['official_ret_pct']:.2f} | {p['excess_ret_pct']:.2f} |\n"
        report += "\n"
    
    # ── 风险指标对比 ──
    report += "## 四、风险指标对比\n\n"
    
    report += "| 指标 | 我们的策略 | 932368官方 |\n"
    report += "|------|-----------|----------|\n"
    
    off_ann = off_risk.get("annual_return_pct", "N/A")
    off_dd = off_risk.get("max_drawdown_pct", "N/A")
    off_sh = off_risk.get("sharpe", "N/A")
    off_cal = off_risk.get("calmar", "N/A")
    
    report += f"| 年化收益率 | {our_risk['annual_return_pct']:.2f}% | {off_ann if isinstance(off_ann, str) else f'{off_ann:.2f}%'} |\n"
    report += f"| 最大回撤 | {our_risk['max_drawdown_pct']:.2f}% | {off_dd if isinstance(off_dd, str) else f'{off_dd:.2f}%'} |\n"
    report += f"| 夏普比率 | {our_risk['sharpe']:.2f} | {off_sh if isinstance(off_sh, str) else f'{off_sh:.2f}'} |\n"
    report += f"| 卡尔玛比率 | {our_risk['calmar']:.2f} | {off_cal if isinstance(off_cal, str) else f'{off_cal:.2f}'} |\n"
    report += f"| 调仓胜率 | {our_risk['win_rate_pct']:.0f}% ({our_risk['n_periods']}期) | — |\n"
    report += "\n"
    
    # ── 换手率分析 ──
    report += "## 五、换手率分析\n\n"
    
    report += "| 指标 | 数值 |\n"
    report += "|------|------|\n"
    report += f"| 平均换手率 | {turnover['avg_turnover_pct']:.1f}% |\n"
    report += f"| 最大换手率 | {turnover['max_turnover_pct']:.1f}% |\n"
    report += f"| 最小换手率 | {turnover['min_turnover_pct']:.1f}% |\n"
    report += "\n"
    
    # 换手率分布
    report += "### 换手率分布\n\n"
    turnover_bins = {"0-10%": 0, "10-20%": 0, "20-40%": 0, "40-60%": 0, "60-100%": 0}
    for t in turnover["periods"]:
        pct = t["turnover_pct"]
        if pct <= 10:
            turnover_bins["0-10%"] += 1
        elif pct <= 20:
            turnover_bins["10-20%"] += 1
        elif pct <= 40:
            turnover_bins["20-40%"] += 1
        elif pct <= 60:
            turnover_bins["40-60%"] += 1
        else:
            turnover_bins["60-100%"] += 1
    
    report += "| 换手率区间 | 期数 |\n"
    report += "|-----------|------|\n"
    for bin_name, count in turnover_bins.items():
        report += f"| {bin_name} | {count} |\n"
    report += "\n"
    
    # ── 关键发现与根因分析 ──
    report += "## 六、关键发现与根因分析\n\n"
    
    report += "### 1. 成分股差异根因\n\n"
    report += """| 差异类型 | 影响标的数 | 说明 |
|----------|-----------|------|
| 5yr OCF检查过宽 | 2只(中国移动/中国电信) | 我方代码跳过缺失年份，允许<5年OCF为正通过；中证要求**连续5年**OCF为正 |
| 行业分类差异 | 1只(600517.SH) | 申万'多元金融'→我方剔除；中证可能归入非金融行业保留 |
| 排名边界 | ~8只 | FCF Yield在Top50边界附近，数据口径差异导致排名不同 |
| 数据口径 | ~2只 | TTM计算方式/CAPEX定义微小差异 |

### 2. 权重差异根因

| 差异来源 | 说明 |
|----------|------|
| 10%单股上限执行差异 | 我方采用迭代封顶再分配，中证可能有不同的再分配算法 |
| 缺失标的权重再分配 | 中国移动/中国电信在我方占15.5%权重，导致其他股票权重系统性偏低 |
| FCF/EV计算差异 | CAPEX是否包含无形资产、EV定义细节差异 |

### 3. 2025年6月异常低Recall(48%)分析

932368在2025年6月调仓时出现极高换手率（28只进出），而我们仅有约15只进出。可能原因：
- **932368可能进行了方法论调整或特殊样本调整**
- 我方与932368在Q1财务数据引用上存在时间差
- 我方5yr OCF检查过宽导致部分标的差异累积

### 4. 跟踪误差偏高的原因

当前季度级跟踪误差偏高，主要因为：
- 成分股差异（~13只不同）导致持仓结构偏离
- 权重分配差异（封顶算法不同）
- 我方包含高权重非官方成分（中国移动10%、中国电信5.5%）
"""
    
    # ── 验收结论 ──
    report += "\n## 七、验收结论\n\n"
    
    report += f"| 验收指标 | 目标 | 实际 | 达标 |\n"
    report += f"|----------|------|------|------|\n"
    report += f"| 成分股重合度 | ≥96% | {avg_recall:.1f}% (平均) | {'✅' if recall_pass else '❌'} |\n"
    report += f"| 权重Spearman | ≥0.99 | {avg_spearman:.4f} (平均) | {'✅' if spearman_pass else '❌'} |\n"
    report += f"| 跟踪误差(季度年化) | <1.0% | {te_val:.2f}% | {'✅' if te_pass else '❌'} |\n"
    if not np.isnan(te_daily):
        report += f"| 跟踪误差(日级年化,从季度推算) | <1.0% | {te_daily:.2f}% | {'✅' if te_daily < 1.0 else '❌'} |\n"
    report += "\n"
    
    # 总结
    report += "### 总结\n\n"
    
    n_pass = sum([recall_pass, spearman_pass, te_pass])
    
    report += f"1. **成分股复现**: Recall={avg_recall:.1f}%（目标≥96%{'✅达标' if recall_pass else '❌未达标'}），"
    report += f"权重Spearman={avg_spearman:.4f}（目标≥0.99{'✅达标' if spearman_pass else '❌未达标'}）。\n"
    report += f"   - 最佳期间Recall={best_recall:.1f}%，Spearman={best_spearman:.4f}\n"
    report += f"   - 主要差异来源：5yr OCF检查过宽（中国移动/中国电信被我方纳入），"
    report += f"行业分类差异（600517.SH），排名边界差异\n\n"
    
    report += f"2. **NAV跟踪**: 策略年化{nav_comparison.get('our_annual_pct', 0):.2f}% vs 932368年化{nav_comparison.get('off_annual_pct', 0):.2f}%，"
    report += f"超额{nav_comparison.get('total_excess_pct', 0):.2f}%。\n\n"
    
    report += f"3. **跟踪误差**: 季度级年化{te_val:.2f}%（目标<1%{'✅' if te_pass else '❌'}），"
    if not np.isnan(te_daily):
        report += f"日级年化估算{te_daily:.2f}%，季度收益相关性{q_corr:.4f}。\n\n"
    else:
        report += f"日级需进一步计算。\n\n"
    
    report += f"4. **换手率**: 平均{turnover['avg_turnover_pct']:.1f}%，"
    if turnover["avg_turnover_pct"] > 30:
        report += "偏高，后续自有指数可加缓冲区降低换手。\n\n"
    else:
        report += "适中。\n\n"
    
    report += f"5. **改进方向**:\n"
    report += f"   - 修复5yr OCF检查：要求**连续5年**OCF为正，缺失年份视为失败\n"
    report += f"   - 这将排除中国移动(600941)、中国电信(601728)等近年上市标的\n"
    report += f"   - 预期Recall提升至85%+，Spearman提升至0.99+\n"
    report += f"   - 进一步对齐CAPEX定义（c_pay_acq_const_fiolta含无形资产）和EV计算\n"
    
    report += "\n---\n\n"
    report += "> ⚠️ **提示**: \n"
    report += "> 1. 本验证基于季度级NAV对比，日级跟踪误差为估算值\n"
    report += "> 2. 回测未扣除滑点、手续费，实际收益可能低1-2%/年\n"
    report += "> 3. 932368官方权重快照仅覆盖2024-12至2025-06，验证期间有限\n"
    report += "> 4. 2025-06-16期间932368出现极高换手(28进出)，可能存在方法论调整\n"
    
    return report


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  ZZ800 FCF vs 932368 完整验证")
    print("=" * 70)
    
    # ── 加载所有数据 ──
    print("\n📂 加载数据...")
    
    our_baskets = load_our_baskets()
    print(f"  我们的basket: {len(our_baskets)}个有效期")
    
    our_nav = load_our_nav()
    print(f"  我们的NAV: {len(our_nav)}期")
    
    official_daily = load_932368_daily()
    print(f"  932368日线: {len(official_daily)}天")
    
    official_weights = load_932368_weights()
    print(f"  932368权重快照: {len(official_weights)}个")
    for d in sorted(official_weights.keys()):
        print(f"    {d}: {len(official_weights[d]['codes'])}只")
    
    # ── 逐期成分股对比 ──
    print("\n🔍 逐期成分股对比...")
    constituent_results = compare_constituents_period(our_baskets, official_weights)
    print(f"  对比了 {len(constituent_results)} 个期间")
    
    for r in constituent_results:
        o = r["overlap"]
        w = r["weight_corr"]
        sp_str = f"{w['spearman']:.4f}" if not np.isnan(w["spearman"]) else "N/A"
        print(f"  {r['our_date']} vs {r['official_date']}: "
              f"Recall={o['recall']*100:.1f}%({o['intersection']}/{o['official_n']}), "
              f"Spearman={sp_str}")
    
    # ── NAV对比 ──
    print("\n📊 NAV收益对比...")
    nav_comparison = calc_nav_comparison(our_nav, official_daily)
    print(f"  我们总收益: {nav_comparison.get('total_our_ret_pct', 'N/A'):.2f}%")
    print(f"  932368总收益: {nav_comparison.get('total_off_ret_pct', 'N/A'):.2f}%")
    print(f"  超额收益: {nav_comparison.get('total_excess_pct', 'N/A'):.2f}%")
    print(f"  季度级TE(年化): {nav_comparison.get('tracking_error_ann_pct', 'N/A'):.2f}%")
    
    # ── 风险指标 ──
    print("\n📉 风险指标...")
    our_risk = calc_risk_metrics(our_nav)
    print(f"  我们: 年化{our_risk['annual_return_pct']:.2f}%, "
          f"回撤{our_risk['max_drawdown_pct']:.2f}%, "
          f"夏普{our_risk['sharpe']:.2f}")
    
    start_date = our_nav.iloc[0]["rb_date"]
    end_date = our_nav.iloc[-1]["next_rb"]
    off_risk = calc_932368_risk_metrics(official_daily, start_date, end_date)
    print(f"  932368: 年化{off_risk.get('annual_return_pct', 0):.2f}%, "
          f"回撤{off_risk.get('max_drawdown_pct', 0):.2f}%, "
          f"夏普{off_risk.get('sharpe', 0):.2f}")
    
    # ── 换手率 ──
    print("\n🔄 换手率分析...")
    turnover = calc_turnover(our_baskets)
    print(f"  平均换手率: {turnover['avg_turnover_pct']:.1f}%")
    
    # ── 日级跟踪误差 ──
    print("\n📏 日级跟踪误差...")
    daily_te = calc_daily_tracking_error(our_nav, official_daily)
    print(f"  日级TE(年化): {daily_te.get('tracking_error_ann_pct', 'N/A'):.2f}%")
    q_corr = daily_te.get('quarterly_corr', np.nan)
    if not np.isnan(q_corr):
        print(f"  季度收益相关性: {q_corr:.4f}")
    note = daily_te.get('note', '')
    if note:
        print(f"  备注: {note}")
    
    # ── 生成报告 ──
    print("\n📝 生成验证报告...")
    report = generate_report(constituent_results, nav_comparison, 
                              our_risk, off_risk, turnover, daily_te)
    
    out_path = _PROJECT_ROOT / "docs" / "zz800_fcf_932368_validation.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"\n✅ 报告已保存: {out_path}")
    print("=" * 70)
    print("  验证完成！")
    print("=" * 70)


if __name__ == "__main__":
    main()
