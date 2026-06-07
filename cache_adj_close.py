#!/usr/bin/env python3
"""
cache_adj_close.py — 批量下载HS300+ZZ800成分股复权价数据到本地缓存

缓存结构: data/adj_close_cache/{ts_code}.csv
每只股票: trade_date, close, adj_factor, adj_close
时间范围: 2015-01-01 ~ 今天

用法:
  python cache_adj_close.py          # 全量下载(首次)
  python cache_adj_close.py --update # 只更新到今天(增量)
"""
import os, time, json, argparse
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path('/Users/luzilong/Work/weekly_harness/.env'))
import tushare as ts
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()

ROOT = Path('/Users/luzilong/Work/weekly_harness')
CACHE_DIR = ROOT / 'data' / 'adj_close_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = "20150101"
END_DATE = pd.Timestamp.now().strftime("%Y%m%d")

# 指数成分股列表
INDEX_CODES = {
    '000300.SH': 'HS300',
    '000906.SH': 'ZZ800',  # ZZ800 = HS300 + ZZ500
}


def get_index_constituents(index_code):
    """获取指数当前成分股列表"""
    # 使用 index_weight 接口获取最新成分股
    df = pro.index_weight(index_code=index_code, start_date="20250101", end_date=END_DATE)
    if df is not None and not df.empty:
        latest_date = df['trade_date'].max()
        latest = df[df['trade_date'] == latest_date]
        codes = latest['con_code'].unique().tolist()
        print(f"  {index_code}: {len(codes)}只成分股 (日期: {latest_date})")
        return codes
    return []


def get_all_constituents():
    """获取HS300+ZZ800的全部成分股(去重)"""
    all_codes = set()
    for idx_code, idx_name in INDEX_CODES.items():
        codes = get_index_constituents(idx_code)
        all_codes.update(codes)
    # 再加上篮子历史中出现过的股票
    for basket_path in [
        ROOT / 'output/hs300_fcf_fixed_lenient/all_baskets_2015_2026.json',
        ROOT / 'output/hs300_fcf_lenient_buffer/all_baskets_2015_2026.json',
        ROOT / 'output/hs300_fcf/all_baskets_2015_2026.json',
    ]:
        if basket_path.exists():
            with open(basket_path) as f:
                baskets = json.load(f)
            for rb_date, stocks in baskets.items():
                if isinstance(stocks, list):
                    for s in stocks:
                        if isinstance(s, dict) and 'ts_code' in s:
                            all_codes.add(s['ts_code'])
    
    print(f"  总计: {len(all_codes)}只股票需缓存")
    return sorted(all_codes)


def cache_stock(ts_code, update_mode=False):
    """下载单只股票复权价数据并缓存"""
    cache_file = CACHE_DIR / f"{ts_code}.csv"
    
    if update_mode and cache_file.exists():
        # 增量: 只拉最新日期之后的数据
        existing = pd.read_csv(cache_file)
        last_date = str(existing['trade_date'].max())
        start = last_date
    else:
        start = START_DATE
    
    # 下载 daily 数据
    try:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=END_DATE,
                       fields="ts_code,trade_date,close")
        if df is None or df.empty:
            return False
        time.sleep(0.05)
    except Exception as e:
        time.sleep(0.3)
        return False
    
    # 下载复权因子
    try:
        df_adj = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=END_DATE)
        if df_adj is None or df_adj.empty:
            return False
        time.sleep(0.05)
    except Exception as e:
        time.sleep(0.3)
        return False
    
    # 合并
    df['trade_date'] = df['trade_date'].astype(str)
    df_adj['trade_date'] = df_adj['trade_date'].astype(str)
    merged = df.merge(df_adj[['trade_date', 'adj_factor']], on='trade_date', how='left')
    merged = merged.sort_values('trade_date')
    merged['adj_factor'] = merged['adj_factor'].ffill().bfill()
    merged['adj_close'] = merged['close'].astype(float) * merged['adj_factor'].astype(float)
    
    # 增量模式: 合并旧数据
    if update_mode and cache_file.exists():
        existing = pd.read_csv(cache_file)
        existing['trade_date'] = existing['trade_date'].astype(str)
        # 去重合并
        combined = pd.concat([existing, merged], ignore_index=True)
        combined = combined.drop_duplicates(subset='trade_date', keep='last')
        combined = combined.sort_values('trade_date')
        merged = combined
    
    merged.to_csv(cache_file, index=False)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--update', action='store_true', help='增量更新(只拉最新日期之后)')
    args = parser.parse_args()
    
    print("=" * 60)
    print("复权价缓存下载器")
    print(f"模式: {'增量更新' if args.update else '全量下载'}")
    print(f"缓存目录: {CACHE_DIR}")
    print("=" * 60)
    
    # 获取成分股列表
    print("\nStep 1: 获取成分股列表...")
    all_codes = get_all_constituents()
    
    # 检查已有缓存
    existing = set(f.stem for f in CACHE_DIR.glob("*.csv"))
    need_download = [c for c in all_codes if c not in existing]
    need_update = [c for c in all_codes if c in existing]
    
    if args.update:
        print(f"\nStep 2: 增量更新 {len(all_codes)}只股票...")
        targets = all_codes
    else:
        print(f"\nStep 2: 全量下载...")
        print(f"  已缓存: {len(existing)}只")
        print(f"  需下载: {len(need_download)}只")
        targets = need_download
    
    success = 0
    failed = 0
    t0 = time.time()
    
    for i, code in enumerate(targets):
        ok = cache_stock(code, update_mode=args.update)
        if ok:
            success += 1
        else:
            failed += 1
        
        if (i+1) % 50 == 0 or i == len(targets)-1:
            elapsed = time.time() - t0
            rate = (i+1) / elapsed if elapsed > 0 else 0
            print(f"  [{i+1}/{len(targets)}] 成功={success}, 失败={failed}, "
                  f"速率={rate:.1f}只/s, 耗时={elapsed:.0f}s")
    
    total = time.time() - t0
    print(f"\n{'='*60}")
    print(f"✅ 完成!")
    print(f"   成功: {success}, 失败: {failed}")
    print(f"   总耗时: {total:.0f}s ({total/60:.1f}min)")
    print(f"   缓存文件: {len(list(CACHE_DIR.glob('*.csv')))}只")
    print(f"   目录: {CACHE_DIR}")


if __name__ == "__main__":
    main()