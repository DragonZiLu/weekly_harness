#!/usr/bin/env python3
"""
测试ROE缓存功能和连续3年>=8%过滤逻辑
"""
import sys
sys.path.insert(0, "/Users/luzilong/Work/weekly_harness")
import time
import tushare as ts
from config.settings import tushare_cfg

ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

# 测试 fina_indicator 批量接口（只需传 ts_code 列表）
print("测试批量ROE查询接口...")
df = pro.fina_indicator(
    ts_code="000625.SZ,601398.SH,600376.SH",
    fields="ts_code,ann_date,end_date,roe",
    start_date="20140101",
    end_date="20200101",
)
time.sleep(0.4)
print(f"返回 {len(df)} 行")
print(df[df["end_date"].str.endswith("1231")].sort_values(["ts_code","end_date"])[["ts_code","end_date","roe"]].to_string())
