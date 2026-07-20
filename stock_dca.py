"""
stock_dca.py — 股票组合定投收益计算器（含股息再投资）
========================================================

模拟一个股票组合的年度定投，并计算年化收益率（XIRR）。
默认组合：华能国际 + 国电电力 : 中国神华 = 1:1
  - 中国神华  50%
  - 华能国际  25%（电力组合内一半）
  - 国电电力  25%（电力组合内一半）

股息处理：
  - 再投资模式：除权日按"持股数 × 每股派现"得到现金，以当日不复权收盘价再买入
  - 对照模式：分红收现金，不再买入

使用方法：
  # 默认：组合 2016-2026 每年1月定投10万，股息再投资
  python stock_dca.py

  # 自定义金额 / 区间
  python stock_dca.py --annual 100000 --start 2016 --end 2026

  # 关闭股息再投资（仅价格回报）
  python stock_dca.py --no-reinvest
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import tushare as ts

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ── 组合定义 ──
STOCKS = {
    "600011.SH": "华能国际",
    "600795.SH": "国电电力",
    "601088.SH": "中国神华",
}
# 权重（华能+国电 : 神华 = 1:1，电力内各半）
WEIGHTS = {
    "600011.SH": 0.25,
    "600795.SH": 0.25,
    "601088.SH": 0.50,
}


# ═══════════════════════════════════════════════════════
#  数据获取
# ═══════════════════════════════════════════════════════

def init_tushare():
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


def fetch_daily(pro, ts_code: str, start: str, end: str) -> dict[str, float]:
    """获取日行情，返回 {trade_date(str YYYYMMDD): close}"""
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    if df is None or df.empty:
        raise ValueError(f"无日行情数据: {ts_code} {start}~{end}")
    df = df.dropna(subset=["trade_date", "close"])
    df = df.sort_values("trade_date")
    return {str(d): float(c) for d, c in zip(df["trade_date"], df["close"])}


def fetch_dividends(pro, ts_code: str, start: str, end: str) -> dict[str, float]:
    """获取现金分红(每股派现)，返回 {ex_date(str YYYYMMDD): cash_div}"""
    try:
        div = pro.dividend(ts_code=ts_code)
    except Exception as e:
        print(f"  ⚠️ {ts_code} 分红接口失败: {e}")
        return {}
    if div is None or div.empty:
        return {}
    div = div[div["ex_date"].notna()]
    div["ex_date"] = div["ex_date"].astype(str)
    div = div[(div["ex_date"] >= start) & (div["ex_date"] <= end)]
    result: dict[str, float] = {}
    for _, r in div.iterrows():
        ex = str(r["ex_date"])
        cash = float(r["cash_div"]) if pd.notna(r["cash_div"]) else 0.0
        if cash > 0:
            result[ex] = result.get(ex, 0.0) + cash
    return result


# ═══════════════════════════════════════════════════════
#  计算工具
# ═══════════════════════════════════════════════════════

def xirr(cashflows: list[tuple[str, float]]) -> float:
    """
    XIRR：基于实际日期的年化收益率
    cashflows: [(date_str YYYYMMDD, amount)] 负=投入, 正=回收
    """
    dates = [datetime.strptime(d, "%Y%m%d") for d, _ in cashflows]
    amounts = [a for _, a in cashflows]
    d0 = dates[0]

    def npv(rate: float) -> float:
        return sum(
            a / (1 + rate) ** ((dt - d0).days / 365.0)
            for dt, a in zip(dates, amounts)
        )

    lo, hi = -0.99, 10.0
    for _ in range(300):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def compute_jan_invest_dates(all_dates: list[str], start_year: int, end_year: int) -> list[str]:
    """每个年份1月的第一个交易日（在区间内）"""
    dates = sorted(all_dates)
    invest = []
    for y in range(start_year, end_year + 1):
        cand = [d for d in dates if d.startswith(f"{y}01")]
        if cand:
            invest.append(cand[0])
    return invest


# ═══════════════════════════════════════════════════════
#  核心模拟
# ═══════════════════════════════════════════════════════

def simulate(annual_amount: float, start_year: int, end_year: int, reinvest: bool):
    pro = init_tushare()

    # 提前一点，确保能取到1月首个交易日
    start_date = f"{start_year - 1}1230"
    end_date = datetime.now().strftime("%Y%m%d")

    print(f"  📡 拉取行情数据 ({start_date} ~ {end_date}) ...")
    close_map: dict[str, dict[str, float]] = {}
    div_map: dict[str, dict[str, float]] = {}
    for code, name in STOCKS.items():
        close_map[code] = fetch_daily(pro, code, start_date, end_date)
        div_map[code] = fetch_dividends(pro, code, start_date, end_date)
        print(f"     · {name}({code}): {len(close_map[code])} 交易日, "
              f"{len(div_map[code])} 次分红")

    all_dates = sorted(set().union(*[set(m.keys()) for m in close_map.values()]))
    # 前向填充价格，处理个别停牌日（保证定投日/除权日都有价可买）
    _filled = {}
    for code in STOCKS:
        fc = {}
        last = None
        for d in all_dates:
            if d in close_map[code]:
                last = close_map[code][d]
            fc[d] = last
        _filled[code] = fc
    close_map = _filled
    invest_dates = compute_jan_invest_dates(all_dates, start_year, end_year)
    print(f"  📅 定投日({len(invest_dates)}次): {', '.join(invest_dates)}")

    shares = {c: 0.0 for c in STOCKS}
    div_cash_collected = 0.0        # 仅对照模式累计
    total_invested = 0.0
    total_div_reinvested = 0.0      # 再投资模式累计投入分红额
    cashflows = []                  # (date, amount)

    # 逐年追踪（在主循环内用当时累积份额记录，避免用到最终份额）
    yearly_value = {}

    for date in all_dates:
        # 1) 年度定投
        if date in invest_dates:
            for code, w in WEIGHTS.items():
                price = close_map[code][date]
                shares[code] += (annual_amount * w) / price
            total_invested += annual_amount
            cashflows.append((date, -annual_amount))

        # 2) 分红处理
        for code in STOCKS:
            if date in div_map[code]:
                dps = div_map[code][date]
                price = close_map[code][date]
                div_cash = shares[code] * dps
                if reinvest:
                    total_div_reinvested += div_cash
                    shares[code] += div_cash / price
                else:
                    div_cash_collected += div_cash

        # 3) 记录当年末市值（用当前已累积份额）
        y = date[:4]
        yearly_value[y] = sum(shares[c] * close_map[c][date] for c in STOCKS)

    yearly = sorted(((y, v) for y, v in yearly_value.items()))

    final_date = all_dates[-1]
    final_value = sum(shares[c] * close_map[c][final_date] for c in STOCKS)
    final_total = final_value + (div_cash_collected if not reinvest else 0.0)
    cashflows.append((final_date, final_total))

    annual_ret = xirr(cashflows)

    return {
        "annual_amount": annual_amount,
        "total_invested": total_invested,
        "total_div_reinvested": total_div_reinvested if reinvest else div_cash_collected,
        "reinvest": reinvest,
        "final_value": final_value,
        "div_cash_collected": div_cash_collected,
        "final_total": final_total,
        "annual_ret": annual_ret,
        "n_invest": len(invest_dates),
        "invest_dates": invest_dates,
        "final_date": final_date,
        "yearly": yearly,
        "shares": dict(shares),
        "close_map": close_map,
        "final_close": {c: close_map[c][final_date] for c in STOCKS},
    }


# ═══════════════════════════════════════════════════════
#  报告
# ═══════════════════════════════════════════════════════

def print_report(r_reinvest, r_price):
    W = 78
    print(f"\n{'=' * W}")
    print(f"  📊 股票组合定投收益（含股息再投资）")
    print(f"  🔧 组合: 华能国际25% + 国电电力25% + 中国神华50%")
    print(f"  💰 每年1月定投 {r_reinvest['annual_amount']:,.0f} 元 × {r_reinvest['n_invest']}次")
    print(f"  📅 区间: {r_reinvest['invest_dates'][0]} ~ {r_reinvest['final_date']}")
    print(f"{'=' * W}")

    total_in = r_reinvest["total_invested"]
    # 再投资模式
    a = r_reinvest
    gain_a = a["final_total"] - total_in
    ret_a = gain_a / total_in * 100
    print(f"\n  ── 方案A: 股息再投资 ──")
    print(f"  {'累计投入':>10}: {total_in:>12,.0f} 元")
    print(f"  {'累计再投分红':>10}: {a['total_div_reinvested']:>12,.0f} 元")
    print(f"  {'期末市值':>10}: {a['final_total']:>12,.0f} 元")
    print(f"  {'总收益':>10}: {gain_a:>+12,.0f} 元  ({ret_a:+.1f}%)")
    print(f"  {'年化(XIRR)':>10}: {a['annual_ret']*100:>11.2f}%")

    # 对照模式（收现金）
    b = r_price
    gain_b = b["final_total"] - total_in
    ret_b = gain_b / total_in * 100
    div_contrib = a["final_total"] - b["final_total"]
    print(f"\n  ── 方案B: 分红收现金（仅价格回报） ──")
    print(f"  {'期末市值':>10}: {b['final_value']:>12,.0f} 元")
    print(f"  {'累计分红现金':>10}: {b['div_cash_collected']:>12,.0f} 元")
    print(f"  {'总资产':>10}: {b['final_total']:>12,.0f} 元  ({ret_b:+.1f}%)")
    print(f"  {'年化(XIRR)':>10}: {b['annual_ret']*100:>11.2f}%")

    print(f"\n  ── 股息再投资的贡献 ──")
    print(f"  · 再投资比收现金多赚: {div_contrib:>10,.0f} 元")
    print(f"  · 年化提升: {(a['annual_ret']-b['annual_ret'])*100:+.2f} pp")

    # 逐年市值（再投资模式）
    print(f"\n  ── 逐年市值（再投资模式） ──")
    print(f"  {'年份':>5}  {'年末市值':>14}")
    print(f"  {'─'*5}  {'─'*14}")
    for y, v in a["yearly"]:
        print(f"  {y:>5}  {v:>14,.0f}")

    print(f"\n{'=' * W}")


# ═══════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="股票组合定投收益计算器（含股息再投资）")
    parser.add_argument("--annual", type=float, default=100000, help="每年定投金额，默认100000")
    parser.add_argument("--start", type=int, default=2016, help="定投起始年（含），默认2016")
    parser.add_argument("--end", type=int, default=2026, help="定投结束年（含），默认2026")
    parser.add_argument("--no-reinvest", action="store_true", help="分红不再投资（仅价格回报）")
    args = parser.parse_args()

    print(f"\n{'#' * 78}")
    print(f"  # 股票组合定投模拟：华能国际+国电电力 : 中国神华 = 1:1")
    print(f"  # 每年1月定投 {args.annual:,.0f} 元，{args.start}-{args.end}，股息再投资")
    print(f"{'#' * 78}")

    r_reinvest = simulate(args.annual, args.start, args.end, reinvest=True)
    r_price = simulate(args.annual, args.start, args.end, reinvest=False)
    print_report(r_reinvest, r_price)


if __name__ == "__main__":
    main()
