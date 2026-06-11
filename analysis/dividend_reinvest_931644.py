"""
931644（中证800红利）10年股息再投资收益分析
============================================

背景说明
--------
- 931644.CSI 是「中证800红利」策略指数，基期2013-12-31，基点1000
- Tushare 只有价格版本，无对应官方全收益指数（H31644.CSI 无数据）
- 股息率代理方案：用「中证红利全收益(H00922) - 中证红利价格(000922)」
  推算逐年真实股息率，作为 931644 的股息代理
  （两者成分相近，股息率差异 < 0.5pp）
- 再投机制：每年最后一个交易日，将该年股息以当日收盘价全额买入
- 区间：最近10年 2016-06-13 ~ 2026-06-10

使用方法
--------
1. 安装依赖：pip install tushare pandas numpy
2. 替换下方 TUSHARE_TOKEN 为你的 token
3. python analysis/dividend_reinvest_931644.py

复现说明
--------
如果你算出的结果不同，常见原因：
1. 起始日期不同：本脚本用 2016-06-13（近10年的最近交易日）
2. 股息率假设不同：本脚本用真实中证红利股息率，而非固定5%
3. 再投时点不同：本脚本是年末最后交易日一次性再投
4. 是否含手续费：本脚本不扣除任何费用
"""

import time
import pandas as pd
import numpy as np

# ─── 配置 ────────────────────────────────────────────────────────────────────
import os, sys
from pathlib import Path
# 从项目根 .env 读取 token
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
if not TUSHARE_TOKEN:
    from config.settings import tushare_cfg
    TUSHARE_TOKEN = tushare_cfg.token
INITIAL       = 1_000_000           # 初始投入 100万
START         = "20160611"          # 回测起点（往前10年）
END           = "20260611"          # 回测终点
TARGET_CODE   = "931644.CSI"        # 目标指数：中证800红利（价格版）
DIV_TR_CODE   = "H00922.CSI"        # 中证红利 全收益版（用于推算股息率）
DIV_PR_CODE   = "000922.CSI"        # 中证红利 价格版
BENCH_HS300   = "H00300.CSI"        # 沪深300 全收益（对比基准）
# ─────────────────────────────────────────────────────────────────────────────


def get_index_daily(pro, ts_code: str, start: str, end: str) -> pd.Series:
    """拉取指数日行情，返回 DatetimeIndex 的 close Series"""
    df = pro.index_daily(
        ts_code=ts_code,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        fields="trade_date,close",
    )
    if df is None or df.empty:
        return pd.Series(dtype=float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date")["close"].sort_index()


def calc_annual_div_rate(tr_series: pd.Series, pr_series: pd.Series) -> dict:
    """
    用「全收益 - 价格」推算逐年真实股息率。

    原理：
        TR_年末 / TR_年初 = 价格涨幅 + 股息再投收益
        => 股息率 ≈ (TR收益率) - (价格收益率)
    """
    div_rates = {}
    for yr in range(2015, 2027):
        tr_end = tr_series[tr_series.index.year <= yr]
        tr_beg = tr_series[tr_series.index.year <= yr - 1]
        pr_end = pr_series[pr_series.index.year <= yr]
        pr_beg = pr_series[pr_series.index.year <= yr - 1]
        if any(s.empty for s in [tr_end, tr_beg, pr_end, pr_beg]):
            continue
        tr_ret = tr_end.iloc[-1] / tr_beg.iloc[-1] - 1
        pr_ret = pr_end.iloc[-1] / pr_beg.iloc[-1] - 1
        div_rates[yr] = round((tr_ret - pr_ret) * 100, 2)
    return div_rates


def simulate_div_reinvest(
    price_series: pd.Series,
    div_rates: dict,
    initial: float,
    default_div_rate: float = 5.0,
) -> pd.Series:
    """
    模拟股息年末再投资。

    参数
    ----
    price_series     : 价格指数的日收盘价 Series
    div_rates        : {年份: 股息率%}，逐年真实股息率
    initial          : 初始投资金额
    default_div_rate : 无真实股息率时的默认值（%）

    返回
    ----
    nav Series：每日账户净值（元）
    """
    p0 = float(price_series.iloc[0])
    shares = initial / p0          # 初始持股数

    nav_records = []
    for date, price in price_series.items():
        nav_records.append({"date": date, "nav": shares * float(price)})

        # 判断是否是该年最后一个交易日
        yr_data = price_series[price_series.index.year == date.year]
        if not yr_data.empty and date == yr_data.index[-1]:
            dy = div_rates.get(date.year, default_div_rate) / 100
            # 年末按当日收盘价再投：股息额 = 当前持股 × 当日价格 × 股息率
            dividend_income = shares * float(price) * dy
            new_shares      = dividend_income / float(price)
            shares         += new_shares

    nav = pd.DataFrame(nav_records).set_index("date")["nav"]
    return nav


def performance_stats(nav: pd.Series, years: float, rf: float = 0.025) -> dict:
    """计算常用绩效指标"""
    dr    = nav.pct_change().dropna()
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr  = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    mdd   = float(((nav - nav.cummax()) / nav.cummax()).min()) * 100
    ann_vol = dr.std() * (252 ** 0.5) * 100
    ann_ret = ((1 + dr.mean()) ** 252 - 1)
    sharpe  = (ann_ret - rf) / (dr.std() * 252 ** 0.5)
    return {
        "total_ret": total * 100,
        "cagr": cagr * 100,
        "mdd": mdd,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "final_nav": nav.iloc[-1],
    }


def main():
    import tushare as ts

    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    print("正在拉取数据……")
    target = get_index_daily(pro, TARGET_CODE, "20130101", END)
    time.sleep(0.12)
    tr_div = get_index_daily(pro, DIV_TR_CODE, "20130101", END)
    time.sleep(0.12)
    pr_div = get_index_daily(pro, DIV_PR_CODE, "20130101", END)
    time.sleep(0.12)
    bench  = get_index_daily(pro, BENCH_HS300, "20130101", END)

    print(f"931644 数据范围: {target.index[0].date()} ~ {target.index[-1].date()}")

    # ── 1. 推算逐年股息率 ─────────────────────────────────────────────────────
    div_rates = calc_annual_div_rate(tr_div, pr_div)
    print("\n📋 中证红利逐年真实股息率（用于代理 931644 股息）:")
    for yr, rate in sorted(div_rates.items()):
        print(f"  {yr}: {rate:.2f}%")

    # ── 2. 截取回测区间 ───────────────────────────────────────────────────────
    s = target[target.index >= pd.to_datetime(START)]
    s_bench = bench[bench.index >= pd.to_datetime(START)]
    yrs = (s.index[-1] - s.index[0]).days / 365.25

    print(f"\n回测起点: {s.index[0].date()}  价格={s.iloc[0]:.2f}")
    print(f"回测终点: {s.index[-1].date()}  价格={s.iloc[-1]:.2f}")
    print(f"回测年数: {yrs:.2f} 年")

    # ── 3. 含股息再投 ─────────────────────────────────────────────────────────
    nav_div = simulate_div_reinvest(s, div_rates, INITIAL)

    # ── 4. 仅价格 ─────────────────────────────────────────────────────────────
    nav_price = s / s.iloc[0] * INITIAL

    # ── 5. 沪深300全收益基准 ──────────────────────────────────────────────────
    nav_hs300 = s_bench / s_bench.iloc[0] * INITIAL

    # ── 6. 输出汇总表 ─────────────────────────────────────────────────────────
    stats_div   = performance_stats(nav_div,   yrs)
    stats_price = performance_stats(nav_price, yrs)
    stats_hs300 = performance_stats(nav_hs300, yrs)

    print(f"\n{'='*72}")
    print(f"  {'策略':30} {'最终市值(万)':>10} {'总收益':>9} {'年化CAGR':>9} {'最大回撤':>9} {'夏普':>7}")
    print(f"  {'-'*70}")
    rows = [
        ("931644(800红利) 含股息再投", stats_div),
        ("931644(800红利) 仅价格",     stats_price),
        ("沪深300全收益 H00300",        stats_hs300),
    ]
    for name, s_ in rows:
        print(
            f"  {name:30} {s_['final_nav']/10000:>9.0f}万"
            f" {s_['total_ret']:>+8.1f}%"
            f" {s_['cagr']:>8.2f}%"
            f" {s_['mdd']:>+8.1f}%"
            f" {s_['sharpe']:>7.2f}"
        )

    # ── 7. 逐年明细 ───────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  📅 逐年年末净值")
    print(f"  {'年':4}  {'931644含股息':>12} {'本年涨幅':>9} {'931644仅价格':>12} {'本年涨幅':>9} {'股息贡献':>9}")
    print(f"  {'-'*68}")
    for yr in range(2017, 2027):
        yn = nav_div[nav_div.index.year == yr]
        yp = nav_price[nav_price.index.year == yr]
        if yn.empty or yp.empty:
            continue
        prev_n = nav_div[nav_div.index.year == yr - 1]
        prev_p = nav_price[nav_price.index.year == yr - 1]
        n_val  = yn.iloc[-1];  p_val  = yp.iloc[-1]
        n_prev = prev_n.iloc[-1] if not prev_n.empty else float(INITIAL)
        p_prev = prev_p.iloc[-1] if not prev_p.empty else float(INITIAL)
        yr_n = (n_val / n_prev - 1) * 100
        yr_p = (p_val / p_prev - 1) * 100
        diff = yr_n - yr_p
        print(
            f"  {yr}   {n_val/10000:>8.0f}万 {yr_n:>+7.1f}%"
            f"   {p_val/10000:>8.0f}万 {yr_p:>+7.1f}%"
            f"   +{diff:.2f}pp"
        )

    # ── 8. 股息贡献汇总 ───────────────────────────────────────────────────────
    mult_price = s.iloc[-1] / s.iloc[0]
    mult_total = nav_div.iloc[-1] / INITIAL
    print(f"\n  📊 股息再投贡献汇总:")
    print(f"     价格涨幅：×{mult_price:.3f}  ({(mult_price-1)*100:+.1f}%)  年化 {stats_price['cagr']:.2f}%")
    print(f"     含股息涨幅：×{mult_total:.3f}  ({(mult_total-1)*100:+.1f}%)  年化 {stats_div['cagr']:.2f}%")
    extra_cagr = stats_div['cagr'] - stats_price['cagr']
    extra_total = (mult_total / mult_price - 1) * 100
    print(f"     股息再投额外贡献：+{extra_total:.1f}%（年化额外 +{extra_cagr:.2f}pp）")
    print(f"\n  注：股息率以中证红利真实股息率代理，不扣手续费。")
    print(f"      若想用固定股息率，修改 default_div_rate 参数（如 5.5 表示5.5%/年）。")


if __name__ == "__main__":
    main()
