#!/usr/bin/env python3
"""
E版 + 诚信过滤（现金实现率 ≥ 0.3）全流程回测
================================================

在原 E版策略基础上，加入诚信度预过滤：剔除过去 2 个会计年度
累计 FCF / 累计归母净利润 < 0.3 的标的，再做缓冲区选股。

流程：
  1. 加载 X版完整排名池（复用已有，不重复调 get_fcf_basket）
  2. 对每期排名池做诚信过滤（剔除现金实现率 < 0.3）
  3. 对过滤后的排名池应用 E版缓冲区（low=30, high=70, top_n=50）
  4. FCF加权计算 NAV
  5. 对比原始 E版

用法:
  python run_e_integrity_full.py              # 完整运行
  python run_e_integrity_full.py --nav-only   # 跳过选股，仅算NAV+报告
"""

import sys, json, time, argparse
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime
from compute_nav_cached import get_adj_close_cached

# ─── 参数解析 ───
parser = argparse.ArgumentParser()
parser.add_argument('--nav-only', action='store_true', help='跳过选股，用已有basket算NAV+报告')
args = parser.parse_args()

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))

MIN_CASH_REALIZATION = 0.3  # 诚信过滤阈值
DATA_DIR = PROJECT_ROOT / "data" / "fcf_financials"

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

TOP_N = 50
CAP = 0.10
OUT_DIR = "output/zz800_fcf_e_integrity"
E_OUT_DIR = "output/zz800_fcf_lenient_buffer_e40"
X_OUT_DIR = "output/zz800_fcf_full_universe"


# ═══════════════════════════════════════════════════════════════
# Part 1: 诚信度数据加载（复用 analyze_integrity_zz800.py 逻辑）
# ═══════════════════════════════════════════════════════════════

def load_cash_realization_data() -> dict:
    """
    加载年报数据，构建现金实现率索引。
    
    返回: {(ts_code, year): {ocf, capex, np, ann_date}}
    """
    print("[1/3] 加载年报财务数据...")
    data: dict = {}
    
    # ── 年度现金流 ──
    cf_files = sorted(DATA_DIR.glob("cashflow_20[0-9][0-9].csv"))
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
        except Exception:
            pass
    
    # ── 年度归母净利润 ──
    np_files = sorted(DATA_DIR.glob("income_20*_annual.csv"))
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
                ann = str(row.get("ann_date", ""))[:8] if pd.notna(row.get("ann_date")) else ""
                if ann and (not data[key]["ann_date"] or ann > data[key]["ann_date"]):
                    data[key]["ann_date"] = ann
        except Exception:
            pass
    
    # ── 补充缺失的 ann_date ──
    filled = 0
    for key, d in data.items():
        if not d["ann_date"]:
            code, year = key
            d["ann_date"] = f"{year+1}0430"
            filled += 1
    
    complete = sum(1 for d in data.values()
                   if pd.notna(d["ocf"]) and pd.notna(d["capex"]) and pd.notna(d["np"]))
    print(f"  总记录: {len(data)} 条, 数据完整: {complete} 条, 覆盖 {len(set(k[0] for k in data))} 只股票")
    
    return data


def get_available_years(data: dict, code: str, ref_date: str, n_years: int = 2) -> list | None:
    """
    获取 ref_date 之前已公告的最新 n_years 个会计年度。
    条件：年报已公告 (ann_date <= ref_date) 且 OCF/CapEx/NP 都有值。
    """
    ref_dt = datetime.strptime(ref_date, "%Y-%m-%d").date()
    ref_year = ref_dt.year
    
    if ref_dt.month >= 5:
        latest_year = ref_year - 1
    else:
        latest_year = ref_year - 2
    
    available = []
    for y in range(latest_year, 2000, -1):
        if len(available) >= n_years:
            break
        key = (code, y)
        d = data.get(key)
        if d is None:
            continue
        if not (pd.notna(d["ocf"]) and pd.notna(d["capex"]) and pd.notna(d["np"])):
            continue
        ann = d["ann_date"]
        try:
            ann_dt = datetime.strptime(ann, "%Y%m%d").date()
        except:
            ann_dt = datetime(y + 1, 4, 30).date()
        if ann_dt <= ref_dt:
            available.append(y)
    
    available.sort()
    return available if len(available) >= n_years else None


def compute_cash_realization(data: dict, code: str, ref_date: str, n_years: int = 2) -> float | None:
    """计算现金实现率 = n_years年累计FCF / n_years年累计NP"""
    years = get_available_years(data, code, ref_date, n_years)
    if years is None:
        return None
    
    total_fcf = 0.0
    total_np = 0.0
    for y in years:
        d = data[(code, y)]
        total_fcf += d["ocf"] - d["capex"]
        total_np += d["np"]
    
    if total_np == 0:
        return None
    if total_np < 0 and total_fcf < 0:
        return None
    
    return total_fcf / total_np


# ═══════════════════════════════════════════════════════════════
# Part 2: 缓冲区选股（复用原有逻辑）
# ═══════════════════════════════════════════════════════════════

def fcf_weights(stocks, cap=CAP, max_iter=100):
    if not stocks: return stocks
    fcf_vals = [max(s.get('fcf', 0), 0) for s in stocks]
    total = sum(fcf_vals)
    if total <= 0:
        w = 1.0 / len(stocks)
        for s in stocks: s['weight'] = round(w, 6)
        return stocks
    weights = [v / total for v in fcf_vals]
    for _ in range(max_iter):
        overflow = sum(w - cap for w in weights if w > cap)
        if overflow < 1e-9: break
        capped = [min(w, cap) for w in weights]
        below = sum(c for c in capped if c < cap)
        if below <= 0: break
        weights = [min(c + overflow * (c / below), cap) if c < cap else cap for c in capped]
    tw = sum(weights)
    for s, w in zip(stocks, weights): s['weight'] = round(w / tw, 6)
    return stocks


def apply_buffer(ranked, prev_codes, low, high, top_n):
    must = ranked[:low]
    buffer = ranked[low:high]
    buffer_old = [s for s in buffer if s['ts_code'] in prev_codes]
    buffer_new = [s for s in buffer if s['ts_code'] not in prev_codes]
    selected = must + buffer_old
    remaining = top_n - len(selected)
    if remaining > 0: selected.extend(buffer_new[:remaining])
    return selected[:top_n]


# ═══════════════════════════════════════════════════════════════
# Part 3: 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    # ── 加载 X版排名池 ──
    x_rp = PROJECT_ROOT / X_OUT_DIR / "rankings_2015_2026.json"
    if not x_rp.exists():
        print("❌ X版排名池不存在！请先运行: python run_bdefx_full.py")
        sys.exit(1)
    
    print("加载 X版完整排名池...")
    with open(x_rp) as f:
        x_rankings = json.load(f)
    print(f"  ✅ 加载 {len(x_rankings)} 期 X版排名池")
    
    # ── 加载 E版原始 baskets（用于对比） ──
    e_bp = PROJECT_ROOT / E_OUT_DIR / "all_baskets_2015_2026.json"
    if e_bp.exists():
        with open(e_bp) as f:
            e_baskets = json.load(f)
        print(f"  ✅ 加载 {len(e_baskets)} 期 E版 baskets")
    else:
        print("⚠️  E版 baskets 不存在，将跳过对比")
        e_baskets = {}
    
    # ── 加载诚信度数据 ──
    if args.nav_only:
        print("[nav-only] 跳过诚信数据加载")
        integrity_data = {}
    else:
        integrity_data = load_cash_realization_data()
    
    # ── 选股阶段：诚信过滤 + E版缓冲区 ──
    ei_baskets = {}
    ei_out = PROJECT_ROOT / OUT_DIR
    ei_out.mkdir(parents=True, exist_ok=True)
    
    if not args.nav_only:
        print(f"\n[2/3] 诚信过滤（现金实现率 ≥ {MIN_CASH_REALIZATION}）+ E版缓冲区选股...")
        
        prev_codes = set()
        filter_stats = []
        
        for i, date_str in enumerate(REBALANCE_DATES):
            ranked = x_rankings.get(date_str, [])
            if not ranked:
                ei_baskets[date_str] = []
                filter_stats.append((date_str, 0, 0, 0))
                continue
            
            # ── 诚信过滤 ──
            filtered = []
            skipped_no_data = 0
            skipped_low_integrity = 0
            for s in ranked:
                code = s.get('ts_code', '')
                cr = compute_cash_realization(integrity_data, code, date_str)
                if cr is None:
                    skipped_no_data += 1
                    continue  # 无数据则直接跳过（无法判断诚信度）
                if cr < MIN_CASH_REALIZATION:
                    skipped_low_integrity += 1
                    continue
                filtered.append(s)
            
            filter_stats.append((date_str, len(ranked), len(filtered),
                                 skipped_no_data + skipped_low_integrity))
            
            # ── E版缓冲区 ──
            if i == 0 or not prev_codes:
                stocks = [dict(s) for s in filtered[:TOP_N]]
            else:
                stocks = [dict(s) for s in apply_buffer(filtered, prev_codes, 30, 70, TOP_N)]
            
            fcf_weights(stocks)
            ei_baskets[date_str] = stocks
            prev_codes = {s['ts_code'] for s in stocks}
            
            if i % 8 == 0 or i == len(REBALANCE_DATES) - 1:
                avg_filtered = np.mean([s[2] for s in filter_stats[-8:]])
                avg_skipped = np.mean([s[3] for s in filter_stats[-8:]])
                print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: "
                      f"原始{len(ranked)}→过滤后{len(filtered)}(-{len(ranked)-len(filtered)})"
                      f" →选中{len(stocks)}只")
        
        # 保存 baskets
        with open(ei_out / "all_baskets_2015_2026.json", "w") as f:
            json.dump(ei_baskets, f, ensure_ascii=False, indent=2)
        
        # 保存过滤统计
        with open(ei_out / "filter_stats.json", "w") as f:
            json.dump(filter_stats, f, ensure_ascii=False, indent=2)
        
        ei_valid = sum(1 for d in ei_baskets if len(ei_baskets[d]) >= 10)
        total_filtered = [s[2] for s in filter_stats if s[2] > 0]
        avg_candidates = np.mean(total_filtered) if total_filtered else 0
        print(f"\n  ✅ E+诚信版: {ei_valid}/{len(ei_baskets)}期有效, "
              f"过滤后平均候选 {avg_candidates:.0f} 只 → {OUT_DIR}/")
    else:
        # nav-only: 从磁盘加载
        print("\n[nav-only] 加载已有 E+诚信版 baskets...")
        bp = PROJECT_ROOT / OUT_DIR / "all_baskets_2015_2026.json"
        if bp.exists():
            with open(bp) as f:
                ei_baskets = json.load(f)
            print(f"  ✅ 加载 {len(ei_baskets)} 期")
        else:
            print("  ❌ baskets 不存在，请先运行不带 --nav-only")
            sys.exit(1)
    
    # ── NAV 计算 ──
    print("\n[3/3] 计算 NAV...")
    
    nav_df = pd.DataFrame([
        {'rb_date': REBALANCE_DATES[i], 'next_rb': REBALANCE_DATES[i+1]}
        for i in range(len(REBALANCE_DATES) - 1)
    ])
    
    # ── 基准数据加载 ──
    df_idx_price = pd.read_csv("data/index_daily/932368.CSI.csv")
    df_idx_price['trade_date'] = df_idx_price['trade_date'].astype(str)
    df_idx_price = df_idx_price[['trade_date', 'close']].rename(columns={'close': 'p'}).sort_values('trade_date')
    
    df_hs_p = pd.read_csv("data/index_daily/000300.SH.csv")
    df_hs_p['trade_date'] = df_hs_p['trade_date'].astype(str)
    df_hs_p = df_hs_p[['trade_date', 'close']].rename(columns={'close': 'hs_p'})
    df_hs_tr = pd.read_csv("data/index_daily/H00300.CSI.csv")
    df_hs_tr['trade_date'] = df_hs_tr['trade_date'].astype(str)
    df_hs_tr = df_hs_tr[['trade_date', 'close']].rename(columns={'close': 'hs_tr'})
    
    df_div = df_hs_p.merge(df_hs_tr, on='trade_date', how='inner')
    df_div['div_adj'] = df_div['hs_tr'] / df_div['hs_p']
    df_idx = df_idx_price.merge(df_div[['trade_date', 'div_adj']], on='trade_date', how='inner')
    df_idx['close'] = df_idx['p'] * df_idx['div_adj']
    
    df_hs = pd.read_csv("data/index_daily/H00300.CSI.csv")
    df_hs['trade_date'] = df_hs['trade_date'].astype(str)
    df_hs = df_hs[['trade_date', 'close']].sort_values('trade_date')
    
    def idx_ret(df, s, e):
        sk, ek = s.replace('-', ''), e.replace('-', '')
        try:
            p0 = float(df[df['trade_date'] <= sk]['close'].iloc[-1])
            p1 = float(df[df['trade_date'] <= ek]['close'].iloc[-1])
            return (p1 / p0 - 1) * 100 if p0 > 0 else 0.0
        except IndexError:
            return 0.0
    
    def calc_nav(baskets, min_stocks=5, min_weight=0.3):
        nav = 1.0
        rows = []
        for _, row in nav_df.iterrows():
            rb, nrb = row['rb_date'], row['next_rb']
            stocks = baskets.get(rb, [])
            if len(stocks) < min_stocks:
                continue
            w_ret, w_tot = 0.0, 0.0
            for s in stocks:
                r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
                if r:
                    w_ret += s['weight'] * (r[1] / r[0] - 1)
                    w_tot += s['weight']
            if w_tot < min_weight:
                continue
            pr = w_ret / w_tot
            nav *= (1 + pr)
            rows.append({'rb_date': rb, 'next_rb': nrb, 'period_ret': pr * 100, 'nav': nav})
        return pd.DataFrame(rows)
    
    # 计算 E+诚信版 NAV
    print("  计算 E+诚信版 NAV...")
    ei_nav = calc_nav(ei_baskets)
    ei_nav.to_csv(PROJECT_ROOT / OUT_DIR / "backtest_nav_tr.csv", index=False)
    print(f"    E+诚信版: {len(ei_nav)}期, 期末NAV={ei_nav.iloc[-1]['nav']:.4f}x")
    
    # 计算 E版 NAV（对比）
    if e_baskets:
        print("  计算 E版 NAV（对比）...")
        e_nav = calc_nav(e_baskets)
        print(f"    E版: {len(e_nav)}期, 期末NAV={e_nav.iloc[-1]['nav']:.4f}x")
    else:
        # 尝试加载已有 E版 NAV
        e_nav_path = PROJECT_ROOT / E_OUT_DIR / "backtest_nav_tr.csv"
        if e_nav_path.exists():
            e_nav = pd.read_csv(e_nav_path)
            print(f"    E版（加载已有）: {len(e_nav)}期, 期末NAV={e_nav.iloc[-1]['nav']:.4f}x")
        else:
            e_nav = pd.DataFrame()
    
    # 计算基准 NAV
    print("  计算基准 NAV...")
    i_nav_val, h_nav_val = 1.0, 1.0
    idx_navs, hs_navs = [], []
    for _, row in nav_df.iterrows():
        ir = idx_ret(df_idx, row['rb_date'], row['next_rb'])
        hr = idx_ret(df_hs, row['rb_date'], row['next_rb'])
        i_nav_val *= (1 + ir / 100)
        h_nav_val *= (1 + hr / 100)
        idx_navs.append(i_nav_val)
        hs_navs.append(h_nav_val)
    
    # ── 绩效计算 ──
    def perf_stats(nav_series, rets_series):
        n = len(rets_series)
        if n == 0: return {}
        ann = (nav_series.iloc[-1] ** (4 / n) - 1) * 100
        vol = rets_series.std() * 2
        peak = nav_series.cummax()
        mdd = ((peak - nav_series) / peak).max() * 100
        sharpe = (ann - 2.0) / vol if vol > 0 else 0
        calmar = ann / mdd if mdd > 0 else 0
        win = (rets_series > 0).mean() * 100
        return {'ann': ann, 'vol': vol, 'mdd': -mdd, 'sharpe': sharpe,
                'calmar': calmar, 'win': win, 'nav': nav_series.iloc[-1]}
    
    def turnover(baskets):
        dates = [r['rb_date'] for _, r in nav_df.iterrows() if baskets.get(r['rb_date'])]
        tos = []
        for i in range(1, len(dates)):
            prev = {s['ts_code'] for s in baskets.get(dates[i-1], [])}
            curr = {s['ts_code'] for s in baskets.get(dates[i], [])}
            if curr: tos.append(len(curr - prev) / len(curr))
        return np.mean(tos) * 100 if tos else 0
    
    ei_perf = perf_stats(ei_nav['nav'], ei_nav['period_ret'])
    ei_to = turnover(ei_baskets)
    
    if len(e_nav) > 0:
        e_perf = perf_stats(e_nav['nav'], e_nav['period_ret'])
        e_to = turnover(e_baskets) if e_baskets else 0
    else:
        e_perf = {}
        e_to = 0
    
    idx_ret_series = pd.Series([
        idx_ret(df_idx, row['rb_date'], row['next_rb'])
        for _, row in nav_df.iterrows()
    ])
    hs_ret_series = pd.Series([
        idx_ret(df_hs, row['rb_date'], row['next_rb'])
        for _, row in nav_df.iterrows()
    ])
    idx_perf = perf_stats(pd.Series(idx_navs), idx_ret_series)
    hs_perf = perf_stats(pd.Series(hs_navs), hs_ret_series)
    
    years = (datetime(2026, 6, 15) - datetime(2015, 3, 16)).days / 365.25
    
    # ── 过滤统计 ──
    fs_path = PROJECT_ROOT / OUT_DIR / "filter_stats.json"
    if fs_path.exists():
        with open(fs_path) as f:
            filter_stats = json.load(f)
        all_orig = [s[1] for s in filter_stats if s[1] > 0]
        all_filt = [s[2] for s in filter_stats if s[1] > 0]
        avg_filter_pass_rate = (np.mean(all_filt) / np.mean(all_orig) * 100) if all_orig else 0
    else:
        avg_filter_pass_rate = 0
    
    # ── 生成报告 ──
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    N = len(ei_nav)
    
    lines = []
    lines.append("# E版 + 诚信过滤 回测报告")
    lines.append("")
    lines.append(f"> 生成时间：{now}")
    lines.append(f"> 回测区间：2015-03-16 → 2026-06-15（共 {N} 期，{years:.1f} 年）")
    lines.append(f"> 诚信过滤：现金实现率（2年累计FCF/累计NP）≥ {MIN_CASH_REALIZATION}")
    lines.append("> 全收益模式（含分红再投资，复权价计算）")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、策略说明")
    lines.append("")
    lines.append("| 维度 | 说明 |")
    lines.append("|------|------|")
    lines.append(f"| **选股池** | ZZ800（中证800，000906.SH） |")
    lines.append(f"| **诚信过滤** | 剔除现金实现率（2年累计FCF/累计归母净利润）< {MIN_CASH_REALIZATION} 的标的 |")
    lines.append(f"| **缓冲区** | E版 ±40%（前30必选，31-70粘性保留） |")
    lines.append(f"| **持仓数** | Top 50 |")
    lines.append(f"| **加权方式** | FCF绝对值加权 + 单股10%封顶 |")
    lines.append(f"| **调仓频率** | 季度（3/6/9/12月） |")
    lines.append(f"| **过滤后平均候选数** | {avg_filter_pass_rate:.0f}% 通过率 |")
    lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append("## 二、核心指标对比")
    lines.append("")
    lines.append("| 指标 | E+诚信版 | E版（基准） | 差异 | 932368 | 沪深300全收益 |")
    lines.append("|------|----------|------------|------|--------|---------------|")
    
    diff_ann = ei_perf.get('ann', 0) - e_perf.get('ann', 0) if e_perf else 0
    diff_mdd = ei_perf.get('mdd', 0) - e_perf.get('mdd', 0) if e_perf else 0
    diff_sharpe = ei_perf.get('sharpe', 0) - e_perf.get('sharpe', 0) if e_perf else 0
    diff_nav = ei_perf.get('nav', 1) - e_perf.get('nav', 1) if e_perf else 0
    
    for label, ei_key, fmt in [
        ('年化收益', 'ann', '.2f'), ('最大回撤', 'mdd', '.2f'),
        ('夏普比率', 'sharpe', '.3f'), ('期末净值', 'nav', '.4f'),
        ('换手率', 'to', '.1f'),
    ]:
        if ei_key == 'to':
            ei_v = ei_to
            e_v = e_to
            unit = '%'
        elif ei_key == 'nav':
            ei_v = ei_perf.get('nav', 0)
            e_v = e_perf.get('nav', 0)
            unit = 'x'
        else:
            ei_v = ei_perf.get(ei_key, 0)
            e_v = e_perf.get(ei_key, 0)
            unit = '%'
        
        diff_v = ei_v - e_v
        diff_sign = '+' if diff_v > 0 else ''
        
        ei_str = f"{ei_v:{fmt}}{unit}"
        e_str = f"{e_v:{fmt}}{unit}" if e_perf else '—'
        diff_str = f"**{diff_sign}{diff_v:{fmt}}{unit}**" if e_perf else '—'
        
        if ei_key == 'nav':
            idx_v = f"{idx_perf.get('nav', 0):.4f}x"
            hs_v = f"{hs_perf.get('nav', 0):.4f}x"
        elif ei_key == 'to':
            idx_v = '—'
            hs_v = '—'
        else:
            idx_v = f"{idx_perf.get(ei_key, 0):{fmt}}%"
            hs_v = f"{hs_perf.get(ei_key, 0):{fmt}}%"
        
        lines.append(f"| {label} | {ei_str} | {e_str} | {diff_str} | {idx_v} | {hs_v} |")
    
    lines.append("")
    lines.append(f"> **差异解读**：年化差异 {diff_ann:+.2f}%，最大回撤差异 {diff_mdd:+.2f}%，"
                 f"期末NAV差异 {diff_nav:+.4f}x")
    
    # ── 逐年收益 ──
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 三、逐年收益对比")
    lines.append("")
    lines.append("| 年份 | E+诚信版 | E版 | 差异 | 932368 | 沪深300 |")
    lines.append("|------|----------|-----|------|--------|---------|")
    
    # 合并数据
    m = ei_nav[['rb_date', 'next_rb', 'period_ret', 'nav']].copy()
    m.columns = ['rb_date', 'next_rb', 'ei_ret', 'ei_nav']
    m['idx_ret'] = idx_ret_series.values[:len(m)]
    m['hs_ret'] = hs_ret_series.values[:len(m)]
    
    if len(e_nav) > 0:
        m = m.merge(
            e_nav[['rb_date', 'period_ret']].rename(columns={'period_ret': 'e_ret'}),
            on='rb_date', how='left'
        )
    
    for yr in sorted(m['rb_date'].str[:4].unique()):
        rows = m[m['rb_date'].str[:4] == yr]
        ei_yr = (1 + rows['ei_ret'] / 100).prod() - 1
        idx_yr = (1 + rows['idx_ret'] / 100).prod() - 1
        hs_yr = (1 + rows['hs_ret'] / 100).prod() - 1
        
        def pct(v): return f"{'+' if v >= 0 else ''}{v*100:.1f}%"
        
        if len(e_nav) > 0:
            e_yr = (1 + rows['e_ret'] / 100).prod() - 1
            diff_yr = ei_yr - e_yr
            lines.append(f"| {yr} | {pct(ei_yr)} | {pct(e_yr)} | {pct(diff_yr)} | {pct(idx_yr)} | {pct(hs_yr)} |")
        else:
            lines.append(f"| {yr} | {pct(ei_yr)} | — | — | {pct(idx_yr)} | {pct(hs_yr)} |")
    
    # ── 过滤效果分析 ──
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 四、诚信过滤效果分析")
    lines.append("")
    
    if fs_path.exists():
        with open(fs_path) as f:
            filter_stats = json.load(f)
        
        # 按年份汇总
        yr_stats = {}
        for s in filter_stats:
            yr = s[0][:4]
            if yr not in yr_stats:
                yr_stats[yr] = {'orig': [], 'filt': [], 'skipped': []}
            yr_stats[yr]['orig'].append(s[1])
            yr_stats[yr]['filt'].append(s[2])
            yr_stats[yr]['skipped'].append(s[3])
        
        lines.append("| 年份 | 原始候选 | 过滤后 | 淘汰数 | 通过率 |")
        lines.append("|------|----------|--------|--------|--------|")
        for yr in sorted(yr_stats.keys()):
            s = yr_stats[yr]
            avg_orig = np.mean(s['orig'])
            avg_filt = np.mean(s['filt'])
            avg_skip = np.mean(s['skipped'])
            rate = avg_filt / avg_orig * 100 if avg_orig > 0 else 0
            lines.append(f"| {yr} | {avg_orig:.0f} | {avg_filt:.0f} | {avg_skip:.0f} | {rate:.0f}% |")
    
    # ── 结论 ──
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 五、结论")
    lines.append("")
    
    if e_perf:
        if diff_ann > 0.3:
            conclusion = (
                f"✅ **有效提升**：加入诚信过滤后，E+诚信版年化 {ei_perf.get('ann',0):.2f}% vs E版 {e_perf.get('ann',0):.2f}%，"
                f"提升 {diff_ann:.2f} 个百分点。最大回撤从 {e_perf.get('mdd',0):.2f}% 改善至 {ei_perf.get('mdd',0):.2f}%。\n\n"
                f"现金实现率 ≥ {MIN_CASH_REALIZATION} 的诚信过滤是有效的排雷工具，建议在 E版策略中正式引入。"
            )
        elif diff_ann > -0.3:
            conclusion = (
                f"⚡ **基本持平**：E+诚信版年化 {ei_perf.get('ann',0):.2f}% vs E版 {e_perf.get('ann',0):.2f}%，"
                f"差异 {diff_ann:+.2f} 个百分点。\n\n"
                f"诚信过滤没有显著提升也没有拖累，考虑到它可能降低踩雷概率（样本外），"
                f"建议作为可选的质量过滤保留。"
            )
        else:
            conclusion = (
                f"❌ **拖累收益**：E+诚信版年化 {ei_perf.get('ann',0):.2f}% vs E版 {e_perf.get('ann',0):.2f}%，"
                f"降低了 {abs(diff_ann):.2f} 个百分点。\n\n"
                f"诚信过滤过于严格（阈值 {MIN_CASH_REALIZATION}），可能剔除了部分"
                f"高成长但短期现金实现率低的优质标的。建议：1）降低阈值到 0.1；2）或仅作为软性加权因子而非硬性剔除。"
            )
    else:
        conclusion = "（E版数据缺失，无法生成对比结论）"
    
    lines.append(conclusion)
    
    # 写入报告
    report_path = PROJECT_ROOT / "docs" / "2026-06-11_zz800_e_integrity_filter.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    
    print(f"\n✅ 报告已生成: {report_path}")
    
    # 打印控制台摘要
    print("\n" + "=" * 70)
    print("E版 + 诚信过滤 回测完成")
    print("=" * 70)
    if e_perf:
        print(f"  E+诚信版: 年化 {ei_perf.get('ann',0):.2f}% | 最大回撤 {ei_perf.get('mdd',0):.2f}% | "
              f"夏普 {ei_perf.get('sharpe',0):.3f} | NAV {ei_perf.get('nav',0):.4f}x | 换手 {ei_to:.1f}%")
        print(f"  E版基准:  年化 {e_perf.get('ann',0):.2f}% | 最大回撤 {e_perf.get('mdd',0):.2f}% | "
              f"夏普 {e_perf.get('sharpe',0):.3f} | NAV {e_perf.get('nav',0):.4f}x | 换手 {e_to:.1f}%")
        print(f"  差异:     {diff_ann:+.2f}%               {diff_mdd:+.2f}%               "
              f"{diff_sharpe:+.3f}              {diff_nav:+.4f}x")
    else:
        print(f"  E+诚信版: 年化 {ei_perf.get('ann',0):.2f}% | 最大回撤 {ei_perf.get('mdd',0):.2f}% | "
              f"夏普 {ei_perf.get('sharpe',0):.3f} | NAV {ei_perf.get('nav',0):.4f}x")
    print(f"  932368:   年化 {idx_perf.get('ann',0):.2f}% | 最大回撤 {idx_perf.get('mdd',0):.2f}% | "
          f"夏普 {idx_perf.get('sharpe',0):.3f} | NAV {idx_perf.get('nav',0):.4f}x")
    print(f"  沪深300:  年化 {hs_perf.get('ann',0):.2f}% | 最大回撤 {hs_perf.get('mdd',0):.2f}% | "
          f"夏普 {hs_perf.get('sharpe',0):.3f} | NAV {hs_perf.get('nav',0):.4f}x")


if __name__ == "__main__":
    main()
