"""Step 2: 基于预计算的 FCF 篮子，计算回测收益。

用法:
  python tests/calc_fcf_returns.py --baskets data/fcf_baskets_300_top50_annual.json
"""
import sys, json, argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from config.settings import tushare_cfg
import tushare as ts


def fetch_prices(pro, codes: set, start: str, end: str) -> dict:
    """批量拉取所有标的的日线数据，带重试"""
    prices = {}
    start_dt = datetime.strptime(start.replace("-", ""), "%Y%m%d")
    end_dt = datetime.strptime(end.replace("-", ""), "%Y%m%d")
    cur = start_dt
    total_fetched = 0
    import time
    while cur < end_dt:
        batch_end = min(cur + timedelta(days=92), end_dt)
        s_str = cur.strftime("%Y%m%d")
        e_str = batch_end.strftime("%Y%m%d")
        for attempt in range(3):
            try:
                df = pro.daily(start_date=s_str, end_date=e_str,
                              fields="ts_code,trade_date,close")
                time.sleep(0.3)
                if df is not None and not df.empty:
                    for code in df["ts_code"].unique():
                        sub = df[df["ts_code"] == code][["trade_date", "close"]].copy()
                        sub["trade_date"] = sub["trade_date"].astype(str)
                        sub = sub.sort_values("trade_date")
                        if code not in prices:
                            prices[code] = sub
                        else:
                            prices[code] = pd.concat([prices[code], sub]).drop_duplicates("trade_date").sort_values("trade_date")
                    total_fetched += len(df)
                break  # success
            except Exception as ex:
                print(f"    ⚠️ {s_str}-{e_str} attempt {attempt+1}: {ex}")
                time.sleep(2)
        if total_fetched and total_fetched % 50000 == 0:
            print(f"    已拉取 {total_fetched} 条价格数据...", flush=True)
        cur = batch_end + timedelta(days=1)
    return prices


def calc_returns(baskets: dict, prices: dict, benchmark_close: pd.Series,
                 cash: float = 1_000_000, commission: float = 0.001,
                 max_weight: float = 0.10) -> dict:
    """计算回测收益"""
    dates = sorted(baskets.keys())
    if not dates:
        return {}

    # 所有交易日 = 价格数据中的所有日期
    all_dates = set()
    for code, df in prices.items():
        all_dates |= set(df["trade_date"].tolist())
    all_dates = sorted(all_dates)
    all_dates = [d for d in all_dates if d >= dates[0].replace("-", "")]
    if not all_dates:
        return {}

    # 基准净值
    bench_dates = sorted(benchmark_close.index.tolist())
    bench_start_val = None
    for d in all_dates:
        bd = d.replace("-", "")
        if bd in benchmark_close.index:
            bench_start_val = benchmark_close[bd]
            break
    if bench_start_val is None:
        bench_start_val = 1000

    # 日净值计算
    navs = []  # [(date, portfolio_value, benchmark_value)]
    cash_balance = cash
    holdings = {}  # {code: shares}

    price_cache = {}
    for code, df in prices.items():
        df_indexed = df.set_index("trade_date")["close"].to_dict()
        price_cache[code] = df_indexed

    next_rebalance_idx = 0

    for day in all_dates:
        # 检查是否调仓日（或之后第一个交易日）
        while next_rebalance_idx < len(dates) and dates[next_rebalance_idx] <= day:
            rb_date = dates[next_rebalance_idx]
            targets = baskets[rb_date]
            next_rebalance_idx += 1

            # 计算当前总资产
            total = cash_balance
            for code, shares in holdings.items():
                p = price_cache.get(code, {}).get(day)
                if p and p > 0:
                    total += shares * p

            # 生成 target holdings
            target_holdings = {}
            total_alloc = total * 0.98  # 留2%现金
            for code, meta in targets.items():
                w = min(meta["weight"], max_weight)
                p = price_cache.get(code, {}).get(day)
                if p and p > 0 and w > 0:
                    target_shares = int(total_alloc * w / p / 100) * 100  # 整手
                    target_holdings[code] = target_shares

            # 卖
            for code in list(holdings.keys()):
                if code not in target_holdings:
                    p = price_cache.get(code, {}).get(day)
                    if p and p > 0:
                        cash_balance += holdings[code] * p * (1 - commission)
                    del holdings[code]

            # 买
            for code, target_shares in target_holdings.items():
                current = holdings.get(code, 0)
                if target_shares > current:
                    buy_shares = target_shares - current
                    p = price_cache.get(code, {}).get(day)
                    if p and p > 0:
                        cost = buy_shares * p * (1 + commission)
                        if cost <= cash_balance:
                            cash_balance -= cost
                            holdings[code] = target_shares
                elif target_shares < current:
                    sell_shares = current - target_shares
                    p = price_cache.get(code, {}).get(day)
                    if p and p > 0:
                        cash_balance += sell_shares * p * (1 - commission)
                        holdings[code] = target_shares

        # 计算当日总资产
        total = cash_balance
        for code, shares in holdings.items():
            p = price_cache.get(code, {}).get(day)
            if p and p > 0:
                total += shares * p

        # 基准
        bd = day.replace("-", "")
        bv = benchmark_close.get(bd)
        if bv is None:
            bv = navs[-1][2] if navs else bench_start_val

        navs.append((day, total, float(bv)))

    return {
        "navs": navs,
        "start_date": all_dates[0],
        "end_date": all_dates[-1],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baskets", required=True, help="预计算的篮子 JSON")
    parser.add_argument("--benchmark", default="000300.SH", help="基准指数")
    parser.add_argument("--cash", type=float, default=1_000_000)
    parser.add_argument("--commission", type=float, default=0.001)
    parser.add_argument("--report", default=None, help="输出报告路径")
    args = parser.parse_args()

    # 加载篮子
    with open(args.baskets) as f:
        baskets = json.load(f)
    print(f"加载篮子: {len(baskets)} 期")

    # 收集所有标的
    all_codes = set()
    for d, s in baskets.items():
        all_codes |= set(s.keys())
    print(f"涉及标的: {len(all_codes)} 只")

    # 拉取价格
    dates = sorted(baskets.keys())
    start = dates[0]
    end = dates[-1]
    print(f"\n拉取价格数据: {start} ~ {end}")
    pro = ts.pro_api(tushare_cfg.token)
    prices = fetch_prices(pro, all_codes, start, end)
    print(f"价格数据: {len(prices)}/{len(all_codes)} 只")

    # 拉取基准
    print(f"\n拉取基准 {args.benchmark} ...")
    bm_df = pro.index_daily(ts_code=args.benchmark, start_date=start.replace("-",""),
                            end_date=end.replace("-",""), fields="trade_date,close")
    benchmark_close = pd.Series()
    if bm_df is not None and not bm_df.empty:
        bm_df["trade_date"] = bm_df["trade_date"].astype(str)
        benchmark_close = bm_df.set_index("trade_date")["close"].sort_index()

    # 计算收益
    print(f"\n计算收益...")
    results = calc_returns(baskets, prices, benchmark_close, cash=args.cash,
                          commission=args.commission)

    if not results:
        print("❌ 计算失败")
        return

    navs = results["navs"]
    df = pd.DataFrame(navs, columns=["date", "strategy", "benchmark"])

    # 收益指标
    days = len(df)
    start_val = df["strategy"].iloc[0]
    end_val = df["strategy"].iloc[-1]
    total_ret = (end_val / start_val - 1) * 100

    start_bm = df["benchmark"].iloc[0]
    end_bm = df["benchmark"].iloc[-1]
    bm_ret = (end_bm / start_bm - 1) * 100 if start_bm > 0 else 0

    # 年化
    years = days / 244
    ann_ret = ((1 + total_ret/100) ** (1/years) - 1) * 100 if years > 0 else 0
    ann_bm = ((1 + bm_ret/100) ** (1/years) - 1) * 100 if years > 0 else 0

    # 最大回撤
    cummax = df["strategy"].cummax()
    drawdown = (df["strategy"] - cummax) / cummax * 100
    max_dd = drawdown.min()

    # 夏普
    daily_ret = df["strategy"].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(244) if daily_ret.std() > 0 else 0

    # 逐年收益
    print(f"\n{'='*65}")
    print(f"  HS300 FCF 回测结果")
    print(f"  {results['start_date']} ~ {results['end_date']}  ({years:.1f}年)")
    print(f"{'='*65}")
    print(f"  策略累计收益: {total_ret:+.1f}%")
    print(f"  策略年化收益: {ann_ret:+.1f}%")
    print(f"  基准({args.benchmark})累计: {bm_ret:+.1f}%")
    print(f"  基准年化: {ann_bm:+.1f}%")
    print(f"  超额收益: {total_ret - bm_ret:+.1f}%")
    print(f"  最大回撤: {max_dd:.1f}%")
    print(f"  夏普比率: {sharpe:.2f}")
    print(f"  年化超额: {ann_ret - ann_bm:+.1f}%")

    # 逐年
    df["year"] = df["date"].str[:4]
    print(f"\n  逐年收益:")
    print(f"  {'年份':<6} {'策略':>10} {'基准':>10} {'超额':>10} {'回撤':>10}")
    for yr, grp in df.groupby("year"):
        s_start = grp["strategy"].iloc[0]
        s_end = grp["strategy"].iloc[-1]
        b_start = grp["benchmark"].iloc[0]
        b_end = grp["benchmark"].iloc[-1]
        s_ret = (s_end/s_start - 1) * 100
        b_ret = (b_end/b_start - 1) * 100
        cummax_y = grp["strategy"].cummax()
        dd_y = (grp["strategy"] - cummax_y) / cummax_y * 100
        print(f"  {yr:<6} {s_ret:+9.1f}% {b_ret:+9.1f}% {s_ret-b_ret:+9.1f}% {dd_y.min():+9.1f}%")

    # 保存报告
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            f.write(f"# HS300 FCF 回测报告\n\n")
            f.write(f"- 期间: {results['start_date']} ~ {results['end_date']}\n")
            f.write(f"- 策略累计: {total_ret:+.1f}%\n")
            f.write(f"- 策略年化: {ann_ret:+.1f}%\n")
            f.write(f"- 基准累计: {bm_ret:+.1f}%\n")
            f.write(f"- 超额收益: {total_ret - bm_ret:+.1f}%\n")
            f.write(f"- 最大回撤: {max_dd:.1f}%\n")
            f.write(f"- 夏普比率: {sharpe:.2f}\n")
        print(f"\n报告: {report_path}")


if __name__ == "__main__":
    main()
