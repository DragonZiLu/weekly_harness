#!/usr/bin/env python3
"""
绘制三只十倍股的十年 FCF + 股价曲线
兆易创新(603986)、紫金矿业(601899)、天孚通信(300394)
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

STOCKS = {
    "603986.SH": {"name": "兆易创新", "color_fcf": "#E74C3C", "color_px": "#C0392B"},
    "601899.SH": {"name": "紫金矿业", "color_fcf": "#2ECC71", "color_px": "#27AE60"},
    "300394.SZ": {"name": "天孚通信", "color_fcf": "#3498DB", "color_px": "#2980B9"},
}


def load_annual_fcf():
    """加载所有年报 FCF"""
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
    """获取每年末的后复权价（用最后一个交易日）"""
    f = PROJECT_DIR / f"data/adj_close_cache/{ts_code}.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f, parse_dates=["trade_date"])
    df["year"] = df["trade_date"].dt.year
    # 取每年最后一个交易日的 adj_close
    annual = df.groupby("year")["adj_close"].last().reset_index()
    annual = annual[(annual["year"] >= 2015) & (annual["year"] <= 2025)]
    return annual


def main():
    df_cf = load_annual_fcf()
    
    # 准备画图
    fig, axes = plt.subplots(3, 1, figsize=(16, 18))
    fig.suptitle("十倍股：十年 FCF 现金流 vs 股价走势", fontsize=18, fontweight='bold', y=0.98)
    
    for idx, (ts_code, info) in enumerate(STOCKS.items()):
        ax1 = axes[idx]
        ax2 = ax1.twinx()
        
        # --- FCF 数据 ---
        stock_cf = df_cf[(df_cf["ts_code"] == ts_code) & (df_cf["year"] >= 2015) & (df_cf["year"] <= 2025)]
        stock_cf = stock_cf.sort_values("year")
        
        years_fcf = stock_cf["year"].tolist()
        fcf_vals = stock_cf["fcf"].tolist()
        
        # --- 股价数据 ---
        df_px = load_annual_price(ts_code)
        if df_px is not None:
            px_years = df_px["year"].tolist()
            px_vals = df_px["adj_close"].tolist()
        
        # 画 FCF 柱状图
        colors_fcf = ['#e74c3c' if v < 0 else '#2ecc71' for v in fcf_vals]
        bars = ax1.bar(years_fcf, [v/1e8 for v in fcf_vals], color=colors_fcf, 
                       alpha=0.85, width=0.6, label='FCF（亿元）')
        ax1.set_ylabel('FCF（亿元）', fontsize=12, color='#2c3e50')
        ax1.tick_params(axis='y', labelcolor='#2c3e50')
        ax1.axhline(y=0, color='#bdc3c7', linewidth=0.8, linestyle='-')
        
        # 在柱子上标注数值
        for bar, val in zip(bars, fcf_vals):
            y_pos = bar.get_height()
            if y_pos >= 0:
                ax1.text(bar.get_x() + bar.get_width()/2, y_pos + max(fcf_vals)/1e8*0.03,
                         f'{val/1e8:.1f}', ha='center', va='bottom', fontsize=8, color='#2c3e50')
            else:
                ax1.text(bar.get_x() + bar.get_width()/2, y_pos - max(abs(np.array(fcf_vals)))/1e8*0.03,
                         f'{val/1e8:.1f}', ha='center', va='top', fontsize=8, color='#e74c3c')
        
        # 画股价线
        if df_px is not None and len(px_vals) > 0:
            # 找到 FCF 和股价年份的交集
            common_years = sorted(set(px_years) & set(years_fcf))
            px_common = [px_vals[px_years.index(y)] for y in common_years]
            
            # 归一化：以 2016 年为基准 = 100
            if 2016 in common_years:
                base_idx = common_years.index(2016)
                px_base = px_common[base_idx]
                px_normalized = [v / px_base * 100 for v in px_common]
                ax2.plot(common_years, px_normalized, color='#e74c3c', linewidth=2.5, 
                        marker='o', markersize=6, markerfacecolor='white', 
                        markeredgewidth=2, markeredgecolor='#e74c3c',
                        label='股价（2016=100）', zorder=5)
                ax2.set_ylabel('股价指数（2016=100）', fontsize=12, color='#c0392b')
                ax2.tick_params(axis='y', labelcolor='#c0392b')
                
                # 标注最终值
                ax2.annotate(f'{px_normalized[-1]:.0f}',
                            xy=(common_years[-1], px_normalized[-1]),
                            fontsize=11, fontweight='bold', color='#c0392b',
                            xytext=(10, 0), textcoords='offset points',
                            ha='left', va='center')
        
        # 设置
        ax1.set_title(f'{ts_code}  {info["name"]}', fontsize=15, fontweight='bold', 
                      color='#2c3e50', pad=10)
        ax1.set_xticks(range(2015, 2026))
        ax1.set_xlim(2014.5, 2025.5)
        ax1.grid(axis='y', alpha=0.3, linestyle='--')
        
        # 标注 FCF 起点和终点
        if len(fcf_vals) >= 2:
            start_val = fcf_vals[0] / 1e8
            end_val = fcf_vals[-1] / 1e8
            growth = (fcf_vals[-1] / fcf_vals[0] - 1) * 100 if fcf_vals[0] != 0 else float('inf')
            note = (f"FCF: {start_val:.1f}亿 → {end_val:.1f}亿"
                    f"{'  (+' + str(int(growth)) + '%)' if growth != float('inf') else '（扭转型）'}")
            ax1.text(0.02, 0.95, note, transform=ax1.transAxes, fontsize=10,
                    color='#2c3e50', fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='#ecf0f1', alpha=0.85))
    
    # 图例
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='#2ecc71', alpha=0.85, label='FCF > 0'),
        Patch(facecolor='#e74c3c', alpha=0.85, label='FCF < 0'),
        plt.Line2D([0], [0], color='#e74c3c', linewidth=2.5, marker='o', 
                   markerfacecolor='white', markeredgewidth=2, markeredgecolor='#e74c3c',
                   label='股价指数（2016=100）'),
    ]
    fig.legend(handles=legend_elements, loc='lower center', ncol=3, frameon=True, 
              fontsize=11, bbox_to_anchor=(0.5, 0.01))
    
    plt.tight_layout(rect=[0, 0.06, 1, 0.95])
    
    output_path = PROJECT_DIR / "output/ten_bagger_fcf_curves.png"
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"✅ 图表已保存: {output_path}")
    
    # 也输出一个数据表
    print("\n" + "=" * 80)
    for ts_code, info in STOCKS.items():
        stock_cf = df_cf[(df_cf["ts_code"] == ts_code) & (df_cf["year"] >= 2015) & (df_cf["year"] <= 2025)]
        stock_cf = stock_cf.sort_values("year")
        df_px = load_annual_price(ts_code)
        
        print(f"\n--- {ts_code} {info['name']} ---")
        print(f"{'年份':<8} {'FCF(亿)':<12} {'后复权价':<12} {'股价指数':<10}")
        print("-" * 45)
        
        for _, row in stock_cf.iterrows():
            yr = row["year"]
            fcf = row["fcf"] / 1e8
            px_row = df_px[df_px["year"] == yr] if df_px is not None else pd.DataFrame()
            px = px_row["adj_close"].values[0] if not px_row.empty else None
            px_idx = px / df_px[df_px["year"] == 2016]["adj_close"].values[0] * 100 if (df_px is not None and not px_row.empty and 2016 in df_px["year"].values) else None
            print(f"{yr:<8} {fcf:>+10.1f}  {px:>10.2f}  {px_idx:>8.0f}" if px else f"{yr:<8} {fcf:>+10.1f}")


if __name__ == "__main__":
    main()
