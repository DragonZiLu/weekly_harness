#!/usr/bin/env python3
"""
compute_fixed_nav.py — 用修正后篮子计算回测 NAV
================================================

方法：对每个调仓期，批量拉取所有持仓股票在该期间起止日的
后复权价格（close × adj_factor），计算加权收益率，累加得到 NAV。

优化：
  - 使用 tushare adj_factor 批量查询替代逐只 daily 查询
  - 利用 daily_basic_cache 缓存减少 API 调用
"""

import sys, json, time, os
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
import tushare as ts
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

FIXED_BASKETS_PATH = PROJECT_ROOT / "output" / "zz800_fcf_fixed" / "all_baskets_2015_2026.json"
OLD_NAV_PATH = PROJECT_ROOT / "output" / "zz800_fcf" / "backtest_nav_tr.csv"
OFFICIAL_PATH = PROJECT_ROOT / "data" / "932368_daily.csv"
OUTPUT_DIR = PROJECT_ROOT / "output" / "zz800_fcf_fixed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_adj_close(ts_code, start_date, end_date):
    """获取 ts_code 在 start_date~end_date 期间的起始和结束后复权价格。
    
    后复权价格 = close × adj_factor
    需要分别调用 pro.daily() 和 pro.adj_factor() 两个API。
    返回 (start_adj_close, end_adj_close) 或 None
    """
    start_d = start_date.replace("-", "")
    end_d = end_date.replace("-", "")
    
    try:
        # 获取期间数据（取起止日附近各5个交易日以应对非交易日）
        df = pro.daily(
            ts_code=ts_code, 
            start_date=start_d, end_date=end_d,
            fields="ts_code,trade_date,close"
        )
        if df is None or df.empty:
            return None
        
        # 获取复权因子
        df_adj = pro.adj_factor(
            ts_code=ts_code,
            start_date=start_d, end_date=end_d,
        )
        time.sleep(0.02)
        
        if df_adj is None or df_adj.empty:
            return None
        
        # 合并
        df["trade_date"] = df["trade_date"].astype(str)
        df_adj["trade_date"] = df_adj["trade_date"].astype(str)
        merged = df.merge(df_adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
        merged = merged.sort_values("trade_date")
        
        # 前向填充 adj_factor（有时数据缺失）
        merged["adj_factor"] = merged["adj_factor"].ffill().bfill()
        
        if merged.empty:
            return None
        
        # 计算后复权价格
        merged["adj_close"] = merged["close"].astype(float) * merged["adj_factor"].astype(float)
        
        # 起始和结束价格
        start_row = merged.iloc[0]
        end_row = merged.iloc[-1]
        
        start_price = float(start_row["adj_close"])
        end_price = float(end_row["adj_close"])
        
        if start_price <= 0:
            return None
        
        return (start_price, end_price)
    except Exception as e:
        time.sleep(0.1)
        return None


def main():
    print("=" * 60)
    print("修正后 ZZ800 FCF 篮子回测 NAV 计算")
    print("=" * 60)
    
    # Load fixed baskets
    with open(FIXED_BASKETS_PATH) as f:
        baskets = json.load(f)
    
    # Load old NAV for rebalance dates
    old_nav = pd.read_csv(OLD_NAV_PATH)
    old_dates = old_nav["rb_date"].tolist()
    old_next_dates = old_nav["next_rb"].tolist()
    
    # Filter baskets to match old NAV dates
    valid_basket_dates = sorted([d for d in baskets if len(baskets[d]) >= 10])
    
    # Use the old NAV dates as our rebalance periods
    # For each period, we know: rb_date → next_rb (start → end)
    
    print(f"  修正后篮子期数: {len(valid_basket_dates)}")
    print(f"  旧版NAV期数: {len(old_dates)}")
    
    # Compute NAV
    nav = 1.0
    nav_series = []
    t_total = time.time()
    
    for i, rb_date in enumerate(old_dates):
        t0 = time.time()
        
        # Get basket for this rebalance date
        if rb_date not in baskets or len(baskets[rb_date]) < 10:
            # Try closest date
            closest = None
            for d in valid_basket_dates:
                if d >= rb_date:
                    closest = d
                    break
            if closest is None:
                closest = valid_basket_dates[-1]
            basket_stocks = baskets[closest]
            actual_date = closest
        else:
            basket_stocks = baskets[rb_date]
            actual_date = rb_date
        
        next_rb = old_next_dates[i]
        
        # Get weights
        weights = {s["ts_code"]: s["weight"] for s in basket_stocks}
        codes = list(weights.keys())
        
        period_return = 0.0
        n_valid = 0
        
        for code in codes:
            w = weights[code]
            result = get_adj_close(code, actual_date, next_rb)
            if result is None:
                continue
            
            start_price, end_price = result
            if start_price <= 0:
                continue
            
            stock_ret = (end_price - start_price) / start_price
            period_return += w * stock_ret
            n_valid += 1
        
        if n_valid > 0:
            nav = nav * (1 + period_return)
        
        elapsed = time.time() - t0
        elapsed_total = time.time() - t_total
        
        print(f"[{i+1}/{len(old_dates)}] {rb_date} → {next_rb}: "
              f"ret={period_return*100:.2f}%, NAV={nav:.4f}, "
              f"n_valid={n_valid}/{len(codes)}, "
              f"elapsed={elapsed:.1f}s (total={elapsed_total:.0f}s)")
        
        nav_series.append({
            "rb_date": rb_date,
            "next_rb": next_rb,
            "ret": round(period_return, 6),
            "nav": round(nav, 6),
            "n_valid": n_valid,
        })
    
    # Save
    df_nav = pd.DataFrame(nav_series)
    df_nav.to_csv(OUTPUT_DIR / "backtest_nav_tr.csv", index=False)
    
    print(f"\n{'=' * 60}")
    print(f"回测完成!")
    print(f"{'=' * 60}")
    print(f"  终值 NAV: {nav:.4f}")
    print(f"  总收益: {(nav - 1) * 100:.2f}%")
    n_years = len(nav_series) / 4
    if n_years > 0:
        annual = (nav ** (1/n_years) - 1) * 100
        print(f"  年化收益: {annual:.2f}% ({n_years:.1f}年)")
    print(f"  期数: {len(nav_series)}")
    print(f"  总耗时: {time.time() - t_total:.0f}s")
    
    # Compare with old NAV
    print(f"\n对比旧版回测:")
    old_final = old_nav["nav"].iloc[-1]
    old_total_ret = (old_final - 1) * 100
    old_annual = (old_final ** (1/n_years) - 1) * 100
    
    print(f"  旧版终值: {old_final:.4f}, 总收益: {old_total_ret:.2f}%, 年化: {old_annual:.2f}%")
    print(f"  修正终值: {nav:.4f}, 总收益: {(nav-1)*100:.2f}%, 年化: {annual:.2f}%")
    print(f"  NAV变化: {(nav/old_final - 1) * 100:.2f}%")
    
    # Compare with 932368
    official = pd.read_csv(OFFICIAL_PATH, dtype={"trade_date": str})
    official["date"] = pd.to_datetime(official["trade_date"], format="%Y%m%d")
    # Get official returns for the same period
    first_date = official["date"].min()
    last_date = official["date"].max()
    off_start = official[official["date"] <= pd.Timestamp(old_dates[0])].iloc[-1]["close"]
    off_end = official[official["date"] <= pd.Timestamp(old_next_dates[-1])].iloc[-1]["close"]
    off_ret = (off_end / off_start - 1) * 100
    off_annual = ((off_end / off_start) ** (1/n_years) - 1) * 100
    
    print(f"\n对比 932368 官方指数:")
    print(f"  932368 总收益: {off_ret:.2f}%")
    print(f"  932368 年化: {off_annual:.2f}%")
    print(f"  超额收益(修正vs官方): {(nav - off_end/off_start) * 100:.2f}%")
    
    # Per-period comparison
    print(f"\n逐期收益对比:")
    for i in range(len(nav_series)):
        old_ret = old_nav.iloc[i]["ret"] * 100
        new_ret = nav_series[i]["ret"] * 100
        diff = new_ret - old_ret
        print(f"  {nav_series[i]['rb_date']}: "
              f"旧={old_ret:.2f}%, 修正={new_ret:.2f}%, 差={diff:.2f}%")


if __name__ == "__main__":
    main()