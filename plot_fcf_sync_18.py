#!/usr/bin/env python3
"""
绘制 18 只 FCF-股价同步增长标的的十年双轴曲线
"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from pathlib import Path
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'Heiti SC', 'PingFang SC', 'SimHei']
matplotlib.rcParams['axes.unicode_minus'] = False

PROJECT_DIR = Path(__file__).parent
FCF_DIR = PROJECT_DIR / "data/fcf_financials"

STOCKS = [
    ("603986.SH", "兆易创新", 82.4, "1.00"),
    ("300394.SZ", "天孚通信", 53.5, "1.01"),
    ("600183.SH", "生益科技", 29.3, "0.71"),
    ("603893.SH", "瑞芯微",  12.8, "1.01"),
    ("601899.SH", "紫金矿业", 11.5, "0.76"),
    ("300718.SZ", "长盛轴承", 9.9,  "1.19"),
    ("300408.SZ", "三环集团", 9.0,  "0.79"),
    ("002821.SZ", "凯莱英",   8.0,  "1.06"),
    ("300124.SZ", "汇川技术", 6.7,  "1.49"),
    ("300628.SZ", "亿联网络", 6.5,  "1.06"),
    ("002008.SZ", "大族激光", 6.3,  "0.85"),
    ("300285.SZ", "国瓷材料", 6.2,  "1.28"),
    ("603444.SH", "吉比特",   5.9,  "1.19"),
    ("600522.SH", "中天科技", 5.8,  "0.71"),
    ("300136.SZ", "信维通信", 5.8,  "1.31"),
    ("000426.SZ", "兴业银锡", 5.6,  "1.25"),
    ("603993.SH", "洛阳钼业", 5.0,  "1.17"),
    ("002056.SZ", "横店东磁", 5.0,  "1.27"),
]

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

def load_annual_price(ts_code):
    f = PROJECT_DIR / f"data/adj_close_cache/{ts_code}.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f, parse_dates=["trade_date"])
    df["year"] = df["trade_date"].dt.year
    annual = df.groupby("year")["adj_close"].last().reset_index()
    annual = annual[(annual["year"] >= 2015) & (annual["year"] <= 2025)]
    return annual


def main():
    df_cf = load_annual_fcf()
    
    n_cols = 3
    n_rows = 6
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(24, 32))
    fig.suptitle("FCF 现金流 vs 股价 — 18只基本面同步驱动标的（2015-2025）", 
                 fontsize=20, fontweight='bold', y=0.985)
    
    for idx, (ts_code, name, mult, ratio) in enumerate(STOCKS):
        row, col = divmod(idx, n_cols)
        ax1 = axes[row][col]
        ax2 = ax1.twinx()
        
        # --- FCF 数据 ---
        stock_cf = df_cf[(df_cf["ts_code"] == ts_code) & (df_cf["year"] >= 2015) & (df_cf["year"] <= 2025)]
        stock_cf = stock_cf.sort_values("year")
        years_fcf = stock_cf["year"].tolist()
        fcf_vals = stock_cf["fcf"].tolist()
        
        # --- 股价 ---
        df_px = load_annual_price(ts_code)
        px_years, px_vals = [], []
        if df_px is not None and len(df_px) > 0:
            px_years = df_px["year"].tolist()
            px_vals = df_px["adj_close"].tolist()
        
        # FCF 柱状图
        fcf_billion = [v / 1e8 for v in fcf_vals]
        colors_bar = ['#e74c3c' if v < 0 else '#27ae60' for v in fcf_billion]
        ax1.bar(years_fcf, fcf_billion, color=colors_bar, alpha=0.85, width=0.6, zorder=2)
        ax1.axhline(y=0, color='#7f8c8d', linewidth=0.6, linestyle='-')
        ax1.set_ylabel('FCF（亿）', fontsize=8, color='#2c3e50')
        ax1.tick_params(axis='y', labelsize=7, colors='#2c3e50')
        
        # FCF 数值标注（只标首尾和最大）
        if fcf_billion:
            abs_max = max(abs(np.array(fcf_billion)))
            for i, (yr, val) in enumerate(zip(years_fcf, fcf_billion)):
                if i == 0 or i == len(fcf_billion)-1 or abs(val) > abs_max * 0.8:
                    y_off = abs_max * 0.06
                    ax1.text(yr, val + y_off if val >= 0 else val - y_off,
                            f'{val:.1f}', ha='center', fontsize=5.5,
                            color='#e74c3c' if val < 0 else '#27ae60', fontweight='bold')
        
        # 股价线
        if px_vals:
            common = sorted(set(px_years) & set(years_fcf))
            px_dict = dict(zip(px_years, px_vals))
            px_c = [px_dict[y] for y in common]
            
            base_year = 2016
            if base_year in px_dict and px_dict[base_year] > 0:
                base = px_dict[base_year]
                px_norm = [v / base * 100 for v in px_c]
                ax2.plot(common, px_norm, color='#e74c3c', linewidth=2, marker='o',
                        markersize=3.5, markerfacecolor='white', markeredgewidth=1.5,
                        markeredgecolor='#e74c3c', zorder=5)
                ax2.set_ylabel('股价(2016=100)', fontsize=7, color='#c0392b')
                ax2.tick_params(axis='y', labelsize=6, colors='#c0392b')
                
                # 终点标注
                ax2.annotate(f'{px_norm[-1]:.0f}',
                            xy=(common[-1], px_norm[-1]), fontsize=7.5,
                            fontweight='bold', color='#c0392b',
                            xytext=(8, 0), textcoords='offset points',
                            ha='left', va='center')
        
        # 子图标题
        ax1.set_title(f'{ts_code}  {name}  |  {mult:.1f}x  ratio={ratio}', 
                      fontsize=9.5, fontweight='bold', color='#2c3e50')
        ax1.set_xticks(range(2015, 2026, 2))
        ax1.set_xlim(2014.3, 2025.7)
        ax1.tick_params(axis='x', labelsize=6.5)
        ax1.grid(axis='y', alpha=0.2, linestyle='--')
        
        # 标注 FCF 变化
        if len(fcf_vals) >= 2:
            s, e = fcf_vals[0]/1e8, fcf_vals[-1]/1e8
            ax1.text(0.5, 0.92, f'FCF: {s:.1f}→{e:.1f}亿', transform=ax1.transAxes,
                    fontsize=6.5, color='#2c3e50', ha='center',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='#ecf0f1', alpha=0.7))
    
    # 图例
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='#27ae60', alpha=0.85, label='FCF > 0'),
        Patch(facecolor='#e74c3c', alpha=0.85, label='FCF < 0'),
        plt.Line2D([0], [0], color='#e74c3c', linewidth=2, marker='o',
                   markerfacecolor='white', markeredgewidth=1.5, markeredgecolor='#e74c3c',
                   label='股价指数（2016=100, 右轴）'),
    ]
    fig.legend(handles=legend_elements, loc='lower center', ncol=3, 
              frameon=True, fontsize=11, bbox_to_anchor=(0.5, 0.006))
    
    plt.tight_layout(rect=[0, 0.04, 1, 0.97])
    
    output = PROJECT_DIR / "output/fcf_sync_18_curves.png"
    plt.savefig(output, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"✅ 图表已保存: {output}")

if __name__ == "__main__":
    main()
