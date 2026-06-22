#!/usr/bin/env python3
"""
筛选 FCF 与股价同步增长的标的：FCF 年化增速 ≈ 股价年化增速（基本面驱动型）
覆盖 5 倍以上所有标的。
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
    # 1. 加载 5 倍以上标的
    df_all = pd.read_csv(PROJECT_DIR / "output/ten_bagger_zz800_10y.csv")
    df_5x = df_all[df_all["multiple"] >= 5.0].copy()
    print(f"📋 5 倍以上标的: {len(df_5x)} 只")

    # 2. 加载 FCF 数据
    df_cf = load_annual_fcf()
    
    # 3. 逐只计算 FCF 年化增速
    results = []
    for _, row in df_5x.iterrows():
        ts_code = row["ts_code"]
        name = row["name"]
        mult = row["multiple"]
        price_cagr = row["cagr_pct"] / 100  # 转小数
        
        stock_cf = df_cf[(df_cf["ts_code"] == ts_code) & (df_cf["year"] >= 2015) & (df_cf["year"] <= 2025)]
        stock_cf = stock_cf.sort_values("year")
        
        if stock_cf.empty:
            continue
        
        years = stock_cf["year"].tolist()
        fcf_vals = stock_cf["fcf"].tolist()
        n = len(years)
        
        if n < 3:
            continue
        
        # ---- 方法1：起点→终点 CAGR ----
        fcf_start = fcf_vals[0]
        fcf_end = fcf_vals[-1]
        n_years = years[-1] - years[0]
        
        fcf_cagr_simple = None
        if fcf_start > 0 and fcf_end > 0 and n_years > 0:
            fcf_cagr_simple = (fcf_end / fcf_start) ** (1 / n_years) - 1
        
        # ---- 方法2：指数回归（对数线性拟合）更鲁棒 ----
        # log(FCF) = a + b*t，则年化 = exp(b*1) - 1
        pos_mask = np.array(fcf_vals) > 0
        if pos_mask.sum() >= 3:
            t = np.array(years)[pos_mask] - years[0]
            log_fcf = np.log(np.array(fcf_vals)[pos_mask])
            if len(t) >= 3:
                slope, intercept = np.polyfit(t, log_fcf, 1)
                fcf_cagr_fit = np.exp(slope) - 1
            else:
                fcf_cagr_fit = None
        else:
            fcf_cagr_fit = None
        
        # 优先用拟合，没有则用简单
        fcf_cagr = fcf_cagr_fit if fcf_cagr_fit is not None else fcf_cagr_simple
        
        # ---- FCF 趋势稳定性 ----
        # 计算逐年 FCF 增速的标准差（越小越稳定）
        yoy_growths = []
        for i in range(1, n):
            if fcf_vals[i-1] > 0 and fcf_vals[i] > 0:
                yoy_growths.append(fcf_vals[i] / fcf_vals[i-1] - 1)
        growth_volatility = np.std(yoy_growths) if len(yoy_growths) >= 3 else None
        
        # FCF 正年数占比
        fcf_positive_ratio = sum(1 for v in fcf_vals if v > 0) / n
        
        # 后 3 年 FCF 均值 vs 前 3 年
        early = np.mean(fcf_vals[:3])
        late = np.mean(fcf_vals[-3:])
        
        results.append({
            "ts_code": ts_code,
            "name": name,
            "sector": row.get("sector", ""),
            "price_multiple": mult,
            "price_cagr": price_cagr,
            "fcf_cagr_fit": fcf_cagr_fit,
            "fcf_cagr_simple": fcf_cagr_simple,
            "fcf_cagr": fcf_cagr,
            "fcf_start": fcf_start,
            "fcf_end": fcf_end,
            "fcf_first_year": years[0],
            "fcf_last_year": years[-1],
            "growth_volatility": growth_volatility,
            "fcf_positive_ratio": fcf_positive_ratio,
            "early_avg": early,
            "late_avg": late,
            "n_years": n,
        })
    
    df_r = pd.DataFrame(results)
    
    # 只保留能计算 FCF CAGR 的（起点 FCF 为正）
    df_valid = df_r[df_r["fcf_cagr"].notna()].copy()
    print(f"📊 有有效 FCF CAGR（起点FCF>0且≥3个正年）: {len(df_valid)} 只")
    
    # 计算 FCF/股价 增速比
    df_valid["fcf_px_ratio"] = df_valid["fcf_cagr"] / df_valid["price_cagr"]
    
    # 筛选"同步增长"型：FCF 增速 ≈ 股价增速（ratio 在 0.8~1.5 之间）
    df_sync = df_valid[
        (df_valid["fcf_px_ratio"] >= 0.7) & 
        (df_valid["fcf_px_ratio"] <= 1.5) &
        (df_valid["fcf_positive_ratio"] >= 0.6)  # 至少 60% 年份 FCF 为正
    ].sort_values("price_multiple", ascending=False)
    
    print(f"\n🎯 FCF增速≈股价增速（0.7~1.5倍，基本面同步驱动）: {len(df_sync)} 只\n")
    
    # ---- 输出 ----
    header = f"{'排名':<5} {'代码':<12} {'名称':<10} {'股价':>6} {'股价年化':>8} {'FCF年化':>8} {'FCF/股价':>8} {'FCF起点':>10} {'FCF终点':>10} {'正年%':>6} {'波动率':>7}"
    print(header)
    print("-" * 100)
    
    for i, (_, r) in enumerate(df_sync.iterrows(), 1):
        start_str = f"{r['fcf_start']/1e8:>7.1f}亿"
        end_str = f"{r['fcf_end']/1e8:>7.1f}亿"
        print(f"#{i:<4} {r['ts_code']:<12} {r['name']:<10} "
              f"{r['price_multiple']:>4.1f}x {r['price_cagr']*100:>6.1f}% "
              f"{r['fcf_cagr']*100:>6.1f}% {r['fcf_px_ratio']:>6.2f}  "
              f"{start_str} {end_str} "
              f"{r['fcf_positive_ratio']*100:>5.0f}% "
              f"{r['growth_volatility']:.2f}" if r['growth_volatility'] is not None else "N/A")
    
    # ---- 按 ratio 最接近 1 的 TOP ----
    print(f"\n{'='*100}")
    print("  🥇 最完美同步 TOP 20（FCF/股价 比率最接近 1.0）")
    print(f"{'='*100}")
    
    df_best = df_valid.copy()
    df_best["ratio_deviation"] = abs(df_best["fcf_px_ratio"] - 1.0)
    df_best = df_best.sort_values("ratio_deviation").head(20)
    
    header2 = f"{'排名':<5} {'代码':<12} {'名称':<10} {'股价':>6} {'股价年化':>8} {'FCF年化':>8} {'FCF/股价':>8} {'偏差':>6} {'FCF起点':>10} {'FCF终点':>10}"
    print(header2)
    print("-" * 95)
    
    for i, (_, r) in enumerate(df_best.iterrows(), 1):
        start_str = f"{r['fcf_start']/1e8:>7.1f}亿"
        end_str = f"{r['fcf_end']/1e8:>7.1f}亿"
        print(f"#{i:<4} {r['ts_code']:<12} {r['name']:<10} "
              f"{r['price_multiple']:>4.1f}x {r['price_cagr']*100:>6.1f}% "
              f"{r['fcf_cagr']*100:>6.1f}% {r['fcf_px_ratio']:>6.2f}  "
              f"{abs(r['fcf_px_ratio']-1):>5.3f} "
              f"{start_str} {end_str}")
    
    # ---- 特别关注：高倍数 + 完美同步 ----
    print(f"\n{'='*100}")
    print("  ⭐ 高倍数（10x+）且基本面对应的标的")
    print(f"{'='*100}")
    df_high_sync = df_valid[
        (df_valid["price_multiple"] >= 5.0) &
        (df_valid["fcf_px_ratio"] >= 0.7) &
        (df_valid["fcf_px_ratio"] <= 1.5)
    ].sort_values("price_multiple", ascending=False)
    
    for i, (_, r) in enumerate(df_high_sync.iterrows(), 1):
        print(f"  {r['ts_code']:<12} {r['name']:<10}  "
              f"{r['price_multiple']:>4.1f}x  "
              f"股价年化{r['price_cagr']*100:>5.1f}%  FCF年化{r['fcf_cagr']*100:>5.1f}%  "
              f"比率{r['fcf_px_ratio']:.2f}  "
              f"FCF:{r['fcf_start']/1e8:.1f}→{r['fcf_end']/1e8:.1f}亿")
    
    # 保存
    output = PROJECT_DIR / "output/fcf_sync_stocks_5x.csv"
    df_r.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"\n💾 全量结果: {output}")
    
    return df_r


if __name__ == "__main__":
    df = main()
