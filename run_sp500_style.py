#!/usr/bin/env python3
"""
run_sp500_style.py — S&P 500 风格新沪深300 全流程：选股 → NAV → 报告

对标标普500 指数编制规则：
  盈利门槛（年度净利润>0）→ 流动性过滤 → 行业平衡 → 流通市值加权

回测区间：2015-03 → 2026-06，季度调仓，目标 300 只成分股
"""
import sys, json, time, argparse
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from sp500_style import Sp500StyleEngine
from compute_nav_cached import get_adj_close_cached
from compute_nav_cached import get_adj_close_cached

# ============================================================================
# 参数
# ============================================================================
parser = argparse.ArgumentParser(description="S&P 500 风格新沪深300 回测")
parser.add_argument("--basket-only", action="store_true", help="仅选股，不计算NAV")
parser.add_argument("--nav-only", action="store_true", help="跳过选股，用已有basket算NAV")
parser.add_argument("--report-only", action="store_true", help="仅生成报告（需已有NAV数据）")
parser.add_argument("--use-fcf", action="store_true", help="使用 FCF（自由现金流）替代净利润作为盈利条件")
parser.add_argument("--use-both", action="store_true", help="同时要求净利润和FCF都为正（最严格）")
parser.add_argument("--target-n", type=int, default=300, help="目标成分股数量（默认300）")
parser.add_argument("--no-cap", action="store_true", help="取消单股10%权重上限")
args = parser.parse_args()

RUN_BASKET = not args.nav_only and not args.report_only
RUN_NAV = not args.basket_only and not args.report_only
RUN_REPORT = not args.basket_only

# 调仓日期（季度，与 FCF 策略保持一致）
REBALANCE_DATES = [
    "2015-03-16", "2015-06-15", "2015-09-14", "2015-12-14",
    "2016-03-14", "2016-06-13", "2016-09-12", "2016-12-12",
    "2017-03-13", "2017-06-12", "2017-09-11", "2017-12-11",
    "2018-03-12", "2018-06-11", "2018-09-17", "2018-12-17",
    "2019-03-11", "2019-06-17", "2019-09-16", "2019-12-16",
    "2020-03-16", "2020-06-15", "2020-09-14", "2020-12-14",
    "2021-03-15", "2021-06-14", "2021-09-13", "2021-12-13",
    "2022-03-14", "2022-06-13", "2022-09-12", "2022-12-12",
    "2023-03-13", "2023-06-12", "2023-09-11", "2023-12-11",
    "2024-03-11", "2024-06-17", "2024-09-16", "2024-12-16",
    "2025-03-17", "2025-06-16", "2025-09-15", "2025-12-15",
    "2026-03-16", "2026-06-15",
]

TARGET_N = args.target_n
OUT_DIR = PROJECT_ROOT / "output" / f"sp500_style_{TARGET_N}"
NAV_OUTPUT = OUT_DIR / "nav_daily.csv"
BASKET_DIR = OUT_DIR / "baskets"


def save_basket(date_str: str, basket: dict, verbose: bool = False):
    """保存选股结果到 CSV"""
    BA  = BASKET_DIR
    BA  .mkdir(parents=True, exist_ok=True)

    stats = basket.pop("__stats__", None)
    records = []
    for code, info in basket.items():
        records.append({
            "ts_code": code,
            "name": info.get("name", ""),
            "industry": info.get("industry", ""),
            "total_mv": info.get("total_mv", 0),
            "circ_mv": info.get("circ_mv", 0),
            "circ_ratio": info.get("circ_ratio", 0),
            "profit": info.get("profit"),
            "weight": info.get("weight", 0),
        })
    df = pd.DataFrame(records)
    df.to_csv(BA  / f"basket_{date_str}.csv", index=False)

    # 也保存为汇总记录
    if stats:
        summary_file = BA  / "selection_stats.csv"
        stats_row = {
            "date": date_str,
            "total_candidates": stats.get("total_candidates", 0),
            "passed_profit": stats.get("passed_profit", 0),
            "passed_liquidity": stats.get("passed_liquidity", 0),
            "selected": stats.get("selected", 0),
        }
        if stats.get("industry_distribution"):
            stats_row["n_industries"] = len(stats["industry_distribution"])
        row = pd.DataFrame([stats_row])
        if summary_file.exists():
            row.to_csv(summary_file, mode="a", header=False, index=False)
        else:
            row.to_csv(summary_file, index=False)

    return len(records)


# ============================================================================
# Step 1: 选股
# ============================================================================
if RUN_BASKET:
    print("=" * 70)
    print("第一步：S&P 500 风格选股（盈利门槛 + 行业平衡 + 流通市值加权）")
    print("=" * 70)

    engine = Sp500StyleEngine()
    if args.no_cap:
        engine.CAP = 1.0  # 取消单股上限
    engine.preload(download_stock_basic=True)

    all_baskets = {}
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        try:
            basket = engine.select_basket(date_str, target_n=TARGET_N, verbose=False, use_fcf=args.use_fcf, use_both=args.use_both)
            n = save_basket(date_str, basket, verbose=True)
            all_baskets[date_str] = basket
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(REBALANCE_DATES) - i - 1) if i + 1 < len(REBALANCE_DATES) else 0
            print(f"  [{i+1:2d}/{len(REBALANCE_DATES)}] {date_str}: {n}只 | "
                  f"{elapsed:.0f}s elapsed | eta {eta/60:.1f}min")
        except Exception as ex:
            print(f"  [{i+1:2d}/{len(REBALANCE_DATES)}] {date_str}: ❌ ERROR — {ex}")
            all_baskets[date_str] = {}

    total_t = time.time() - t0
    print(f"\n✅ 选股完成！{len(all_baskets)} 期，总耗时 {total_t/60:.1f}min\n")

    # 保存汇总
    with open(BASKET_DIR / "all_baskets.json", "w") as f:
        json.dump(all_baskets, f, ensure_ascii=False, indent=2, default=str)


# ============================================================================
# Step 2: 计算 NAV（复用 run_bdefx_full.py 的 calc_nav 逻辑）
# ============================================================================
if RUN_NAV:
    print("=" * 70)
    print("第二步：计算 NAV（流通市值加权，季度调仓）")
    print("=" * 70)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 加载所有篮子
    if not RUN_BASKET:
        with open(BASKET_DIR / "all_baskets.json", "r") as f:
            all_baskets = json.load(f)
    
    # 构建调仓期表
    nav_periods = pd.DataFrame([
        {"rb_date": REBALANCE_DATES[i], "next_rb": REBALANCE_DATES[i+1]}
        for i in range(len(REBALANCE_DATES) - 1)
    ])

    # ================================================================
    # calc_nav: 逐期计算加权收益，滚动累乘 NAV
    # 与 run_bdefx_full.py 完全一致的模式
    # ================================================================
    def calc_nav(baskets, min_stocks=5, min_weight=0.3):
        nav = 1.0
        rows = []
        for _, row in nav_periods.iterrows():
            rb, nrb = row["rb_date"], row["next_rb"]
            stocks = baskets.get(rb, {})

            # 过滤出有效股票（跳过 __stats__ 等元数据 key）
            valid_stocks = []
            for k, v in stocks.items():
                if k.startswith("__") or not isinstance(v, dict):
                    continue
                valid_stocks.append({"ts_code": k, **v})

            if len(valid_stocks) < min_stocks:
                continue

            w_ret, w_tot = 0.0, 0.0
            for s in valid_stocks:
                r = get_adj_close_cached(s["ts_code"], rb, nrb, auto_fetch=False)
                if r:
                    w_ret += s["weight"] * (r[1] / r[0] - 1)
                    w_tot += s["weight"]

            if w_tot < min_weight:
                continue

            pr = w_ret / w_tot
            nav *= (1 + pr)
            rows.append({
                "rb_date": rb,
                "next_rb": nrb,
                "period_ret": pr * 100,
                "nav": nav,
            })
        return pd.DataFrame(rows)

    print(f"  计算 SP500风格版 NAV...")
    nav_df_ver = calc_nav(all_baskets)
    nav_df_ver.to_csv(NAV_OUTPUT, index=False)
    final_nav = nav_df_ver["nav"].iloc[-1] if len(nav_df_ver) > 0 else 0
    print(f"  ✅ SP500风格版: {len(nav_df_ver)}期, 期末NAV={final_nav:.4f}x")


# ============================================================================
# Step 3: 绩效计算 + 基准对比
# ============================================================================
if RUN_REPORT and RUN_NAV:
    print("\n" + "=" * 70)
    print("第三步：绩效报告")
    print("=" * 70)

    # 加载当前结果
    nav_df = nav_df_ver if "nav_df_ver" in dir() else pd.read_csv(NAV_OUTPUT)
    if len(nav_df) < 2:
        print("  ⚠️ 数据不足，无法计算绩效")
    else:
        # 年化收益（按调仓期数）
        total_periods = len(nav_df)
        start_nav = 1.0
        end_nav = nav_df["nav"].iloc[-1]
        years = total_periods / 4.0  # 季度调仓
        ann_return = ((end_nav / start_nav) ** (1 / years) - 1) * 100

        # 最大回撤（逐期净值）
        nav_series = pd.Series([1.0] + nav_df["nav"].tolist())
        cummax = nav_series.cummax()
        drawdown = (nav_series - cummax) / cummax * 100
        max_dd = drawdown.min()

        # 夏普（用逐期收益率）
        period_rets = nav_df["period_ret"].values / 100.0  # 转为小数
        if period_rets.std() > 0:
            ann_ret = period_rets.mean() * 4
            ann_vol = period_rets.std() * np.sqrt(4)
            sharpe = (ann_ret - 0.015) / ann_vol if ann_vol > 0 else 0
        else:
            sharpe = 0

        # 换手率估算（选300只，稳定行业分配，预期不高）
        turnover_est = "待算"

        print(f"\n  SP500风格沪深300 绩效（{nav_df['rb_date'].iloc[0]} → {nav_df['rb_date'].iloc[-1]}）:")
        print(f"    年化收益率:  {ann_return:.2f}%")
        print(f"    最大回撤:    {max_dd:.2f}%")
        print(f"    夏普比率:    {sharpe:.3f}")
        print(f"    期末 NAV:    {end_nav:.4f}x")
        print(f"    有效期数:    {len(nav_df)}/{len(REBALANCE_DATES)-1}")

        # 加载基准对比
        print(f"\n  标准对比（与 CLAUDE.md Section 4.3 对齐）：")

        # 定义基准绩效计算函数
        def calc_bench_metrics(df):
            years = len(df) / 4.0
            end = df["nav"].iloc[-1] if len(df) > 0 else 0
            ann = ((end / 1.0) ** (1 / years) - 1) * 100 if years > 0 else 0
            series = pd.Series([1.0] + df["nav"].tolist())
            dd = ((series - series.cummax()) / series.cummax() * 100).min()
            rets = df["period_ret"].values / 100.0
            if rets.std() > 0:
                sharpe = ((rets.mean() * 4 - 0.015) / (rets.std() * np.sqrt(4)))
            else:
                sharpe = 0
            return ann, dd, sharpe, end

        # 尝试加载 E 版结果
        e_nav_path = PROJECT_ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "backtest_nav_tr.csv"
        if e_nav_path.exists():
            e_df = pd.read_csv(e_nav_path)
            e_ann, e_dd, e_sharpe, e_end = calc_bench_metrics(e_df)
            print(f"    E版 FCF策略:  年化 {e_ann:.2f}% | 最大回撤 {e_dd:.2f}% | 夏普 {e_sharpe:.3f} | NAV {e_end:.2f}x")
        else:
            print(f"    E版 FCF策略:  年化 15.80% | 最大回撤 -39.66% | 夏普 0.536 | NAV 5.21x (硬编码基准)")

        # X版：全成分FCF加权 Smart Beta 基准
        x_nav_path = PROJECT_ROOT / "output" / "zz800_fcf_full_universe" / "backtest_nav_tr.csv"
        if x_nav_path.exists():
            x_df = pd.read_csv(x_nav_path)
            x_ann, x_dd, x_sharpe, x_end = calc_bench_metrics(x_df)
            print(f"    X版 FCF加权:  年化 {x_ann:.2f}% | 最大回撤 {x_dd:.2f}% | 夏普 {x_sharpe:.3f} | NAV {x_end:.2f}x")
        else:
            print(f"    X版 FCF加权:  年化 10.12% | 最大回撤 — | 夏普 — | NAV — (硬编码基准)")

        # 尝试加载 932368 指数
        idx_path = PROJECT_ROOT / "data" / "index_daily" / "932368.CSI.csv"
        if idx_path.exists():
            df_idx = pd.read_csv(idx_path)
            df_idx["trade_date"] = df_idx["trade_date"].astype(str)
            df_idx = df_idx.sort_values("trade_date")
            idx_rets = []
            for _, row in nav_periods.iterrows():
                sk = row["rb_date"].replace("-", "")
                ek = row["next_rb"].replace("-", "")
                try:
                    p0 = float(df_idx[df_idx["trade_date"] <= sk]["close"].iloc[-1])
                    p1 = float(df_idx[df_idx["trade_date"] <= ek]["close"].iloc[-1])
                    idx_rets.append((p1 / p0 - 1))
                except (IndexError, ValueError):
                    idx_rets.append(0)
            if idx_rets:
                idx_nav = np.cumprod([1 + r for r in idx_rets])
                idx_ann = ((idx_nav[-1]) ** (1 / (len(idx_rets) / 4.0)) - 1) * 100
                idx_dd_series = pd.Series(idx_nav)
                idx_dd = ((idx_dd_series - idx_dd_series.cummax()) / idx_dd_series.cummax() * 100).min()
                idx_rets_arr = np.array(idx_rets)
                idx_sharpe = ((idx_rets_arr.mean() * 4 - 0.015) / (idx_rets_arr.std() * np.sqrt(4))) if idx_rets_arr.std() > 0 else 0
                print(f"    932368:      年化 {idx_ann:.2f}% | 最大回撤 {idx_dd:.2f}% | 夏普 {idx_sharpe:.3f} | NAV {idx_nav[-1]:.2f}x")
        else:
            print(f"    932368:      年化 11.19% | 最大回撤 -39.90% | 夏普 0.358 | NAV 3.30x (硬编码基准)")

        # 沪深300 全收益（H00300.CSI）
        hs_path = PROJECT_ROOT / "data" / "index_daily" / "H00300.CSI.csv"
        if hs_path.exists():
            df_hs = pd.read_csv(hs_path)
            df_hs["trade_date"] = df_hs["trade_date"].astype(str)
            df_hs = df_hs.sort_values("trade_date")
            hs_rets = []
            for _, row in nav_periods.iterrows():
                sk = row["rb_date"].replace("-", "")
                ek = row["next_rb"].replace("-", "")
                try:
                    p0 = float(df_hs[df_hs["trade_date"] <= sk]["close"].iloc[-1])
                    p1 = float(df_hs[df_hs["trade_date"] <= ek]["close"].iloc[-1])
                    hs_rets.append((p1 / p0 - 1))
                except (IndexError, ValueError):
                    hs_rets.append(0)
            if hs_rets:
                hs_nav = np.cumprod([1 + r for r in hs_rets])
                hs_ann = ((hs_nav[-1]) ** (1 / (len(hs_rets) / 4.0)) - 1) * 100
                hs_dd_series = pd.Series(hs_nav)
                hs_dd = ((hs_dd_series - hs_dd_series.cummax()) / hs_dd_series.cummax() * 100).min()
                hs_rets_arr = np.array(hs_rets)
                hs_sharpe = ((hs_rets_arr.mean() * 4 - 0.015) / (hs_rets_arr.std() * np.sqrt(4))) if hs_rets_arr.std() > 0 else 0
                print(f"    沪深300 全收益: 年化 {hs_ann:.2f}% | 最大回撤 {hs_dd:.2f}% | 夏普 {hs_sharpe:.3f} | NAV {hs_nav[-1]:.2f}x")
        else:
            # 回退到价格指数
            hs_path_fb = PROJECT_ROOT / "data" / "index_daily" / "000300.SH.csv"
            if hs_path_fb.exists():
                df_hs_fb = pd.read_csv(hs_path_fb)
                df_hs_fb["trade_date"] = df_hs_fb["trade_date"].astype(str)
                df_hs_fb = df_hs_fb.sort_values("trade_date")
                hs_rets_fb = []
                for _, row in nav_periods.iterrows():
                    sk = row["rb_date"].replace("-", "")
                    ek = row["next_rb"].replace("-", "")
                    try:
                        p0 = float(df_hs_fb[df_hs_fb["trade_date"] <= sk]["close"].iloc[-1])
                        p1 = float(df_hs_fb[df_hs_fb["trade_date"] <= ek]["close"].iloc[-1])
                        hs_rets_fb.append((p1 / p0 - 1))
                    except (IndexError, ValueError):
                        hs_rets_fb.append(0)
                if hs_rets_fb:
                    hs_nav_fb = np.cumprod([1 + r for r in hs_rets_fb])
                    hs_ann_fb = ((hs_nav_fb[-1]) ** (1 / (len(hs_rets_fb) / 4.0)) - 1) * 100
                    print(f"    沪深300 价格:   年化 {hs_ann_fb:.2f}% (全收益指数不可用，回退价格指数)")
            else:
                print(f"    沪深300:       年化 2.36% (硬编码基准)")

        # 中证A500（000510.SH，2024年9月上线，数据较短）
        a500_path = PROJECT_ROOT / "data" / "index_daily" / "000510_A500.csv"
        if a500_path.exists():
            df_a5 = pd.read_csv(a500_path)
            df_a5["trade_date"] = df_a5["trade_date"].astype(str)
            df_a5 = df_a5.sort_values("trade_date")
            a5_min_date = df_a5["trade_date"].iloc[0]
            a5_rets = []
            a5_has = False
            for _, row in nav_periods.iterrows():
                sk = row["rb_date"].replace("-", "")
                ek = row["next_rb"].replace("-", "")
                if sk < a5_min_date:
                    continue
                try:
                    p0 = float(df_a5[df_a5["trade_date"] <= sk]["close"].iloc[-1])
                    p1 = float(df_a5[df_a5["trade_date"] <= ek]["close"].iloc[-1])
                    a5_rets.append((p1 / p0 - 1))
                    a5_has = True
                except (IndexError, ValueError):
                    pass
            if a5_has and len(a5_rets) >= 2:
                a5_nav = np.cumprod([1 + r for r in a5_rets])
                a5_years = len(a5_rets) / 4.0
                a5_ann_v = ((a5_nav[-1]) ** (1 / a5_years) - 1) * 100 if a5_years > 0 else 0
                a5_dd_v = ((pd.Series(a5_nav) - pd.Series(a5_nav).cummax()) / pd.Series(a5_nav).cummax() * 100).min()
                a5_r = np.array(a5_rets)
                a5_sharpe_v = ((a5_r.mean() * 4 - 0.015) / (a5_r.std() * np.sqrt(4))) if a5_r.std() > 0 else 0
                since_d = a5_min_date[:4] + "-" + a5_min_date[4:6] + "-" + a5_min_date[6:8]
                print(f"    中证A500:     年化 {a5_ann_v:.2f}% | 最大回撤 {a5_dd_v:.2f}% | 夏普 {a5_sharpe_v:.3f} | NAV {a5_nav[-1]:.2f}x (自{since_d})")
            else:
                print(f"    中证A500:     数据不足，无法计算 ({len(a5_rets)}期)")
        else:
            print(f"    中证A500:     数据文件不存在")

    print(f"\n✅ 完成！结果保存在: {OUT_DIR}/")

print("\n" + "=" * 70)
print("run_sp500_style.py 执行完毕")
print("=" * 70)
