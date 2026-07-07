#!/usr/bin/env python3
"""E/X 两版的 FCF/MV、EV/MV、FCF/EV 历史趋势"""
import json, numpy as np

with open('output/zz800_fcf_lenient_buffer_e40/all_baskets_2015_2026.json') as f:
    e_b = json.load(f)
with open('output/zz800_fcf_full_universe/all_baskets_2015_2026.json') as f:
    x_b = json.load(f)

rows = []
for d in sorted(x_b.keys()):
    if d not in e_b: continue
    
    # E version
    es = e_b[d]
    tf_e = sum(s.get('fcf',0) for s in es)
    te_e = sum(s.get('ev',0) for s in es)
    tmv_e = sum(s.get('total_mv',0)*10000 for s in es)  # 万→元
    fcf_mv_e = tf_e/tmv_e*100 if tmv_e else 0
    ev_mv_e  = te_e/tmv_e if tmv_e else 0
    fcf_ev_e = tf_e/te_e*100 if te_e else 0
    
    # X version
    xs = x_b[d]
    tf_x = sum(s.get('fcf',0) for s in xs)
    te_x = sum(s.get('ev',0) for s in xs)
    tmv_x = sum(s.get('total_mv',0)*10000 for s in xs)
    fcf_mv_x = tf_x/tmv_x*100 if tmv_x else 0
    ev_mv_x  = te_x/tmv_x if tmv_x else 0
    fcf_ev_x = tf_x/te_x*100 if te_x else 0
    
    rows.append((d, fcf_mv_e, ev_mv_e, fcf_ev_e, fcf_mv_x, ev_mv_x, fcf_ev_x))

print(f"{'调仓日':<14s} {'E_FCF/MV':>9s} {'E_EV/MV':>8s} {'E_FCF/EV':>9s} | {'X_FCF/MV':>9s} {'X_EV/MV':>8s} {'X_FCF/EV':>9s}")
print('-'*75)

for r in rows:
    d = r[0]
    flag = ' ★ 当前' if d == sorted(x_b.keys())[-1] else ''
    print(f"{d:<14s} {r[1]:>8.2f}% {r[2]:>7.2f}x {r[3]:>8.2f}% | {r[4]:>8.2f}% {r[5]:>7.2f}x {r[6]:>8.2f}%{flag}")

# summary
e_fmv_mean = np.mean([r[1] for r in rows])
e_emv_mean = np.mean([r[2] for r in rows])
e_fev_mean = np.mean([r[3] for r in rows])
x_fmv_mean = np.mean([r[4] for r in rows])
x_emv_mean = np.mean([r[5] for r in rows])
x_fev_mean = np.mean([r[6] for r in rows])

last = rows[-1]
print(f"\n{'均值':<14s} {e_fmv_mean:>8.2f}% {e_emv_mean:>7.2f}x {e_fev_mean:>8.2f}% | {x_fmv_mean:>8.2f}% {x_emv_mean:>7.2f}x {x_fev_mean:>8.2f}%")
print(f"{'当前':<14s} {last[1]:>8.2f}% {last[2]:>7.2f}x {last[3]:>8.2f}% | {last[4]:>8.2f}% {last[5]:>7.2f}x {last[6]:>8.2f}%")

# today recalc
import time, sys
from pathlib import Path
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))
from config.settings import tushare_cfg
import tushare as ts
import pandas as pd
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

print("\n--- 今天(06-22)重算 ---")
for ver_name, b, label in [('E', e_b, 'E'), ('X', x_b, 'X')]:
    latest_d = sorted(b.keys())[-1]
    stocks = b[latest_d]
    codes = [s['ts_code'] for s in stocks]
    
    mv_map = {}
    for delta in range(5):
        d = (pd.Timestamp('2026-06-23') - pd.Timedelta(days=delta)).strftime('%Y%m%d')
        try:
            df = pro.daily_basic(trade_date=d, fields='ts_code,total_mv')
            time.sleep(0.3)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    c = str(row['ts_code'])
                    if c in codes and c not in mv_map:
                        mv = row.get('total_mv')
                        if pd.notna(mv) and float(mv)>0:
                            mv_map[c] = float(mv)
            if sum(1 for c in codes if c in mv_map) >= 0.95*len(codes):
                break
        except: time.sleep(1)
    
    tf, te, tmv = 0, 0, 0
    for s in stocks:
        new_mv = mv_map.get(s['ts_code'])
        if new_mv is None: continue
        old_ev = s.get('ev',0); old_mv = s.get('total_mv',0)
        ev = old_ev + (new_mv - old_mv) * 10000
        if ev <= 0: continue
        tf += s.get('fcf',0)
        te += ev
        tmv += new_mv * 10000
    
    fcf_mv = tf/tmv*100; ev_mv = te/tmv; fcf_ev = tf/te*100
    print(f"  {label}版: FCF/MV={fcf_mv:.2f}%  EV/MV={ev_mv:.2f}x  FCF/EV={fcf_ev:.2f}%  (命中{sum(1 for c in codes if c in mv_map)}/{len(codes)})")
