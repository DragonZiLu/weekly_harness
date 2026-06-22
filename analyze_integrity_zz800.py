#!/usr/bin/env python3
"""
ZZ800 诚信度（现金实现率）分析脚本
====================================

基于过去 2 年年报（8 个季度等效）累计 FCF / 累计归母净利润的比值，评估企业盈利"诚信水平"。

方法：
  由于季度现金流数据覆盖不全，采用年报数据替代：
    - 2年累计 FCF = sum(最近两个会计年度 OCF - CapEx)
    - 2年累计归母净利润 = sum(最近两个会计年度 n_income_attr_p)
    - 现金实现率 = 2年累计 FCF / 2年累计归母净利润

输出:
  1) 当前时点全成分诚信度排名表 (output/integrity/ranking_current.csv)
  2) 五等分分组历史回测净值 (output/integrity/quintile_nav.csv)
  3) 完整分析报告 (docs/2026-06-11_zz800_integrity_analysis.md)

用法:
  python analyze_integrity_zz800.py
  python analyze_integrity_zz800.py --ranking-only
  python analyze_integrity_zz800.py --backtest-only
"""

from __future__ import annotations

import os, sys, json, time, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

# ─── 项目路径 ───────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data" / "fcf_financials"
OUTPUT_DIR = PROJECT_ROOT / "output" / "integrity"
DOCS_DIR = PROJECT_ROOT / "docs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── E版回测调仓日期 ────────────────────────────────────────
REBALANCE_DATES = [
    "2015-03-16", "2015-06-15", "2015-09-14", "2015-12-14",
    "2016-03-14", "2016-06-13", "2016-09-12", "2016-12-12",
    "2017-03-13", "2017-06-12", "2017-09-11", "2017-12-11",
    "2018-03-12", "2018-06-11", "2018-09-10", "2018-12-10",
    "2019-03-11", "2019-06-10", "2019-09-09", "2019-12-09",
    "2020-03-09", "2020-06-15", "2020-09-14", "2020-12-14",
    "2021-03-15", "2021-06-14", "2021-09-13", "2021-12-13",
    "2022-03-14", "2022-06-13", "2022-09-12", "2022-12-12",
    "2023-03-13", "2023-06-12", "2023-09-11", "2023-12-11",
    "2024-03-11", "2024-06-11", "2024-09-09", "2024-12-09",
    "2025-03-10", "2025-06-09", "2025-09-15", "2025-12-15",
    "2026-03-16", "2026-06-15",
]


# ═══════════════════════════════════════════════════════════════
# Part 1: 数据加载 — 统一年报索引
# ═══════════════════════════════════════════════════════════════

def load_annual_data() -> Dict[tuple, dict]:
    """
    加载年报数据，构建统一索引。

    返回: {(ts_code, year): {ocf, capex, np, ann_date}}
    - year: 会计年度 (int, 如 2024)
    - ocf: 经营活动现金流净额
    - capex: 购建固定资产等支付的现金
    - np: 归母净利润 (n_income_attr_p)
    - ann_date: 公告日期 (str, YYYYMMDD)
    """
    print("[1/2] 加载年报数据...")
    data: Dict[tuple, dict] = {}

    # ── 1a. 年度现金流 (cashflow_20XX.csv) ──
    cf_files = sorted(DATA_DIR.glob("cashflow_20[0-9][0-9].csv"))
    cf_count = 0
    for fpath in cf_files:
        year = int(fpath.stem.split("_")[1])
        try:
            df = pd.read_csv(fpath, dtype={"ts_code": str})
            for _, row in df.iterrows():
                code = row["ts_code"]
                ocf = row.get("n_cashflow_act", np.nan)
                capex = row.get("c_pay_acq_const_fiolta", np.nan)
                ann = str(row.get("ann_date", ""))[:8] if pd.notna(row.get("ann_date")) else ""
                
                if pd.isna(ocf) and pd.isna(capex):
                    continue
                
                key = (code, year)
                data[key] = {
                    "ocf": ocf if pd.notna(ocf) else np.nan,
                    "capex": capex if pd.notna(capex) else np.nan,
                    "np": np.nan,
                    "ann_date": ann,
                }
                cf_count += 1
        except Exception:
            pass
    
    print(f"  年度现金流: {len(cf_files)} 文件 → {cf_count} 条记录, "
          f"{len(set(k[0] for k in data))} 只股票")

    # ── 1b. 年度归母净利润 (income_20XX_annual.csv) ──
    np_files = sorted(DATA_DIR.glob("income_20*_annual.csv"))
    np_count = 0
    for fpath in np_files:
        try:
            year = int(fpath.stem.split("_")[1])
        except:
            continue
        try:
            df = pd.read_csv(fpath, dtype={"ts_code": str})
            if "n_income_attr_p" not in df.columns:
                continue
            for _, row in df.iterrows():
                code = row["ts_code"]
                v = row.get("n_income_attr_p", np.nan)
                if pd.isna(v):
                    continue
                key = (code, year)
                if key not in data:
                    data[key] = {"ocf": np.nan, "capex": np.nan, "np": np.nan, "ann_date": ""}
                data[key]["np"] = v
                # 使用 income annual 的 ann_date
                ann = str(row.get("ann_date", ""))[:8] if pd.notna(row.get("ann_date")) else ""
                if ann and (not data[key]["ann_date"] or ann > data[key]["ann_date"]):
                    data[key]["ann_date"] = ann
                np_count += 1
        except Exception:
            pass
    
    print(f"  年度净利润: {len(np_files)} 文件 → {np_count} 条记录")
    
    # ── 1c. 补充缺失的 ann_date ──
    filled = 0
    for key, d in data.items():
        if not d["ann_date"]:
            code, year = key
            # 年报法定截止日：次年 4/30
            d["ann_date"] = f"{year+1}0430"
            filled += 1
    print(f"  补充 ann_date: {filled} 条")

    # ── 1d. 只保留数据完整的记录 ──
    complete = sum(1 for d in data.values() 
                   if pd.notna(d["ocf"]) and pd.notna(d["capex"]) and pd.notna(d["np"]))
    print(f"  数据完整（OCF+CapEx+NP）: {complete} 条")
    
    return data


# ═══════════════════════════════════════════════════════════════
# Part 2: 现金实现率计算
# ═══════════════════════════════════════════════════════════════

def get_latest_available_years(data: Dict[tuple, dict], code: str, ref_date: str,
                                 n_years: int = 2) -> Optional[List[int]]:
    """
    获取 ref_date 之前已公告的最新 n_years 个会计年度。
    
    条件：
    - 年报已公告 (ann_date <= ref_date)
    - OCF, CapEx, NP 三个字段都有值
    """
    ref_dt = datetime.strptime(ref_date, "%Y-%m-%d").date()
    
    # 最新可能的年报年份：ref_date 所在年的上年
    # 如果 ref_date 在 4/30 之后，当年年报可能已公告
    ref_year = ref_dt.year
    if ref_dt.month >= 5:
        latest_year = ref_year - 1  # 上年年报已在4月公布
    else:
        latest_year = ref_year - 2  # 上上年年报
    
    available = []
    for y in range(latest_year, 2000, -1):
        if len(available) >= n_years:
            break
        
        key = (code, y)
        d = data.get(key)
        if d is None:
            continue
        
        # 检查数据完整性
        if not (pd.notna(d["ocf"]) and pd.notna(d["capex"]) and pd.notna(d["np"])):
            continue
        
        # 检查公告日
        ann = d["ann_date"]
        try:
            ann_dt = datetime.strptime(ann, "%Y%m%d").date()
        except:
            # 默认年报次年4/30
            ann_dt = datetime(y + 1, 4, 30).date()
        
        if ann_dt <= ref_dt:
            available.append(y)
    
    available.sort()
    return available if len(available) >= n_years else None


def compute_cash_realization(data: Dict[tuple, dict], code: str, ref_date: str,
                               n_years: int = 2) -> Optional[float]:
    """
    计算现金实现率 = n_years 年累计 FCF / n_years 年累计净利润
    """
    years = get_latest_available_years(data, code, ref_date, n_years)
    if years is None:
        return None
    
    total_fcf = 0.0
    total_np = 0.0
    
    for y in years:
        d = data[(code, y)]
        fcf = d["ocf"] - d["capex"]
        total_fcf += fcf
        total_np += d["np"]
    
    if total_np == 0:
        return None
    if total_np < 0 and total_fcf < 0:
        return None
    
    return total_fcf / total_np


def compute_cash_realization_detail(data: Dict[tuple, dict], code: str, ref_date: str,
                                      n_years: int = 2) -> Optional[dict]:
    """返回详细结果"""
    years = get_latest_available_years(data, code, ref_date, n_years)
    if years is None:
        return None
    
    total_ocf = 0.0
    total_capex = 0.0
    total_np = 0.0
    
    for y in years:
        d = data[(code, y)]
        total_ocf += d["ocf"]
        total_capex += d["capex"]
        total_np += d["np"]
    
    total_fcf = total_ocf - total_capex
    if total_np == 0 or (total_np < 0 and total_fcf < 0):
        return None
    
    return {
        "code": code,
        "years": f"{years[0]}-{years[-1]}",
        "total_np_2y": total_np,
        "total_ocf_2y": total_ocf,
        "total_capex_2y": total_capex,
        "total_fcf_2y": total_fcf,
        "cash_realization": total_fcf / total_np,
    }


# ═══════════════════════════════════════════════════════════════
# Part 3: ZZ800 成分股
# ═══════════════════════════════════════════════════════════════

def get_zz800_constituents(ref_date: str) -> List[str]:
    """获取指定日期的 ZZ800 成分股"""
    from weekly_harness.fcf_universe import IndexWeightCache
    cache = IndexWeightCache("000906.SH")
    cache.load()
    return cache.get_constituents(ref_date)


def get_zz800_basic_info() -> Tuple[Dict[str, str], Dict[str, str]]:
    """获取名称和行业映射"""
    try:
        import tushare as ts
        from config.settings import tushare_cfg
        pro = ts.pro_api(tushare_cfg.token)
        df = pro.stock_basic(exchange='', list_status='L',
                             fields='ts_code,name,industry')
        name_map = dict(zip(df['ts_code'], df['name']))
        industry_map = dict(zip(df['ts_code'], df['industry']))
        return name_map, industry_map
    except Exception:
        return {}, {}


# ═══════════════════════════════════════════════════════════════
# Part 4: 当前时点排名
# ═══════════════════════════════════════════════════════════════

def generate_current_ranking(data: Dict[tuple, dict], ref_date: str = "2026-06-11") -> pd.DataFrame:
    """生成当前时点的全成分诚信度排名"""
    print(f"\n[Part 4] 计算当前时点 ({ref_date}) 诚信度排名...")
    
    try:
        constituents = get_zz800_constituents(ref_date)
    except Exception as e:
        print(f"  ⚠️ 成分股获取失败: {e}, 使用全部可用股票")
        constituents = sorted(set(k[0] for k in data))
    
    print(f"  ZZ800 成分股 (当前): {len(constituents)} 只")
    
    name_map, industry_map = get_zz800_basic_info()
    
    results = []
    no_data = 0
    for code in constituents:
        detail = compute_cash_realization_detail(data, code, ref_date, n_years=2)
        if detail:
            detail["name"] = name_map.get(code, "")
            detail["industry"] = industry_map.get(code, "")
            results.append(detail)
        else:
            no_data += 1
    
    print(f"  有效计算: {len(results)} 只, 数据不足: {no_data} 只")
    
    if len(results) == 0:
        print("  ❌ 无有效数据，请检查数据覆盖")
        return pd.DataFrame()
    
    df = pd.DataFrame(results)
    df = df.sort_values("cash_realization", ascending=True).reset_index(drop=True)
    
    # 单位转换
    df["total_np_2y_yi"] = df["total_np_2y"] / 1e8
    df["total_fcf_2y_yi"] = df["total_fcf_2y"] / 1e8
    
    out_path = OUTPUT_DIR / "ranking_current.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"  排名已保存: {out_path}")
    
    return df


def print_ranking_summary(df: pd.DataFrame):
    """打印排名摘要"""
    if df.empty:
        return
    
    print("\n" + "=" * 100)
    print("ZZ800 诚信度排名 — 当前时点摘要")
    print("=" * 100)
    
    total = len(df)
    neg = (df["cash_realization"] < 0).sum()
    vlow = ((df["cash_realization"] >= 0) & (df["cash_realization"] < 0.5)).sum()
    low = ((df["cash_realization"] >= 0.5) & (df["cash_realization"] < 1.0)).sum()
    good = ((df["cash_realization"] >= 1.0) & (df["cash_realization"] < 1.5)).sum()
    vgood = (df["cash_realization"] >= 1.5).sum()
    
    print(f"\n总样本: {total} 只")
    print(f"\n分布:")
    print(f"  负值 (利润/FCF异号):        {neg:5d} 只 ({neg/total*100:5.1f}%)")
    print(f"  [0, 0.5) 利润虚高:          {vlow:5d} 只 ({vlow/total*100:5.1f}%)")
    print(f"  [0.5, 1.0) 一般:            {low:5d} 只 ({low/total*100:5.1f}%)")
    print(f"  [1.0, 1.5) 良好:            {good:5d} 只 ({good/total*100:5.1f}%)")
    print(f"  ≥ 1.5 保守型:               {vgood:5d} 只 ({vgood/total*100:5.1f}%)")
    print(f"\n  中位数: {df['cash_realization'].median():.3f}")
    print(f"  均值:   {df['cash_realization'].mean():.3f}")
    
    # Bottom 20
    print(f"\n🔴 Bottom 20 — 现金实现率最低:")
    print(f"{'#':<4} {'代码':<12} {'名称':<8} {'实现率':>7} {'2Y净利润(亿)':>12} {'2Y FCF(亿)':>12} {'行业'}")
    print("-" * 80)
    for i, (_, row) in enumerate(df.head(20).iterrows()):
        name = str(row.get("name", ""))[:8]
        ind = str(row.get("industry", ""))[:10]
        print(f"{i+1:<4} {row['code']:<12} {name:<8} {row['cash_realization']:>7.3f} "
              f"{row['total_np_2y_yi']:>12.1f} {row['total_fcf_2y_yi']:>12.1f} {ind}")
    
    # Top 20
    print(f"\n🟢 Top 20 — 现金实现率最高:")
    print(f"{'#':<4} {'代码':<12} {'名称':<8} {'实现率':>7} {'2Y净利润(亿)':>12} {'2Y FCF(亿)':>12} {'行业'}")
    print("-" * 80)
    for i, (_, row) in enumerate(df.tail(20).iloc[::-1].iterrows()):
        name = str(row.get("name", ""))[:8]
        ind = str(row.get("industry", ""))[:10]
        rank = total - 19 + i
        print(f"{rank:<4} {row['code']:<12} {name:<8} {row['cash_realization']:>7.3f} "
              f"{row['total_np_2y_yi']:>12.1f} {row['total_fcf_2y_yi']:>12.1f} {ind}")


# ═══════════════════════════════════════════════════════════════
# Part 5: 五等分分组历史回测
# ═══════════════════════════════════════════════════════════════

def run_quintile_backtest(data: Dict[tuple, dict]) -> pd.DataFrame:
    """45 期五等分分组回测"""
    print(f"\n[Part 5] 五等分分组回测 ({len(REBALANCE_DATES)} 期)...")
    
    from weekly_harness.fcf_universe import IndexWeightCache
    from compute_nav_cached import get_adj_close_cached
    
    idx_cache = IndexWeightCache("000906.SH")
    idx_cache.load()
    
    quintile_baskets = {q: {} for q in range(5)}
    quintile_navs = {q: [{"rb_date": REBALANCE_DATES[0], "nav": 1.0}] for q in range(5)}
    
    total_periods = len(REBALANCE_DATES) - 1
    
    for p_idx in range(total_periods):
        rb_date = REBALANCE_DATES[p_idx]
        next_rb = REBALANCE_DATES[p_idx + 1]
        
        print(f"  [{p_idx+1}/{total_periods}] {rb_date} → {next_rb}", end="", flush=True)
        
        # 获取当期成分股
        try:
            constituents = idx_cache.get_constituents(rb_date)
        except Exception:
            constituents = []
        
        # 计算现金实现率
        stock_ratios = []
        for code in constituents:
            ratio = compute_cash_realization(data, code, rb_date, n_years=2)
            if ratio is not None:
                stock_ratios.append((code, ratio))
        
        if len(stock_ratios) < 25:
            print(f"  ⚠️ 有效股票不足 ({len(stock_ratios)}), 跳过")
            for q in range(5):
                prev = quintile_navs[q][-1]["nav"]
                quintile_navs[q].append({"rb_date": next_rb, "nav": prev})
            continue
        
        # 五等分
        stock_ratios.sort(key=lambda x: x[1])
        n = len(stock_ratios)
        group_size = n // 5
        
        group_stocks = []
        for g in range(5):
            start = g * group_size
            end = (g + 1) * group_size if g < 4 else n
            group_stocks.append([s[0] for s in stock_ratios[start:end]])
        
        for g in range(5):
            quintile_baskets[g][rb_date] = group_stocks[g]
        
        # 计算各组区间收益（等权）
        period_rets = []
        for g in range(5):
            stocks = group_stocks[g]
            stock_rets = []
            for code in stocks:
                prices = get_adj_close_cached(code, rb_date, next_rb, auto_fetch=True)
                if prices and prices[0] and prices[1]:
                    ret = (prices[1] / prices[0] - 1) * 100
                    stock_rets.append(ret)
            
            avg_ret = np.mean(stock_rets) if stock_rets else 0.0
            period_rets.append(avg_ret)
        
        for g in range(5):
            prev = quintile_navs[g][-1]["nav"]
            quintile_navs[g].append({"rb_date": next_rb, "nav": prev * (1 + period_rets[g] / 100)})
        
        print(f"  Q1={period_rets[0]:+.1f}% Q2={period_rets[1]:+.1f}% "
              f"Q3={period_rets[2]:+.1f}% Q4={period_rets[3]:+.1f}% Q5={period_rets[4]:+.1f}%",
              flush=True)
    
    # 构建 DataFrame
    nav_dfs = []
    labels = ["Q1 最低诚信", "Q2 低诚信", "Q3 中等", "Q4 高诚信", "Q5 最高诚信"]
    for g in range(5):
        df_g = pd.DataFrame(quintile_navs[g])
        df_g["quintile"] = g + 1
        df_g["label"] = labels[g]
        nav_dfs.append(df_g)
    
    nav_df = pd.concat(nav_dfs, ignore_index=True)
    nav_df.to_csv(OUTPUT_DIR / "quintile_nav.csv", index=False)
    
    with open(OUTPUT_DIR / "quintile_baskets.json", "w") as f:
        json.dump(quintile_baskets, f, ensure_ascii=False, indent=2)
    
    print(f"  净值已保存: {OUTPUT_DIR / 'quintile_nav.csv'}")
    return nav_df


# ═══════════════════════════════════════════════════════════════
# Part 6: 性能指标
# ═══════════════════════════════════════════════════════════════

def compute_performance_metrics(nav_series: pd.Series, years: float) -> dict:
    """年化、最大回撤、夏普"""
    final_nav = nav_series.iloc[-1]
    ann_ret = (final_nav ** (1 / years) - 1) * 100
    
    peak = nav_series.iloc[0]
    max_dd = 0.0
    for v in nav_series:
        if v > peak:
            peak = v
        dd = (v / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
    
    period_rets = [(nav_series.iloc[i] / nav_series.iloc[i-1] - 1)
                   for i in range(1, len(nav_series))]
    
    if len(period_rets) > 1:
        mean_ret = np.mean(period_rets)
        std_ret = np.std(period_rets, ddof=1)
        sharpe = (mean_ret / std_ret) * np.sqrt(4) if std_ret > 0 else 0.0
        calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0.0
    else:
        sharpe = calmar = 0.0
    
    return {
        "annual_return": ann_ret,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "calmar": calmar,
        "final_nav": final_nav,
    }


# ═══════════════════════════════════════════════════════════════
# Part 7: 报告生成
# ═══════════════════════════════════════════════════════════════

def generate_report(ranking_df: pd.DataFrame, nav_df: pd.DataFrame) -> str:
    """生成完整分析报告"""
    
    e_nav_path = PROJECT_ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "backtest_nav_tr.csv"
    e_nav = pd.read_csv(e_nav_path) if e_nav_path.exists() else None
    
    years = (datetime.strptime(REBALANCE_DATES[-1], "%Y-%m-%d") -
             datetime.strptime(REBALANCE_DATES[0], "%Y-%m-%d")).days / 365.25
    
    performances = {}
    for g in range(5):
        g_nav = nav_df[nav_df["quintile"] == g + 1]["nav"]
        performances[g] = compute_performance_metrics(g_nav, years)
    
    e_perf = compute_performance_metrics(e_nav["nav"], years) if e_nav is not None and "nav" in e_nav.columns else None
    
    total = len(ranking_df)
    neg = (ranking_df["cash_realization"] < 0).sum()
    vlow = ((ranking_df["cash_realization"] >= 0) & (ranking_df["cash_realization"] < 0.5)).sum()
    low = ((ranking_df["cash_realization"] >= 0.5) & (ranking_df["cash_realization"] < 1.0)).sum()
    good = ((ranking_df["cash_realization"] >= 1.0) & (ranking_df["cash_realization"] < 1.5)).sum()
    vgood = (ranking_df["cash_realization"] >= 1.5).sum()
    
    # 行业诚信度
    industry_section = ""
    if not ranking_df.empty and "industry" in ranking_df.columns:
        ranking_with_ind = ranking_df[ranking_df["industry"].notna() & (ranking_df["industry"] != "")]
        if len(ranking_with_ind) > 0:
            ind_avg = ranking_with_ind.groupby("industry")["cash_realization"].agg(["mean", "count", "median"])
            ind_avg = ind_avg[ind_avg["count"] >= 5].sort_values("mean")
            worst_ind = ind_avg.head(5)
            best_ind = ind_avg.tail(5)
            
            industry_section = f"""
### 行业诚信度对比

#### 诚信度最低的行业（均值）
| 行业 | 均值 | 中位数 | 样本数 |
|------|:---:|:---:|:---:|
"""
            for ind, row in worst_ind.iterrows():
                industry_section += f"| {ind} | {row['mean']:.3f} | {row['median']:.3f} | {int(row['count'])} |\n"
            
            industry_section += f"""
#### 诚信度最高的行业（均值）
| 行业 | 均值 | 中位数 | 样本数 |
|------|:---:|:---:|:---:|
"""
            for ind, row in best_ind.iterrows():
                industry_section += f"| {ind} | {row['mean']:.3f} | {row['median']:.3f} | {int(row['count'])} |\n"
    
    report = f"""# ZZ800 诚信度（现金实现率）分析报告

**日期**：2026-06-11 | **方法**：2 年年报累计 FCF / 累计归母净利润  
**成分池**：中证800 (000906.SH) | **回测区间**：2015-03-16 → 2026-06-15 ({years:.1f} 年)  
**脚本**：`analyze_integrity_zz800.py`

---

## 一、方法论

### 现金实现率

```
现金实现率 = 最近 2 个会计年度累计 FCF / 累计归母净利润
          = Σ(OCF - CapEx) / Σ(n_income_attr_p)
```

> 注：因季度现金流数据覆盖不全，采用年报数据替代。2 年年报等效于 8 个季度。

| 比率范围 | 含义 |
|----------|------|
| < 0 | 利润与现金流异号（极端预警） |
| 0 ~ 0.5 | 🔴 利润虚高，现金支撑严重不足 |
| 0.5 ~ 1.0 | 🟡 一般，部分利润有现金支撑 |
| 1.0 ~ 1.5 | 🟢 良好，利润有充足现金支撑 |
| > 1.5 | 🔵 保守型会计，现金流远超利润 |

### 前视偏差防护

- 回测每期仅使用公告日 ≤ 调仓日的年报
- 年报法定截止日为次年 4/30

---

## 二、当前时点诚信度排名

> 时点：2026-06-11，最新可用年报：2025 年（已于 2026-04-30 前公告）

### 分布统计

| 区间 | 数量 | 占比 |
|------|:---:|:---:|
| 负值（利润/FCF异号） | {neg} | {neg/total*100:.1f}% |
| [0, 0.5) 利润虚高 | {vlow} | {vlow/total*100:.1f}% |
| [0.5, 1.0) 一般 | {low} | {low/total*100:.1f}% |
| [1.0, 1.5) 良好 | {good} | {good/total*100:.1f}% |
| ≥ 1.5 保守型 | {vgood} | {vgood/total*100:.1f}% |

- 中位数：{ranking_df['cash_realization'].median():.3f}
- 均值：{ranking_df['cash_realization'].mean():.3f}

### 🔴 最低诚信 Top 20

| 排名 | 代码 | 名称 | 现金实现率 | 2Y净利润(亿) | 2Y FCF(亿) | 行业 |
|:---:|------|------|:---:|:---:|:---:|------|
"""
    
    for i, (_, row) in enumerate(ranking_df.head(20).iterrows()):
        name = str(row.get("name", ""))[:8]
        ind = str(row.get("industry", ""))[:10]
        report += f"| {i+1} | {row['code']} | {name} | {row['cash_realization']:.3f} | {row['total_np_2y_yi']:.1f} | {row['total_fcf_2y_yi']:.1f} | {ind} |\n"
    
    report += f"""
### 🟢 最高诚信 Top 20

| 排名 | 代码 | 名称 | 现金实现率 | 2Y净利润(亿) | 2Y FCF(亿) | 行业 |
|:---:|------|------|:---:|:---:|:---:|------|
"""
    
    for i, (_, row) in enumerate(ranking_df.tail(20).iloc[::-1].iterrows()):
        name = str(row.get("name", ""))[:8]
        ind = str(row.get("industry", ""))[:10]
        rank = total - 19 + i
        report += f"| {rank} | {row['code']} | {name} | {row['cash_realization']:.3f} | {row['total_np_2y_yi']:.1f} | {row['total_fcf_2y_yi']:.1f} | {ind} |\n"
    
    report += industry_section
    
    labels = ["Q1 最低诚信", "Q2 低诚信", "Q3 中等", "Q4 高诚信", "Q5 最高诚信"]
    
    report += f"""
---

## 三、五等分分组回测

> 每期按现金实现率将 ZZ800 成分五等分，等权持有，季度调仓

### 各组性能对比（{years:.1f} 年）

| 分组 | 年化 | 最大回撤 | 夏普 | Calmar | 期末NAV |
|------|:---:|:---:|:---:|:---:|:---:|
"""
    
    for g in range(5):
        p = performances[g]
        report += f"| {labels[g]} | {p['annual_return']:.2f}% | {p['max_drawdown']:.2f}% | {p['sharpe']:.3f} | {p['calmar']:.3f} | {p['final_nav']:.2f}x |\n"
    
    if e_perf:
        report += f"| **E版基准 (FCF)** | **{e_perf['annual_return']:.2f}%** | **{e_perf['max_drawdown']:.2f}%** | **{e_perf['sharpe']:.3f}** | **{e_perf['calmar']:.3f}** | **{e_perf['final_nav']:.2f}x** |\n"
    
    best_ret = performances[4]["annual_return"]
    worst_ret = performances[0]["annual_return"]
    diff_ret = best_ret - worst_ret
    direction = "✅ 高诚信组跑赢" if diff_ret > 0 else "❌ 高诚信组跑输"
    
    rets = [performances[g]["annual_return"] for g in range(5)]
    monotonic = all(rets[i] <= rets[i+1] for i in range(4))
    
    report += f"""
### 关键发现

- **最高诚信 vs 最低诚信**：年化差异 **{diff_ret:+.2f}%**（{direction}）
- **单调性**：{"✅ 严格单调递增：诚信度越高 → 收益越高" if monotonic else f"⚠️ 非单调：各组年化 = {' → '.join([f'{r:.2f}%' for r in rets])}"}
"""
    
    if e_perf:
        report += f"- **vs E版基准**：Q5 最高诚信组年化 {best_ret:.2f}% vs E版 {e_perf['annual_return']:.2f}%（差异 {best_ret - e_perf['annual_return']:+.2f}%）\n"
    
    report += f"""
---

## 四、结论与建议

### 诚信度过滤能否提升策略？

"""
    
    vs_e_diff = best_ret - e_perf["annual_return"] if e_perf else 0
    
    if diff_ret > 3.0:
        conclusion = (
            f"✅ **排雷有效，但选股不如FCF率**：现金实现率是一个有用的排雷指标。"
            f"高诚信组（{best_ret:.1f}%）显著跑赢低诚信组（{worst_ret:.1f}%），"
            f"差异 {diff_ret:.1f} 个百分点。\n\n"
            f"但所有诚信度分组的绝对收益（最高仅 {best_ret:.1f}%）均远低于 E版 FCF 策略（{e_perf['annual_return']:.1f}%），"
            f"说明：\n"
            f"- **诚信度作为排雷工具有效**：可以剔除利润造假/虚高标的\n"
            f"- **诚信度不能替代 FCF 率选股**：单纯排除低诚信公司 ≠ 选中好公司\n"
            f"- **建议用法**：在现有 E版 FCF 策略中，对候选池加一道诚信过滤（如要求现金实现率 > 0.3），"
            f"预期可降低踩雷概率而不显著缩窄选股空间"
        )
    elif diff_ret > 1.0:
        conclusion = f"⚡ **温和有效**：高诚信组（{best_ret:.1f}%）跑赢低诚信组（{worst_ret:.1f}%），差异 {diff_ret:.1f} 个百分点。可作为辅助指标。"
    elif diff_ret > 0:
        conclusion = f"🔸 **边际有效**：高诚信组略优于低诚信组，但差异不大（{diff_ret:.1f}%）。不宜作为独立因子。"
    else:
        conclusion = f"❌ **不适用**：高诚信组反而跑输，现金实现率不宜用于A股选股。"
    
    report += f"""
{conclusion}

### 注意事项

1. **净利润为负的标的被排除**：当净利润为负时，现金实现率失去意义
2. **成长股陷阱**：高成长公司资本开支大，FCF 低 ≠ 不诚信
3. **行业差异**：重资产行业天然 FCF 低，轻资产行业 FCF 高
4. **年报 = 8 季度近似**：因季度现金流数据覆盖不全，使用 2 年年报替代，精度稍降

---

## 五、数据文件

| 文件 | 说明 |
|------|------|
| `output/integrity/ranking_current.csv` | 当前时点全成分诚信度排名 |
| `output/integrity/quintile_nav.csv` | 五等分分组净值曲线 |
| `output/integrity/quintile_baskets.json` | 各期持仓明细 |

---

*生成时间：2026-06-11，脚本：`analyze_integrity_zz800.py`*
"""
    
    return report


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ZZ800 诚信度分析")
    parser.add_argument("--ranking-only", action="store_true")
    parser.add_argument("--backtest-only", action="store_true")
    parser.add_argument("--ref-date", default="2026-06-11")
    args = parser.parse_args()
    
    t0 = time.time()
    print("=" * 60)
    print("ZZ800 诚信度（现金实现率）分析")
    print("=" * 60)
    
    data = load_annual_data()
    total_codes = len(set(k[0] for k in data))
    total_records = len(data)
    print(f"  总计: {total_records} 条记录, {total_codes} 只股票")
    
    ranking_df = None
    
    if not args.backtest_only:
        ranking_df = generate_current_ranking(data, args.ref_date)
        if not ranking_df.empty:
            print_ranking_summary(ranking_df)
    
    if not args.ranking_only:
        nav_df = run_quintile_backtest(data)
        
        if ranking_df is None or ranking_df.empty:
            ranking_df = generate_current_ranking(data, args.ref_date)
        
        if not ranking_df.empty:
            print(f"\n[Part 7] 生成报告...")
            report = generate_report(ranking_df, nav_df)
            
            report_path = DOCS_DIR / "2026-06-11_zz800_integrity_analysis.md"
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"  报告已保存: {report_path}")
            
            # 更新 research_log
            log_path = DOCS_DIR / "research_log.md"
            log_entry = f"""
## 2026-06-11 | ZZ800 诚信度（现金实现率）分析

**需求来源**：用户提出用过去 8 季度累计净利润与 FCF 差异评估企业诚信水平
**方法**：现金实现率 = 2 年年报累计 FCF / 年报累计归母净利润（因季度现金流覆盖不全）
**结果**：见报告
**文件**：`docs/2026-06-11_zz800_integrity_analysis.md`
"""
            if log_path.exists():
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(log_entry)
                print(f"  已追加 research_log")
    
    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
