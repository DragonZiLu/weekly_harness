"""
ZZ800 万宝路挖掘 — 10年股息再投（本地缓存版）
===============================================
策略：先拉数据到本地，再离线计算，彻底避开 Tushare 频率限制

数据源：
  - 日线价格: data/adj_close_cache/{ts_code}.csv (已有 1832 只)
  - 分红数据: data/dividend_cache/{ts_code}.csv (本脚本首次下载)

用法：
  python mine_marlboro_zz800_10y_local.py          # 全量（下载+计算）
  python mine_marlboro_zz800_10y_local.py --calc-only  # 仅计算（数据已下载）
"""
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Tuple
from dataclasses import dataclass

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

import tushare as ts
import pandas as pd
from config.settings import tushare_cfg

ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

# ─── 常量 ──────────────────────────────────────────────
BUY_DATE = "2015-01-05"
YEARS = 10
LIST_CUTOFF = "20150101"
INDEX_CODE = "000906.SH"

COMMISSION_RATE = 0.00025
LOT_SIZE = 100

PRICE_CACHE = _PROJECT_ROOT / "data" / "adj_close_cache"
DIV_CACHE = _PROJECT_ROOT / "data" / "dividend_cache"
DIV_CACHE.mkdir(parents=True, exist_ok=True)

# 万宝路阈值
DIV_CONTRIB_THRESHOLD = 0.6
DIV_RATIO_THRESHOLD = 50
MIN_TOTAL_RET = 0

# ─── 数据结构 ──────────────────────────────────────────


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


@dataclass
class DivEvent:
    date: str           # 除权除息日
    cash_per_share: float
    stk_div: float = 0
    close: float = 0   # 除权日收盘价


# ─── Step 0: 下载分红数据 ──────────────────────────────


def download_dividends(ts_code: str) -> bool:
    """下载单只股票的全量分红数据到本地缓存"""
    cache_file = DIV_CACHE / f"{ts_code}.csv"
    if cache_file.exists():
        return True  # 已有缓存

    try:
        df = pro.dividend(
            ts_code=ts_code,
            fields="ts_code,ann_date,ex_date,record_date,cash_div,base_share,stk_div",
        )
        if df is None or df.empty:
            # 无分红记录，也写个空文件避免重复请求
            cache_file.write_text("")
            return True

        df.to_csv(cache_file, index=False)
        time.sleep(0.06)  # Tushare 免费版限频
        return True
    except Exception as e:
        time.sleep(0.5)
        return False


def download_all_dividends(codes: list) -> tuple:
    """批量下载分红数据（带重试）"""
    print(f"\n  📥 下载分红数据 → data/dividend_cache/")
    print(f"     目标: {len(codes)} 只")

    success = 0
    failed = []
    for i, code in enumerate(codes):
        if (i + 1) % 100 == 0:
            print(f"     进度: {i+1}/{len(codes)} (成功{success}, 失败{len(failed)})")

        ok = download_dividends(code)
        if ok:
            success += 1
        else:
            failed.append(code)

    # 重试失败的
    if failed:
        print(f"  🔄 重试 {len(failed)} 只失败标的...")
        still_failed = []
        for code in failed:
            time.sleep(1.0)
            ok = download_dividends(code)
            if not ok:
                still_failed.append(code)
        failed = still_failed

    print(f"  ✅ 分红缓存完成: {success}/{len(codes)} 成功")
    if failed:
        print(f"  ⚠️ 仍失败 {len(failed)} 只: {failed[:5]}...")
    return success, failed


# ─── 从本地缓存加载数据 ────────────────────────────────


def load_prices_local(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """从本地 adj_close_cache 加载日线价格"""
    cache_file = PRICE_CACHE / f"{ts_code}.csv"
    if not cache_file.exists():
        return pd.DataFrame()

    df = pd.read_csv(cache_file)

    # 处理 trade_date 可能是 float (如 20150105.0) 的情况
    if df["trade_date"].dtype == "float64" or df["trade_date"].dtype == "float":
        df = df.dropna(subset=["trade_date"])
        if df.empty:
            return pd.DataFrame()
        # 先转 int 去掉 .0，再转字符串
        df["trade_date"] = df["trade_date"].astype("int64").astype(str).str.zfill(8)
    else:
        df["trade_date"] = df["trade_date"].astype(str).str.zfill(8)

    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 处理缺少 close 列的情况：用 adj_close 代替（会有轻微偏差但远好过丢弃）
    if "close" not in df.columns and "adj_close" in df.columns:
        df["close"] = df["adj_close"]
    elif "close" not in df.columns:
        return pd.DataFrame()  # 完全没有可用价格

    start_dt = pd.Timestamp(start[:10])
    end_dt = pd.Timestamp(end[:10])
    df = df[(df["trade_date"] >= start_dt) & (df["trade_date"] <= end_dt)]
    return df


def load_dividends_local(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """从本地 dividend_cache 加载分红数据"""
    cache_file = DIV_CACHE / f"{ts_code}.csv"
    if not cache_file.exists():
        return pd.DataFrame()

    # 空文件 = 无分红
    if cache_file.stat().st_size == 0:
        return pd.DataFrame()

    df = pd.read_csv(cache_file)
    if df.empty:
        return df

    # 筛选有现金分红的
    df = df[df["cash_div"].fillna(0) > 0].copy()

    # 解析日期 — Tushare 存的是 float (如 20260612.0)，需先转 int 去掉 .0
    if df["ex_date"].dtype == "float64" or df["ex_date"].dtype == "float":
        df["ex_date"] = (df["ex_date"].fillna(0).astype("int64").astype(str).str.zfill(8))
    else:
        df["ex_date"] = df["ex_date"].astype(str).str.zfill(8)
    df["ex_date"] = pd.to_datetime(df["ex_date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["ex_date"]).sort_values("ex_date")

    # 时间范围过滤
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


# ─── 本地版模拟（零 API 调用）────────────────────────────


def simulate_local(
    ts_code: str, buy_date: str, cash: float, years: int = 10
) -> Tuple[List[YearRow], float, float, float]:
    """本地版股息再投模拟 — 价格和分红均从本地缓存读取"""

    start_dt = pd.Timestamp(buy_date[:10])
    end_dt = start_dt + timedelta(days=365 * years)
    start_str = buy_date[:10]
    end_str = end_dt.strftime("%Y-%m-%d")

    # 从本地加载
    price_df = load_prices_local(ts_code, start_str, end_str)
    div_df = load_dividends_local(ts_code, start_str, end_str)

    if price_df.empty:
        return [], 0, 0, 1.0

    buy_price = get_price_on_date(price_df, start_str)
    if buy_price <= 0:
        return [], 0, 0, 1.0

    # 买入
    commission = cash * COMMISSION_RATE
    invest = cash - commission
    if buy_price <= 0 or pd.isna(buy_price):
        return [], 0, 0, 1.0

    max_shares = invest / buy_price
    if pd.isna(max_shares) or max_shares <= 0:
        return [], 0, 0, 1.0
    shares = int(max_shares // LOT_SIZE * LOT_SIZE)
    actual_invest = shares * buy_price + shares * buy_price * COMMISSION_RATE
    remaining_cash = cash - actual_invest

    # 构建除权事件
    events: List[DivEvent] = []
    for _, row in div_df.iterrows():
        ex_dt = row["ex_date"]
        ex_str = ex_dt.strftime("%Y-%m-%d") if isinstance(ex_dt, pd.Timestamp) else str(ex_dt)[:10]
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
    split_factor = 1.0

    for y in range(years):
        year = start_dt.year + y
        y_start = start_dt if y == 0 else pd.Timestamp(f"{year}-01-01")
        y_end = end_dt if y == years - 1 else pd.Timestamp(f"{year}-12-31")

        y_start_val = shares * get_price_on_date(price_df, y_start.strftime("%Y-%m-%d")) + remaining_cash
        if y_start_val <= 0:
            y_start_val = 1.0  # 避免除零

        y_div_cash = 0.0
        y_div_count = 0
        for ev in events:
            ev_year = pd.Timestamp(ev.date).year
            if ev_year == year:
                # 送转股
                if ev.stk_div > 0:
                    bonus_ratio = ev.stk_div / 10.0
                    bonus_shares = int(shares * bonus_ratio)
                    shares += bonus_shares
                    split_factor *= (1.0 + bonus_ratio)

                # 现金分红再投
                div_amount = shares * ev.cash_per_share
                if pd.isna(div_amount) or div_amount <= 0:
                    continue
                holding_days = (pd.Timestamp(ev.date) - start_dt).days
                if holding_days < 365:
                    tax_rate = 0.20 if holding_days < 30 else 0.10
                    div_amount *= (1 - tax_rate)

                if ev.close > 0 and div_amount > 0:
                    ratio = div_amount / ev.close
                    if pd.isna(ratio):
                        remaining_cash += div_amount
                    else:
                        new_shares = int(ratio // LOT_SIZE * LOT_SIZE)
                        if new_shares > 0:
                            commission_div = new_shares * ev.close * COMMISSION_RATE
                            remaining_cash += div_amount - (new_shares * ev.close + commission_div)
                            shares += new_shares
                        else:
                            remaining_cash += div_amount
                    y_div_cash += div_amount
                    y_div_count += 1
                elif div_amount > 0:
                    remaining_cash += div_amount
                    y_div_cash += div_amount
                    y_div_count += 1

        total_div_cash += y_div_cash
        total_div_count += y_div_count

        y_end_price = get_price_on_date(price_df, y_end.strftime("%Y-%m-%d"))
        y_end_val = shares * y_end_price + remaining_cash
        y_total_ret = (y_end_val / y_start_val - 1) * 100 if y_start_val > 0 else 0
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


# ─── 主流程 ────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calc-only", action="store_true", help="仅计算，跳过数据下载")
    args = parser.parse_args()

    print("=" * 70)
    print("  ZZ800 万宝路挖掘 — 10年 (本地缓存版)")
    print("=" * 70)

    # ── 获取成分股 ──
    print("\n[1/4] 获取 ZZ800 成分股...")
    df = pro.index_weight(index_code=INDEX_CODE, trade_date='20260529')
    all_codes = sorted(set(df['con_code'].tolist()))
    print(f"  成分股: {len(all_codes)} 只")

    # 查上市日期 + 名称 + 行业
    name_map = {}
    list_date_map = {}
    for i in range(0, len(all_codes), 200):
        batch = all_codes[i:i+200]
        sb = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry,list_date')
        for _, r in sb.iterrows():
            name_map[r['ts_code']] = (r['name'], r.get('industry', '') or '')
            list_date_map[r['ts_code']] = r.get('list_date', '')
        time.sleep(0.3)

    before_2015 = [c for c in all_codes if list_date_map.get(c, '9999') < LIST_CUTOFF]
    print(f"  2015年前上市: {len(before_2015)} 只 (剔除 {len(all_codes) - len(before_2015)} 只)")

    # ── 下载分红数据 ──
    if not args.calc_only:
        print("\n[2/4] 下载分红数据...")
        download_all_dividends(before_2015)
    else:
        print(f"\n[2/4] 跳过下载 (--calc-only)")
        existing = len(list(DIV_CACHE.glob("*.csv")))
        print(f"  分红缓存已有: {existing} 只")

    # ── 检查价格缓存覆盖率 ──
    print("\n[3/4] 检查价格缓存覆盖率...")
    price_missing = [c for c in before_2015 if not (PRICE_CACHE / f"{c}.csv").exists()]
    print(f"  价格缓存缺失: {len(price_missing)} 只")
    if price_missing:
        print(f"  缺失列表(前20): {[name_map.get(c, (c,))[0] for c in price_missing[:20]]}")
        print(f"  ⚠️ 需先运行: python cache_adj_close.py")
        # 只计算有缓存的
        codes_to_run = [c for c in before_2015 if c not in price_missing]
        print(f"  实际可计算: {len(codes_to_run)} 只")
    else:
        codes_to_run = before_2015

    # ── 逐只计算 ──
    print(f"\n[4/4] 逐只评估 10年股息再投 (本地计算, 无API调用)...")
    print(f"  标的数: {len(codes_to_run)} 只")

    results = []
    errors = 0
    t0 = time.time()

    for idx, code in enumerate(codes_to_run):
        name, industry = name_map.get(code, (code[:6], ''))

        if idx % 50 == 0:
            elapsed = time.time() - t0
            rate = idx / elapsed if elapsed > 0 else 0
            print(f"  进度: {idx}/{len(codes_to_run)} (速率={rate:.1f}只/s) ...")

        try:
            rows, final_val, cagr, split_factor = simulate_local(code, BUY_DATE, 100000, years=YEARS)
            if not rows:
                continue

            buy_price = rows[0].start_value / rows[0].shares if rows[0].shares > 0 else 0
            last_price = rows[-1].price
            eff_buy = buy_price / split_factor if split_factor > 0 else buy_price
            price_chg = (last_price / eff_buy - 1) * 100 if eff_buy > 0 else 0
            total_ret = (final_val / 100000 - 1) * 100
            div_contrib = total_ret - price_chg
            div_total = sum(r.div_cash for r in rows)
            div_ratio = div_total / 100000 * 100
            div_amplify = (rows[-1].shares / rows[0].shares - 1) * 100 if rows[0].shares > 0 else 0
            max_yr_loss = min(r.total_return for r in rows)

            results.append({
                'code': code, 'name': name, 'industry': industry,
                'total_ret': total_ret, 'cagr': cagr,
                'price_chg': price_chg, 'div_contrib': div_contrib,
                'div_ratio': div_ratio, 'div_amplify': div_amplify,
                'buy_price': buy_price, 'last_price': last_price,
                'div_total': div_total, 'max_yr_loss': max_yr_loss,
            })

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  ⚠️ {name}({code}): {e}")

    elapsed = time.time() - t0
    print(f"\n  完成: {len(results)} 只有效数据, {errors} 只失败, 耗时 {elapsed:.0f}s")

    # ── 输出 ──
    output_dir = _PROJECT_ROOT / "data"
    output_dir.mkdir(exist_ok=True)

    if not results:
        print("❌ 无结果")
        return

    # 全量保存
    df_all = pd.DataFrame(results)
    cols_order = ['code', 'name', 'industry', 'total_ret', 'cagr', 'price_chg',
                  'div_contrib', 'div_ratio', 'div_amplify', 'div_total',
                  'buy_price', 'last_price', 'max_yr_loss']
    df_all = df_all[[c for c in cols_order if c in df_all.columns]]
    df_all.to_csv(output_dir / "marlboro_zz800_10y_all.csv", index=False, encoding='utf-8-sig')
    print(f"  💾 全量 {len(results)} 只 → data/marlboro_zz800_10y_all.csv")

    # 万宝路筛选
    results.sort(key=lambda x: x['div_contrib'] / max(x['total_ret'], 0.01), reverse=True)

    marlboro = [
        r for r in results
        if r['div_contrib'] > 0
        and r['div_contrib'] / max(r['total_ret'], 0.01) > DIV_CONTRIB_THRESHOLD
        and r['div_ratio'] > DIV_RATIO_THRESHOLD
        and r['total_ret'] > MIN_TOTAL_RET
    ]

    stable = [
        r for r in results
        if r['div_contrib'] > 0
        and r['div_contrib'] / max(r['total_ret'], 0.01) > 0.4
        and r['div_ratio'] > 40
        and r['total_ret'] > 30
        and r not in marlboro
    ]

    print("\n" + "=" * 90)
    print(f"  🚬 万宝路型标的 (ZZ800, 10年窗口) — {len(marlboro)} 只")
    print("=" * 90)

    print(f"\n  {'名称':<8} {'代码':<12} {'总收益':>7} {'CAGR':>6} {'股息贡献':>8} {'股息占比':>7} {'分红/本金':>8} {'股价变化':>7} {'最差年':>6} {'行业'}")
    print(f"  {'─' * 100}")
    for r in marlboro:
        div_pct = r['div_contrib'] / r['total_ret'] * 100 if r['total_ret'] > 0 else 0
        print(f"  {r['name']:<8} {r['code']:<12} {r['total_ret']:>+6.1f}% {r['cagr']:>+5.1f}% "
              f"{r['div_contrib']:>+7.1f}% {div_pct:>6.0f}% {r['div_ratio']:>7.0f}% "
              f"{r['price_chg']:>+6.1f}% {r['max_yr_loss']:>+5.1f}% {r['industry']}")

    if stable:
        print(f"\n  {'─' * 100}")
        print(f"  📊 稳健红利型 — {len(stable)} 只")
        print(f"  {'─' * 100}")
        for r in stable[:15]:
            div_pct = r['div_contrib'] / r['total_ret'] * 100 if r['total_ret'] > 0 else 0
            print(f"  {r['name']:<8} {r['code']:<12} {r['total_ret']:>+6.1f}% {r['cagr']:>+5.1f}% "
                  f"{r['div_contrib']:>+7.1f}% {div_pct:>6.0f}% {r['div_ratio']:>7.0f}% "
                  f"{r['price_chg']:>+6.1f}% {r['max_yr_loss']:>+5.1f}% {r['industry']}")

    # 保存分类结果
    pd.DataFrame(marlboro).to_csv(output_dir / "marlboro_zz800_10y_pure.csv", index=False, encoding='utf-8-sig')
    pd.DataFrame(stable).to_csv(output_dir / "marlboro_zz800_10y_stable.csv", index=False, encoding='utf-8-sig')
    print(f"\n  💾 万宝路 {len(marlboro)} 只 → data/marlboro_zz800_10y_pure.csv")
    print(f"  💾 稳健 {len(stable)} 只 → data/marlboro_zz800_10y_stable.csv")

    # Top 20
    print(f"\n\n  {'─' * 100}")
    print(f"  🏆 ZZ800 股息再投 10年总收益 Top 20")
    print(f"  {'─' * 100}")
    all_sorted = sorted(results, key=lambda x: x['total_ret'], reverse=True)
    for rank, r in enumerate(all_sorted[:20], 1):
        div_pct = r['div_contrib'] / r['total_ret'] * 100 if r['total_ret'] > 0 else 0
        tag = "🚬" if r in marlboro else ("⭐" if r in stable else "  ")
        print(f"  {rank:>2}. {tag} {r['name']:<8} {r['code']:<12} "
              f"{r['total_ret']:>+6.1f}% {r['cagr']:>+5.1f}% "
              f"股息{div_pct:.0f}% 分红{r['div_ratio']:.0f}% {r['industry']}")

    print()


if __name__ == "__main__":
    main()
