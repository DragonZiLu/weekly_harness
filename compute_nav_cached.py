#!/usr/bin/env python3
"""
compute_nav_cached.py — 使用本地缓存的复权价数据计算NAV

缓存命中时直接读取，未命中时自动从Tushare下载并写入缓存，不丢弃任何持仓。

用法:
  python compute_nav_cached.py --basket <basket_json_path> --output <output_csv_path>

示例:
  python compute_nav_cached.py \
    --basket output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json \
    --output output/zz800_fcf_lenient_buffer/backtest_nav_tr.csv

  python compute_nav_cached.py \
    --basket output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json \
    --output output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv
"""
import json, argparse, time
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path('/Users/luzilong/Work/weekly_harness')
CACHE_DIR = ROOT / 'data' / 'adj_close_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

RATE_INTERVAL = 0.13   # Tushare 限速: 约460次/分钟
_last_api_call = 0.0   # 全局限速时间戳

# 延迟初始化 tushare，避免无需下载时也要求配置
_pro = None

def _get_pro():
    global _pro
    if _pro is None:
        import tushare as ts
        import sys
        sys.path.insert(0, str(ROOT / 'weekly_harness'))
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        from config.settings import tushare_cfg
        _pro = ts.pro_api(tushare_cfg.token)
    return _pro


def _rate_limit():
    global _last_api_call
    elapsed = time.time() - _last_api_call
    if elapsed < RATE_INTERVAL:
        time.sleep(RATE_INTERVAL - elapsed)
    _last_api_call = time.time()


def fetch_and_cache(ts_code: str) -> bool:
    """从Tushare下载股票全历史复权价并写入缓存，返回是否成功"""
    pro = _get_pro()
    try:
        _rate_limit()
        df_factor = pro.adj_factor(
            ts_code=ts_code, start_date="20140101", end_date="20261231",
            fields="ts_code,trade_date,adj_factor"
        )
        _rate_limit()
        df_price = pro.daily(
            ts_code=ts_code, start_date="20140101", end_date="20261231",
            fields="ts_code,trade_date,close"
        )
        if df_factor is None or df_price is None or df_factor.empty or df_price.empty:
            print(f"    ⚠️ {ts_code}: API返回空数据")
            return False

        merged = df_price.merge(
            df_factor[['trade_date', 'adj_factor']], on='trade_date', how='left'
        )
        merged['adj_factor'] = merged['adj_factor'].fillna(1.0)
        merged['adj_close'] = merged['close'] * merged['adj_factor']
        merged = merged[['trade_date', 'adj_close']].dropna()

        cache_file = CACHE_DIR / f"{ts_code}.csv"
        merged.to_csv(cache_file, index=False)
        print(f"    📥 {ts_code}: 下载完成 ({len(merged)}条)")
        return True

    except Exception as e:
        print(f"    ❌ {ts_code}: 下载失败 — {e}")
        return False


def get_adj_close_cached(ts_code, start_date, end_date, auto_fetch=True):
    """
    从本地缓存获取复权价。
    若缓存不存在且 auto_fetch=True，自动从Tushare下载后重试。
    返回 (start_price, end_price) 或 None（真正失败）。
    """
    cache_file = CACHE_DIR / f"{ts_code}.csv"

    # 缓存不存在 → 尝试下载
    if not cache_file.exists():
        if auto_fetch:
            ok = fetch_and_cache(ts_code)
            if not ok:
                return None
        else:
            return None

    try:
        df = pd.read_csv(cache_file)
    except Exception:
        return None

    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date')

    start_d = start_date.replace("-", "")
    end_d   = end_date.replace("-", "")

    # 精确区间
    mask = (df['trade_date'] >= start_d) & (df['trade_date'] <= end_d)
    period = df[mask]

    if len(period) >= 2:
        start_price = float(period.iloc[0]['adj_close'])
        end_price   = float(period.iloc[-1]['adj_close'])
    else:
        # 找最近交易日（兜底）
        before_start = df[df['trade_date'] <= start_d]
        after_end    = df[df['trade_date'] >= end_d]

        if before_start.empty or after_end.empty:
            # 缓存数据区间不覆盖所需日期 → 重新下载一次
            if auto_fetch:
                print(f"    🔄 {ts_code}: 缓存区间不覆盖 {start_d}~{end_d}，重新下载")
                fetch_and_cache(ts_code)
                return get_adj_close_cached(ts_code, start_date, end_date, auto_fetch=False)
            return None

        start_price = float(before_start.iloc[-1]['adj_close'])
        end_price   = float(after_end.iloc[0]['adj_close'])

    if start_price <= 0:
        return None

    return (start_price, end_price)


def main():
    parser = argparse.ArgumentParser(description='使用缓存复权价计算NAV（缺失时自动下载）')
    parser.add_argument('--basket',    required=True, help='篮子JSON路径')
    parser.add_argument('--output',    required=True, help='输出CSV路径')
    parser.add_argument('--nav-chain', default=None,  help='NAV日期链CSV(用于对比的基准版本)')
    parser.add_argument('--label',     default='策略', help='版本标签')
    parser.add_argument('--no-fetch',  action='store_true', help='禁止自动下载缺失缓存')
    args = parser.parse_args()

    auto_fetch = not args.no_fetch

    basket_path = Path(args.basket)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"{args.label} NAV计算(本地缓存{'，自动补全缺失' if auto_fetch else '，不自动下载'})")
    print("=" * 60)

    # 加载篮子
    with open(basket_path) as f:
        baskets = json.load(f)

    # 加载日期链
    if args.nav_chain:
        nav_df = pd.read_csv(args.nav_chain)
    else:
        # 默认用同目录下已有的或B版
        nav_df = pd.read_csv(basket_path.parent / 'backtest_nav_tr.csv') \
            if (basket_path.parent / 'backtest_nav_tr.csv').exists() \
            else pd.read_csv(ROOT / 'output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv')

    rb_dates = nav_df['rb_date'].tolist()
    next_rbs = nav_df['next_rb'].tolist()

    # 加载对比链（B版或指定）
    cmp_nav = None
    cmp_path = ROOT / 'output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv'
    if cmp_path.exists():
        cmp_nav = pd.read_csv(cmp_path)

    valid_dates = sorted([d for d in baskets if len(baskets[d]) >= 10])
    print(f"  篮子有效期数: {len(valid_dates)} / {len(baskets)}")
    print(f"  NAV期数: {len(rb_dates)}")
    print(f"  缓存文件: {len(list(CACHE_DIR.glob('*.csv')))}只")

    nav = 1.0
    nav_records = []
    n_cache_hits = 0
    n_cache_misses = 0
    n_downloaded = 0

    for i, rb_date in enumerate(rb_dates):
        # 找对应篮子
        if rb_date not in baskets or len(baskets[rb_date]) < 10:
            closest = next((d for d in valid_dates if d >= rb_date), valid_dates[-1])
            basket = baskets[closest]
            actual_date = closest
        else:
            basket = baskets[rb_date]
            actual_date = rb_date

        next_rb = next_rbs[i]
        weights = {s['ts_code']: s['weight'] for s in basket}

        weighted_ret = 0.0
        valid_weight = 0.0
        n_valid = 0

        for code, w in weights.items():
            cache_existed = (CACHE_DIR / f"{code}.csv").exists()
            result = get_adj_close_cached(code, actual_date, next_rb, auto_fetch=auto_fetch)
            if not cache_existed and (CACHE_DIR / f"{code}.csv").exists():
                n_downloaded += 1
            if result is not None:
                start_p, end_p = result
                ret = end_p / start_p - 1
                weighted_ret += w * ret
                valid_weight += w
                n_valid += 1
                n_cache_hits += 1
            else:
                n_cache_misses += 1

        # 归一化：若仍有股票真正缺失（下载失败），将有效权重归一到1.0
        if 0 < valid_weight < 1.0 - 1e-6:
            weighted_ret = weighted_ret / valid_weight
        period_ret = weighted_ret
        nav *= (1 + period_ret)

        # 对比信息
        cmp_info = ""
        if cmp_nav is not None and i < len(cmp_nav):
            cmp_row = cmp_nav.iloc[i]
            if cmp_row.get('rb_date','') == rb_date:  # only compare if dates align
                b_ret = cmp_row.get('ret') or cmp_row.get('period_ret', 0)
                diff  = (period_ret - b_ret) * 100
                if abs(diff) > 0.5:
                    cmp_info = f", B版={b_ret*100:.2f}%, diff={diff:+.2f}pp"

        nav_records.append({
            'rb_date': rb_date,
            'next_rb': next_rb,
            'ret':     round(period_ret, 6),
            'nav':     round(nav, 6),
            'n_valid': n_valid,
        })

        print(f"[{i+1}/{len(rb_dates)}] {rb_date} → {next_rb}: "
              f"ret={period_ret*100:.2f}%, NAV={nav:.4f}, "
              f"n_valid={n_valid}/{len(weights)}{cmp_info}")

    # 保存
    df_out = pd.DataFrame(nav_records)
    df_out.to_csv(output_path, index=False)

    n_years = len(nav_records) / 4
    annual  = (nav ** (1 / n_years) - 1) * 100

    print(f"\n{'='*60}")
    print(f"✅ {args.label} NAV计算完成!")
    print(f"   最终NAV:   {nav:.4f}")
    print(f"   年化收益:  {annual:.2f}%")
    print(f"   回测期间:  {rb_dates[0]} ~ {next_rbs[-1]} ({n_years:.1f}年)")
    print(f"   缓存命中:  {n_cache_hits},  未命中: {n_cache_misses}")
    if n_downloaded:
        print(f"   本次下载:  {n_downloaded}只新缓存")
    print(f"   零收益期:  {sum(1 for r in df_out['ret'] if r == 0)}")
    print(f"   输出:      {output_path}")

    if cmp_nav is not None:
        b_final  = cmp_nav.iloc[-1]['nav']
        b_annual = (b_final ** (1 / n_years) - 1) * 100
        print(f"\n   B版对比: NAV={b_final:.4f}, 年化={b_annual:.2f}%")
        print(f"   差异:    {annual - b_annual:+.2f}pp")


if __name__ == "__main__":
    main()
