"""
fund_dca_calc.py — 基金定投收益计算器
=========================================

标准模板，支持任意基金定投回算，输出：
  1. 定投 vs 一次性投入对比
  2. 分红再投入 vs 分红收现金对比
  3. IRR 年化收益率
  4. 逐年净值 / 分红 / 资产明细

使用方法：
  # 基本用法
  python fund_dca_calc.py --code 515180.SH --start 2020-01-01

  # 自定义月投金额
  python fund_dca_calc.py --code 515180.SH --start 2019-01-01 --monthly 5000

  # 指定结束日期
  python fund_dca_calc.py --code 510300.SH --start 2020-01-01 --end 2025-12-31

  # 分红不投入模式
  python fund_dca_calc.py --code 515180.SH --start 2020-01-01 --no-reinvest
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import tushare as ts

# ── 路径 ──
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════

@dataclass
class DCAConfig:
    """定投配置"""
    ts_code: str           # 基金代码 e.g. "515180.SH"
    start_date: str        # 定投开始日期 "2020-01-01"
    end_date: str          # 定投结束日期 (默认今天)
    monthly_amount: float  # 每月投入金额
    reinvest: bool         # 分红是否再投入
    commission: float      # 申购费率 (暂未计入)

@dataclass
class YearResult:
    """单年结果"""
    year: int
    months: int                    # 当年投入月数
    year_invested: float           # 当年投入
    cum_invested: float            # 累计投入
    end_value_a: float             # 年末市值(再投入)
    end_value_b: float             # 年末市值(收现金)
    year_div: float                # 当年分红
    cum_div_a: float               # 累计分红(再投入)
    cum_div_b: float               # 累计分红(收现金)
    start_nav: float               # 年初单位净值
    end_nav: float                 # 年末单位净值
    start_adj: float               # 年初复权净值
    end_adj: float                 # 年末复权净值

@dataclass
class DCAResult:
    """定投完整结果"""
    config: DCAConfig
    n_months: int                   # 总定投月数
    total_invested: float           # 累计投入

    # 方案A: 分红再投入
    shares_a: float                 # 持有份额(再投入)
    value_a: float                  # 最终市值(再投入)
    total_div_a: float              # 累计分红(再投入)

    # 方案B: 分红收现金
    shares_b: float                 # 持有份额(收现金)
    value_b: float                  # 最终市值(收现金)
    total_div_b: float              # 累计分红(收现金)

    # 方案C: 一次性投入(同总金额, 分红再投入)
    shares_c: float
    value_c: float

    # IRR
    monthly_irr: float
    annual_irr: float

    # 一次性投入年化
    lump_cagr: float

    # 逐年
    yearly: list[YearResult] = field(default_factory=list)

    # 原始数据
    fund_name: str = ""
    start_nav: float = 0.0
    end_nav: float = 0.0
    start_adj: float = 0.0
    end_adj: float = 0.0


# ═══════════════════════════════════════════════════════
#  数据获取
# ═══════════════════════════════════════════════════════

def init_tushare():
    """初始化 tushare pro"""
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


def fetch_nav(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """获取基金净值(去重)"""
    df = pro.fund_nav(ts_code=ts_code, start_date=start.replace("-", ""),
                      end_date=end.replace("-", ""))
    df = df.drop_duplicates(subset="nav_date", keep="last")
    df = df.sort_values("nav_date").reset_index(drop=True)
    df["nav_date"] = df["nav_date"].astype(str)
    df["dt"] = pd.to_datetime(df["nav_date"])
    return df


def fetch_dividends(pro, ts_code: str, start: str, end: str) -> dict[str, float]:
    """获取基金分红(去重), 返回 {ex_date: div_cash}"""
    div = pro.fund_div(ts_code=ts_code)
    div = div[div["ex_date"].astype(str) >= start.replace("-", "")]
    div = div[div["ex_date"].astype(str) <= end.replace("-", "")]
    div = div.sort_values("ex_date").drop_duplicates(subset="ex_date", keep="first")
    result = {}
    for _, r in div.iterrows():
        dc = float(r["div_cash"]) if pd.notna(r["div_cash"]) else 0.0
        result[str(r["ex_date"])] = dc
    return result


def fetch_fund_name(pro, ts_code: str) -> str:
    """获取基金名称"""
    try:
        info = pro.fund_basic(ts_code=ts_code, fields="ts_code,name")
        if not info.empty:
            return info.iloc[0]["name"]
    except Exception:
        pass
    return ts_code


# ═══════════════════════════════════════════════════════
#  核心计算
# ═══════════════════════════════════════════════════════

def calc_monthly_first_dates(df: pd.DataFrame) -> set[str]:
    """找出每月第一个交易日"""
    df["ym"] = df["dt"].dt.to_period("M")
    return set(df.groupby("ym").first()["nav_date"].values)


def calc_irr(cashflows: list[tuple[int, float]]) -> float:
    """
    计算月度IRR (二分法)
    cashflows: [(月份偏移, 金额), ...]  负=投入, 正=回收
    """
    def npv(rate, cfs):
        return sum(amt / (1 + rate) ** t for t, amt in cfs)

    lo, hi = -0.5, 2.0
    for _ in range(300):
        mid = (lo + hi) / 2
        if npv(mid, cashflows) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def run_dca(config: DCAConfig) -> DCAResult:
    """执行定投模拟"""
    pro = init_tushare()

    # 获取数据
    fund_name = fetch_fund_name(pro, config.ts_code)
    df = fetch_nav(pro, config.ts_code, config.start_date, config.end_date)
    div_dict = fetch_dividends(pro, config.ts_code, config.start_date, config.end_date)
    monthly_first_dates = calc_monthly_first_dates(df)

    n_months = len(monthly_first_dates)
    if n_months == 0:
        raise ValueError(f"无交易日数据: {config.ts_code} {config.start_date}~{config.end_date}")

    final_nav = float(df.iloc[-1]["unit_nav"])
    first_nav = float(df.iloc[0]["unit_nav"])
    first_adj = float(df.iloc[0]["adj_nav"]) if pd.notna(df.iloc[0]["adj_nav"]) else first_nav
    final_adj = float(df.iloc[-1]["adj_nav"]) if pd.notna(df.iloc[-1]["adj_nav"]) else final_nav

    # ── 方案A: 定投+分红再投入 ──
    shares_a = 0.0
    total_invested = 0.0
    total_div_a = 0.0
    cashflows_irr = []  # (月份偏移, 金额)

    # ── 方案B: 定投+分红收现金 ──
    shares_b = 0.0
    total_div_b = 0.0

    # ── 方案C: 一次性投入+分红再投入 ──
    # 先不知道总投入金额，跑完A后再算
    # 预跑一遍拿总投入
    temp_invested = 0
    for _, row in df.iterrows():
        if row["nav_date"] in monthly_first_dates:
            temp_invested += config.monthly_amount
    lump_sum = temp_invested  # 一次性投入同金额
    shares_c = lump_sum / first_nav
    for _, row in df.iterrows():
        if row["nav_date"] in div_dict:
            shares_c += shares_c * div_dict[row["nav_date"]] / float(row["unit_nav"])

    # ── 逐年跟踪 ──
    cur_year = None
    yearly: list[YearResult] = []
    cur_year_invested = 0.0
    cur_year_div_a = 0.0
    cur_year_div_b = 0.0
    cur_year_months = 0
    cum_invested = 0.0
    cum_div_a = 0.0
    cum_div_b = 0.0
    prev_year_data = None

    ref_date = df.iloc[0]["dt"]

    for _, row in df.iterrows():
        date = row["nav_date"]
        nav = float(row["unit_nav"])
        year = int(date[:4])

        # 新年份 → 保存上一年
        if year != cur_year:
            if cur_year is not None and prev_year_data is not None:
                yr = YearResult(
                    year=cur_year,
                    months=cur_year_months,
                    year_invested=cur_year_invested,
                    cum_invested=cum_invested,
                    end_value_a=shares_a * prev_nav,
                    end_value_b=shares_b * prev_nav,
                    year_div=cur_year_div_a,
                    cum_div_a=cum_div_a,
                    cum_div_b=cum_div_b,
                    start_nav=prev_year_data["start_nav"],
                    end_nav=prev_nav,
                    start_adj=prev_year_data["start_adj"],
                    end_adj=prev_year_data.get("end_adj", prev_nav),
                )
                yearly.append(yr)
            cur_year = year
            cur_year_invested = 0.0
            cur_year_div_a = 0.0
            cur_year_div_b = 0.0
            cur_year_months = 0
            prev_year_data = {
                "start_nav": nav,
                "start_adj": float(row["adj_nav"]) if pd.notna(row["adj_nav"]) else nav,
            }
        prev_nav = nav

        # 分红
        if date in div_dict:
            div_per = div_dict[date]
            # A: 再投入
            div_amt_a = shares_a * div_per
            total_div_a += div_amt_a
            cur_year_div_a += div_amt_a
            cum_div_a += div_amt_a
            shares_a += div_amt_a / nav
            # B: 收现金
            div_amt_b = shares_b * div_per
            total_div_b += div_amt_b
            cur_year_div_b += div_amt_b
            cum_div_b += div_amt_b

        # 每月定投
        if date in monthly_first_dates:
            buy = config.monthly_amount / nav
            shares_a += buy
            shares_b += buy
            total_invested += config.monthly_amount
            cur_year_invested += config.monthly_amount
            cum_invested += config.monthly_amount
            cur_year_months += 1
            # IRR现金流
            month_offset = (row["dt"] - ref_date).days / 30.44
            cashflows_irr.append((month_offset, -config.monthly_amount))

    # 最后一年
    if cur_year is not None and prev_year_data is not None:
        yr = YearResult(
            year=cur_year,
            months=cur_year_months,
            year_invested=cur_year_invested,
            cum_invested=cum_invested,
            end_value_a=shares_a * final_nav,
            end_value_b=shares_b * final_nav,
            year_div=cur_year_div_a,
            cum_div_a=cum_div_a,
            cum_div_b=cum_div_b,
            start_nav=prev_year_data["start_nav"],
            end_nav=final_nav,
            start_adj=prev_year_data["start_adj"],
            end_adj=final_adj,
        )
        yearly.append(yr)

    # 最终回收
    value_a = shares_a * final_nav
    value_b = shares_b * final_nav
    value_c = shares_c * final_nav

    # IRR
    end_month_offset = (df.iloc[-1]["dt"] - ref_date).days / 30.44
    cashflows_irr.append((end_month_offset, value_a))
    monthly_irr = calc_irr(cashflows_irr)
    annual_irr = (1 + monthly_irr) ** 12 - 1

    # 一次性投入CAGR
    years = n_months / 12
    lump_cagr = ((value_c / lump_sum) ** (1 / years) - 1) * 100 if years > 0 else 0

    return DCAResult(
        config=config,
        n_months=n_months,
        total_invested=total_invested,
        shares_a=shares_a,
        value_a=value_a,
        total_div_a=total_div_a,
        shares_b=shares_b,
        value_b=value_b,
        total_div_b=total_div_b,
        shares_c=shares_c,
        value_c=value_c,
        monthly_irr=monthly_irr,
        annual_irr=annual_irr,
        lump_cagr=lump_cagr,
        yearly=yearly,
        fund_name=fund_name,
        start_nav=first_nav,
        end_nav=final_nav,
        start_adj=first_adj,
        end_adj=final_adj,
    )


# ═══════════════════════════════════════════════════════
#  报告输出
# ═══════════════════════════════════════════════════════

def print_report(r: DCAResult):
    """打印完整报告"""
    c = r.config
    W = 80

    print(f"\n{'=' * W}")
    print(f"  📊 {r.fund_name} ({c.ts_code}) 定投收益计算")
    print(f"  📅 定投期间: {c.start_date} ~ {c.end_date}  |  每月: {c.monthly_amount:,.0f} 元")
    print(f"{'=' * W}")

    # ── 1. 净值概况 ──
    print(f"\n  ── 1. 基金净值概况 ──")
    nav_chg = (r.end_nav - r.start_nav) / r.start_nav * 100
    adj_chg = (r.end_adj - r.start_adj) / r.start_adj * 100
    print(f"  {'项目':>16}  {'数值':>10}  {'说明':>30}")
    print(f"  {'─'*16}  {'─'*10}  {'─'*30}")
    print(f"  {'起始单位净值':>16}  {r.start_nav:>10.4f}")
    print(f"  {'当前单位净值':>16}  {r.end_nav:>10.4f}")
    print(f"  {'起始复权净值':>16}  {r.start_adj:>10.4f}")
    print(f"  {'当前复权净值':>16}  {r.end_adj:>10.4f}")
    print(f"  {'单位净值涨幅':>16}  {nav_chg:>+9.1f}%  {'不含分红'}")
    print(f"  {'复权净值涨幅':>16}  {adj_chg:>+9.1f}%  {'含分红再投入'}")
    total_div_per_share = r.end_adj - r.end_nav - (r.start_adj - r.start_nav)
    print(f"  {'累计每股分红':>16}  {total_div_per_share:>10.4f}  {'≈复权-单位差额'}")

    # ── 2. 三种方案对比 ──
    print(f"\n  ── 2. 三种方案对比 ──")
    total_b = r.value_b + r.total_div_b  # B总资产=市值+分红现金
    print(f"  {'项目':>18}  {'定投+分红再投':>14}  {'定投+分红收现':>14}  {'一次投入+再投':>14}")
    print(f"  {'─'*18}  {'─'*14}  {'─'*14}  {'─'*14}")
    print(f"  {'累计投入':>18}  {r.total_invested:>14,.0f}  {r.total_invested:>14,.0f}  {r.total_invested:>14,.0f}")
    print(f"  {'持有份额':>18}  {r.shares_a:>14,.2f}  {r.shares_b:>14,.2f}  {r.shares_c:>14,.2f}")
    print(f"  {'当前市值':>18}  {r.value_a:>14,.0f}  {r.value_b:>14,.0f}  {r.value_c:>14,.0f}")
    print(f"  {'累计分红':>18}  {r.total_div_a:>14,.0f}  {r.total_div_b:>14,.0f}  {'—':>14}")
    print(f"  {'总资产':>18}  {r.value_a:>14,.0f}  {total_b:>14,.0f}  {r.value_c:>14,.0f}")
    print(f"  {'总收益':>18}  {r.value_a-r.total_invested:>+14,.0f}  {total_b-r.total_invested:>+14,.0f}  {r.value_c-r.total_invested:>+14,.0f}")
    ret_a = (r.value_a - r.total_invested) / r.total_invested * 100
    ret_b = (total_b - r.total_invested) / r.total_invested * 100
    ret_c = (r.value_c - r.total_invested) / r.total_invested * 100
    print(f"  {'收益率(累计)':>18}  {ret_a:>+13.1f}%  {ret_b:>+13.1f}%  {ret_c:>+13.1f}%")

    # ── 3. IRR年化 ──
    print(f"\n  ── 3. 年化收益率 ──")
    print(f"  {'方法':>24}  {'年化':>8}")
    print(f"  {'─'*24}  {'─'*8}")
    print(f"  {'定投 IRR':>24}  {r.annual_irr*100:>7.2f}%")
    print(f"  {'一次性投入 CAGR':>24}  {r.lump_cagr:>7.2f}%")

    # ── 4. 逐年明细 ──
    print(f"\n  ── 4. 逐年明细 ──")
    print(f"  {'年份':>4}  {'月数':>4}  {'累计投入':>9}  {'年末市值(再投)':>14}  {'年末市值(收现)':>14}  {'当年分红':>9}  {'分红率':>7}  {'净值涨幅':>8}")
    print(f"  {'─'*4}  {'─'*4}  {'─'*9}  {'─'*14}  {'─'*14}  {'─'*9}  {'─'*7}  {'─'*8}")

    prev_nav = r.start_nav
    for yr in r.yearly:
        avg_val = (yr.end_value_a + yr.end_value_b) / 2 if yr.end_value_b > 0 else yr.end_value_a
        div_rate = yr.year_div / avg_val * 100 if avg_val > 0 else 0
        nav_chg = (yr.end_nav - yr.start_nav) / yr.start_nav * 100 if yr.start_nav > 0 else 0
        print(f"  {yr.year:>4}  {yr.months:>4}  {yr.cum_invested:>9,.0f}  {yr.end_value_a:>14,.0f}  {yr.end_value_b:>14,.0f}  {yr.year_div:>9,.0f}  {div_rate:>6.2f}%  {nav_chg:>+7.1f}%")
        prev_nav = yr.end_nav

    # ── 5. 关键结论 ──
    print(f"\n  ── 5. 关键结论 ──")
    reinvest_diff = r.value_a - total_b
    print(f"  · 定投{r.n_months}个月，累计投入{r.total_invested:,.0f}元")
    print(f"  · 分红再投入比收现金多赚 {reinvest_diff:,.0f} 元")
    print(f"  · 定投IRR年化 {r.annual_irr*100:.2f}%，一次性投入年化 {r.lump_cagr:.2f}%")
    if r.annual_irr*100 < r.lump_cagr:
        print(f"  · 定投年化低于一次性投入，因后期资金在场时间短")
    else:
        print(f"  · 定投年化高于一次性投入，因下跌时攒了更多便宜份额")
    print(f"  · 基金复权涨幅 {adj_chg:+.1f}% ≠ 定投收益率 {ret_a:+.1f}%（资金非全程在场）")

    print(f"\n{'=' * W}")


# ═══════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="基金定投收益计算器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 易方达红利ETF，2020年起每月定投1万
  python fund_dca_calc.py --code 515180.SH --start 2020-01-01

  # 沪深300ETF，2019年起每月5千
  python fund_dca_calc.py --code 510300.SH --start 2019-01-01 --monthly 5000

  # 指定结束日期
  python fund_dca_calc.py --code 515180.SH --start 2020-01-01 --end 2025-12-31
        """,
    )

    parser.add_argument("--code", required=True, help="基金代码 e.g. 515180.SH")
    parser.add_argument("--start", required=True, help="定投开始日期 e.g. 2020-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="定投结束日期 (默认今天)")
    parser.add_argument("--monthly", type=float, default=10000, help="每月投入金额 (默认: 10000)")
    parser.add_argument("--no-reinvest", action="store_true", help="分红不投入（默认再投入）")

    args = parser.parse_args()

    config = DCAConfig(
        ts_code=args.code,
        start_date=args.start,
        end_date=args.end,
        monthly_amount=args.monthly,
        reinvest=not args.no_reinvest,
        commission=0.0,
    )

    result = run_dca(config)
    print_report(result)


if __name__ == "__main__":
    main()
