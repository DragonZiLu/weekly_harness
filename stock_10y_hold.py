"""
单只股票10年持有 + 股息再投资 评估器
=========================================

输入股票代码和买入日期，模拟：
  - 一次性买入，持有10年
  - 每次分红到账后按除权日收盘价再买入
  - 持股>1年，红利免税
  - 含佣金0.025%（单边）

用法：
  python stock_10y_hold.py --code 600036.SH --buy 2015-01-05 --cash 100000
  python stock_10y_hold.py --code 000651.SZ --buy 2013-06-01 --cash 500000
  python stock_10y_hold.py --code 600900.SH --buy 2014-03-15 --cash 100000 --verbose
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

import tushare as ts
import pandas as pd
from config.settings import tushare_cfg

ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

# ─── constants ─────────────────────────────────────────────────
COMMISSION_RATE = 0.00025   # 万2.5（单边）
AQ_TAX_FREE_YEARS = 1       # 持股>1年红利免税
LOT_SIZE = 100              # A股最小交易单位（1手 = 100股）

def round_to_lot(shares: float) -> int:
    """向下取整到整手（A股按100股整数倍交易）"""
    return int(shares // LOT_SIZE * LOT_SIZE)

# ─── data structures ───────────────────────────────────────────
@dataclass
class DivEvent:
    date: str           # 除权除息日 YYYY-MM-DD
    cash_per_share: float
    stk_div: float = 0  # 送转股比例（每10股送X股）
    close: float = 0    # 除权日收盘价（用于再投）

@dataclass
class YearRow:
    year: int
    start_value: float
    end_value: float
    shares: float
    price: float
    total_return: float
    price_return: float
    div_return: float
    div_count: int
    div_cash: float
    dividend_yield: float


def fetch_prices(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """获取原始（未复权）日线价格 — 送转股通过 simulate() 中 stk_div 手动处理"""
    dfs = []
    d = datetime.strptime(start[:10], "%Y-%m-%d")
    end_dt = datetime.strptime(end[:10], "%Y-%m-%d")
    
    while d < end_dt:
        chunk_end = min(d + timedelta(days=365), end_dt)
        try:
            df = pro.daily(
                ts_code=ts_code,
                start_date=d.strftime("%Y%m%d"),
                end_date=chunk_end.strftime("%Y%m%d"),
                fields="trade_date,open,high,low,close,pre_close,vol",
            )
            if not df.empty:
                dfs.append(df)
        except Exception:
            pass
        d = chunk_end + timedelta(days=1)

    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df.sort_values("trade_date").reset_index(drop=True)
    
    # 不做任何复权调整 — 送转股影响通过 simulate() 中 stk_div 手动处理
    # 现金分红通过 simulate() 中 div_cash 显式再投处理
    # 避免前复权因子与显式送转股/分红再投双重计入导致的股数虚增
    
    return df


def fetch_dividends(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """获取分红数据（含除权日）"""
    df = pro.dividend(
        ts_code=ts_code,
        fields="ts_code,ann_date,ex_date,record_date,cash_div,base_share,stk_div",
    )
    if df.empty:
        return pd.DataFrame()
    df = df[df["cash_div"] > 0].copy()
    df["ex_date"] = pd.to_datetime(df["ex_date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["ex_date"]).sort_values("ex_date")
    if "start" in df.columns:  # not in output
        pass
    
    # 筛选时间范围
    start_dt = pd.Timestamp(start[:10])
    end_dt = pd.Timestamp(end[:10])
    df = df[(df["ex_date"] >= start_dt) & (df["ex_date"] <= end_dt)]
    return df.reset_index(drop=True)


def get_price_on_date(price_df: pd.DataFrame, date_str: str) -> float:
    """获取 date_str 及之前最近交易日的收盘价"""
    target = pd.Timestamp(date_str)
    valid = price_df[price_df["trade_date"] <= target]
    if valid.empty:
        valid = price_df[price_df["trade_date"] >= target]
        if valid.empty:
            return 0
    return float(valid.iloc[-1]["close"])


def simulate(
    ts_code: str, buy_date: str, cash: float, verbose: bool = False, years: int = 10
) -> Tuple[List[YearRow], float, float, float]:
    """
    模拟持有N年+股息再投资（默认10年）

    Returns: (yearly_rows, final_value, cagr, split_factor)
        split_factor: 送转股累积因子（初始=1，每10送X则乘以(1+X/10)）
        用于将原始买入价换算为送转后的有效买入价: eff_buy = raw_buy / split_factor
    """
    # 日期范围
    start_dt = pd.Timestamp(buy_date[:10])
    end_dt = start_dt + timedelta(days=365 * years)

    start_str = buy_date[:10]
    end_str = end_dt.strftime("%Y-%m-%d")

    # 获取名称
    try:
        sb = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,industry")
        stock_name = sb.iloc[0]["name"] if not sb.empty else ts_code
        industry = sb.iloc[0].get("industry", "") if not sb.empty else ""
    except Exception:
        stock_name, industry = ts_code, ""

    # 拉取数据
    if verbose:
        print(f"  📥 拉取 {stock_name}({ts_code}) 价格数据...")
    price_df = fetch_prices(ts_code, start_str, end_str)

    if verbose:
        print(f"  📥 拉取分红数据...")
    div_df = fetch_dividends(ts_code, start_str, end_str)

    if price_df.empty:
        print("❌ 无价格数据")
        return [], 0, 0, 1.0

    # 买入价
    buy_price = get_price_on_date(price_df, start_str)
    if buy_price <= 0:
        print(f"❌ {buy_date} 无有效价格")
        return [], 0, 0, 1.0

    # 扣除佣金买入（A股按手交易，100股/手）
    commission = cash * COMMISSION_RATE
    invest = cash - commission
    max_shares = invest / buy_price
    shares = round_to_lot(max_shares)
    actual_invest = shares * buy_price + shares * buy_price * COMMISSION_RATE
    remaining_cash = cash - actual_invest

    if verbose:
        print(f"\n  💰 买入: {stock_name} × {shares}股 @ {buy_price:.2f}")
        print(f"     实际投入: {actual_invest:,.0f} 元，佣金: {shares*buy_price*COMMISSION_RATE:,.0f}")

    # 构造成除权事件序列
    events: List[DivEvent] = []
    for _, row in div_df.iterrows():
        ex_dt = row["ex_date"]
        if isinstance(ex_dt, pd.Timestamp):
            ex_str = ex_dt.strftime("%Y-%m-%d")
        else:
            ex_str = str(ex_dt)[:10]
        price = get_price_on_date(price_df, ex_str)
        if price > 0:
            stk = float(row.get("stk_div", 0) or 0)
            events.append(DivEvent(
                date=ex_str,
                cash_per_share=float(row["cash_div"]),
                stk_div=stk,
                close=price,
            ))

    # 逐年模拟
    yearly_rows: List[YearRow] = []
    total_div_cash = 0.0
    total_div_count = 0
    start_shares = shares
    start_value = shares * buy_price + remaining_cash
    split_factor = 1.0  # 送转股累积因子（用于换算买入价的"送转后等效值"）

    for y in range(years):
        year = start_dt.year + y
        y_start = pd.Timestamp(f"{year}-01-01")
        if y == 0:
            y_start = start_dt
        y_end = pd.Timestamp(f"{year}-12-31")
        if y == years - 1:
            y_end = end_dt

        y_start_val = shares * get_price_on_date(price_df, y_start.strftime("%Y-%m-%d")) + remaining_cash

        # 处理当年的分红事件
        y_div_cash = 0.0
        y_div_count = 0
        for ev in events:
            ev_year = pd.Timestamp(ev.date).year
            if ev_year == year:
                # 先处理送转股（增加持股数 + 更新 split_factor）
                if ev.stk_div > 0:
                    bonus_ratio = ev.stk_div / 10.0
                    bonus_shares = int(shares * bonus_ratio)
                    shares += bonus_shares
                    split_factor *= (1.0 + bonus_ratio)
                    if verbose:
                        print(f"    🎁 {ev.date} 10送{ev.stk_div:.0f}股 → +{bonus_shares}股 (共{shares}股) split_factor={split_factor:.2f}")
                
                # 现金分红再投
                div_amount = shares * ev.cash_per_share
                # 持股>1年免税
                holding_days = (pd.Timestamp(ev.date) - start_dt).days
                if holding_days < 365:
                    tax_rate = 0.20 if holding_days < 30 else 0.10
                    div_amount *= (1 - tax_rate)
                # 股息再投资（A股按手交易，100股/手，不足一手留现金）
                if ev.close > 0 and div_amount > 0:
                    max_reinvest_shares = div_amount / ev.close
                    new_shares = round_to_lot(max_reinvest_shares)
                    if new_shares > 0:
                        commission_div = new_shares * ev.close * COMMISSION_RATE
                        actual_reinvest = new_shares * ev.close + commission_div
                        remaining_cash += div_amount - actual_reinvest
                        shares += new_shares
                        y_div_cash += div_amount
                        y_div_count += 1
                        
                        if verbose:
                            print(f"    💸 {ev.date} 分红 {ev.cash_per_share:.2f}/股 "
                                  f"→ {div_amount:,.0f}元 → 买入{new_shares}股@{ev.close:.2f}")
                    else:
                        # 买不起一手，现金留存
                        remaining_cash += div_amount
                        y_div_cash += div_amount
                        y_div_count += 1
                        if verbose:
                            print(f"    💸 {ev.date} 分红 {ev.cash_per_share:.2f}/股 "
                                  f"→ {div_amount:,.0f}元 (不足1手，留存现金)")
                elif div_amount > 0:
                    remaining_cash += div_amount
                    y_div_cash += div_amount
                    y_div_count += 1

        total_div_cash += y_div_cash
        total_div_count += y_div_count

        # 年末数据
        y_end_price = get_price_on_date(price_df, y_end.strftime("%Y-%m-%d"))
        y_end_val = shares * y_end_price + remaining_cash
        y_total_ret = (y_end_val / y_start_val - 1) * 100 if y_start_val > 0 else 0
        # 用 split_factor 换算"送转后等效买入价"来消除送转股的失真
        eff_buy_price = buy_price / split_factor if split_factor > 0 else buy_price
        y_price_ret = (y_end_price / eff_buy_price - 1) * 100 if eff_buy_price > 0 else 0
        y_div_yield = y_div_cash / y_start_val * 100 if y_start_val > 0 else 0

        yearly_rows.append(YearRow(
            year=year, start_value=y_start_val, end_value=y_end_val,
            shares=shares, price=y_end_price,
            total_return=y_total_ret, price_return=y_price_ret,
            div_return=y_div_yield, div_count=y_div_count,
            div_cash=y_div_cash, dividend_yield=y_div_yield,
        ))

    final_val = shares * get_price_on_date(price_df, end_str) + remaining_cash
    cagr = ((final_val / start_value) ** (1 / years) - 1) * 100 if start_value > 0 else 0

    return yearly_rows, final_val, cagr, split_factor


def print_report(
    ts_code: str, stock_name: str, industry: str,
    buy_date: str, cash: float,
    rows: List[YearRow], final_val: float, cagr: float,
    split_factor: float = 1.0,
):
    """打印报告"""
    buy_price = 0
    if rows:
        buy_price = rows[0].start_value / rows[0].shares if rows[0].shares > 0 else 0

    # 用 split_factor 换算"送转后等效买入价"
    eff_buy = buy_price / split_factor if split_factor > 0 else buy_price

    print("\n" + "=" * 80)
    print(f"  📈 {stock_name} ({ts_code}) — 10年股息再投资评估")
    print(f"  📅 {buy_date[:10]} → {pd.Timestamp(buy_date[:10]) + timedelta(days=365*10):%Y-%m-%d}")
    print(f"  📊 行业: {industry}")
    print(f"  💰 初始投入: {cash:,.0f} 元 | 买入价: {buy_price:.2f}")
    print("=" * 80)

    # ── 概览 ──
    total_ret = (final_val / cash - 1) * 100 if cash > 0 else 0
    first_shares = rows[0].shares if rows else 0
    last_shares = rows[-1].shares if rows else 0
    div_amplification = (last_shares / first_shares - 1) * 100 if first_shares > 0 else 0
    last_price = rows[-1].price if rows else 0
    # 用送转后等效买入价计算股价变化（消除送转股的价格跳跃失真）
    price_change = (last_price / eff_buy - 1) * 100 if eff_buy > 0 else 0
    div_contrib = total_ret - price_change

    print(f"\n  📊 10年总览")
    print(f"  {'─' * 40}")
    print(f"  初始持股: {first_shares:,.0f} 股")
    print(f"  最终持股: {last_shares:,.0f} 股 (股息再投增持 {div_amplification:+.1f}%)")
    print(f"  买入价 → 最终价: {buy_price:.2f} → {last_price:.2f} ({price_change:+.1f}%)")
    print(f"  最终资产: {final_val:,.0f} 元")
    print(f"  总收益率: {total_ret:+.2f}%")
    print(f"    ├─ 股价贡献: {price_change:+.2f}%")
    print(f"    └─ 股息再投贡献: {div_contrib:+.2f}%")
    print(f"  年化 CAGR: {cagr:+.2f}%")

    # ── 逐年明细 ──
    print(f"\n  📅 逐年明细")
    print(f"  {'年份':<6} {'年初资产':>12s} {'年末资产':>12s} {'持股(股)':>10s} {'股价':>8s} {'收益率':>8s} {'分红次数':>8s} {'分红金额':>10s} {'股息率':>6s}")
    print(f"  {'─' * 90}")
    for r in rows:
        print(f"  {r.year:<6} {r.start_value:>12,.0f} {r.end_value:>12,.0f} "
              f"{r.shares:>10,.0f} {r.price:>8.2f} {r.total_return:>+7.1f}% "
              f"{r.div_count:>8} {r.div_cash:>10,.0f} {r.dividend_yield:>5.1f}%")

    # ── 分红汇总 ──
    total_div = sum(r.div_cash for r in rows)
    total_div_cnt = sum(r.div_count for r in rows)
    print(f"\n  💸 分红汇总: {total_div_cnt} 次分红，累计 {total_div:,.0f} 元 ({total_div/cash*100:.1f}% of 初始投入)")

    # ── 评分 ──
    print(f"\n  {'─' * 40}")
    if cagr > 12:
        verdict = "🌟🌟🌟 卓越 — 10年翻3倍以上"
    elif cagr > 8:
        verdict = "🌟🌟   优秀 — 超越大多数基金经理"
    elif cagr > 5:
        verdict = "🌟    良好 — 跑赢通胀+理财"
    elif cagr > 2:
        verdict = "⭐    及格 — 勉强保值"
    else:
        verdict = "💀    不及格 — 不如存银行"
    print(f"  综合评定: {verdict}")


# ─── main ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="单只股票10年持有+股息再投资评估")
    parser.add_argument("--code", type=str, required=True, help="股票代码 (如 600036.SH)")
    parser.add_argument("--buy", type=str, required=True, help="买入日期 (如 2015-01-05)")
    parser.add_argument("--cash", type=float, required=True, help="投入金额 (元)")
    parser.add_argument("--verbose", action="store_true", help="显示每次分红再投明细")
    args = parser.parse_args()

    # 名称
    try:
        sb = pro.stock_basic(ts_code=args.code, fields="ts_code,name,industry")
        name = sb.iloc[0]["name"] if not sb.empty else args.code
        industry = sb.iloc[0].get("industry", "") if not sb.empty else ""
    except Exception:
        name, industry = args.code, ""

    # 模拟
    rows, final_val, cagr, split_factor = simulate(args.code, args.buy, args.cash, args.verbose)

    if not rows:
        print("❌ 模拟失败")
        return

    print_report(args.code, name, industry, args.buy, args.cash, rows, final_val, cagr, split_factor)
    print()


if __name__ == "__main__":
    main()
