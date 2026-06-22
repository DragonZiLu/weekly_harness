#!/usr/bin/env python3
"""
查找中证800成分股中，近一年（2025-06-18 ~ 2026-06-18）股价上涨超100%的股票。
"""
import sys
import time
from pathlib import Path
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import tushare_cfg
import tushare as ts

ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

# ──────────────────────────────────────────────────────────
# 1. 获取最新中证800成分股
# ──────────────────────────────────────────────────────────
idx_file = _PROJECT_ROOT / "data" / "index_weights" / "index_weight_000906.SH.csv"
df_idx = pd.read_csv(idx_file, dtype={"con_code": str, "trade_date": str})
df_idx["trade_date"] = pd.to_datetime(df_idx["trade_date"])
latest_date = df_idx["trade_date"].max()
latest_constituents = df_idx[df_idx["trade_date"] == latest_date]
codes = sorted(latest_constituents["con_code"].unique().tolist())
print(f"中证800最新成分日期: {latest_date.strftime('%Y-%m-%d')}")
print(f"成分股数量: {len(codes)}")

# ──────────────────────────────────────────────────────────
# 2. 拉取近一年价格数据（分批，避免限流）
# ──────────────────────────────────────────────────────────
start_date = "20250601"
end_date = "20260618"

results = []
batch_size = 1  # Tushare daily 不支持批量 ts_code
total = len(codes)

print(f"\n开始拉取价格数据...")
for i, code in enumerate(codes):
    try:
        df = pro.daily(
            ts_code=code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close"
        )
        time.sleep(0.15)  # 控制频率，避免限流
        if df is not None and not df.empty:
            df = df.sort_values("trade_date")
            results.append(df)
    except Exception as e:
        print(f"  ⚠️ {code} 拉取失败: {e}")
        time.sleep(0.5)

    if (i + 1) % 50 == 0:
        print(f"  进度: {i+1}/{total}")

print(f"  完成: {len(results)}/{total} 只标的有数据")

# ──────────────────────────────────────────────────────────
# 3. 计算近一年涨跌幅
# ──────────────────────────────────────────────────────────
if not results:
    print("没有拉取到任何数据！")
    sys.exit(1)

all_prices = pd.concat(results, ignore_index=True)
all_prices["trade_date"] = pd.to_datetime(all_prices["trade_date"])

# 取每只股票的 first/last 收盘价（在区间内的最早和最新交易日）
first_prices = all_prices.sort_values("trade_date").groupby("ts_code").first()["close"].reset_index()
first_prices.columns = ["ts_code", "first_close"]

last_prices = all_prices.sort_values("trade_date").groupby("ts_code").last()["close"].reset_index()
last_prices.columns = ["ts_code", "last_close"]

merged = first_prices.merge(last_prices, on="ts_code")
merged["pct_change"] = (merged["last_close"] / merged["first_close"] - 1) * 100

# ──────────────────────────────────────────────────────────
# 4. 筛选涨幅 > 100% 的标的
# ──────────────────────────────────────────────────────────
gainers = merged[merged["pct_change"] > 100].sort_values("pct_change", ascending=False)

# ──────────────────────────────────────────────────────────
# 5. 补充名称
# ──────────────────────────────────────────────────────────
# 使用 stock_basic 缓存或 API 获取名称
stock_basic_file = _PROJECT_ROOT / "data" / "stock_basic.csv"
name_map = {}
if stock_basic_file.exists():
    basic = pd.read_csv(stock_basic_file, dtype={"ts_code": str})
    name_map = dict(zip(basic["ts_code"], basic["name"]))

# 如果缓存不完整，从 API 补拉
missing = [c for c in gainers["ts_code"].tolist() if c not in name_map]
if missing:
    try:
        df_comp = pro.stock_basic(
            exchange="", list_status="L",
            fields="ts_code,name"
        )
        for _, row in df_comp.iterrows():
            name_map[row["ts_code"]] = row["name"]
    except Exception:
        pass

print(f"\n{'='*80}")
print(f"中证800成分股 — 近一年（2025-06-18 ~ 2026-06-18）涨幅超100%的标的")
print(f"{'='*80}")
print(f"共 {len(gainers)} 只\n")

for _, row in gainers.iterrows():
    name = name_map.get(row["ts_code"], row["ts_code"])
    print(f"  {row['ts_code']}  {name:<10s}  期初: {row['first_close']:>8.2f}  "
          f"期末: {row['last_close']:>8.2f}  涨幅: {row['pct_change']:>+7.1f}%")

print(f"\n---")
print(f"总计: {len(gainers)} 只标的涨幅超过100%")
print(f"中证800成分股总数: {len(codes)}")
print(f"占比: {len(gainers)/len(codes)*100:.1f}%")

# 简单统计
print(f"\n涨幅分布:")
bins = [100, 150, 200, 300, 500, float("inf")]
labels = ["100-150%", "150-200%", "200-300%", "300-500%", ">500%"]
gainers["range"] = pd.cut(gainers["pct_change"], bins=bins, labels=labels, right=False)
dist = gainers["range"].value_counts().sort_index()
for label, count in dist.items():
    print(f"  {label}: {count} 只")
