#!/usr/bin/env python3
"""验证tushare ROE接口 — 看首开股份/长安汽车历史ROE"""
import sys
sys.path.insert(0, "/Users/luzilong/Work/weekly_harness")
import time
import tushare as ts
from config.settings import tushare_cfg

ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

for ts_code, name in [("600376.SH","首开股份"),("000625.SZ","长安汽车"),("601398.SH","工商银行"),("601939.SH","建设银行")]:
    try:
        df = pro.fina_indicator(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,roe",
            start_date="20140101",
            end_date="20200101",
        )
        time.sleep(0.4)
        # 只取年报（end_date 12月31日）
        df = df[df["end_date"].str.endswith("1231")].drop_duplicates("end_date").sort_values("end_date")
        print(f"\n=== {name} ({ts_code}) 历史年报ROE ===")
        print(df[["end_date","ann_date","roe"]].to_string(index=False))
    except Exception as e:
        print(f"ERROR {ts_code}: {e}")
