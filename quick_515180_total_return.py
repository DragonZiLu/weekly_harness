"""
quick_515180_total_return.py — 515180 全收益水平（从2019年7月起）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import tushare as ts

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import tushare_cfg

ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

# ── 拉取 515180 复权净值 ──
start = "20190701"
end = "20260701"
df = pro.fund_nav(ts_code="515180.SH", start_date=start, end_date=end)
df = df.drop_duplicates(subset="nav_date", keep="last").sort_values("nav_date").reset_index(drop=True)
df["adj_nav"] = pd.to_numeric(df["adj_nav"], errors="coerce")
df["dt"] = pd.to_datetime(df["nav_date"].astype(str))
df = df.dropna(subset=["adj_nav"])
df = df.sort_values("dt").reset_index(drop=True)

if df.empty:
    print("未获取到数据，请检查 Tushare token 或网络")
    sys.exit(1)

# ── 关键点计算 ──
start_nav = df.iloc[0]["adj_nav"]
start_dt = df.iloc[0]["dt"]
end_nav = df.iloc[-1]["adj_nav"]
end_dt = df.iloc[-1]["dt"]
years = (end_dt - start_dt).days / 365.25

tr = (end_nav / start_nav - 1) * 100
cagr = (end_nav / start_nav) ** (1 / years) - 1

# 最大回撤
peak = df["adj_nav"].cummax()
dd = (df["adj_nav"] - peak) / peak
max_dd = float(dd.min() * 100)
max_dd_dt = df.iloc[int(dd.idxmin())]["dt"]

# 夏普（无风险利率假设 2.5%）
daily_ret = df["adj_nav"].pct_change().dropna()
rf_daily = 0.025 / 252
excess = daily_ret - rf_daily
if excess.std() > 0:
    sharpe = float(excess.mean() / excess.std() * np.sqrt(252))
else:
    sharpe = 0.0

print("=" * 60)
print("515180.SH  中证800红利ETF — 全收益水平")
print("=" * 60)
print(f"  起始日期 : {start_dt.date()}")
print(f"  结束日期 : {end_dt.date()}")
print(f"  时间跨度 : {years:.1f} 年")
print(f"  起始复权净值 : {start_nav:.4f}")
print(f"  最新复权净值 : {end_nav:.4f}")
print(f"  总收益   : {tr:+.2f}%")
print(f"  年化收益 : {cagr*100:.2f}%")
print(f"  最大回撤 : {max_dd:.2f}% (谷: {max_dd_dt.date()})")
print(f"  夏普比率 : {sharpe:.3f}")
print(f"  交易日数 : {len(df)} 天")
print("=" * 60)

# ── 分年收益 ──
print("\n分年收益（全收益口径）:")
print("-" * 40)
for y in range(start_dt.year, end_dt.year + 1):
    year_df = df[(df["dt"] >= f"{y}-01-01") & (df["dt"] <= f"{y}-12-31")]
    if len(year_df) < 2:
        continue
    y_start = year_df.iloc[0]["adj_nav"]
    y_end = year_df.iloc[-1]["adj_nav"]
    y_ret = (y_end / y_start - 1) * 100
    print(f"  {y} : {y_ret:+.2f}%")

# ── 滚动收益百分位 ──
print(f"\n滚动收益分布（买入后持有至今，共 {len(df)} 个入场点）:")
print("-" * 40)
for label, months in [("1年", 12), ("3年", 36), ("5年", 60)]:
    rets = []
    for i in range(len(df)):
        target_dt = df.iloc[i]["dt"] + pd.DateOffset(months=months)
        mask = df["dt"] >= target_dt
        if mask.any():
            sell_nav = df[mask].iloc[0]["adj_nav"]
            buy_nav = df.iloc[i]["adj_nav"]
            rets.append((sell_nav / buy_nav - 1) * 100)
    if rets:
        arr = np.array(rets)
        print(f"  滚动{label}: 平均 {arr.mean():+.1f}%  "
              f"最优 {arr.max():+.1f}%  最差 {arr.min():+.1f}%  "
              f"胜率 {(arr>0).mean()*100:.0f}%  "
              f"样本 {len(arr)}")
