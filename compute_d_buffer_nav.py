#!/usr/bin/env python3
"""
compute_d_buffer_nav.py — 计算D版(B版基线+缓冲区)篮子的NAV
复用B版NAV日期链和计算逻辑
"""
import json, time, os
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path('/Users/luzilong/Work/weekly_harness/.env'))
import tushare as ts
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

ROOT = Path('/Users/luzilong/Work/weekly_harness')

D_BASKETS_PATH = ROOT / 'output/hs300_fcf_lenient_buffer/all_baskets_2015_2026.json'
OLD_NAV_PATH = ROOT / 'output/hs300_fcf/backtest_nav_tr.csv'
OUTPUT_DIR = ROOT / 'output/hs300_fcf_lenient_buffer'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_adj_close(ts_code, start_date, end_date):
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
    except Exception:
        time.sleep(0.15)
        return None


def main():
    print("=" * 60)
    print("D版(B版基线+缓冲区) NAV 计算")
    print("=" * 60)

    with open(D_BASKETS_PATH) as f:
        baskets = json.load(f)

    old_nav = pd.read_csv(OLD_NAV_PATH)
    rb_dates = old_nav["rb_date"].tolist()
    next_rbs = old_nav["next_rb"].tolist()
    
    # ★ 使用B版NAV的日期链(与D版篮子完全对齐)
    b_nav_path = ROOT / 'output/hs300_fcf_fixed_lenient/backtest_nav_tr.csv'
    b_nav_df = pd.read_csv(b_nav_path)
    rb_dates = b_nav_df["rb_date"].tolist()
    next_rbs = b_nav_df["next_rb"].tolist()

    # 同时加载B版NAV用于实时对比
    b_nav_path = ROOT / 'output/hs300_fcf_fixed_lenient/backtest_nav_tr.csv'
    b_nav = pd.read_csv(b_nav_path) if b_nav_path.exists() else None

    valid_dates = sorted([d for d in baskets if len(baskets[d]) >= 10])
    print(f"  D版篮子有效期数: {len(valid_dates)} / {len(baskets)}")
    print(f"  A版NAV期数: {len(rb_dates)}")

    nav = 1.0
    nav_records = []
    t_total = time.time()

    for i, rb_date in enumerate(rb_dates):
        t0 = time.time()

        if rb_date not in baskets or len(baskets[rb_date]) < 10:
            closest = None
            for d in valid_dates:
                if d >= rb_date:
                    closest = d
                    break
            if closest is None:
                closest = valid_dates[-1]
            basket = baskets[closest]
            actual_date = closest
        else:
            basket = baskets[rb_date]
            actual_date = rb_date

        next_rb = next_rbs[i]
        weights = {s["ts_code"]: s["weight"] for s in basket}

        weighted_ret = 0.0
        n_valid = 0

        for code, w in weights.items():
            result = get_adj_close(code, actual_date, next_rb)
            if result is not None:
                start_p, end_p = result
                ret = end_p / start_p - 1
                weighted_ret += w * ret
                n_valid += 1

        period_ret = weighted_ret
        nav *= (1 + period_ret)
        elapsed = time.time() - t0

        # B版同期NAV对比
        b_nav_val = ""
        if b_nav is not None and i < len(b_nav):
            b_ret = b_nav.iloc[i]['ret']
            b_nav_val = f", B版ret={b_ret*100:.2f}%, diff={((period_ret - b_ret)*100):+.2f}pp"

        nav_records.append({
            "rb_date": rb_date,
            "next_rb": next_rb,
            "ret": round(period_ret, 6),
            "nav": round(nav, 6),
            "n_valid": n_valid
        })

        print(f"[{i+1}/{len(rb_dates)}] {rb_date} → {next_rb}: "
              f"ret={period_ret*100:.2f}%, NAV={nav:.4f}, "
              f"n_valid={n_valid}/{len(weights)}{b_nav_val}")

    out_csv = OUTPUT_DIR / "backtest_nav_tr.csv"
    df_out = pd.DataFrame(nav_records)
    df_out.to_csv(out_csv, index=False)

    # 最终统计
    n_years = len(nav_records) / 4
    annual = (nav ** (1 / n_years) - 1) * 100

    print(f"\n{'='*60}")
    print(f"✅ D版NAV计算完成!")
    print(f"   最终NAV: {nav:.4f}")
    print(f"   年化收益: {annual:.2f}%")
    print(f"   回测期间: {rb_dates[0]} ~ {next_rbs[-1]} ({n_years:.1f}年)")
    print(f"   输出: {out_csv}")

    # 与B版对比
    if b_nav is not None:
        b_final = b_nav.iloc[-1]['nav']
        b_annual = (b_final ** (1 / n_years) - 1) * 100
        print(f"\n   B版对比:")
        print(f"     B版: NAV={b_final:.4f}, 年化={b_annual:.2f}%")
        print(f"     D版: NAV={nav:.4f}, 年化={annual:.2f}%")
        print(f"     差异: {annual - b_annual:+.2f}pp")


if __name__ == "__main__":
    main()
