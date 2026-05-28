"""
Monthly ETF DCA calculator.

Example:
  python run_etf_dca.py --code 515180.SH --start 2020-01-01 --monthly 10000
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from weekly_harness.backtest import BacktestDataFetcher


PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data" / "dca"


def _normalize_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        return code
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def calculate_etf_dca(
    code: str,
    start: str,
    end: str,
    monthly_amount: float,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    fetcher = BacktestDataFetcher()
    prices = fetcher.fetch_price_data(code, start, end, is_etf=True).copy()
    dividends = fetcher.fetch_dividend_data(code, start, end, is_etf=True).copy()

    if prices.empty:
        raise RuntimeError(f"No ETF price data returned for {code}")

    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.sort_values("trade_date").reset_index(drop=True)

    if not dividends.empty:
        dividends["ex_date_dt"] = pd.to_datetime(dividends["ex_date"], format="%Y%m%d")
        dividends = dividends.sort_values("ex_date_dt").reset_index(drop=True)

    monthly_buys = prices.groupby(prices["trade_date"].dt.to_period("M"), as_index=False).first()
    monthly_buys = monthly_buys[["trade_date", "close"]].rename(columns={"close": "buy_price"})

    dividend_by_date = {}
    if not dividends.empty:
        dividend_by_date = {
            row.ex_date_dt: float(row.cash_per_share)
            for row in dividends.itertuples()
        }
    buy_by_date = {
        row.trade_date: float(row.buy_price)
        for row in monthly_buys.itertuples()
    }

    shares_cash_mode = 0.0
    cash_dividend_cum = 0.0
    shares_reinvest_mode = 0.0
    invested_cum = 0.0
    daily_records = []
    dividend_records = []

    for row in prices.itertuples():
        trade_date = row.trade_date
        close = float(row.close)
        buy_amount = 0.0

        if trade_date in buy_by_date:
            buy_amount = monthly_amount
            invested_cum += monthly_amount
            shares_cash_mode += monthly_amount / close
            shares_reinvest_mode += monthly_amount / close

        if trade_date in dividend_by_date:
            dps = dividend_by_date[trade_date]
            cash_dividend = shares_cash_mode * dps
            reinvest_dividend = shares_reinvest_mode * dps
            shares_before_reinvest = shares_reinvest_mode

            cash_dividend_cum += cash_dividend
            shares_reinvest_mode += reinvest_dividend / close

            dividend_records.append(
                {
                    "date": trade_date.strftime("%Y-%m-%d"),
                    "cash_per_share": dps,
                    "shares_cash_mode": shares_cash_mode,
                    "cash_dividend": cash_dividend,
                    "shares_reinvest_before": shares_before_reinvest,
                    "reinvest_dividend": reinvest_dividend,
                    "reinvest_price": close,
                }
            )

        daily_records.append(
            {
                "date": trade_date,
                "year": trade_date.year,
                "close": close,
                "buy_amount": buy_amount,
                "invested_cum": invested_cum,
                "shares_cash_mode": shares_cash_mode,
                "cash_dividend_cum": cash_dividend_cum,
                "asset_cash_mode": shares_cash_mode * close + cash_dividend_cum,
                "shares_reinvest_mode": shares_reinvest_mode,
                "asset_reinvest_mode": shares_reinvest_mode * close,
            }
        )

    daily = pd.DataFrame(daily_records)
    div_detail = pd.DataFrame(dividend_records)
    yearly_records = []

    for year, group in daily.groupby("year"):
        last = group.iloc[-1]
        dividend_year = 0.0
        if not div_detail.empty:
            dividend_year = div_detail[
                pd.to_datetime(div_detail["date"]).dt.year == year
            ]["cash_dividend"].sum()

        yearly_records.append(
            {
                "year": year,
                "invested_year": group["buy_amount"].sum(),
                "invested_cum": last["invested_cum"],
                "close": last["close"],
                "asset_cash_mode": last["asset_cash_mode"],
                "profit_cash_mode": last["asset_cash_mode"] - last["invested_cum"],
                "return_cash_mode_pct": (last["asset_cash_mode"] / last["invested_cum"] - 1) * 100,
                "dividend_year": dividend_year,
                "dividend_cum": last["cash_dividend_cum"],
                "asset_reinvest_mode": last["asset_reinvest_mode"],
                "profit_reinvest_mode": last["asset_reinvest_mode"] - last["invested_cum"],
                "return_reinvest_mode_pct": (last["asset_reinvest_mode"] / last["invested_cum"] - 1) * 100,
            }
        )

    yearly = pd.DataFrame(yearly_records)
    last = daily.iloc[-1]
    summary = {
        "code": code,
        "start": prices["trade_date"].min().strftime("%Y-%m-%d"),
        "end": prices["trade_date"].max().strftime("%Y-%m-%d"),
        "monthly_amount": monthly_amount,
        "months": len(monthly_buys),
        "invested": last["invested_cum"],
        "last_close": last["close"],
        "shares_cash_mode": last["shares_cash_mode"],
        "cash_dividend_cum": last["cash_dividend_cum"],
        "asset_cash_mode": last["asset_cash_mode"],
        "profit_cash_mode": last["asset_cash_mode"] - last["invested_cum"],
        "return_cash_mode_pct": (last["asset_cash_mode"] / last["invested_cum"] - 1) * 100,
        "shares_reinvest_mode": last["shares_reinvest_mode"],
        "asset_reinvest_mode": last["asset_reinvest_mode"],
        "profit_reinvest_mode": last["asset_reinvest_mode"] - last["invested_cum"],
        "return_reinvest_mode_pct": (last["asset_reinvest_mode"] / last["invested_cum"] - 1) * 100,
    }
    return summary, yearly, div_detail


def write_report(summary: dict, yearly: pd.DataFrame, dividends: pd.DataFrame) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    code_tag = summary["code"].replace(".", "_")
    yearly_path = DATA_DIR / f"{code_tag}_monthly_dca_yearly.csv"
    div_path = DATA_DIR / f"{code_tag}_monthly_dca_dividends.csv"
    report_path = DATA_DIR / f"{code_tag}_monthly_dca_report.md"

    yearly.round(4).to_csv(yearly_path, index=False)
    dividends.round(4).to_csv(div_path, index=False)

    lines = [
        f"# {summary['code']} 月定投测算",
        "",
        "## 假设",
        "",
        f"- 区间：{summary['start']} 至 {summary['end']}",
        f"- 每月投入：{summary['monthly_amount']:,.0f} 元",
        "- 买入日：每月第一个可交易日，按收盘价成交",
        "- 费用：未计交易佣金、税费、滑点",
        "- 分红：同时给出现金留存和除息日收盘价再投两个口径",
        "",
        "## 汇总",
        "",
        f"- 累计投入：{summary['invested']:,.2f} 元（{summary['months']} 个月）",
        f"- 最新收盘价：{summary['last_close']:.3f}",
        f"- 现金分红口径：资产 {summary['asset_cash_mode']:,.2f} 元，"
        f"收益 {summary['profit_cash_mode']:,.2f} 元，"
        f"收益率 {summary['return_cash_mode_pct']:.2f}%，"
        f"累计现金分红 {summary['cash_dividend_cum']:,.2f} 元",
        f"- 分红再投口径：资产 {summary['asset_reinvest_mode']:,.2f} 元，"
        f"收益 {summary['profit_reinvest_mode']:,.2f} 元，"
        f"收益率 {summary['return_reinvest_mode_pct']:.2f}%",
        "",
        "## 年度表",
        "",
        yearly.round(2).to_markdown(index=False),
        "",
        "## 分红明细",
        "",
        dividends.round(4).to_markdown(index=False) if not dividends.empty else "无分红记录",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate monthly ETF DCA results.")
    parser.add_argument("--code", default="515180.SH", help="ETF ts_code, e.g. 515180.SH")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--monthly", type=float, default=10000.0)
    args = parser.parse_args()

    code = _normalize_code(args.code)
    summary, yearly, dividends = calculate_etf_dca(code, args.start, args.end, args.monthly)
    report_path = write_report(summary, yearly, dividends)

    print(f"{summary['code']} 月定投测算完成")
    print(f"区间: {summary['start']} ~ {summary['end']}")
    print(f"累计投入: {summary['invested']:,.2f} 元 ({summary['months']} 个月)")
    print(
        "现金分红口径: "
        f"资产 {summary['asset_cash_mode']:,.2f} 元, "
        f"收益 {summary['profit_cash_mode']:,.2f} 元, "
        f"收益率 {summary['return_cash_mode_pct']:.2f}%, "
        f"累计分红 {summary['cash_dividend_cum']:,.2f} 元"
    )
    print(
        "分红再投口径: "
        f"资产 {summary['asset_reinvest_mode']:,.2f} 元, "
        f"收益 {summary['profit_reinvest_mode']:,.2f} 元, "
        f"收益率 {summary['return_reinvest_mode_pct']:.2f}%"
    )
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
