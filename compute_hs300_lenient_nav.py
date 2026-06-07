#!/usr/bin/env python3
"""
compute_hs300_lenient_nav.py — 计算HS300 B版(fixed+宽松OCF)篮子的NAV

方法：与C版相同，逐期计算加权后复权收益率，累加得到NAV
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

# B版 baskets (fixed + lenient OCF)
BASKETS_PATH = ROOT / 'output/hs300_fcf_fixed_lenient/all_baskets_2015_2026.json'
# A版 NAV for rebalance date chain
OLD_NAV_PATH = ROOT / 'output/hs300_fcf/backtest_nav_tr.csv'
OUTPUT_DIR = ROOT / 'output/hs300_fcf_fixed_lenient'
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
    print("HS300 B版(fixed+宽松OCF) NAV 计算")
    print("=" * 60)

    with open(BASKETS_PATH) as f:
        baskets = json.load(f)

    old_nav = pd.read_csv(OLD_NAV_PATH)
    rb_dates = old_nav["rb_date"].tolist()
    next_rbs = old_nav["next_rb"].tolist()

    valid_dates = sorted([d for d in baskets if len(baskets[d]) >= 10])
    print(f"  B版篮子有效期数: {len(valid_dates)} / {len(baskets)}")
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

        nav_records.append({
            "rb_date": rb_date,
            "next_rb": next_rb,
            "ret": round(period_ret, 6),
            "nav": round(nav, 6),
            "n_valid": n_valid
        })

        print(f"[{i+1}/{len(rb_dates)}] {rb_date} → {next_rb}: "
              f"ret={period_ret*100:.2f}%, NAV={nav:.4f}, "
              f"n_valid={n_valid}/{len(weights)}, {elapsed:.1f}s")

    # Save
    df_out = pd.DataFrame(nav_records)
    out_path = OUTPUT_DIR / 'backtest_nav_tr.csv'
    df_out.to_csv(out_path, index=False)

    n_years = len(nav_records) / 4
    total_ret = nav - 1
    annual_ret = (nav ** (1/n_years) - 1) * 100

    print(f"\n{'='*60}")
    print(f"HS300 B版回测完成!")
    print(f"{'='*60}")
    print(f"  终值 NAV: {nav:.4f}")
    print(f"  总收益: {total_ret*100:.2f}%")
    print(f"  年化收益: {annual_ret:.2f}% ({n_years:.1f}年)")

    # Compare with A版 and C版
    a_nav_val = old_nav.iloc[-1]["nav"]
    c_nav = pd.read_csv(ROOT / 'output/hs300_fcf_fixed/backtest_nav_tr.csv')
    c_nav_val = c_nav.iloc[-1]["nav"]

    print(f"\n三版对比:")
    print(f"  A版(原始宽松): NAV={a_nav_val:.4f}, 年化={(a_nav_val**(1/n_years)-1)*100:.2f}%")
    print(f"  B版(fixed+宽松): NAV={nav:.4f}, 年化={annual_ret:.2f}%")
    print(f"  C版(fixed+严格): NAV={c_nav_val:.4f}, 年化={(c_nav_val**(1/n_years)-1)*100:.2f}%")

    # Compare with 932366
    idx_932366 = pd.read_csv(ROOT / 'data/932366_daily.csv', dtype={'trade_date': str})
    idx_932366 = idx_932366.sort_values('trade_date')
    start_d = rb_dates[0].replace("-", "")
    end_d = next_rbs[-1].replace("-", "")
    idx_sub = idx_932366[(idx_932366['trade_date'] >= start_d) & (idx_932366['trade_date'] <= end_d)]
    if not idx_sub.empty:
        idx_start = float(idx_sub.iloc[0]['close'])
        idx_end = float(idx_sub.iloc[-1]['close'])
        idx_annual = ((idx_end/idx_start)**(1/n_years) - 1) * 100
        print(f"\n  932366官方指数: 年化={idx_annual:.2f}%, 总={(idx_end/idx_start-1)*100:.2f}%")
        print(f"  B版超额(vs 932366): {annual_ret - idx_annual:.2f}pp")


if __name__ == "__main__":
    main()