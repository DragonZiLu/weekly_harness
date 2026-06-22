#!/usr/bin/env python3
"""
筛选 10 年 3 倍以上、FCF 稳健增长驱动股价的优质复利标的
"""
import pandas as pd
import numpy as np
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
FCF_DIR = PROJECT_DIR / "data/fcf_financials"


def load_annual_fcf():
    dfs = []
    for y in range(2010, 2026):
        f = FCF_DIR / f"cashflow_{y}.csv"
        if f.exists():
            df = pd.read_csv(f)
            df = df[df["end_date"].astype(str).str.endswith("1231")]
            dfs.append(df)
    df_all = pd.concat(dfs, ignore_index=True)
    for c in ["n_cashflow_act", "c_pay_acq_const_fiolta"]:
        df_all[c] = pd.to_numeric(df_all[c], errors="coerce")
    df_all["fcf"] = df_all["n_cashflow_act"] - df_all["c_pay_acq_const_fiolta"]
    df_all["year"] = df_all["end_date"].astype(str).str[:4].astype(int)
    return df_all


def main():
    # 1. 加载全量 800 标的十年收益
    df_all = pd.read_csv(PROJECT_DIR / "output/ten_bagger_zz800_10y.csv")
    df_3x = df_all[df_all["multiple"] >= 3.0].copy()
    print(f"📋 3 倍以上标的: {len(df_3x)} 只")

    df_cf = load_annual_fcf()

    results = []
    for _, row in df_3x.iterrows():
        ts_code = row["ts_code"]
        name = row["name"]
        mult = row["multiple"]
        price_cagr = row["cagr_pct"] / 100

        stock_cf = df_cf[(df_cf["ts_code"] == ts_code) & (df_cf["year"] >= 2015) & (df_cf["year"] <= 2025)]
        stock_cf = stock_cf.sort_values("year")

        if stock_cf.empty:
            continue

        years = stock_cf["year"].tolist()
        fcf_vals = stock_cf["fcf"].tolist()
        n = len(years)

        if n < 4:
            continue

        # ---- FCF CAGR（对数线性拟合）----
        pos_mask = np.array(fcf_vals) > 0
        fcf_cagr_fit = None
        r2 = None
        if pos_mask.sum() >= 4:
            t = np.array(years)[pos_mask] - years[0]
            log_fcf = np.log(np.array(fcf_vals)[pos_mask])
            if len(t) >= 4:
                slope, intercept = np.polyfit(t, log_fcf, 1)
                fcf_cagr_fit = np.exp(slope) - 1
                pred = np.exp(intercept + slope * t)
                ss_res = np.sum((np.array(fcf_vals)[pos_mask] - pred) ** 2)
                ss_tot = np.sum((np.array(fcf_vals)[pos_mask] - np.mean(np.array(fcf_vals)[pos_mask])) ** 2)
                r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        # ---- 简单 CAGR ----
        fcf_start = fcf_vals[0]
        fcf_end = fcf_vals[-1]
        n_years = years[-1] - years[0]
        fcf_cagr_simple = None
        if fcf_start > 0 and fcf_end > 0 and n_years > 0:
            fcf_cagr_simple = (fcf_end / fcf_start) ** (1 / n_years) - 1

        fcf_cagr = fcf_cagr_fit if fcf_cagr_fit is not None else fcf_cagr_simple
        if fcf_cagr is None:
            continue

        # ---- FCF 稳定性指标 ----
        yoy_growths = []
        for i in range(1, n):
            if fcf_vals[i - 1] > 0 and fcf_vals[i] > 0:
                yoy_growths.append(fcf_vals[i] / fcf_vals[i - 1] - 1)
        growth_vol = np.std(yoy_growths) if len(yoy_growths) >= 3 else None

        fcf_pos_ratio = sum(1 for v in fcf_vals if v > 0) / n

        # 增速趋势：逐年增速的符号
        if len(yoy_growths) >= 3:
            positive_growth_years = sum(1 for g in yoy_growths if g > 0)
            growth_consistency = positive_growth_years / len(yoy_growths)
        else:
            growth_consistency = None

        # FCF 是否逐年递增（单调性）
        increasing_streak = 0
        for i in range(1, n):
            if fcf_vals[i] > fcf_vals[i - 1]:
                increasing_streak += 1
        monotonicity = increasing_streak / (n - 1)  # 递增年数占比

        fcf_px_ratio = fcf_cagr / price_cagr if price_cagr != 0 else None

        # 后5年 vs 前5年均值
        mid = len(years) // 2
        early_avg = np.mean(fcf_vals[:mid])
        late_avg = np.mean(fcf_vals[mid:])

        results.append({
            "ts_code": ts_code,
            "name": name,
            "sector": row.get("sector", ""),
            "price_multiple": mult,
            "price_cagr": price_cagr,
            "fcf_cagr": fcf_cagr,
            "fcf_px_ratio": fcf_px_ratio,
            "fcf_start": fcf_start,
            "fcf_end": fcf_end,
            "fcf_first_year": years[0],
            "fcf_last_year": years[-1],
            "n_years": n,
            "growth_volatility": growth_vol,
            "fcf_positive_ratio": fcf_pos_ratio,
            "growth_consistency": growth_consistency,
            "monotonicity": monotonicity,
            "r2": r2,
            "early_avg": early_avg,
            "late_avg": late_avg,
        })

    df_r = pd.DataFrame(results)
    print(f"📊 有有效 FCF CAGR（≥4个正年）: {len(df_r)} 只")

    # ---- 筛选条件：FCF 稳健驱动 ----
    df_valid = df_r[
        (df_r["fcf_px_ratio"] >= 0.6) &            # FCF增速至少达到股价增速60%
        (df_r["fcf_positive_ratio"] >= 0.7) &       # 至少70%年份FCF为正
        (df_r["monotonicity"] >= 0.5) &             # 至少一半年份FCF递增
        (df_r["fcf_cagr"] > 0)                       # FCF正增长
    ].copy()

    # 综合评分：ratio 接近 1 + 低波动 + 高一致性
    df_valid["ratio_deviation"] = abs(df_valid["fcf_px_ratio"] - 1.0)
    df_valid["score"] = (
        -df_valid["ratio_deviation"] * 2 +
        -df_valid["growth_volatility"].fillna(2) * 0.3 +
        df_valid["growth_consistency"].fillna(0) * 0.5 +
        df_valid["monotonicity"] * 0.8 +
        df_valid["r2"].fillna(0)
    )

    df_best = df_valid.sort_values("score", ascending=False)

    print(f"\n🎯 FCF 稳健驱动型（ratio≥0.6, 正年≥70%, 递增≥50%）: {len(df_best)} 只\n")

    # 输出 TOP 40
    header = (f"{'排名':<5} {'代码':<12} {'名称':<10} {'股价':>6} "
              f"{'股价年化':>8} {'FCF年化':>8} {'比率':>6} {'FCF十年':>18} "
              f"{'正年%':>5} {'递增%':>6} {'波动率':>6} {'R²':>5}")
    print(header)
    print("-" * 110)

    for i, (_, r) in enumerate(df_best.head(40).iterrows(), 1):
        fcf_change = f"{r['fcf_start']/1e8:.1f}→{r['fcf_end']/1e8:.1f}亿"
        print(f"#{i:<4} {r['ts_code']:<12} {r['name']:<10} "
              f"{r['price_multiple']:>4.1f}x {r['price_cagr']*100:>6.1f}% "
              f"{r['fcf_cagr']*100:>6.1f}% {r['fcf_px_ratio']:>5.2f}  "
              f"{fcf_change:>18} "
              f"{r['fcf_positive_ratio']*100:>4.0f}% "
              f"{r['monotonicity']*100:>5.0f}% "
              f"{r['growth_volatility']:.2f}" if r['growth_volatility'] is not None else "N/A")

    # 分类统计
    print(f"\n{'='*60}")
    print("  按 FCF/股价增速比分段")
    print(f"{'='*60}")
    for lo, hi, label in [
        (0.9, 1.5, "🎯 完美同步 (0.9~1.5)"),
        (0.7, 0.9, "📊 适度扩张 (0.7~0.9)"),
        (0.6, 0.7, "📈 估值部分贡献 (0.6~0.7)"),
    ]:
        sub = df_best[(df_best["fcf_px_ratio"] >= lo) & (df_best["fcf_px_ratio"] < hi)]
        if not sub.empty:
            avg_mult = sub["price_multiple"].mean()
            print(f"  {label}: {len(sub)} 只, 平均 {avg_mult:.1f}x")

    # 按倍数分段
    print(f"\n{'='*60}")
    print("  按股价倍数十段")
    print(f"{'='*60}")
    for lo, hi, label in [
        (10, 200, "🔥 10倍+"),
        (5, 10, "⭐ 5-10倍"),
        (3, 5, "📈 3-5倍"),
    ]:
        sub = df_best[(df_best["price_multiple"] >= lo) & (df_best["price_multiple"] < hi)]
        print(f"  {label}: {len(sub)} 只, 平均FCF/股价比率 {sub['fcf_px_ratio'].mean():.2f}")

    output = PROJECT_DIR / "output/fcf_compounders_3x.csv"
    df_r.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"\n💾 全量结果: {output}")

    return df_r


if __name__ == "__main__":
    df = main()
