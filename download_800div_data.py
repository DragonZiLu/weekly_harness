#!/usr/bin/env python3
"""
download_800div_data.py — 预下载中证800红利指数（931644）所需数据

下载内容：
  1. dividend 接口：所有中证800成分股的分红历史（cash_div_tax, end_date, div_proc等）
    → 存储到 data/dividend_history/{ts_code}.csv
  2. income 接口：归母净利润（用于计算股利支付率）
    → 若 data/fcf_financials/income_{year}.csv 已存在则复用
    → 缺失年份调用 Tushare 下载

使用方式：
  python download_800div_data.py           # 下载全部（分红+净利润）
  python download_800div_data.py --div-only   # 仅下载分红
  python download_800div_data.py --inc-only   # 仅下载净利润
"""

import sys, os, time, argparse
from pathlib import Path
from typing import Optional, List
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from config.settings import tushare_cfg
import tushare as ts

# ─── 路径设置 ────────────────────────────────────────────────
DIV_DIR = PROJECT_ROOT / "data" / "dividend_history"
FCF_DIR = PROJECT_ROOT / "data" / "fcf_financials"
IDX_DIR = PROJECT_ROOT / "data" / "index_weights"

DIV_DIR.mkdir(parents=True, exist_ok=True)
FCF_DIR.mkdir(parents=True, exist_ok=True)

RATE_INTERVAL = 0.15  # Tushare 限速间隔（秒）

# ─── Tushare 初始化 ─────────────────────────────────────────
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

_last_call = 0.0

def rate_limit():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < RATE_INTERVAL:
        time.sleep(RATE_INTERVAL - elapsed)
    _last_call = time.time()


# ══════════════════════════════════════════════════════════════
# 1. 获取 CSI800 成分股列表
# ══════════════════════════════════════════════════════════════

def get_zz800_stocks() -> List[str]:
    """获取中证800成分股列表（所有出现过的一级以上标的）"""
    cached = IDX_DIR / "index_weight_000906.SH.csv"
    if cached.exists():
        df = pd.read_csv(cached, dtype={"con_code": str})
        codes = sorted(df["con_code"].unique().tolist())
        print(f"  📂 CSI800 成分股缓存: {len(codes)}只（全历史）")
        return codes

    # 从 Tushare 下载
    print("  下载 CSI800 成分股权重...")
    dfs = []
    for s, e in [("20140101","20161231"),("20170101","20191231"),
                 ("20200101","20221231"),("20230101","20261231")]:
        rate_limit()
        df = pro.index_weight(index_code="000906.SH", start_date=s, end_date=e)
        if df is not None and not df.empty:
            dfs.append(df)
            print(f"    {s[:4]}-{e[:4]}: {len(df)}条")
    if dfs:
        full = pd.concat(dfs, ignore_index=True)
        full.to_csv(cached, index=False)
        codes = sorted(full["con_code"].unique().tolist())
        print(f"  ✅ 共{len(codes)}只CSI800成分股（全历史）")
        return codes
    return []


# ══════════════════════════════════════════════════════════════
# 2. 下载分红历史数据
# ══════════════════════════════════════════════════════════════

def download_dividend_history(stocks: List[str]):
    """逐只下载分红历史，缓存到 data/dividend_history/{ts_code}.csv"""
    print(f"\n{'='*60}")
    print(f"下载分红历史（{len(stocks)}只标的）...")
    print(f"{'='*60}")

    already = set(f.stem for f in DIV_DIR.glob("*.csv"))
    to_fetch = [s for s in stocks if s not in already]
    print(f"  已有缓存: {len(already)}只, 待下载: {len(to_fetch)}只")

    if not to_fetch:
        print("  ✅ 全部分红数据已缓存，跳过下载")
        return

    n_ok, n_fail, n_empty = 0, 0, 0
    for i, ts_code in enumerate(to_fetch):
        try:
            rate_limit()
            df = pro.dividend(
                ts_code=ts_code,
                fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,"
                       "ex_date,pay_date,stk_div,cash_div,base_share,"
                       "record_date"
            )
            if df is not None and not df.empty:
                df = df.sort_values("end_date", ascending=False)
                df.to_csv(DIV_DIR / f"{ts_code}.csv", index=False)
                n_years = df["end_date"].astype(str).str[:4].nunique()
                n_ok += 1
                if (i + 1) % 30 == 0:
                    print(f"  [{i+1}/{len(to_fetch)}] {ts_code}: {len(df)}条, {n_years}年 — ok")
            else:
                # 空结果：可能是无分红记录（如上市时间短），也保存空文件标记
                pd.DataFrame(columns=["ts_code"]).to_csv(
                    DIV_DIR / f"{ts_code}.csv", index=False
                )
                n_empty += 1
        except Exception as e:
            n_fail += 1
            if n_fail <= 5:
                print(f"  ❌ {ts_code}: {e}")
            time.sleep(0.5)  # 失败后多等一会

    print(f"\n  ✅ 分红下载完成: 有数据={n_ok}, 无分红={n_empty}, 失败={n_fail}")
    if n_fail > 0:
        print(f"  ⚠️ 有{n_fail}只下载失败，可重新运行本脚本补全")


# ══════════════════════════════════════════════════════════════
# 3. 下载净利润数据（income表 — 归母净利润）
# ══════════════════════════════════════════════════════════════

def download_income_data(stocks: List[str]):
    """
    下载income表数据，补充 n_income（归母净利润）字段用于计算股利支付率。

    若 data/fcf_financials/income_{year}.csv 已存在，跳过该年。
    否则调用 Tushare income 接口按年下载。
    """
    print(f"\n{'='*60}")
    print(f"下载净利润数据（income表）...")
    print(f"{'='*60}")

    years = list(range(2012, 2027))  # 2012-2026 覆盖回测所需
    need_years = [y for y in years if not (FCF_DIR / f"income_{y}.csv").exists()]

    # 同时也检查已有缓存中是否包含 n_income 字段
    for y in years:
        p = FCF_DIR / f"income_{y}.csv"
        if p.exists():
            df = pd.read_csv(p, nrows=1, dtype=str)
            if "n_income" in df.columns or "n_income_attr_p" in df.columns:
                if y in need_years:
                    need_years.remove(y)

    if not need_years:
        print("  ✅ 所有年份income数据已缓存（含n_income字段），跳过下载")
        return

    print(f"  需下载年份: {need_years}")

    n_ok, n_fail = 0, 0
    for year in need_years:
        end_date = f"{year}1231"
        try:
            rate_limit()
            # 用全市场查询方式获取（按end_date拉取）
            df = pro.income(
                ts_code="",
                end_date=end_date,
                fields="ts_code,ann_date,f_ann_date,end_date,"
                       "n_income,n_income_attr_p,revenue,operate_profit,"
                       "total_profit,income_tax,"
                       "total_hldr_eqy_inc_min_int_interest,"
                       "n_income_attr_p,"
                       "basic_eps,diluted_eps"
            )
            if df is not None and not df.empty:
                df.to_csv(FCF_DIR / f"income_{year}.csv", index=False)
                n_ok += 1
                print(f"  ✅ {year}年报: {len(df)}只")
            else:
                print(f"  ⚠️ {year}年报: 空数据")
        except Exception as e:
            print(f"  ❌ {year}年报: {e}")
            n_fail += 1

    print(f"\n  ✅ income下载完成: 成功={n_ok}, 失败={n_fail}")


# ══════════════════════════════════════════════════════════════
# 4. 验证缓存完整性
# ══════════════════════════════════════════════════════════════

def verify_cache(stocks: List[str]):
    """快速统计缓存覆盖率"""
    div_cached = [s for s in stocks if (DIV_DIR / f"{s}.csv").exists()]
    div_files = list(DIV_DIR.glob("*.csv"))
    div_with_data = sum(1 for f in div_files if f.stat().st_size > 50)

    inc_years = sorted([int(f.stem.split("_")[-1]) for f in FCF_DIR.glob("income_20*.csv")
                        if f.stem.split("_")[-1].isdigit()])

    print(f"\n{'='*60}")
    print(f"缓存验证")
    print(f"{'='*60}")
    print(f"  分红历史: {div_with_data}/{len(div_files)}个文件有数据")
    print(f"  成分股覆盖率: {len(div_cached)}/{len(stocks)} ({len(div_cached)/max(len(stocks),1)*100:.1f}%)")
    print(f"  Income数据年份: {inc_years[0]}-{inc_years[-1] if inc_years else 'N/A'} "
          f"({len(inc_years)}年)")
    return div_with_data


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="下载800红利指数所需数据")
    parser.add_argument("--div-only", action="store_true", help="仅下载分红历史")
    parser.add_argument("--inc-only", action="store_true", help="仅下载净利润数据")
    args = parser.parse_args()

    do_div = args.div_only or (not args.inc_only)
    do_inc = args.inc_only or (not args.div_only)

    print("=" * 60)
    print("  中证800红利指数（931644）数据预下载")
    print("=" * 60)

    # 获取成分股列表
    stocks = get_zz800_stocks()
    if not stocks:
        print("❌ 无法获取CSI800成分股列表，请检查Tushare配置或网络")
        return

    # 下载分红历史
    if do_div:
        download_dividend_history(stocks)

    # 下载净利润数据
    if do_inc:
        download_income_data(stocks)

    # 验证
    verify_cache(stocks)

    print("\n✅ 数据预下载完成！")


if __name__ == "__main__":
    main()
