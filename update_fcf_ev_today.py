#!/usr/bin/env python3
"""
E版当前持仓 — 用最新（今天 2026-06-23）企业价值重算 FCF/EV
"""
import json, time, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from config.settings import tushare_cfg
import tushare as ts
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

# 1. 加载最新期E版持仓
e_file = ROOT / "output" / "zz800_fcf_lenient_buffer_e40" / "all_baskets_2015_2026.json"
with open(e_file) as f:
    baskets = json.load(f)
latest_date = sorted(baskets.keys())[-1]
stocks = baskets[latest_date]
print(f"E版最新调仓日: {latest_date}, 持仓: {len(stocks)} 只")

# 2. 拉取今天的总市值
print(f"\n拉取今日（2026-06-23）总市值...")
codes = [s['ts_code'] for s in stocks]
mv_map = {}

# 尝试拉取最近的 daily_basic
for delta in range(5):  # 往前找5个交易日
    d = (pd.Timestamp("2026-06-23") - pd.Timedelta(days=delta)).strftime("%Y%m%d")
    try:
        df = pro.daily_basic(trade_date=d, fields="ts_code,total_mv,circ_mv")
        time.sleep(0.3)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row["ts_code"])
                if code in codes and code not in mv_map:
                    mv = row.get("total_mv")
                    if pd.notna(mv) and float(mv) > 0:
                        mv_map[code] = float(mv)
            print(f"  尝试日期 {d}: 命中 {sum(1 for c in codes if c in mv_map)}/{len(codes)}")
            if sum(1 for c in codes if c in mv_map) >= len(codes) * 0.95:
                break
    except Exception as e:
        print(f"  {d} 拉取失败: {e}")
        time.sleep(1)

print(f"  最终获取市值: {len(mv_map)}/{len(codes)}")

# 3. 用今天的EV重算 FCF/EV
# trick: new_ev = new_mv*1e4 + liab - cash
#       old_ev = old_mv*1e4 + liab - cash
#   =>  new_ev = old_ev + (new_mv - old_mv) * 1e4
results = []
for s in stocks:
    code = s['ts_code']
    name = s.get('name', '')
    fcf = s.get('fcf', 0)
    old_ev = s.get('ev', 0)
    old_total_mv = s.get('total_mv', 0)
    old_fcf_yield = s.get('fcf_yield', 0) * 100  # 转百分比

    new_total_mv = mv_map.get(code)
    if new_total_mv is None or fcf is None or fcf <= 0:
        continue

    new_ev = old_ev + (new_total_mv - old_total_mv) * 10_000
    if new_ev <= 0:
        continue

    new_fcf_yield = fcf / new_ev * 100
    results.append({
        'code': code, 'name': name,
        'fcf': fcf / 1e8,
        'old_ev': old_ev / 1e8,
        'old_fcf_yield': old_fcf_yield,
        'new_ev': new_ev / 1e8,
        'new_fcf_yield': new_fcf_yield,
    })

# 4. 统计汇总
print(f"\n{'='*70}")
print("E版 FCF/EV 更新对比")
print(f"{'='*70}")
print(f"重新计算标的: {len(results)}/{len(stocks)}")

old_med = np.median([r['old_fcf_yield'] for r in results])
new_med = np.median([r['new_fcf_yield'] for r in results])
old_mean = np.mean([r['old_fcf_yield'] for r in results])
new_mean = np.mean([r['new_fcf_yield'] for r in results])

print(f"\n  旧（06-15 收盘）: 中位数 {old_med:.2f}%, 均值 {old_mean:.2f}%")
print(f"  新（今天收盘）  : 中位数 {new_med:.2f}%, 均值 {new_mean:.2f}%")
print(f"  变化            : {new_med - old_med:+.2f}pp")
print(f"\n  {'代码':<12s} {'名称':<8s} {'FCF(亿)':>8s} {'旧EV(亿)':>10s} {'旧%':>7s} {'新EV(亿)':>10s} {'新%':>7s} {'变化':>6s}")
print(f"  {'-'*68}")

for r in sorted(results, key=lambda x: x['new_fcf_yield'], reverse=True)[:20]:
    diff = r['new_fcf_yield'] - r['old_fcf_yield']
    print(f"  {r['code']:<12s} {r['name']:<8s} {r['fcf']:>8.1f} {r['old_ev']:>10.1f} {r['old_fcf_yield']:>6.2f}% "
          f"{r['new_ev']:>10.1f} {r['new_fcf_yield']:>6.2f}% {diff:>+5.2f}pp")
