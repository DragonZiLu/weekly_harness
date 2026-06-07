"""
基金 & 指数周期跟踪工具
========================
用途：每周、每月、每季度一键生成跟踪报告，包含：
  - 近 1周 / 1月 / 3月 / 6月 / YTD / 1年 / 3年 收益
  - 最大回撤、夏普比率、波动率
  - 持仓（季度）Top10 明细（仅 ETF）
  - 自动保存 Markdown 到 docs/tracker/

快速使用:
    python analysis/fund_tracker.py               # 快照（默认截至今天）
    python analysis/fund_tracker.py --mode weekly  # 周报（近4周逐周）
    python analysis/fund_tracker.py --mode monthly # 月报（近12月）
    python analysis/fund_tracker.py --mode quarter # 季报（持仓+指标）
    python analysis/fund_tracker.py --end 20250930 # 指定截止日期
    python analysis/fund_tracker.py --no-save      # 不保存文件，仅打印

配置：编辑 analysis/tracker_config.py 管理跟踪标的列表。
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs" / "tracker"

# ── token 读取 ────────────────────────────────────────────────
def _get_pro():
    import tushare as ts
    try:
        from config.settings import tushare_cfg
        token = tushare_cfg.token
    except Exception:
        token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("请配置 tushare token：config/settings.py 或环境变量 TUSHARE_TOKEN")
    ts.set_token(token)
    return ts.pro_api()


# ══════════════════════════════════════════════════════
#  数据获取
# ══════════════════════════════════════════════════════

def fetch_index_daily(pro, code: str, start: str, end: str) -> pd.Series:
    df = pro.index_daily(ts_code=code, start_date=start, end_date=end,
                         fields="trade_date,close")
    time.sleep(0.12)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date")["close"].sort_index()


def fetch_etf_nav(pro, code: str, start: str, end: str) -> pd.Series:
    df = pro.fund_daily(ts_code=code, start_date=start, end_date=end,
                        fields="trade_date,close")
    time.sleep(0.12)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date")["close"].sort_index()


def load_strategy_nav(nav_path: str, start: str, end: str) -> pd.Series:
    """加载自建策略季度 nav（季度节点序列）"""
    path = ROOT / nav_path
    if not path.exists():
        print(f"  [WARN] 策略文件不存在: {nav_path}")
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    df["rb_date"] = pd.to_datetime(df["rb_date"])
    df["next_rb"] = pd.to_datetime(df["next_rb"])
    rows = [{"date": r["rb_date"], "nav": r["nav"]} for _, r in df.iterrows()]
    # 末期：用 next_rb 作为当前持仓的最终节点（含当前期收益）
    last = df.iloc[-1]
    # period_ret 是本期收益（百分比），nav 是本期开始的净值
    if "period_ret" in df.columns and not pd.isna(last["period_ret"]):
        end_nav = last["nav"] * (1 + last["period_ret"] / 100)
    else:
        end_nav = last["nav"]   # 若本期未结束，用截止当日 nav
    rows.append({"date": last["next_rb"], "nav": end_nav})
    s = pd.DataFrame(rows).set_index("date")["nav"]
    s = s[~s.index.duplicated(keep="last")]  # 去重，保留最后一个
    return s[(s.index >= pd.to_datetime(start)) & (s.index <= pd.to_datetime(end) + pd.Timedelta(days=30))]


# ── 股息补偿 ──────────────────────────────────────────
_DIV_CACHE: dict = {}

def get_hs300_div_by_year(pro) -> dict:
    global _DIV_CACHE
    if _DIV_CACHE:
        return _DIV_CACHE
    tr = fetch_index_daily(pro, "H00300.CSI", "20140101", datetime.today().strftime("%Y%m%d"))
    pr = fetch_index_daily(pro, "000300.SH",  "20140101", datetime.today().strftime("%Y%m%d"))
    res = {}
    for yr in range(2015, datetime.today().year + 1):
        te = tr[tr.index.year <= yr].iloc[-1]  if not tr[tr.index.year<=yr].empty else None
        tb = tr[tr.index.year <= yr-1].iloc[-1] if not tr[tr.index.year<=yr-1].empty else None
        pe = pr[pr.index.year <= yr].iloc[-1]  if not pr[pr.index.year<=yr].empty else None
        pb = pr[pr.index.year <= yr-1].iloc[-1] if not pr[pr.index.year<=yr-1].empty else None
        if all(v is not None and v > 0 for v in [te, tb, pe, pb]):
            res[yr] = round((te/tb - 1)*100 - (pe/pb - 1)*100 + 0.3, 2)
    _DIV_CACHE = res
    return res


def build_nav_series(pro, cfg: dict, start: str, end: str,
                     div_by_year: dict = None) -> pd.Series:
    """根据配置类型构建标准化 nav 序列（起始=1.0）"""
    t = cfg["type"]

    if t == "strategy":
        s = load_strategy_nav(cfg["nav_path"], start, end)
        if s.empty:
            return s
        return s / s.iloc[0]

    elif t == "index_tr":
        s = fetch_index_daily(pro, cfg["code"], start, end)
        if s.empty:
            return s
        return s / s.iloc[0]

    elif t == "etf":
        s = fetch_etf_nav(pro, cfg["code"], start, end)
        if s.empty:
            return s
        return s / s.iloc[0]

    elif t == "index_price":
        s = fetch_index_daily(pro, cfg["code"], start, end)
        if s.empty or div_by_year is None:
            return s / s.iloc[0] if not s.empty else s
        add = cfg.get("div_add", 0.0)
        p0 = s.iloc[0]
        shares = 1.0 / p0
        nav_list = []
        for date, price in s.items():
            nav_list.append({"date": date, "nav": shares * price})
            yr_data = s[s.index.year == date.year]
            if not yr_data.empty and date == yr_data.index[-1]:
                dy = (div_by_year.get(date.year, 2.5) + add) / 100
                shares += shares * price * dy / price
        return pd.DataFrame(nav_list).set_index("date")["nav"]

    return pd.Series(dtype=float)


# ══════════════════════════════════════════════════════
#  策略 nav 辅助：月末/季末插值
# ══════════════════════════════════════════════════════

def strategy_nav_at(qtr_nav: pd.Series, target_date: pd.Timestamp) -> float:
    """
    对季度节点 nav 进行线性插值，估算 target_date 的 nav。
    target_date 落在 [t0, t1] 区间内，按天数线性插值。
    若 target_date >= 最后节点，返回最后一个值。
    若 target_date < 第一个节点，返回 NaN。
    """
    if qtr_nav.empty:
        return float("nan")
    if target_date <= qtr_nav.index[0]:
        return float("nan")
    if target_date >= qtr_nav.index[-1]:
        return float(qtr_nav.iloc[-1])
    # 找左右节点
    left_idx  = qtr_nav.index[qtr_nav.index <= target_date][-1]
    right_idx = qtr_nav.index[qtr_nav.index >  target_date][0]
    v0 = qtr_nav[left_idx]
    v1 = qtr_nav[right_idx]
    span = (right_idx - left_idx).days
    elapsed = (target_date - left_idx).days
    if span == 0:
        return float(v0)
    return float(v0 + (v1 - v0) * elapsed / span)


def strategy_period_ret(qtr_nav: pd.Series, start_date: pd.Timestamp,
                         end_date: pd.Timestamp) -> float:
    """用线性插值估算策略在 [start_date, end_date] 区间的收益（%）"""
    v0 = strategy_nav_at(qtr_nav, start_date)
    v1 = strategy_nav_at(qtr_nav, end_date)
    if np.isnan(v0) or np.isnan(v1) or v0 <= 0:
        return float("nan")
    return (v1 / v0 - 1) * 100


# ══════════════════════════════════════════════════════
#  统计指标
# ══════════════════════════════════════════════════════

def _max_dd(nav: pd.Series) -> float:
    dd = (nav - nav.cummax()) / nav.cummax() * 100
    return float(dd.min())


def _sharpe(nav: pd.Series, rf: float = 0.025, is_strategy: bool = False) -> float:
    if is_strategy:
        qr = nav.resample("QE").last().pct_change().dropna()
        if len(qr) < 4:
            return float("nan")
        ar = (1 + qr.mean()) ** 4 - 1
        av = qr.std() * (4 ** 0.5)
        return float((ar - rf) / av) if av > 0 else float("nan")
    dr = nav.pct_change().dropna()
    if len(dr) < 10:
        return float("nan")
    ar = (1 + dr.mean()) ** 252 - 1
    av = dr.std() * (252 ** 0.5)
    return float((ar - rf) / av) if av > 0 else float("nan")


def _vol(nav: pd.Series, is_strategy: bool = False) -> float:
    if is_strategy:
        qr = nav.resample("QE").last().pct_change().dropna()
        return float(qr.std() * (4 ** 0.5) * 100) if len(qr) > 2 else float("nan")
    dr = nav.pct_change().dropna()
    return float(dr.std() * (252 ** 0.5) * 100) if len(dr) > 5 else float("nan")


def align_to_daily(qtr_nav: pd.Series, ref_nav: pd.Series) -> pd.Series:
    """把季度节点 nav 前向填充到 ref_nav 的日历（用于回撤/夏普计算）"""
    merged_idx = qtr_nav.index.union(ref_nav.index)
    return qtr_nav.reindex(merged_idx).ffill().reindex(ref_nav.index)


# ══════════════════════════════════════════════════════
#  ETF 持仓查询
# ══════════════════════════════════════════════════════

def get_etf_portfolio(pro, code: str, period: str,
                      bmap: dict = None, top_n: int = 10) -> pd.DataFrame:
    df = pro.fund_portfolio(ts_code=code,
                             fields="ts_code,ann_date,end_date,symbol,stk_mkv_ratio,mkv")
    time.sleep(0.15)
    if df is None or df.empty:
        return pd.DataFrame()
    sub = df[df["end_date"] == period].copy()
    if sub.empty:
        # 找最近已有的期
        available = sorted(df["end_date"].dropna().unique(), reverse=True)
        for p in available:
            sub = df[df["end_date"] == p].copy()
            if not sub.empty:
                break
    if sub.empty:
        return pd.DataFrame()
    if bmap:
        sub["name"]     = sub["symbol"].map(lambda x: bmap.get(x, {}).get("name", ""))
        sub["industry"] = sub["symbol"].map(lambda x: bmap.get(x, {}).get("industry", ""))
    return sub.sort_values("stk_mkv_ratio", ascending=False).head(top_n).reset_index(drop=True)


def latest_quarter(end_date: str) -> str:
    """返回不晚于 end_date 的最近季末 YYYYMMDD"""
    d = pd.to_datetime(end_date)
    quarter_ends = []
    for yr in [d.year - 1, d.year]:
        for month, day in [(3,31),(6,30),(9,30),(12,31)]:
            quarter_ends.append(pd.Timestamp(yr, month, day))
    past = [q for q in quarter_ends if q <= d]
    return past[-1].strftime("%Y%m%d") if past else f"{d.year-1}1231"


# ══════════════════════════════════════════════════════
#  Markdown 生成器（输出到 lines 列表）
# ══════════════════════════════════════════════════════

class MdWriter:
    """收集所有输出行，既能打印又能写文件"""
    def __init__(self, save: bool = True):
        self._lines: list[str] = []
        self.save = save

    def write(self, text: str = ""):
        self._lines.append(text)
        print(text)

    def h1(self, text: str):
        self.write(f"# {text}")
        self.write()

    def h2(self, text: str):
        self.write(f"## {text}")
        self.write()

    def h3(self, text: str):
        self.write(f"### {text}")
        self.write()

    def hr(self):
        self.write("---")
        self.write()

    def table_row(self, cells: list, widths: list = None) -> str:
        return "| " + " | ".join(str(c) for c in cells) + " |"

    def table_sep(self, n: int, aligns: list = None) -> str:
        seps = []
        for i in range(n):
            a = aligns[i] if aligns else "r"
            if a == "l":   seps.append(":---")
            elif a == "r": seps.append("---:")
            else:          seps.append(":---:")
        return "| " + " | ".join(seps) + " |"

    def flush_to_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self._lines), encoding="utf-8")
        print(f"\n[fund_tracker] 报告已保存: {path}")


# ══════════════════════════════════════════════════════
#  报告生成
# ══════════════════════════════════════════════════════

PERIOD_DEFS = [
    ("1W",  timedelta(days=7)),
    ("1M",  timedelta(days=30)),
    ("3M",  timedelta(days=91)),
    ("6M",  timedelta(days=183)),
    ("YTD", None),          # 特殊：年初至今
    ("1Y",  timedelta(days=365)),
    ("3Y",  timedelta(days=365*3)),
]


def _get_period_ret(r: dict, key: str, today: pd.Timestamp) -> str:
    """计算单个标的的区间收益，策略用插值，其他用日序列"""
    nav = r["nav"]
    if nav is None or nav.empty:
        return "—"

    if key == "YTD":
        sd = pd.Timestamp(year=today.year, month=1, day=1)
    else:
        delta = dict(PERIOD_DEFS)[key]
        sd = today - delta

    if r.get("is_strategy"):
        # 线性插值
        ret = strategy_period_ret(nav, sd, today)
        if np.isnan(ret):
            return "—"
        return f"{ret:+.2f}%"
    else:
        sub = nav[(nav.index >= sd) & (nav.index <= today)]
        if len(sub) < 2:
            return "—"
        return f"{(sub.iloc[-1]/sub.iloc[0]-1)*100:+.2f}%"


def build_snapshot_section(md: MdWriter, results: list, end_date: str,
                            ref_nav: pd.Series = None):
    today = pd.to_datetime(end_date)
    md.h2(f"📊 跟踪快照  截至 {end_date}")

    period_keys = [k for k, _ in PERIOD_DEFS]
    header = ["名称", "类别"] + period_keys + ["最大回撤", "夏普", "波动率"]
    aligns = ["l", "l"] + ["r"] * (len(period_keys) + 3)
    md.write(md.table_row(header))
    md.write(md.table_sep(len(header), aligns))

    for r in results:
        nav = r["nav"]
        if nav is None or nav.empty:
            continue

        # 回撤/夏普/波动用 ffill 后的日序列
        is_strat = r.get("is_strategy", False)
        if is_strat and ref_nav is not None:
            nav_daily = align_to_daily(nav, ref_nav)
        else:
            nav_daily = nav

        ret_cells = [_get_period_ret(r, k, today) for k, _ in PERIOD_DEFS]
        dd = _max_dd(nav_daily)
        sh = _sharpe(nav_daily, is_strategy=is_strat)
        vl = _vol(nav_daily, is_strategy=is_strat)

        dd_s = f"{dd:+.1f}%"
        sh_s = f"{sh:.2f}"  if not np.isnan(sh) else "N/A"
        vl_s = f"{vl:.1f}%" if not np.isnan(vl) else "N/A"
        bm   = "▶ " if r.get("benchmark") else ""
        name = bm + r["name"]

        md.write(md.table_row([name, r["category"]] + ret_cells + [dd_s, sh_s, vl_s]))

    md.write()

    # 注脚
    md.write("> 自建策略区间收益采用季度节点线性插值估算；最大回撤/夏普/波动基于季度节点前向填充的日序列。")
    md.write()


def build_periodic_section(md: MdWriter, results: list, mode: str, end_date: str,
                            ref_nav: pd.Series = None):
    today = pd.to_datetime(end_date)

    if mode == "weekly":
        periods_info = [(today - timedelta(days=7*i),
                         today - timedelta(days=7*(i-1)),
                         f"W-{4-i+1}") for i in range(4, 0, -1)]
    else:
        months = []
        d = today.replace(day=1)
        for _ in range(12):
            months.append(d)
            d = (d - timedelta(days=1)).replace(day=1)
        months.reverse()
        periods_info = [(m, m + pd.offsets.MonthEnd(0), m.strftime("%Y-%m")) for m in months]

    mode_name = "周报" if mode == "weekly" else "月报"
    md.h2(f"📅 {mode_name}  截至 {end_date}")

    labels = [lbl for _, _, lbl in periods_info]
    header = ["名称"] + labels
    aligns = ["l"] + ["r"] * len(labels)
    md.write(md.table_row(header))
    md.write(md.table_sep(len(header), aligns))

    for r in results:
        nav = r["nav"]
        if nav is None or nav.empty:
            continue

        is_strat = r.get("is_strategy", False)
        row = [r["name"]]
        for sd, ed, _ in periods_info:
            if is_strat:
                ret = strategy_period_ret(nav, sd, ed)
                row.append("—" if np.isnan(ret) else f"{ret:+.2f}%")
            else:
                nav_d = nav if not (is_strat and ref_nav is not None) else align_to_daily(nav, ref_nav)
                sub = nav_d[(nav_d.index >= sd) & (nav_d.index <= ed)]
                if len(sub) >= 2:
                    row.append(f"{(sub.iloc[-1]/sub.iloc[0]-1)*100:+.2f}%")
                else:
                    row.append("—")
        md.write(md.table_row(row))

    md.write()


def build_portfolio_section(md: MdWriter, pro, results: list, end_date: str, bmap: dict):
    quarter = latest_quarter(end_date)
    md.h2(f"📋 ETF 最新持仓 Top10  （报告期：{quarter}）")

    etf_list = [r for r in results if r.get("cfg", {}).get("type") == "etf"]
    for r in etf_list:
        code = r["cfg"]["code"]
        port = get_etf_portfolio(pro, code, quarter, bmap=bmap)
        md.h3(f"{r['name']}  ({code})")
        if port.empty:
            md.write("*暂无数据*")
            md.write()
            continue
        total_w = port["stk_mkv_ratio"].sum()
        md.write(f"Top10 合计权重：**{total_w:.1f}%**")
        md.write()
        md.write(md.table_row(["#", "代码", "名称", "行业", "权重"]))
        md.write(md.table_sep(5, ["r","l","l","l","r"]))
        for i, row in port.iterrows():
            md.write(md.table_row([
                i+1, row["symbol"],
                str(row.get("name",""))[:8],
                str(row.get("industry",""))[:10],
                f"{row['stk_mkv_ratio']:.2f}%"
            ]))
        md.write()


# ══════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="基金 & 指数周期跟踪")
    parser.add_argument("--mode", choices=["snapshot", "weekly", "monthly", "quarter"],
                        default="snapshot")
    parser.add_argument("--end",  default=datetime.today().strftime("%Y%m%d"))
    parser.add_argument("--start", default="")
    parser.add_argument("--top_n", type=int, default=10)
    parser.add_argument("--no-save", action="store_true", dest="no_save",
                        help="不保存 Markdown 文件")
    args = parser.parse_args()

    end_date = args.end
    save_md  = not args.no_save

    if args.start:
        fetch_start = args.start
    else:
        fetch_start = (pd.to_datetime(end_date) - timedelta(days=365*5)).strftime("%Y%m%d")

    print(f"[fund_tracker] 模式={args.mode}  截至={end_date}  数据起点={fetch_start}")
    pro = _get_pro()

    sys.path.insert(0, str(ROOT / "analysis"))
    from tracker_config import WATCHLIST

    print("[fund_tracker] 加载股票名称表...")
    basic = pro.stock_basic(fields="ts_code,name,industry")
    bmap  = basic.set_index("ts_code")[["name", "industry"]].to_dict("index")

    print("[fund_tracker] 预拉沪深300数据...")
    div_by_year = get_hs300_div_by_year(pro)
    ref_price   = fetch_index_daily(pro, "H00300.CSI", fetch_start, end_date)
    ref_nav     = ref_price / ref_price.iloc[0] if not ref_price.empty else None

    results = []
    for cfg in WATCHLIST:
        print(f"  拉取: {cfg['name']} ({cfg['code']})")
        nav = build_nav_series(pro, cfg, fetch_start, end_date, div_by_year)
        results.append({
            "code":        cfg["code"],
            "name":        cfg["name"],
            "category":    cfg["category"],
            "benchmark":   cfg.get("benchmark", False),
            "is_strategy": cfg["type"] == "strategy",
            "cfg":         cfg,
            "nav":         nav,
        })

    # ── 构建报告 ──
    md = MdWriter(save=save_md)
    mode_name = {"snapshot":"快照","weekly":"周报","monthly":"月报","quarter":"季报"}[args.mode]
    md.h1(f"基金 & 指数跟踪{mode_name}  {end_date}")
    md.write(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}  |  模式：{args.mode}")
    md.write()
    md.hr()

    build_snapshot_section(md, results, end_date, ref_nav)

    if args.mode in ("weekly", "monthly"):
        md.hr()
        build_periodic_section(md, results, args.mode, end_date, ref_nav)

    if args.mode == "quarter":
        md.hr()
        build_portfolio_section(md, pro, results, end_date, bmap)

    # ── 保存文件 ──
    if save_md:
        fname = f"{args.mode}_{end_date}.md"
        out_path = DOCS_DIR / fname
        md.flush_to_file(out_path)


if __name__ == "__main__":
    main()
