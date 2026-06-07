"""
指数策略回测对比工具
===================
支持多指数「买入持有 + 股息再投资」回测，输出逐年净值、分阶段年化、最大回撤、夏普比率。

快速使用:
    python analysis/index_backtest.py

自定义:
    修改文件末尾 __main__ 区块中的 START / END / INITIAL / STRATEGIES 即可。

依赖:
    pip install tushare pandas numpy
    需在 config/settings.py 中配置 tushare_cfg.token，
    或直接在本文件 TUSHARE_TOKEN 变量中填入 token。
"""

import sys
import time
import os
import pandas as pd
import numpy as np

# ── token 配置（优先从 config/settings 读，读不到则用环境变量或直接填）──
TUSHARE_TOKEN = ""  # 直接填 token，或留空走下面自动读取

def _get_pro():
    import tushare as ts
    token = TUSHARE_TOKEN
    if not token:
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from config.settings import tushare_cfg
            token = tushare_cfg.token
        except Exception:
            token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("请配置 tushare token：设置 TUSHARE_TOKEN 变量或环境变量 TUSHARE_TOKEN")
    ts.set_token(token)
    return ts.pro_api()


# ══════════════════════════════════════════════════════
#  核心计算函数
# ══════════════════════════════════════════════════════

def calc_max_dd(nav: pd.Series) -> float:
    """最大回撤（%，负值）"""
    return float(((nav - nav.cummax()) / nav.cummax() * 100).min())


def calc_sharpe(nav: pd.Series, rf: float = 0.025) -> float:
    """年化夏普比率（无风险利率默认 2.5%）"""
    dr = nav.pct_change().dropna()
    ar = (1 + dr.mean()) ** 252 - 1
    av = dr.std() * (252 ** 0.5)
    return float((ar - rf) / av) if av > 0 else 0.0


def fetch_index_daily(pro, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取指数日线，返回 trade_date(str) + close 的 DataFrame，按日期升序。"""
    df = pro.index_daily(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="trade_date,close"
    )
    if df is None or df.empty:
        raise ValueError(f"拉取 {ts_code} 失败或无数据")
    return df.sort_values("trade_date").reset_index(drop=True)


def build_hs300_div_map(pro, start_date: str, end_date: str, extra: float = 0.0) -> dict:
    """
    用「沪深300全收益 - 沪深300价格」推算历年股息率，再加 extra 偏移。
    返回 {year: 股息率%} 字典。
    """
    tr = fetch_index_daily(pro, "H00300.CSI", start_date, end_date)
    pr = fetch_index_daily(pro, "000300.SH",  start_date, end_date)
    time.sleep(0.1)

    for d in [tr, pr]:
        d["trade_date"] = pd.to_datetime(d["trade_date"])
        d.set_index("trade_date", inplace=True)

    res = {}
    for yr in range(int(start_date[:4]) - 1, int(end_date[:4]) + 1):
        te = tr[tr.index.year <= yr]["close"].iloc[-1] if len(tr[tr.index.year <= yr]) > 0 else None
        tb = tr[tr.index.year <= yr - 1]["close"].iloc[-1] if len(tr[tr.index.year <= yr - 1]) > 0 else None
        pe = pr[pr.index.year <= yr]["close"].iloc[-1] if len(pr[pr.index.year <= yr]) > 0 else None
        pb = pr[pr.index.year <= yr - 1]["close"].iloc[-1] if len(pr[pr.index.year <= yr - 1]) > 0 else None
        if all(v and v > 0 for v in [te, tb, pe, pb]):
            res[yr] = round((te / tb - 1) * 100 - (pe / pb - 1) * 100 + extra, 2)
    return res


# ══════════════════════════════════════════════════════
#  回测引擎
# ══════════════════════════════════════════════════════

def sim_with_div(df_in: pd.DataFrame, div_dict: dict, label: str,
                 start: str, end: str, initial: float = 1_000_000) -> dict:
    """
    价格指数 + 年末股息再投资 → 模拟全收益。

    Parameters
    ----------
    df_in    : 包含 trade_date(str) 和 close 列的 DataFrame
    div_dict : {year: 股息率%}，例如 {2020: 3.2, 2021: 2.8}
    label    : 策略名称
    start    : 回测起始日 'YYYYMMDD'
    end      : 回测结束日 'YYYYMMDD'
    initial  : 初始资金（元）
    """
    df = df_in[["trade_date", "close"]].copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = (df.set_index("trade_date")
            .rename(columns={"close": "price"})
            .loc[lambda x: (x.index >= start) & (x.index <= end)])

    p0 = df["price"].iloc[0]
    shares = initial / p0
    nav_list = []

    for date, row in df.iterrows():
        price = row["price"]
        nav_list.append({"date": date, "nav": shares * price})
        yr_data = df[df.index.year == date.year]
        if not yr_data.empty and date == yr_data.index[-1]:
            dy = div_dict.get(date.year, 2.5) / 100
            shares += (shares * price * dy) / price   # 再买入等值份额

    nav = pd.DataFrame(nav_list).set_index("date")["nav"]
    return _build_result(nav, label, initial)


def sim_idx(df_in: pd.DataFrame, label: str,
            start: str, end: str, initial: float = 1_000_000) -> dict:
    """
    全收益指数直接买入持有（指数本身已含股息再投）。

    Parameters
    ----------
    df_in  : 包含 trade_date(str) 和 close 列的 DataFrame
    label  : 策略名称
    start  : 回测起始日 'YYYYMMDD'
    end    : 回测结束日 'YYYYMMDD'
    initial: 初始资金（元）
    """
    df = df_in[["trade_date", "close"]].copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = (df.set_index("trade_date")
            .loc[lambda x: (x.index >= start) & (x.index <= end)])

    p0 = df["close"].iloc[0]
    nav = df["close"] / p0 * initial
    return _build_result(nav, label, initial)


def _build_result(nav: pd.Series, label: str, initial: float) -> dict:
    fv = nav.iloc[-1]
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = ((fv / initial) ** (1 / yrs) - 1) * 100
    return {
        "label":     label,
        "final_val": fv,
        "total_ret": (fv / initial - 1) * 100,
        "cagr":      cagr,
        "max_dd":    calc_max_dd(nav),
        "sharpe":    calc_sharpe(nav),
        "yrs":       yrs,
        "nav":       nav,
        "yr_snap":   {d.year: v for d, v in nav.items()},  # 每交易日的年份末净值（取最后出现值）
    }


def stage_cagr(result: dict, y1: int, y2: int, initial: float = 1_000_000) -> float | None:
    """计算 y1~y2 年的区间年化收益率（%）"""
    v1 = result["yr_snap"].get(y1 - 1, initial)
    v2 = result["yr_snap"].get(y2)
    if not v1 or not v2 or v1 <= 0:
        return None
    return ((v2 / v1) ** (1 / (y2 - y1 + 1)) - 1) * 100


# ══════════════════════════════════════════════════════
#  打印报告
# ══════════════════════════════════════════════════════

def print_report(results: list[dict], start: str, end: str, initial: float = 1_000_000,
                 stages: list[tuple] | None = None):
    """
    打印完整回测报告：汇总表 + 逐年净值 + 分阶段年化。

    Parameters
    ----------
    results : sim_with_div / sim_idx 返回值列表
    start   : 起始日 'YYYYMMDD'
    end     : 结束日 'YYYYMMDD'
    initial : 初始资金
    stages  : [(名称, y1, y2), ...] 分阶段区间，None 则自动生成
    """
    yrs = results[0]["yrs"]
    print(f"\n{'='*90}")
    print(f"  📊 {len(results)}策略对比 — {start} ~ {end}（{yrs:.1f}年）初始{initial/10000:.0f}万")
    print(f"{'='*90}")
    print(f"  {'策略':42} {'最终市值':>12} {'总收益':>9} {'年化CAGR':>9} {'最大回撤':>9} {'夏普':>6}")
    print("  " + "-" * 86)
    for r in results:
        print(
            f"  {r['label'][:42]:42} {r['final_val']:>12,.0f}"
            f" {r['total_ret']:>+8.1f}%  {r['cagr']:>7.2f}%"
            f"  {r['max_dd']:>7.1f}%  {r['sharpe']:>5.2f}"
        )

    # 逐年净值
    all_years = sorted({yr for r in results for yr in r["yr_snap"]})
    w = 13
    hdr = "  " + f"{'年':4}  " + "  ".join(f"{r['label'][:w]:>{w}}" for r in results)
    print(f"\n{'='*max(80, len(hdr))}")
    print("  📅 逐年年末净值")
    print("=" * max(80, len(hdr)))
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for yr in all_years:
        vs = [r["yr_snap"].get(yr, 0) for r in results]
        ps = [r["yr_snap"].get(yr - 1, initial) for r in results]
        ys = [(v / p - 1) * 100 if p else 0 for v, p in zip(vs, ps)]
        best = max(vs)
        parts = []
        for v, y in zip(vs, ys):
            m = "🏆" if v == best else "  "
            parts.append(f"{m}{v/10000:>4.0f}万{y:>+5.1f}%")
        print(f"  {yr}  " + "  ".join(parts))

    # 分阶段
    if stages is None:
        y_min = min(all_years)
        y_max = max(all_years)
        stages = [(f"{y_min}~{y_max} 全周期", y_min, y_max)]

    print(f"\n{'='*70}")
    print("  📈 分阶段年化收益")
    print("=" * 70)
    lbl_short = [r["label"][:8] for r in results]
    print(f"  {'阶段':18}  " + "  ".join(f"{l:>9}" for l in lbl_short))
    print("  " + "-" * 66)
    for name, y1, y2 in stages:
        vals = [stage_cagr(r, y1, y2, initial) for r in results]
        bv = max((v for v in vals if v is not None), default=0)
        parts = []
        for v in vals:
            if v is None:
                parts.append("    N/A  ")
            else:
                parts.append(f"{v:>+6.2f}%{'🏆' if abs(v - bv) < 0.01 else '  '}")
        print(f"  {name:<18}  " + "  ".join(parts))


# ══════════════════════════════════════════════════════
#  预置策略配置
# ══════════════════════════════════════════════════════

# 常用指数代码速查
INDEX_CODES = {
    # 中证系列
    "中证800现金流":   "932368.CSI",
    "沪深300现金流":   "932366.CSI",
    "中证全指现金流":  "932365.CSI",
    "中证A500现金流":  "931082.CSI",
    "中证红利全收益":  "H00922.CSI",
    "沪深300全收益":   "H00300.CSI",
    "沪深300价格":     "000300.SH",
    "中证500全收益":   "H00905.CSI",
    # 国证系列
    "国证自由现金流全收益": "480092.CNI",
    "国证自由现金流价格":   "980092.CNI",
}

# 各现金流指数相对沪深300的额外股息率偏移（百分点）
DIV_EXTRA = {
    "932368.CSI": 0.7,   # 中证800现金流（稍高于沪深300）
    "932366.CSI": 0.5,   # 沪深300现金流（接近沪深300）
    "932365.CSI": 0.8,   # 中证全指现金流（更多中小盘，略高）
    "931082.CSI": 0.6,   # 中证A500现金流
    "980092.CNI": 0.7,   # 国证自由现金流价格
}

FULL_RETURN_CODES = {
    "H00922.CSI",   # 中证红利全收益
    "H00300.CSI",   # 沪深300全收益
    "H00905.CSI",   # 中证500全收益
    "480092.CNI",   # 国证自由现金流全收益
}


# ══════════════════════════════════════════════════════
#  一键运行入口
# ══════════════════════════════════════════════════════

def run_backtest(
    strategies: list[dict],
    start: str = "20160613",
    end:   str = "20260606",
    initial: float = 1_000_000,
    stages: list[tuple] | None = None,
    api_sleep: float = 0.15,
):
    """
    一键运行多策略回测并打印报告。

    strategies 格式（列表，每项为 dict）:
        {
            "ts_code":  "932368.CSI",   # tushare 指数代码
            "label":    "中证800现金流",  # 显示名称
            "mode":     "div",           # "div" = 价格+估算股息, "idx" = 全收益直接用
            "div_extra": 0.7,            # mode="div" 时有效，相对沪深300的额外股息率偏移
        }

    示例:
        run_backtest([
            {"ts_code": "932368.CSI", "label": "中证800现金流", "mode": "div", "div_extra": 0.7},
            {"ts_code": "480092.CNI", "label": "国证自由现金流全收益", "mode": "idx"},
            {"ts_code": "H00922.CSI", "label": "中证红利全收益", "mode": "idx"},
            {"ts_code": "H00300.CSI", "label": "沪深300全收益", "mode": "idx"},
        ])
    """
    pro = _get_pro()

    # 先拉沪深300全收益和价格（用于推算股息率，mode=div 时需要）
    need_div = any(s.get("mode", "div") == "div" for s in strategies)
    div_cache = {}
    if need_div:
        print("计算沪深300历年股息率...")
        hs_tr_df = fetch_index_daily(pro, "H00300.CSI", start, end)
        hs_pr_df = fetch_index_daily(pro, "000300.SH",  start, end)
        time.sleep(api_sleep)
        # 临时存入，build_hs300_div_map 逻辑复用
        for extra in set(s.get("div_extra", 0) for s in strategies if s.get("mode") == "div"):
            div_cache[extra] = _calc_div_from_dfs(hs_tr_df, hs_pr_df, start, end, extra)

    # 拉各指数日线数据
    print("拉取指数日线数据...")
    df_cache = {}
    for s in strategies:
        code = s["ts_code"]
        if code not in df_cache:
            print(f"  {s['label']}({code})...")
            df_cache[code] = fetch_index_daily(pro, code, start, end)
            time.sleep(api_sleep)

    # 运行回测
    results = []
    for s in strategies:
        code  = s["ts_code"]
        label = s["label"]
        mode  = s.get("mode", "idx")
        if mode == "div":
            extra = s.get("div_extra", 0.0)
            div_d = div_cache.get(extra, {})
            r = sim_with_div(df_cache[code], div_d, label, start, end, initial)
        else:
            r = sim_idx(df_cache[code], label, start, end, initial)
        results.append(r)

    print_report(results, start, end, initial, stages)
    return results


def _calc_div_from_dfs(tr_df, pr_df, start, end, extra=0.0):
    """从全收益/价格 df 推算各年股息率"""
    tr = tr_df.copy(); pr = pr_df.copy()
    for d in [tr, pr]:
        d["trade_date"] = pd.to_datetime(d["trade_date"])
        d.set_index("trade_date", inplace=True)
    res = {}
    for yr in range(int(start[:4]) - 1, int(end[:4]) + 1):
        te = tr[tr.index.year <= yr]["close"].iloc[-1] if len(tr[tr.index.year <= yr]) > 0 else None
        tb = tr[tr.index.year <= yr - 1]["close"].iloc[-1] if len(tr[tr.index.year <= yr - 1]) > 0 else None
        pe = pr[pr.index.year <= yr]["close"].iloc[-1] if len(pr[pr.index.year <= yr]) > 0 else None
        pb = pr[pr.index.year <= yr - 1]["close"].iloc[-1] if len(pr[pr.index.year <= yr - 1]) > 0 else None
        if all(v and v > 0 for v in [te, tb, pe, pb]):
            res[yr] = round((te / tb - 1) * 100 - (pe / pb - 1) * 100 + extra, 2)
    return res


# ══════════════════════════════════════════════════════
#  直接运行示例
# ══════════════════════════════════════════════════════

if __name__ == "__main__":

    # ── 参数区（按需修改）──────────────────────────────
    START   = "20160613"   # 回测起始日
    END     = "20260606"   # 回测结束日
    INITIAL = 1_000_000    # 初始资金（元）

    STRATEGIES = [
        {"ts_code": "932368.CSI", "label": "中证800现金流（+股息）",     "mode": "div", "div_extra": 0.7},
        {"ts_code": "932366.CSI", "label": "沪深300现金流（+股息）",     "mode": "div", "div_extra": 0.5},
        {"ts_code": "932365.CSI", "label": "中证全指现金流（+股息）",    "mode": "div", "div_extra": 0.8},
        {"ts_code": "480092.CNI", "label": "国证自由现金流全收益",        "mode": "idx"},
        {"ts_code": "H00922.CSI", "label": "中证红利全收益 H00922",      "mode": "idx"},
        {"ts_code": "H00300.CSI", "label": "沪深300全收益 H00300",       "mode": "idx"},
    ]

    STAGES = [
        ("2016~2017 牛市",   2016, 2017),
        ("2018~2019 熊市",   2018, 2019),
        ("2020~2022 分化期", 2020, 2022),
        ("2023~2026 当前",   2023, 2025),
        ("2016~2026 全周期", 2016, 2025),
    ]
    # ────────────────────────────────────────────────────

    run_backtest(STRATEGIES, start=START, end=END, initial=INITIAL, stages=STAGES)
