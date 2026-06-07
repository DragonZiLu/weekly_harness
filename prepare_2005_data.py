#!/usr/bin/env python3
"""
prepare_2005_data.py — 补全 2005 年起所需数据（接受 cashflow 2008 年前稀疏）
================================================================
仅下载缺失文件，已存在的跳过。
"""
import sys, os, time
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
import tushare as ts

pro = ts.pro_api()
DATA_DIR = PROJECT_ROOT / "data" / "fcf_financials"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def dl(name, func, **kwargs):
    path = DATA_DIR / name
    if path.exists():
        return len(pd.read_csv(path))
    try:
        df = func(**kwargs)
        df.to_csv(path, index=False)
        return len(df)
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        return 0

# ═══════════════════════════════════════════
# Step 1: 年报 income + balancesheet 2000-2010
# ═══════════════════════════════════════════
print("=== Step 1: 年报 2000-2010 ===")
for year in range(2000, 2011):
    ed = f"{year}1231"
    n1 = dl(f"income_{year}.csv", pro.income_vip,
            end_date=ed, fields='ts_code,end_date,operate_profit,total_revenue')
    n2 = dl(f"balancesheet_{year}.csv", pro.balancesheet_vip,
            end_date=ed, fields='ts_code,end_date,total_liab,money_cap,total_assets')
    n3 = dl(f"cashflow_{year}.csv", pro.cashflow_vip,
            end_date=ed, fields='ts_code,end_date,c_fr_sale_sg,c_pay_acq_const_fiolta')
    print(f"  {year}: income={n1} bs={n2} cf={n3}")
    time.sleep(0.3)

# ═══════════════════════════════════════════
# Step 2: 季报 income + balancesheet 2000-2010 Q1/Q2/Q3
# ═══════════════════════════════════════════
print("\n=== Step 2: 季报 2000-2010 ===")
for year in range(2000, 2011):
    for q, qd in [("Q1","0331"), ("Q2","0630"), ("Q3","0930")]:
        ed = f"{year}{qd}"
        n1 = dl(f"income_{year}{q}.csv", pro.income_vip,
                end_date=ed, fields='ts_code,end_date,operate_profit,total_revenue')
        n2 = dl(f"balancesheet_{year}{q}.csv", pro.balancesheet_vip,
                end_date=ed, fields='ts_code,end_date,total_liab,money_cap,total_assets')
        n3 = dl(f"cashflow_{year}{q}.csv", pro.cashflow_vip,
                end_date=ed, fields='ts_code,end_date,c_fr_sale_sg,c_pay_acq_const_fiolta')
        time.sleep(0.2)
    print(f"  {year}Q1/Q2/Q3: income={n1} bs={n2} cf={n3}")

# ═══════════════════════════════════════════
# Step 3: daily_basic 2005-2012（日度市值）
# ═══════════════════════════════════════════
print("\n=== Step 3: daily_basic 2005-2012 ===")
cache_dir = DATA_DIR / "daily_basic_cache"
cache_dir.mkdir(exist_ok=True)

cal = pro.trade_cal(exchange='SSE', start_date='20050101', end_date='20121231', is_open='1')
trade_dates = sorted(cal['cal_date'].tolist())

new_cnt = 0
for i, dt in enumerate(trade_dates):
    fname = cache_dir / f"daily_basic_{dt}.csv"
    if fname.exists():
        continue
    try:
        df = pro.daily_basic(trade_date=dt,
                            fields='ts_code,trade_date,total_mv,circ_mv')
        if len(df) > 0:
            df[['ts_code','trade_date','total_mv','circ_mv']].to_csv(fname, index=False)
            new_cnt += 1
    except:
        pass
    if (i+1) % 200 == 0:
        print(f"  {i+1}/{len(trade_dates)} ({new_cnt} new)")
    time.sleep(0.12)

print(f"  daily_basic done: {new_cnt} new files")

# ═══════════════════════════════════════════
# Step 4: 指数权重 HS300 + ZZ800 2005-2012
# ═══════════════════════════════════════════
print("\n=== Step 4: 指数权重 ===")
idx_dir = PROJECT_ROOT / "data" / "index_weights"
idx_dir.mkdir(exist_ok=True)

for idx in ['000300.SH', '000906.SH']:
    all_rows = []
    for y in range(2005, 2013):
        for m, d in [(3,31), (6,30), (9,30), (12,31)]:
            dt = f"{y}{m:02d}{d:02d}"
            try:
                df = pro.index_weight(index_code=idx, trade_date=dt,
                                      fields='index_code,con_code,trade_date,weight')
                if len(df) > 0:
                    all_rows.append(df)
            except:
                pass
            time.sleep(0.2)
    if all_rows:
        combined = pd.concat(all_rows, ignore_index=True)
        combined.to_csv(idx_dir / f"index_weight_{idx}.csv", index=False)
        print(f"  ✅ {idx}: {len(combined)} rows")
    else:
        print(f"  ⚠️ {idx}: 无数据")

# ═══════════════════════════════════════════
# Step 5: 扩展 adj_close 到 2005
# ═══════════════════════════════════════════
print("\n=== Step 5: adj_close 扩展 ===")
ac_dir = PROJECT_ROOT / "data" / "adj_close_cache"
ac_dir.mkdir(exist_ok=True)

existing = list(ac_dir.glob("*.csv"))
need = []
for f in existing:
    try:
        df = pd.read_csv(f, nrows=5)
        if str(df['trade_date'].iloc[0]) > '20050101':
            need.append(f.stem)
    except:
        pass

print(f"  {len(existing)} 缓存文件, {len(need)} 需扩展")
for i, ts_code in enumerate(need):
    fname = ac_dir / f"{ts_code}.csv"
    try:
        old = pd.read_csv(fname)
        earliest = str(old['trade_date'].min())
        df = pro.daily(ts_code=ts_code, start_date='20050101', end_date=str(int(earliest)-1),
                      fields='trade_date,close,adj_factor')
        if len(df) > 0:
            df['adj_close'] = df['close'] * df['adj_factor']
            combined = pd.concat([df[['trade_date','close','adj_close','adj_factor']], old])
            combined = combined.drop_duplicates('trade_date').sort_values('trade_date')
            combined.to_csv(fname, index=False)
    except:
        pass
    if (i+1) % 100 == 0:
        print(f"  {i+1}/{len(need)}")
    time.sleep(0.15)

print(f"\n{'='*60}")
print("  全部数据准备完成！")
print(f"  ⚠️ 注意: cashflow 2008 年前数据稀疏，回测前建议验证")
print(f"{'='*60}")
