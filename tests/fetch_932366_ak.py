"""Fetch 932366.CSI via akshare and compare with strategy"""
import sys; sys.path.insert(0, '.')
import pandas as pd
import numpy as np

# Try akshare first
print("Trying akshare for 932366.CSI ...")
try:
    import akshare as ak
    # 932366 is CSI 300 Free Cash Flow Index
    # Try multiple symbol formats
    for symbol in ['932366', 'H00932', '000932']:
        try:
            df = ak.stock_zh_index_daily(symbol=f"sh{symbol}")
            if df is not None and not df.empty:
                print(f"  {symbol}: {len(df)} rows")
                print(df.head(2))
                df.to_csv(f'data/932366_daily_{symbol}.csv', index=False)
        except Exception as e:
            print(f"  {symbol}: {type(e).__name__} - {str(e)[:80]}")
except Exception as e:
    print(f"akshare approach failed: {e}")

# Also try index_zh_a_hist for CSI indices
try:
    import akshare as ak
    df = ak.index_zh_a_hist(symbol="932366", period="daily", start_date="20170101", end_date="20260604")
    if df is not None and not df.empty:
        print(f"\n index_zh_a_hist(932366): {len(df)} rows")
        print(df.head(2))
        df.to_csv('data/932366_daily.csv', index=False)
except Exception as e:
    print(f"index_zh_a_hist failed: {e}")
