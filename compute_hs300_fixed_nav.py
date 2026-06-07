#!/usr/bin/env python3
"""
compute_hs300_fixed_nav.py — 计算修正后 HS300 FCF 篮子的 NAV

使用后复权价格 (close × adj_factor)，与 ZZ800 FCF 相同方法。
"""

import json, time, os
import pandas as pd
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path('/Users/luzilong/Work/weekly_harness/.env'))
import tushare as ts
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

ROOT = Path('/Users/luzilong/Work/weekly_harness')

# Load fixed baskets
with open(ROOT / 'output/hs300_fcf_fixed/all_baskets_2015_2026.json') as f:
    baskets = json.load(f)

# Load old baskets for comparison
with open(ROOT / 'output/hs300_fcf/all_baskets_2015_2026.json') as f:
    old_baskets = json.load(f)

# Load old NAV
old_nav = pd.read_csv(ROOT / 'output/hs300_fcf/backtest_nav_tr.csv')

# Only process periods with >= 10 stocks
valid_dates = [d for d in sorted(baskets.keys()) if len(baskets[d]) >= 10]
print(f"Valid periods: {len(valid_dates)} (out of {len(baskets)})")

def get_adj_close(ts_code, start_date, end_date):
    """获取后复权价格 (close × adj_factor)"""
    start_d = start_date.replace("-", "")
    end_d = end_date.replace("-", "")
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d,
                       fields="ts_code,trade_date,close")
        if df is None or df.empty:
            return None
        time.sleep(0.03)
        df_adj = pro.adj_factor(ts_code=ts_code, start_date=start_d, end_date=end_d)
        time.sleep(0.03)
        if df_adj is None or df_adj.empty:
            return None
        df["trade_date"] = df["trade_date"].astype(str)
        df_adj["trade_date"] = df_adj["trade_date"].astype(str)
        merged = df.merge(df_adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
        merged = merged.sort_values("trade_date")
        merged["adj_factor"] = merged["adj_factor"].ffill().bfill()
        merged["adj_close"] = merged["close"].astype(float) * merged["adj_factor"].astype(float)
        start_price = float(merged.iloc[0]["adj_close"])
        end_price = float(merged.iloc[-1]["adj_close"])
        if start_price <= 0:
            return None
        return (start_price, end_price)
    except Exception as e:
        time.sleep(0.15)
        return None

# Compute rebalance chain dates
# Use old NAV to get the rb_date → next_rb mapping
rb_chain = []
for i in range(len(old_nav)):
    rb_chain.append({
        "rb_date": old_nav.iloc[i]["rb_date"],
        "next_rb": old_nav.iloc[i]["next_rb"]
    })

# Compute NAV for fixed baskets
nav_records = []
cumulative_nav = 1.0

for i, chain in enumerate(rb_chain):
    rb = chain["rb_date"]
    next_rb = chain["next_rb"]
    
    if rb not in baskets or len(baskets[rb]) < 10:
        continue
    
    basket = baskets[rb]
    weights = {s["ts_code"]: s["weight"] for s in basket}
    
    weighted_ret = 0.0
    n_valid = 0
    
    t0 = time.time()
    for code, w in weights.items():
        result = get_adj_close(code, rb, next_rb)
        if result is not None:
            start_p, end_p = result
            ret = end_p / start_p - 1
            weighted_ret += w * ret
            n_valid += 1
        else:
            # Stock data unavailable, assume 0 return for its weight portion
            weighted_ret += w * 0  # neutral assumption
    
    period_ret = weighted_ret
    cumulative_nav *= (1 + period_ret)
    elapsed = time.time() - t0
    
    nav_records.append({
        "rb_date": rb,
        "next_rb": next_rb,
        "ret": round(period_ret, 6),
        "nav": round(cumulative_nav, 6),
        "n_valid": n_valid
    })
    
    print(f"[{i+1}/{len(rb_chain)}] {rb} → {next_rb}: ret={period_ret*100:.2f}%, NAV={cumulative_nav:.4f}, n_valid={n_valid}/{len(weights)}, elapsed={elapsed:.1f}s")

# Save
df_nav = pd.DataFrame(nav_records)
out_path = ROOT / 'output/hs300_fcf_fixed' / 'backtest_nav_tr.csv'
df_nav.to_csv(out_path, index=False)

n_periods = len(nav_records)
n_years = n_periods / 4
final_nav = cumulative_nav
total_ret = final_nav - 1
annual_ret = (final_nav) ** (1/n_years) - 1

print(f"\n✅ Saved {len(nav_records)} periods to {out_path}")
print(f"修正后终值 NAV: {final_nav:.4f}")
print(f"总收益: {total_ret*100:.2f}%")
print(f"年化: {annual_ret*100:.2f}%")

# Compare with old
old_final = old_nav.iloc[-1]["nav"]
print(f"\n旧版终值 NAV: {old_final:.4f}")
print(f"旧版总收益: {(old_final-1)*100:.2f}%")