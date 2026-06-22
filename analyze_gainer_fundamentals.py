#!/usr/bin/env python3
"""
分析中证800成分股中近一年涨超100%的119只标的——上涨前的基本面画像。
对比组：未翻倍的中证800成分股。
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

DATA = _PROJECT_ROOT / "data" / "fcf_financials"

# ────────────────────────────────────────
# 1. 加载之前脚本输出的涨幅数据 + 中证800全成分
# ────────────────────────────────────────
print("=" * 80)
print("加载数据...")

# 涨幅计算结果（复用之前脚本输出）
idx_file = _PROJECT_ROOT / "data" / "index_weights" / "index_weight_000906.SH.csv"
df_idx = pd.read_csv(idx_file, dtype={"con_code": str, "trade_date": str})
df_idx["trade_date"] = pd.to_datetime(df_idx["trade_date"])
latest = df_idx["trade_date"].max()
all_codes = sorted(df_idx[df_idx["trade_date"] == latest]["con_code"].unique().tolist())

# stock_basic 获取行业+名称
basic = pd.read_csv(_PROJECT_ROOT / "data" / "stock_basic.csv", dtype={"ts_code": str})
name_map = dict(zip(basic["ts_code"], basic["name"]))
industry_map = dict(zip(basic["ts_code"], basic["industry"]))

# 加载各类财务数据
income_24 = pd.read_csv(DATA / "income_2024_annual.csv", dtype={"ts_code": str})
income_23 = pd.read_csv(DATA / "income_2023_annual.csv", dtype={"ts_code": str})
income_q1 = pd.read_csv(DATA / "income_2025Q1.csv", dtype={"ts_code": str})
cash_q1 = pd.read_csv(DATA / "cashflow_2025Q1.csv", dtype={"ts_code": str})
balance_q1 = pd.read_csv(DATA / "balance_2025Q1.csv", dtype={"ts_code": str})

# 取最新公告行
def latest_per_code(df):
    df = df.copy()
    if "ann_date" in df.columns:
        df["ann_date"] = df["ann_date"].astype(str)
        df = df.sort_values("ann_date").groupby("ts_code").last().reset_index()
    return df

income_24 = latest_per_code(income_24)
income_23 = latest_per_code(income_23)
income_q1 = latest_per_code(income_q1)
cash_q1 = latest_per_code(cash_q1)
balance_q1 = latest_per_code(balance_q1)

# 合并成 profile
profile_cols_24 = ["ts_code", "total_revenue", "operate_profit", "total_profit", "n_income_attr_p", "basic_eps"]
profile_cols_23 = ["ts_code", "total_revenue", "n_income_attr_p"]
profile_cols_q1 = ["ts_code", "total_revenue", "operate_profit", "n_income_attr_p"]
profile_cols_cash = ["ts_code", "free_cashflow", "n_cashflow_act"]
profile_cols_bs = ["ts_code", "total_assets", "total_liab", "total_hldr_eqy_exc_min_int",
                    "total_hldr_eqy_inc_min_int", "total_share", "fix_assets"]

base = income_24[profile_cols_24].rename(columns={
    "total_revenue": "rev_2024", "operate_profit": "op_2024",
    "total_profit": "tp_2024", "n_income_attr_p": "ni_2024", "basic_eps": "eps_2024"
})
base = base.merge(
    income_23[profile_cols_23].rename(columns={"total_revenue": "rev_2023", "n_income_attr_p": "ni_2023"}),
    on="ts_code", how="left")
base = base.merge(
    income_q1[profile_cols_q1].rename(columns={
        "total_revenue": "rev_q1", "operate_profit": "op_q1", "n_income_attr_p": "ni_q1"}),
    on="ts_code", how="left")
base = base.merge(
    cash_q1[profile_cols_cash].rename(columns={"free_cashflow": "fcf_q1", "n_cashflow_act": "ocf_q1"}),
    on="ts_code", how="left")
base = base.merge(
    balance_q1[profile_cols_bs].rename(columns={
        "total_assets": "assets", "total_liab": "liab",
        "total_hldr_eqy_exc_min_int": "equity_excl_min",
        "total_hldr_eqy_inc_min_int": "equity_incl_min",
        "total_share": "shares", "fix_assets": "fix_assets"
    }), on="ts_code", how="left")

# 过滤：仅保留中证800最新成分
base_in = base[base["ts_code"].isin(all_codes)].copy()

# ────────────────────────────────────────
# 2. 计算衍生指标
# ────────────────────────────────────────
print("计算基本面指标...")

# 增长率
base_in["rev_growth"] = (base_in["rev_2024"] / base_in["rev_2023"] - 1) * 100
base_in["ni_growth"] = (base_in["ni_2024"] / base_in["ni_2023"] - 1) * 100

# 利润率
base_in["margin_2024"] = base_in["ni_2024"] / base_in["rev_2024"] * 100
base_in["margin_q1"] = base_in["ni_q1"] / base_in["rev_q1"] * 100

# ROE (TTM估算: 2024 NI / 2025Q1 equity)
base_in["roe"] = base_in["ni_2024"] / base_in["equity_excl_min"] * 100

# 资产负债率
base_in["debt_ratio"] = base_in["liab"] / base_in["assets"] * 100

# 固定资产占比（轻资产 vs 重资产）
base_in["fix_ratio"] = base_in["fix_assets"] / base_in["assets"] * 100

# FCF/营收
base_in["fcf_margin"] = base_in["fcf_q1"] / base_in["rev_q1"] * 100

# 是否有利润（二值）
base_in["has_profit_2024"] = (base_in["ni_2024"] > 0).astype(int)
base_in["has_profit_q1"] = (base_in["ni_q1"] > 0).astype(int)

# 行业分类
base_in["industry"] = base_in["ts_code"].map(industry_map)
base_in["name"] = base_in["ts_code"].map(name_map)

# ────────────────────────────────────────
# 3. 关联涨幅数据，分为"翻倍组"和"未翻倍组"
# ────────────────────────────────────────
print("关联涨幅数据...")
# 重新计算涨幅
import time
from config.settings import tushare_cfg
import tushare as ts
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

gainers_codes = [
    "300308.SZ","002384.SZ","301200.SZ","000657.SZ","301308.SZ","600487.SH","600183.SH","300502.SZ",
    "688525.SH","002281.SZ","300604.SZ","002008.SZ","688347.SH","603986.SH","300285.SZ","688702.SH",
    "002916.SZ","300390.SZ","300136.SZ","300757.SZ","300408.SZ","002463.SZ","600176.SH","002353.SZ",
    "002080.SZ","688183.SH","002436.SZ","300476.SZ","600549.SH","002938.SZ","001389.SZ","600522.SH",
    "300433.SZ","688608.SH","002913.SZ","300661.SZ","688981.SH","601138.SH","300394.SZ","601600.SH",
    "000977.SZ","600584.SH","002384.SZ","002049.SZ","002415.SZ","000063.SZ","600760.SH","600765.SH",
    "600498.SH","002601.SZ","600588.SH","600019.SH","300576.SZ","688111.SH","300751.SZ","600536.SH",
    "688208.SH","601600.SH","002074.SZ","600516.SH","002340.SZ","688005.SH","300458.SZ","603160.SH",
    "688036.SH","300750.SZ","688169.SH","002371.SZ","300115.SZ","300124.SZ","600703.SH","002459.SZ",
    "300014.SZ","601615.SH","688303.SH","000725.SZ","002129.SZ","600481.SH","600570.SH","002230.SZ",
    "600089.SH","601991.SH","688234.SH","002203.SZ","688256.SH","300373.SZ","600362.SH","688295.SH",
    "601179.SH","300274.SZ","688002.SH","000630.SZ","300450.SZ","002202.SZ","301611.SZ","688041.SH",
    "002475.SZ","002460.SZ","600879.SH","002185.SZ","603920.SH","600111.SH","002126.SZ","002466.SZ",
    "002056.SZ","000408.SZ","002600.SZ","300346.SZ","002738.SZ","600875.SH","000703.SZ","603650.SH",
    "002273.SZ","688012.SH","300442.SZ","688027.SH","002756.SZ","300316.SZ"
]

# 去重
gainers_codes = list(dict.fromkeys(gainers_codes))

base_in["is_gainer"] = base_in["ts_code"].isin(gainers_codes).astype(int)

gainer = base_in[base_in["is_gainer"] == 1]
non_gainer = base_in[base_in["is_gainer"] == 0]

print(f"翻倍组: {len(gainer)} 只 (有财务数据: {gainer['ni_2024'].notna().sum()})")
print(f"未翻倍组: {len(non_gainer)} 只 (有财务数据: {non_gainer['ni_2024'].notna().sum()})")

# ────────────────────────────────────────
# 4. 逐项对比
# ────────────────────────────────────────
def compare_metric(name, g, ng, suffix="", win_condition="higher"):
    gv = g[name].dropna()
    ngv = ng[name].dropna()
    med_g = gv.median()
    med_ng = ngv.median()
    mean_g = gv.mean()
    mean_ng = ngv.mean()
    diff = med_g - med_ng
    arrow = "▲" if diff > 0 else "▼"
    return f"{name}{suffix:<12s} {med_g:>10.1f}  | {med_ng:>10.1f}  | {diff:>+8.1f}  {arrow}" if win_condition == "higher" else \
           f"{name}{suffix:<12s} {med_g:>9.1f}  | {med_ng:>9.1f}  | {diff:>7.1f}  {arrow}", med_g, med_ng

print("\n" + "=" * 80)
print("基本面指标对比：翻倍组 vs 未翻倍组（中位数）")
print("=" * 80)
print(f"{'指标':<25s} {'翻倍组(119)':>10s}  | {'未翻倍(681)':>10s}  | {'差值':>10s}")
print("-" * 80)

metrics = [
    ("rev_growth", "营收增速(%)", "higher"),
    ("ni_growth", "利润增速(%)", "higher"),
    ("margin_2024", "净利率(%)", "higher"),
    ("roe", "ROE(%)", "higher"),
    ("eps_2024", "EPS(元/股)", "higher"),
    ("debt_ratio", "资产负债率(%)", "lower"),
    ("fix_ratio", "固定资产占比(%)", "lower"),
    ("fcf_margin", "FCF/营收(%)", "higher"),
    ("rev_2024", "营收(亿元)", "higher"),
]

for col, label, direction in metrics:
    if col in ["rev_2024"]:
        gv = gainer[col].dropna() / 1e8
        ngv = non_gainer[col].dropna() / 1e8
    else:
        gv = gainer[col].dropna()
        ngv = non_gainer[col].dropna()
    
    med_g = gv.median()
    med_ng = ngv.median()
    mean_g = gv.mean()
    mean_ng = ngv.mean()
    diff = med_g - med_ng
    arrow = "▲" if diff > 0 else "▼" if diff < 0 else " "
    
    print(f"{label:<25s} {med_g:>10.1f}  | {med_ng:>10.1f}  | {diff:>+9.1f}  {arrow}")

# ────────────────────────────────────────
# 5. 行业分布对比
# ────────────────────────────────────────
print("\n" + "=" * 80)
print("行业分布对比（Top 10 行业）")
print("=" * 80)

g_ind = gainer["industry"].value_counts()
ng_ind = non_gainer["industry"].value_counts()
print(f"\n{'行业':<20s} {'翻倍组':>8s} {'占比%':>8s} | {'未翻倍组':>8s} {'占比%':>8s} | {'超配':>8s}")
print("-" * 70)

all_ind = pd.concat([g_ind, ng_ind], axis=1).fillna(0)
all_ind.columns = ["gainer", "non_gainer"]
all_ind["g_pct"] = all_ind["gainer"] / len(gainer) * 100
all_ind["ng_pct"] = all_ind["non_gainer"] / len(non_gainer) * 100
all_ind["overweight"] = all_ind["g_pct"] - all_ind["ng_pct"]
all_ind = all_ind.sort_values("overweight", ascending=False)

for ind, row in all_ind.head(15).iterrows():
    g_ct, ng_ct = int(row["gainer"]), int(row["non_gainer"])
    g_pct, ng_pct = row["g_pct"], row["ng_pct"]
    ow = row["overweight"]
    bar = "█" * min(int(abs(ow)), 20)
    print(f"{ind:<20s} {g_ct:>6d}  {g_pct:>6.1f}% | {ng_ct:>6d}  {ng_pct:>6.1f}% | {ow:>+7.1f}% {bar}")

# ────────────────────────────────────────
# 6. 盈利 vs 亏损
# ────────────────────────────────────────
print("\n" + "=" * 80)
print("盈利状况对比")
print("=" * 80)

g_profit = gainer["has_profit_2024"].sum()
g_loss = len(gainer) - g_profit
ng_profit = non_gainer["has_profit_2024"].sum()
ng_loss = len(non_gainer) - ng_profit

print(f"{'':<20s} {'翻倍组':>10s} {'未翻倍组':>10s}")
print(f"{'盈利':<20s} {g_profit:>8d} ({g_profit/len(gainer)*100:>5.1f}%)    {ng_profit:>8d} ({ng_profit/len(non_gainer)*100:>5.1f}%)")
print(f"{'亏损':<20s} {g_loss:>8d} ({g_loss/len(gainer)*100:>5.1f}%)    {ng_loss:>8d} ({ng_loss/len(non_gainer)*100:>5.1f}%)")

# ────────────────────────────────────────
# 7. 翻倍组内部画像：Top 收益增速
# ────────────────────────────────────────
print("\n" + "=" * 80)
print("翻倍组中利润增速最高的20只（上涨前2024年报vs2023）")
print("=" * 80)
top_growth = gainer.dropna(subset=["ni_growth"]).nlargest(20, "ni_growth")
print(f"{'代码':<12s} {'名称':<10s} {'行业':<12s} {'营收增速':>10s} {'利润增速':>10s} {'净利率%':>8s} {'ROE%':>8s}")
print("-" * 80)
for _, r in top_growth.iterrows():
    name = r.get("name", r["ts_code"]) or r["ts_code"]
    ind = r.get("industry", "") or ""
    print(f"{r['ts_code']:<12s} {str(name):<10s} {str(ind):<12s} "
          f"{r['rev_growth']:>+9.1f}% {r['ni_growth']:>+9.1f}% "
          f"{r['margin_2024']:>7.1f}% {r['roe']:>7.1f}%")

# ────────────────────────────────────────
# 8. 翻倍组中亏损公司的画像
# ────────────────────────────────────────
print("\n" + "=" * 80)
print("翻倍组中2024年亏损的公司")
print("=" * 80)
g_loss_stocks = gainer[gainer["has_profit_2024"] == 0]
if len(g_loss_stocks) > 0:
    print(f"{'代码':<12s} {'名称':<10s} {'行业':<12s} {'营收(亿)':>10s} {'亏损(亿)':>10s} {'ROE%':>8s}")
    print("-" * 70)
    for _, r in g_loss_stocks.iterrows():
        name = r.get("name", r["ts_code"]) or r["ts_code"]
        ind = r.get("industry", "") or ""
        rev = r["rev_2024"] / 1e8 if pd.notna(r["rev_2024"]) else float("nan")
        ni = r["ni_2024"] / 1e8 if pd.notna(r["ni_2024"]) else float("nan")
        print(f"{r['ts_code']:<12s} {str(name):<10s} {str(ind):<12s} "
              f"{rev:>9.1f} {ni:>9.1f} {r['roe']:>7.1f}%")
else:
    print("  无（翻倍组全部盈利）")

print("\n" + "=" * 80)
print("核心发现")
print("=" * 80)
