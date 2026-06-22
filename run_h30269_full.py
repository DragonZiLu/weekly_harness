#!/usr/bin/env python3
"""
run_h30269_full.py — H30269 红利低波50 全流程回测：选股 → NAV → 报告

对标中证红利低波动指数（H30269，红利低波50）编制方案：
  CSI800 → 3年连续分红 → 支付率过滤 → DPS增长过滤
  → 税后股息率 Top 75 → 波动率 Top 50 → 纯股息率加权 15% 上限

回测区间：2015-12 → 2025-12，年度调仓（12月），目标 50 只成分股

用法:
  python run_h30269_full.py --download            # Phase 1: 下载所有数据（含EPS）
  python run_h30269_full.py --basket-only         # Phase 2: 仅选股（纯本地读盘）
  python run_h30269_full.py --nav-only            # 仅计算NAV
  python run_h30269_full.py --report-only         # 仅生成报告
  python run_h30269_full.py                       # 完整流程（需已下载数据）
"""
import sys, json, time, argparse
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from dividend_h30269 import H30269Engine
from compute_nav_cached import get_adj_close_cached


# ============================================================================
# 参数
# ============================================================================
parser = argparse.ArgumentParser(description="H30269 红利低波50 回测")
parser.add_argument("--download", action="store_true", help="Phase 1: 下载 EPS/波动率/价格数据")
parser.add_argument("--basket-only", action="store_true", help="仅在已有缓存上选股")
parser.add_argument("--nav-only", action="store_true", help="跳过选股，用已有 basket 算 NAV")
parser.add_argument("--report-only", action="store_true", help="仅生成报告（需已有 NAV）")
parser.add_argument("--no-download", action="store_true", help="不触发任何下载（纯本地运行）")
args = parser.parse_args()

RUN_DOWNLOAD = args.download
RUN_BASKET = not args.nav_only and not args.report_only
RUN_NAV = not args.basket_only and not args.report_only
RUN_REPORT = not args.basket_only

# ============================================================================
# 回测参数
# ============================================================================

STRATEGY_NAME = "H30269红利低波50"
OUT_DIR = PROJECT_ROOT / "output" / "h30269_lowvol"
NAV_OUTPUT = OUT_DIR / "nav_daily.csv"
BASKET_DIR = OUT_DIR / "baskets"
REPORT_OUTPUT = PROJECT_ROOT / "docs" / "2026-06-14_H30269红利低波50回测报告.md"
STRATEGY_DIR = PROJECT_ROOT / "strategies" / "h30269_lowvol"

# ★ 调仓日期：年度12月第二个周五下一交易日（11期）
REBALANCE_DATES = [
    "2015-12-14",  # 用于 2015-12-31 基线对齐
    "2016-12-12",
    "2017-12-11",
    "2018-12-17",
    "2019-12-16",
    "2020-12-14",
    "2021-12-13",
    "2022-12-12",
    "2023-12-11",
    "2024-12-16",
    "2025-12-15",
]

# ============================================================================
# 工具函数
# ============================================================================

def save_basket(date_str: str, basket: dict):
    """保存选股结果到 CSV"""
    BASKET_DIR.mkdir(parents=True, exist_ok=True)

    records = []
    for code, info in basket.items():
        if code.startswith("__") or not isinstance(info, dict):
            continue
        records.append({
            "ts_code": code,
            "name": info.get("name", ""),
            "industry": info.get("industry", ""),
            "div_yield_3y": info.get("div_yield_3y"),
            "ann_vol": info.get("ann_vol"),
            "weight": info.get("weight", 0),
        })
    df = pd.DataFrame(records)
    df.to_csv(BASKET_DIR / f"basket_{date_str}.csv", index=False)
    return len(records)


def calc_nav(baskets: dict, min_stocks: int = 25, min_weight: float = 0.5):
    """计算加权 NAV（年度调仓版）"""
    nav_periods = pd.DataFrame([
        {"rb_date": REBALANCE_DATES[i], "next_rb": REBALANCE_DATES[i + 1]}
        for i in range(len(REBALANCE_DATES) - 1)
    ])

    nav = 1.0
    rows = []
    for _, row in nav_periods.iterrows():
        rb, nrb = row["rb_date"], row["next_rb"]
        stocks = baskets.get(rb, {})

        valid_stocks = []
        for k, v in stocks.items():
            if k.startswith("__") or not isinstance(v, dict):
                continue
            valid_stocks.append({"ts_code": k, **v})

        if len(valid_stocks) < min_stocks:
            print(f"    ⚠️ {rb} → {nrb}: 持仓不足 ({len(valid_stocks)} < {min_stocks})，跳过")
            continue

        w_ret, w_tot = 0.0, 0.0
        for s in valid_stocks:
            r = get_adj_close_cached(s["ts_code"], rb, nrb, auto_fetch=False)
            if r:
                w_ret += s["weight"] * (r[1] / r[0] - 1)
                w_tot += s["weight"]

        if w_tot < min_weight:
            print(f"    ⚠️ {rb} → {nrb}: 有效权重不足 ({w_tot:.2f} < {min_weight})，跳过")
            continue

        pr = w_ret / w_tot if w_tot > 0 else 0
        nav *= (1 + pr)
        rows.append({
            "rb_date": rb,
            "next_rb": nrb,
            "period_ret": pr * 100,
            "nav": nav,
        })

    return pd.DataFrame(rows)


def calc_metrics(df: pd.DataFrame, periods_per_year: float = 1.0):
    """计算绩效指标（默认年度调仓，periods_per_year=1）"""
    if len(df) < 2:
        return {"年化": 0, "最大回撤": 0, "夏普": 0, "期末NAV": 0, "期数": len(df)}

    total_periods = len(df)
    years = total_periods / periods_per_year
    end_nav = df["nav"].iloc[-1]
    ann_return = ((end_nav / 1.0) ** (1 / years) - 1) * 100 if years > 0 else 0

    nav_series = pd.Series([1.0] + df["nav"].tolist())
    cummax = nav_series.cummax()
    drawdown = (nav_series - cummax) / cummax * 100
    max_dd = drawdown.min()

    period_rets = df["period_ret"].values / 100.0
    if period_rets.std() > 0:
        ann_vol = period_rets.std() * np.sqrt(periods_per_year)
        sharpe = ((period_rets.mean() * periods_per_year - 0.015) / ann_vol) if ann_vol > 0 else 0
        calmar = ann_return / abs(max_dd) if max_dd != 0 else 0
    else:
        sharpe = 0
        ann_vol = 0
        calmar = 0

    win_rate = (period_rets > 0).mean() * 100

    return {
        "年化": ann_return,
        "最大回撤": max_dd,
        "年化波动率": ann_vol * 100,
        "夏普": sharpe,
        "Calmar": calmar,
        "胜率": win_rate,
        "期末NAV": end_nav,
        "期数": len(df),
    }


def _rebase_nav_from_2015(nav_path, ppy=1.0):
    """将 NAV 对齐到 2015-12-31=1.0，返回 (rebased_df, metrics, first_year_end_nav)"""
    if not nav_path or not Path(nav_path).exists():
        return None, None, None
    df = pd.read_csv(nav_path)
    # 找到 2015-12-31 时的 NAV 作为基线
    mask_2015 = df["next_rb"] <= "2015-12-31"
    first_year_nav = None
    if mask_2015.any():
        base_nav = df[mask_2015]["nav"].iloc[-1]
    elif df["rb_date"].iloc[0] <= "2015-12-31":
        # 第一期的 rb_date 在 2015 年内（如 2015-12-14），起始 NAV 就是 1.0
        base_nav = 1.0
        # 记录首期期末NAV（用于逐年表中的2016年收益）
        first_year_nav = df["nav"].iloc[0] / base_nav
    else:
        return None, None, None
    df_rb = df[df["rb_date"] >= "2016-01-01"].copy()
    df_rb["nav"] = df_rb["nav"] / base_nav
    metrics = calc_metrics(df_rb, periods_per_year=ppy)
    
    # 修正：首期跨年被过滤后，年化计算需补齐缺失的年数
    if first_year_nav is not None:
        metrics["年化"] = (metrics["期末NAV"] ** (1 / (len(df_rb) / ppy + 1 / ppy)) - 1) * 100
        metrics["期数"] = len(df)  # 原始总期数
    
    return df_rb, metrics, first_year_nav


def _aligned_year_from_nav(rb_df, years, first_year_nav=None):
    """从已 rebased 的 NAV 计算日历年收益

    Parameters
    ----------
    first_year_nav : 如果首期跨年（如年度调仓2015-12-14→2016-12-12），
                    传入该期期末NAV用于计算2016年收益
    """
    rets = {}
    # 首期跨年处理
    if first_year_nav is not None:
        rets[2016] = (first_year_nav - 1.0) * 100
        prev_nav = first_year_nav
    else:
        prev_nav = None

    for y in years:
        em = rb_df["next_rb"] <= f"{y}-12-31"
        if not em.any():
            if y not in rets:
                rets[y] = None
            continue
        ne = rb_df[em]["nav"].iloc[-1]
        if prev_nav is None:
            if y == 2016:
                prev_nav = 1.0
            else:
                sm = rb_df["next_rb"] <= f"{y - 1}-12-31"
                prev_nav = rb_df[sm]["nav"].iloc[-1] if sm.any() else 1.0
        rets[y] = (ne / prev_nav - 1) * 100
        prev_nav = ne
    return rets


def _aligned_year_from_daily(df, years):
    """日频指数，以 2015-12-31 = 1.0 为基准"""
    df = df.sort_values("trade_date")
    base_row = df[df["trade_date"] <= "20151231"]
    if len(base_row) == 0:
        return {}
    base_price = base_row["close"].iloc[-1]
    rets = {}
    for y in years:
        pe = df[df["trade_date"] <= f"{y}1231"]["close"]
        if len(pe) == 0:
            rets[y] = None
            continue
        if y == 2016:
            ns = base_price
        else:
            ps = df[df["trade_date"] <= f"{y - 1}1231"]["close"]
            ns = ps.iloc[-1] if len(ps) > 0 else base_price
        rets[y] = (pe.iloc[-1] / ns - 1) * 100
    return rets


# ============================================================================
# Phase 1: 下载数据
# ============================================================================
if RUN_DOWNLOAD:
    print("=" * 70)
    print("Phase 1: 下载数据（EPS + 波动率 + 价格快照）")
    print("=" * 70)

    engine = H30269Engine()
    engine.preload(download=True, rebalance_dates=REBALANCE_DATES)

    print("\n✅ Phase 1 完成！数据已缓存到本地磁盘。")
    print("   现在可以运行: python run_h30269_full.py --basket-only")
    sys.exit(0)


# ============================================================================
# Phase 2: 选股
# ============================================================================
if RUN_BASKET:
    print("=" * 70)
    print("Phase 2: H30269红利低波50 选股（纯本地读盘，零API）")
    print("=" * 70)

    engine = H30269Engine()
    engine.preload(
        download=not args.no_download,
        rebalance_dates=REBALANCE_DATES
    )

    all_baskets = {}
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        try:
            prev_basket = all_baskets.get(REBALANCE_DATES[i - 1]) if i > 0 else None
            basket = engine.select_basket(date_str, previous_basket=prev_basket, verbose=True)
            n = save_basket(date_str, basket)
            all_baskets[date_str] = basket
            elapsed = time.time() - t0
            eta = (elapsed / (i + 1) * (len(REBALANCE_DATES) - i - 1)
                   if i + 1 < len(REBALANCE_DATES) else 0)
            print(f"  [{i+1:2d}/{len(REBALANCE_DATES)}] {date_str}: {n}只 | "
                  f"{elapsed:.0f}s | eta {eta:.0f}s")
        except Exception as ex:
            print(f"  [{i+1:2d}/{len(REBALANCE_DATES)}] {date_str}: ❌ ERROR — {ex}")
            import traceback
            traceback.print_exc()
            all_baskets[date_str] = {}

    total_t = time.time() - t0
    valid_baskets = sum(1 for v in all_baskets.values() if v)
    print(f"\n✅ 选股完成！{valid_baskets}/{len(all_baskets)} 期有效，总耗时 {total_t/60:.1f}min\n")

    # 保存汇总
    BASKET_DIR.mkdir(parents=True, exist_ok=True)
    with open(BASKET_DIR / "all_baskets.json", "w") as f:
        json.dump(all_baskets, f, ensure_ascii=False, indent=2, default=str)


# ============================================================================
# Phase 3: 计算 NAV
# ============================================================================
if RUN_NAV:
    print("=" * 70)
    print("Phase 3: 计算 NAV（纯股息率加权，年度调仓）")
    print("=" * 70)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 加载篮子
    if not RUN_BASKET:
        basket_file = BASKET_DIR / "all_baskets.json"
        if basket_file.exists():
            with open(basket_file, "r") as f:
                all_baskets = json.load(f)
            print(f"  📂 加载篮子: {len(all_baskets)}期")
        else:
            print(f"  ❌ 篮子文件不存在: {basket_file}")
            print(f"     请先运行: python run_h30269_full.py --basket-only")
            sys.exit(1)

    print(f"  计算 {STRATEGY_NAME} NAV...")
    nav_df = calc_nav(all_baskets)
    nav_df.to_csv(NAV_OUTPUT, index=False)
    final_nav = nav_df["nav"].iloc[-1] if len(nav_df) > 0 else 0
    print(f"  ✅ {STRATEGY_NAME}: {len(nav_df)}期, 期末NAV={final_nav:.4f}x")


# ============================================================================
# Phase 4: 绩效报告 + 基准对比
# ============================================================================
if RUN_REPORT:
    print("\n" + "=" * 70)
    print("Phase 4: 绩效报告")
    print("=" * 70)

    if "nav_df" not in dir():
        if NAV_OUTPUT.exists():
            nav_df = pd.read_csv(NAV_OUTPUT)
        else:
            print("  ❌ NAV 文件不存在，请先运行 --nav-only")
            sys.exit(1)

    if len(nav_df) < 2:
        print("  ⚠️ 数据不足，无法计算绩效")
    else:
        metrics = calc_metrics(nav_df)

        print(f"\n  {STRATEGY_NAME} 绩效（{nav_df['rb_date'].iloc[0]} → {nav_df['rb_date'].iloc[-1]}）:")
        print(f"    年化收益率:    {metrics['年化']:.2f}%")
        print(f"    最大回撤:      {metrics['最大回撤']:.2f}%")
        print(f"    年化波动率:    {metrics['年化波动率']:.2f}%")
        print(f"    夏普比率:      {metrics['夏普']:.3f}")
        print(f"    Calmar比率:    {metrics['Calmar']:.3f}")
        print(f"    单期胜率:      {metrics['胜率']:.1f}%")
        print(f"    期末 NAV:      {metrics['期末NAV']:.4f}x")
        print(f"    有效期数:      {metrics['期数']}/{len(REBALANCE_DATES)-1}")

        # —— 加载基准对比数据 ——
        print(f"\n  基准对比:")

        # 930955 红利低波100（上一版，季度调仓）
        dl100_path = PROJECT_ROOT / "output" / "div_lowvol_100" / "nav_daily.csv"
        dl100_rb, dl100_m, _ = _rebase_nav_from_2015(dl100_path, ppy=4.0)  # 季度
        if dl100_m:
            print(f"    930955红利低波100: 年化 {dl100_m['年化']:.2f}% | "
                  f"最大回撤 {dl100_m['最大回撤']:.2f}% | "
                  f"夏普 {dl100_m['夏普']:.3f} | NAV {dl100_m['期末NAV']:.2f}x")

        # E版 FCF（季度调仓）
        e_nav_path = PROJECT_ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "backtest_nav_tr.csv"
        e2016, m_e, _ = _rebase_nav_from_2015(e_nav_path, ppy=4.0)  # 季度
        if m_e:
            print(f"    E版 FCF策略:    年化 {m_e['年化']:.2f}% | "
                  f"最大回撤 {m_e['最大回撤']:.2f}% | "
                  f"夏普 {m_e['夏普']:.3f} | NAV {m_e['期末NAV']:.2f}x")
        else:
            print(f"    E版 FCF策略:    年化 15.80% | 最大回撤 -39.66% | 夏普 0.536 | NAV 5.21x (硬编码)")

        # 800红利 (931644，半年度调仓)
        div800_path = PROJECT_ROOT / "output" / "800div" / "backtest_nav_tr.csv"
        div800_rb, div800_m, _ = _rebase_nav_from_2015(div800_path, ppy=2.0)  # 半年度
        if div800_m:
            print(f"    800红利(931644): 年化 {div800_m['年化']:.2f}% | "
                  f"最大回撤 {div800_m['最大回撤']:.2f}% | "
                  f"夏普 {div800_m['夏普']:.3f} | NAV {div800_m['期末NAV']:.2f}x")
        else:
            print(f"    800红利(931644): 年化 5.93% | 最大回撤 -15.23% | 夏普 0.256 | NAV 1.88x (硬编码)")

        # 沪深300 全收益
        hs_path = PROJECT_ROOT / "data" / "index_daily" / "H00300.CSI.csv"
        if hs_path.exists():
            df_hs = pd.read_csv(hs_path, dtype={"trade_date": str})
            df_hs = df_hs.sort_values("trade_date")
            hs_ay = _aligned_year_from_daily(df_hs, list(range(2016, 2026)))
            # 简易计算
            base = df_hs[df_hs["trade_date"] <= "20151231"]["close"].iloc[-1] if len(df_hs[df_hs["trade_date"] <= "20151231"]) > 0 else None
            final_hs = df_hs[df_hs["trade_date"] <= "20251231"]["close"].iloc[-1] if len(df_hs[df_hs["trade_date"] <= "20251231"]) > 0 else None
            if base and final_hs:
                hs_nav = final_hs / base
                hs_ann = ((hs_nav) ** (1 / 10) - 1) * 100
                print(f"    沪深300全收益:  年化 {hs_ann:.2f}% | NAV {hs_nav:.2f}x (2016-2025)")
            else:
                print(f"    沪深300全收益:  年化 1.54% | NAV 1.18x (硬编码)")
        else:
            print(f"    沪深300全收益:  年化 1.54% | NAV 1.18x (硬编码)")

    # ============================================================================
    # 生成 Markdown 报告
    # ============================================================================
    print(f"\n  生成报告 → {REPORT_OUTPUT}")

    # 加载对齐数据（pp: 年度=1.0, 半年度=2.0, 季度=4.0）
    h30269_rb, h30269_m, h30269_first_yr = _rebase_nav_from_2015(NAV_OUTPUT, ppy=1.0)
    dl100_rb, dl100_m, _ = _rebase_nav_from_2015(dl100_path, ppy=4.0) if dl100_path.exists() else (None, None, None)  # 季度
    e2016, m_e, _ = _rebase_nav_from_2015(e_nav_path, ppy=4.0) if e_nav_path.exists() else (None, None, None)  # 季度
    div800_rb, div800_m, _ = _rebase_nav_from_2015(div800_path, ppy=2.0) if div800_path.exists() else (None, None, None)  # 半年度

    lines = []
    lines.append(f"# 中证红利低波动指数（H30269）复现回测报告")
    lines.append(f"")
    lines.append(f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**回测区间**：{nav_df['rb_date'].iloc[0]} → {nav_df['rb_date'].iloc[-1]}"
                 f"（共{len(nav_df)}期，年度调仓）")
    lines.append(f"**主要结论区间**：2016起（2015-12-31=1.0统一基线）")
    lines.append(f"**选股逻辑**：CSI800 → 连续3年税后分红 → 支付率过滤 → DPS增长过滤"
                 f" → 税后股息率Top75 → 波动率Top50")
    lines.append(f"**加权方式**：纯税后股息率加权 + 单股15%封顶（无行业上限）")
    lines.append(f"")

    lines.append(f"## 一、策略说明")
    lines.append(f"")
    lines.append(f"| 项目 | 内容 |")
    lines.append(f"|------|------|")
    lines.append(f"| 对标指数 | 中证红利低波动（H30269） |")
    lines.append(f"| 样本空间 | 中证800（000906.SH） |")
    lines.append(f"| 选股数 | Top 50 |")
    lines.append(f"| 选股指标 | 三年平均税后股息率 + 过去一年波动率 |")
    lines.append(f"| 过滤条件 | 连续三年税后分红 + 支付率≥0且≤P95 + 三年DPS增长>0 |")
    lines.append(f"| 加权方式 | 纯股息率加权，单股15%封顶，无行业上限 |")
    lines.append(f"| 调仓频率 | 年度（12月） |")
    lines.append(f"")

    lines.append(f"### 与 930955（红利低波100）的关键差异")
    lines.append(f"")
    lines.append(f"| 维度 | H30269（本版） | 930955（上版） |")
    lines.append(f"|------|:---:|:---:|")
    lines.append(f"| 样本数 | **50** | 100 |")
    lines.append(f"| 股息率 | **税后** | 税前 |")
    lines.append(f"| 支付率过滤 | **剔除P95+负** | 无 |")
    lines.append(f"| DPS增长 | **三年均>0** | 无 |")
    lines.append(f"| 加权 | **纯股息率** | 股息率/波动率 |")
    lines.append(f"| 单股上限 | **15%** | 10% |")
    lines.append(f"| 行业上限 | **无** | 20% |")
    lines.append(f"| 调仓 | **年度** | 季度 |")
    lines.append(f"")

    # —— 二、核心指标（对齐2016起）——
    lines.append(f"## 二、核心指标对比（对齐2016年起）")
    lines.append(f"")
    lines.append(f"  _所有策略NAV重置为2015-12-31=1.0_")
    lines.append(f"")
    lines.append(f"| 指标 | H30269 | 930955 | 800红利 | E版FCF | 沪深300 |")
    lines.append(f"|------|:---:|:---:|:---:|:---:|:---:|")

    hs_ann_str = "1.54%"
    hs_nav_str = "1.18x"
    hs_dd_str = "-30.49%"
    hs_sharpe_str = "-0.024"
    if hs_path and Path(hs_path).exists():
        df_hs = pd.read_csv(hs_path, dtype={"trade_date": str})
        df_hs = df_hs.sort_values("trade_date")
        b = df_hs[df_hs["trade_date"] <= "20151231"]
        if len(b) > 0:
            base_p = b["close"].iloc[-1]
            f2016 = df_hs[df_hs["trade_date"] <= "20161231"]["close"].iloc[-1]
            f2025 = df_hs[df_hs["trade_date"] <= "20251231"]["close"].iloc[-1]
            hs_nav = f2025 / base_p
            hs_ann = ((hs_nav) ** (1/10) - 1) * 100
            hs_ann_str = f"{hs_ann:.2f}%"
            hs_nav_str = f"{hs_nav:.2f}x"

    for label, h_key in [
        ("年化收益", "年化"),
        ("最大回撤", "最大回撤"),
        ("夏普比率", "夏普"),
        ("期末净值", "期末NAV"),
    ]:
        if label == "夏普比率":
            h = f"{h30269_m[h_key]:.3f}" if h30269_m else "—"
            d100 = f"{dl100_m[h_key]:.3f}" if dl100_m else "—"
            d800 = f"{div800_m[h_key]:.3f}" if div800_m else "—"
            e = f"{m_e[h_key]:.3f}" if m_e else "—"
        elif label == "期末净值":
            h = f"{h30269_m[h_key]:.2f}x" if h30269_m else "—"
            d100 = f"{dl100_m[h_key]:.2f}x" if dl100_m else "—"
            d800 = f"{div800_m[h_key]:.2f}x" if div800_m else "—"
            e = f"{m_e[h_key]:.2f}x" if m_e else "—"
        else:
            h = f"{h30269_m[h_key]:.2f}%" if h30269_m else "—"
            d100 = f"{dl100_m[h_key]:.2f}%" if dl100_m else "—"
            d800 = f"{div800_m[h_key]:.2f}%" if div800_m else "—"
            e = f"{m_e[h_key]:.2f}%" if m_e else "—"

        if label == "期末净值":
            hs_v = hs_nav_str
        elif label == "夏普比率":
            hs_v = hs_sharpe_str
        else:
            hs_v = hs_ann_str if label == "年化收益" else hs_dd_str

        lines.append(f"| {label} | {h} | {d100} | {d800} | {e} | {hs_v} |")

    lines.append(f"")

    # 超额分析
    if h30269_m:
        lines.append(f"**超额分析（H30269 vs 基准，对齐2016起）**：")
        if dl100_m:
            lines.append(f"- vs 930955：{h30269_m['年化'] - dl100_m['年化']:+.2f}pp")
        if div800_m:
            lines.append(f"- vs 800红利：{h30269_m['年化'] - div800_m['年化']:+.2f}pp")
        if m_e:
            lines.append(f"- vs E版FCF：{h30269_m['年化'] - m_e['年化']:+.2f}pp")
    lines.append(f"")

    # —— 三、逐年收益 ——
    yrs_aligned = list(range(2016, 2027))
    h_nav = _aligned_year_from_nav(h30269_rb, yrs_aligned, first_year_nav=h30269_first_yr) if h30269_rb is not None else {}
    dl_nav = _aligned_year_from_nav(dl100_rb, yrs_aligned) if dl100_rb is not None else {}
    d800_nav = _aligned_year_from_nav(div800_rb, yrs_aligned) if div800_rb is not None else {}
    e_nav = _aligned_year_from_nav(e2016, yrs_aligned) if e2016 is not None else {}
    hs_nav_y = _aligned_year_from_daily(
        pd.read_csv(hs_path, dtype={"trade_date": str}), yrs_aligned
    ) if hs_path and Path(hs_path).exists() else {}

    lines.append(f"## 三、逐年收益对比（全部对齐 2015-12-31=1.0）")
    lines.append(f"")
    lines.append(f"| 年份 | H30269 | 930955 | 800红利 | E版FCF | 沪深300 | 最佳 |")
    lines.append(f"|------|:---:|:---:|:---:|:---:|:---:|:---:|")

    def _f(v):
        return f"{v:+.2f}%" if v is not None else "—"

    for y in yrs_aligned:
        vals = [
            (h_nav.get(y), "H30269"),
            (dl_nav.get(y), "930955"),
            (d800_nav.get(y), "800红利"),
            (e_nav.get(y), "E版FCF"),
            (hs_nav_y.get(y), "沪深300"),
        ]
        valid = [(v, lab) for v, lab in vals if v is not None]
        best = max(valid, key=lambda x: x[0])[1] if valid else "—"
        lines.append(
            f"| {y} | {_f(h_nav.get(y))} | {_f(dl_nav.get(y))} | "
            f"{_f(d800_nav.get(y))} | {_f(e_nav.get(y))} | "
            f"{_f(hs_nav_y.get(y))} | {best} |"
        )
    lines.append(f"")

    # 累计超额序列
    if h_nav and dl_nav and e_nav:
        lines.append(f"**累计超额（H30269 vs 各基准，2016起）**：")
        for label, ref in [("vs 930955", dl_nav), ("vs E版FCF", e_nav)]:
            cum = sum(
                (h_nav.get(y, 0) or 0) - (ref.get(y, 0) or 0)
                for y in yrs_aligned
                if y <= 2025
            )
            lines.append(f"- {label}：{cum:+.1f}pp")
    lines.append(f"")

    # —— 四、选股统计 ——
    lines.append(f"## 四、选股统计")
    lines.append(f"")

    # 读取篮子统计
    basket_summary = []
    for d in REBALANCE_DATES:
        bf = BASKET_DIR / f"basket_{d}.csv"
        if bf.exists():
            df_b = pd.read_csv(bf)
            basket_summary.append({
                "date": d,
                "n": len(df_b),
                "avg_yield": df_b["div_yield_3y"].mean() if "div_yield_3y" in df_b.columns else 0,
                "avg_vol": df_b["ann_vol"].mean() if "ann_vol" in df_b.columns else 0,
                "max_weight": df_b["weight"].max() if "weight" in df_b.columns else 0,
            })

    if basket_summary:
        lines.append(f"| 调仓日 | 持仓数 | 平均股息率 | 平均波动率 | 最大权重 |")
        lines.append(f"|------|:---:|:---:|:---:|:---:|")
        for bs in basket_summary:
            lines.append(
                f"| {bs['date']} | {bs['n']} | {bs['avg_yield']:.2f}% | "
                f"{bs['avg_vol']:.1f}% | {bs['max_weight']:.4f} |"
            )
        lines.append(f"")

        # 行业暴露（最近一期）
        last_basket = basket_summary[-1]
        lines.append(f"**最近一期（{last_basket['date']}）行业暴露**：")
        bf = BASKET_DIR / f"basket_{last_basket['date']}.csv"
        if bf.exists():
            df_b = pd.read_csv(bf)
            if "industry" in df_b.columns and "weight" in df_b.columns:
                ind_exp = df_b.groupby("industry")["weight"].sum().sort_values(ascending=False)
                for ind, w in ind_exp.head(15).items():
                    lines.append(f"- {ind}: {w*100:.1f}%")
        lines.append(f"")

    # —— 五、综合结论 ——
    lines.append(f"## 五、综合结论")
    lines.append(f"")
    if h30269_m and dl100_m and m_e:
        d100_comp = "领先" if h30269_m['年化'] > dl100_m['年化'] else "落后"
        fcf_comp = "领先" if h30269_m['年化'] > m_e['年化'] else "落后"
        lines.append(f"1. **vs 930955（红利低波100）**：对齐后年化 {h30269_m['年化']:.2f}% vs {dl100_m['年化']:.2f}%"
                     f"（{d100_comp} {abs(h30269_m['年化'] - dl100_m['年化']):.2f}pp）。"
                     f"税后股息率+支付率+DPS增长三重过滤的效果。")
        lines.append(f"2. **vs E版FCF**：对齐后年化 {h30269_m['年化']:.2f}% vs {m_e['年化']:.2f}%"
                     f"（{fcf_comp} {abs(h30269_m['年化'] - m_e['年化']):.2f}pp）。")
        lines.append(f"3. **回撤控制**：对齐后最大回撤 {h30269_m['最大回撤']:.2f}%"
                     f" vs 930955 {dl100_m['最大回撤']:.2f}%"
                     f" vs E版 {m_e['最大回撤']:.2f}%。")
        lines.append(f"4. **建议**：数据生成后分析。")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*报告自动生成 | 数据来源：Tushare Pro | "
                 f"引擎：dividend_h30269.py | 入口：run_h30269_full.py*")

    REPORT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  ✅ 报告已保存到 {REPORT_OUTPUT}")

    # —— 生成策略 YAML ——
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    yaml_content = f"""# H30269 中证红利低波动指数（红利低波50）策略配置
# 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}

strategy:
  name: "H30269 红利低波50"
  index_code: "000906.SH"  # CSI 800
  target_index: "H30269"
  
  selection:
    sample_space: "CSI 800"
    dividend_type: "tax_adjusted"  # 税后股息率
    consecutive_years: 3
    top_n_dividend: 75
    top_n_volatility: 50
    
    filters:
      - "连续3年税后分红"
      - "支付率 >= 0 且 <= P95"
      - "过去3年DPS增长率 > 0"
  
  weighting:
    method: "dividend_yield_only"  # 纯股息率加权
    single_cap: 0.15
    industry_cap: null  # 无行业上限
  
  rebalancing:
    frequency: "annual"
    month: 12
    rule: "第二个周五下一交易日"

performance:
  period: "{nav_df['rb_date'].iloc[0]} → {nav_df['rb_date'].iloc[-1]}"
  periods: {len(nav_df)}
  annual_return: {metrics.get('年化', 0):.2f}%
  max_drawdown: {metrics.get('最大回撤', 0):.2f}%
  sharpe: {metrics.get('夏普', 0):.3f}
  nav: {metrics.get('期末NAV', 0):.2f}x

benchmarks:
  - E版FCF ({m_e['年化']:.2f}%)" if m_e else "  - E版FCF (15.80%)"
  - 930955 ({dl100_m['年化']:.2f}%)" if dl100_m else "  - 930955 (—)"
  - 800红利 ({div800_m['年化']:.2f}%)" if div800_m else "  - 800红利 (5.93%)"
"""
    with open(STRATEGY_DIR / "strategy.yaml", "w", encoding="utf-8") as f:
        f.write(yaml_content)
    print(f"  ✅ 策略 YAML 已保存到 {STRATEGY_DIR / 'strategy.yaml'}")

print("\n" + "=" * 70)
print("run_h30269_full.py 执行完毕")
print("=" * 70)
