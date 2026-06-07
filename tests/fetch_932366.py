"""Quick fetch 932366.CSI benchmark data"""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, '..')
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

from weekly_harness.backtest import BacktestDataFetcher

fetcher = BacktestDataFetcher()

print("Fetching 932366.CSI ...")
df = fetcher.fetch_benchmark_data(
    "932366.CSI", "2017-01-01", "2026-06-01"
)

if df is not None and not df.empty:
    df.to_csv('data/932366_daily.csv', index=False)
    c = df['close'].astype(float)
    t0, t1 = df.iloc[0]['trade_date'], df.iloc[-1]['trade_date']
    ret = (c.iloc[-1] / c.iloc[0] - 1) * 100
    print(f'{len(df)} rows: {t0} ({c.iloc[0]:.2f}) -> {t1} ({c.iloc[-1]:.2f})')
    print(f'932366.CSI 累计收益: {ret:.2f}%')
else:
    print("FAILED: no data")
