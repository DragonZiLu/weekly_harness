#!/usr/bin/env python3
"""
十倍股 FCF 成长分析：45只中证800十年十倍股，其自由现金流（FCF=OCF-Capex）十年间增长情况如何？
"""
import pandas as pd
import numpy as np
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
FCF_DIR = PROJECT_DIR / "data/fcf_financials"

def load_cashflow_yearly():
    """加载所有年报现金流数据（只取年报 end_date 以 1231 结尾的），合并为 DataFrame"""
    dfs = []
    for y in range(2010, 2026):
        f = FCF_DIR / f"cashflow_{y}.csv"
        if f.exists():
            df = pd.read_csv(f)
            df = df[df["end_date"].astype(str).str.endswith("1231")]
            dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    df_all = pd.concat(dfs, ignore_index=True)
    # 处理数值列
    for c in ["n_cashflow_act", "c_pay_acq_const_fiolta"]:
        df_all[c] = pd.to_numeric(df_all[c], errors="coerce")
    # FCF = OCF - Capex（单位：元）
    df_all["fcf"] = df_all["n_cashflow_act"] - df_all["c_pay_acq_const_fiolta"]
    return df_all


def main():
    # 1. 加载十倍股列表
    df_10x = pd.read_csv(PROJECT_DIR / "output/ten_bagger_zz800_10y.csv")
    df_10x = df_10x[df_10x["multiple"] >= 10.0].copy()
    ten_baggers = set(df_10x["ts_code"].tolist())
    print(f"📋 十倍股数量: {len(ten_baggers)}")

    # 2. 加载全部年报 FCF
    df_cf = load_cashflow_yearly()
    print(f"📊 现金流年报记录总数: {len(df_cf)}")
    
    # 过滤只保留十倍股的数据
    df_cf_10x = df_cf[df_cf["ts_code"].isin(ten_baggers)].copy()
    df_cf_10x["end_date"] = df_cf_10x["end_date"].astype(str)
    df_cf_10x["year"] = df_cf_10x["end_date"].str[:4].astype(int)
    
    print(f"📊 十倍股现金流记录: {len(df_cf_10x)}")

    # 3. 对每只十倍股，找 2015/2016 年的 FCF 和最新的 2025 年 FCF
    results = []
    
    for ts_code in sorted(ten_baggers):
        stock_cf = df_cf_10x[df_cf_10x["ts_code"] == ts_code].sort_values("year")
        
        if stock_cf.empty:
            continue
        
        # 取 2015 和 2016 中较早可用的作为起点（TTM口径用两个年报平均）
        cf_2015 = stock_cf[stock_cf["year"] == 2015]
        cf_2016 = stock_cf[stock_cf["year"] == 2016]
        cf_2025 = stock_cf[stock_cf["year"] == 2025]
        
        fcf_start = None
        start_label = ""
        if not cf_2016.empty:
            fcf_start = cf_2016["fcf"].values[0]
            start_label = "2016"
        elif not cf_2015.empty:
            fcf_start = cf_2015["fcf"].values[0]
            start_label = "2015"
        
        fcf_end = cf_2025["fcf"].values[0] if not cf_2025.empty else None
        
        if fcf_start is None or fcf_end is None:
            continue
        
        if fcf_start <= 0:
            # 起点 FCF 为负或零，用"从负转正"标记
            fcf_growth_pct = None
            fcf_cagr = None
        else:
            fcf_growth_pct = (fcf_end / fcf_start - 1) * 100
            n_years = 2025 - int(start_label)
            if fcf_end > 0 and n_years > 0:
                fcf_cagr = (fcf_end / fcf_start) ** (1 / n_years) - 1
            else:
                fcf_cagr = None
        
        # 计算中间年份的趋势（防止单年噪音）
        all_years_fcf = []
        for y in range(int(start_label), 2026):
            row = stock_cf[stock_cf["year"] == y]
            if not row.empty:
                all_years_fcf.append((y, row["fcf"].values[0]))
        
        # 看 FCF 是否持续增长（后5年 vs 前5年）
        early_years = [(y, v) for y, v in all_years_fcf if y < 2021]
        late_years = [(y, v) for y, v in all_years_fcf if y >= 2021]
        early_avg = np.mean([v for _, v in early_years]) if early_years else None
        late_avg = np.mean([v for _, v in late_years]) if late_years else None
        
        # 从 price 表中取信息
        row_10x = df_10x[df_10x["ts_code"] == ts_code]
        name = row_10x["name"].values[0] if not row_10x.empty else "?"
        mult = row_10x["multiple"].values[0] if not row_10x.empty else 0
        cagr_price = row_10x["cagr_pct"].values[0] if not row_10x.empty else 0
        sector = row_10x["sector"].values[0] if not row_10x.empty else "?"
        
        results.append({
            "ts_code": ts_code,
            "name": name,
            "sector": sector,
            "price_multiple": mult,
            "price_cagr": cagr_price,
            "fcf_start_year": start_label,
            "fcf_start": fcf_start,
            "fcf_end": fcf_end,
            "fcf_growth_pct": fcf_growth_pct,
            "fcf_cagr": round(fcf_cagr * 100, 2) if fcf_cagr is not None else None,
            "early_avg_fcf": early_avg,
            "late_avg_fcf": late_avg,
            "n_years_data": len(all_years_fcf),
        })
    
    df_result = pd.DataFrame(results)
    
    # 4. 分析输出
    
    # 按 FCF 增长分类
    df_result["fcf_trend"] = "未知"
    df_result.loc[df_result["fcf_growth_pct"].notna() & (df_result["fcf_growth_pct"] >= 500), "fcf_trend"] = "🔥 爆炸增长(≥500%)"
    df_result.loc[df_result["fcf_growth_pct"].notna() & (df_result["fcf_growth_pct"] >= 100) & (df_result["fcf_growth_pct"] < 500), "fcf_trend"] = "📈 稳健增长(100-500%)"
    df_result.loc[df_result["fcf_growth_pct"].notna() & (df_result["fcf_growth_pct"] >= 0) & (df_result["fcf_growth_pct"] < 100), "fcf_trend"] = "📊 缓慢增长(0-100%)"
    df_result.loc[df_result["fcf_growth_pct"].notna() & (df_result["fcf_growth_pct"] < 0), "fcf_trend"] = "📉 FCF下滑"
    df_result.loc[df_result["fcf_growth_pct"].isna() & (df_result["early_avg_fcf"].notna()), "fcf_trend"] = "🔄 起点FCF为负（扭转型）"
    
    print("\n" + "=" * 80)
    print("  十倍股 FCF 现金流十年变化全景")
    print("=" * 80)
    
    # 按 price multiple 排序
    df_result = df_result.sort_values("price_multiple", ascending=False)
    
    print(f"\n{'排名':<5} {'代码':<12} {'名称':<8} {'股价':>7} {'股价年化':>8} {'FCF起点':>12} {'FCF终点':>12} {'FCF增长':>9} {'FCF年化':>8} {'趋势'}")
    print("-" * 105)
    
    for i, (_, r) in enumerate(df_result.iterrows(), 1):
        fcf_start_str = f"{r['fcf_start']/1e8:>8.1f}亿" if pd.notna(r['fcf_start']) else "N/A"
        fcf_end_str = f"{r['fcf_end']/1e8:>8.1f}亿" if pd.notna(r['fcf_end']) else "N/A"
        growth_str = f"{r['fcf_growth_pct']:>6.0f}%" if pd.notna(r['fcf_growth_pct']) else "负起点→"
        cagr_str = f"{r['fcf_cagr']:>5.1f}%" if pd.notna(r['fcf_cagr']) else "N/A"
        
        print(f"#{i:<4} {r['ts_code']:<12} {r['name']:<8} "
              f"{r['price_multiple']:>5.1f}x {r['price_cagr']:>6.1f}% "
              f"{fcf_start_str:>12} {fcf_end_str:>12} {growth_str:>9} {cagr_str:>8} "
              f"{r['fcf_trend']}")
    
    # 统计分析
    print("\n" + "=" * 80)
    print("  FCF 趋势分布统计")
    print("=" * 80)
    
    trend_counts = df_result["fcf_trend"].value_counts()
    for trend, count in trend_counts.items():
        subset = df_result[df_result["fcf_trend"] == trend]
        avg_mult = subset["price_multiple"].mean()
        print(f"  {trend}: {count} 只, 平均股价倍数 {avg_mult:.1f}x")
    
    # 扭转型（FCF从负转正）的细节
    turnaround = df_result[df_result["fcf_trend"] == "🔄 起点FCF为负（扭转型）"]
    if not turnaround.empty:
        print(f"\n  --- 扭转型细节（FCF从负转正）---")
        for _, r in turnaround.iterrows():
            early = r['early_avg_fcf'] / 1e8 if pd.notna(r['early_avg_fcf']) else 0
            late = r['late_avg_fcf'] / 1e8 if pd.notna(r['late_avg_fcf']) else 0
            print(f"  {r['ts_code']} {r['name']}: 前5年均 {early:.1f}亿 → 后5年均 {late:.1f}亿, 股价 {r['price_multiple']:.1f}x")
    
    # 相关性分析
    valid = df_result[df_result["fcf_growth_pct"].notna()]
    if len(valid) > 5:
        corr = valid["price_multiple"].corr(valid["fcf_growth_pct"])
        corr_cagr = valid["price_cagr"].corr(valid["fcf_cagr"])
        print(f"\n  📐 股价倍数 vs FCF增长率 相关系数: {corr:.3f}")
        print(f"  📐 股价年化 vs FCF年化 相关系数: {corr_cagr:.3f}")
    
    # FCF 是否跟进？
    print("\n" + "=" * 80)
    print("  核心发现：FCF 增长 vs 股价增长对比")
    print("=" * 80)
    
    # 分类：FCF增长跟得上股价的 vs 跟不上的
    for _, r in df_result.iterrows():
        if pd.notna(r['fcf_cagr']) and r['fcf_cagr'] is not None:
            r['fcf_px_ratio'] = r['fcf_cagr'] / r['price_cagr'] if r['price_cagr'] != 0 else None
    
    df_result['fcf_px_ratio'] = np.where(
        df_result['fcf_cagr'].notna() & (df_result['price_cagr'] != 0),
        df_result['fcf_cagr'] / df_result['price_cagr'],
        np.nan
    )
    
    print("\n  FCF增长/股价增长比率（>1说明业绩跑赢股价，<1说明估值扩张贡献更多）:")
    ratio_valid = df_result[df_result['fcf_px_ratio'].notna()]
    print(f"  比率中位数: {ratio_valid['fcf_px_ratio'].median():.2f}")
    print(f"  比率均值: {ratio_valid['fcf_px_ratio'].mean():.2f}")
    print(f"  FCF增速 > 股价增速（基本面驱动）: {(ratio_valid['fcf_px_ratio'] >= 1).sum()} 只")
    print(f"  FCF增速 < 股价增速（估值扩张驱动）: {(ratio_valid['fcf_px_ratio'] < 1).sum()} 只")
    
    # 保存
    output = PROJECT_DIR / "output/ten_bagger_fcf_analysis.csv"
    df_result.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"\n💾 结果已保存: {output}")
    
    return df_result


if __name__ == "__main__":
    df = main()
