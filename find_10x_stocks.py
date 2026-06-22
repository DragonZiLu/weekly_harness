#!/usr/bin/env python3
"""
寻找中证800十倍股：当前成分股中，10年前（2016-06-11左右）买入并持有至今（含股息复投），收益10倍+的标的。
"""
import pandas as pd
import numpy as np
import os
import sys
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path(__file__).parent

def load_index_constituents(index_code: str = "000906.SH", target_date: str = "20260331"):
    """加载指定日期的指数成分股（取最新快照）"""
    weights_file = PROJECT_DIR / f"data/index_weights/index_weight_{index_code}.csv"
    if not weights_file.exists():
        print(f"⚠️  未找到权重文件: {weights_file}")
        return set()
    
    df = pd.read_csv(weights_file)
    df = df[df["index_code"] == index_code]
    
    # 找最近的 trade_date
    latest_date = df["trade_date"].max()
    print(f"📋 指数成分数据最新日期: {latest_date}")
    
    constituents = df[df["trade_date"] == latest_date]["con_code"].unique()
    return set(constituents)


def load_stock_basic():
    """加载股票基本信息（名称）"""
    f = PROJECT_DIR / "data/stock_basic.csv"
    if f.exists():
        df = pd.read_csv(f)
        return dict(zip(df["ts_code"], df["name"]))
    return {}


def get_adj_close_series(ts_code: str):
    """获取某只股票的后复权价序列，返回 DataFrame（trade_date, adj_close）"""
    cache_file = PROJECT_DIR / f"data/adj_close_cache/{ts_code}.csv"
    if not cache_file.exists():
        return None
    try:
        df = pd.read_csv(cache_file, parse_dates=["trade_date"])
        df = df.sort_values("trade_date")
        return df[["trade_date", "adj_close"]]
    except:
        return None


def find_nearest_price(df: pd.DataFrame, target_date: str, direction: str = "forward"):
    """在价格序列中找最接近 target_date 的交易日价格。
    direction='forward' 取 target_date 之后第一个交易日，
    direction='backward' 取 target_date 之前最后一个交易日。
    """
    target = pd.Timestamp(target_date)
    if direction == "forward":
        candidates = df[df["trade_date"] >= target]
    else:
        candidates = df[df["trade_date"] <= target]
    
    if candidates.empty:
        return None, None
    
    row = candidates.iloc[0] if direction == "forward" else candidates.iloc[-1]
    return row["trade_date"], row["adj_close"]


def main():
    # 1. 获取当前 ZZ800 成分股
    print("=" * 60)
    print("🔍 中证800 十年十倍股 筛选器")
    print("=" * 60)
    
    constituents = load_index_constituents("000906.SH")
    print(f"📊 当前中证800成分股数量: {len(constituents)}")
    
    # 2. 加载股票名称
    names = load_stock_basic()
    print(f"📊 已加载 {len(names)} 只股票名称")
    
    # 3. 十年区间：2016-06-11 → 2026-06-11
    start_date = "2016-06-11"
    end_date = "2026-06-11"
    
    results = []
    missing_start = 0
    missing_end = 0
    missing_cache = 0
    
    for ts_code in sorted(constituents):
        df = get_adj_close_series(ts_code)
        if df is None:
            missing_cache += 1
            continue
        
        # 起点的后复权价
        start_dt, start_price = find_nearest_price(df, start_date, "forward")
        if start_price is None:
            missing_start += 1
            continue
        
        # 终点的后复权价
        end_dt, end_price = find_nearest_price(df, end_date, "backward")
        if end_price is None:
            missing_end += 1
            continue
        
        if start_price <= 0:
            continue
        
        total_return = end_price / start_price - 1
        cagr = (end_price / start_price) ** (1 / 10) - 1
        
        name = names.get(ts_code, "?")
        sector = ""
        if ts_code.endswith(".SH"):
            if ts_code[:2] == "68":
                sector = "科创板"
            elif ts_code[:1] == "6":
                sector = "沪市主板"
        elif ts_code.endswith(".SZ"):
            if ts_code[:2] in ("30", "30"):
                sector = "创业板"
            elif ts_code[:2] == "00":
                sector = "深市主板"
        
        results.append({
            "ts_code": ts_code,
            "name": name,
            "sector": sector,
            "start_date": str(start_dt.date()) if hasattr(start_dt, 'date') else str(start_dt)[:10],
            "start_adj_close": round(start_price, 2),
            "end_date": str(end_dt.date()) if hasattr(end_dt, 'date') else str(end_dt)[:10],
            "end_adj_close": round(end_price, 2),
            "total_return_pct": round(total_return * 100, 1),
            "cagr_pct": round(cagr * 100, 2),
            "multiple": round(end_price / start_price, 2),
        })
    
    # 4. 排序输出
    df_result = pd.DataFrame(results)
    df_result = df_result.sort_values("multiple", ascending=False)
    
    print(f"\n📊 统计：")
    print(f"   有缓存的: {len(results)} 只")
    print(f"   缺缓存文件: {missing_cache} 只")
    print(f"   缺起点数据: {missing_start} 只")
    print(f"   缺终点数据: {missing_end} 只")
    
    # 筛选 10 倍以上
    df_10x = df_result[df_result["multiple"] >= 10.0]
    
    print(f"\n🔥 {'='*50}")
    print(f"🔥 十年十倍股（2016-06 → 2026-06，含股息复投）: {len(df_10x)} 只")
    print(f"🔥 {'='*50}\n")
    
    if not df_10x.empty:
        for i, (_, row) in enumerate(df_10x.iterrows(), 1):
            print(f"  #{i:2d}  {row['ts_code']:12s} {row['name']:<8s}  "
                  f"{row['total_return_pct']:>8.1f}%  "
                  f"{row['multiple']:>6.1f}x  "
                  f"年化 {row['cagr_pct']:>6.2f}%  "
                  f"{row['sector']}")
    
    print(f"\n{'-'*50}")
    # 5-10 倍的也列出来（紧邻门槛）
    df_5x = df_result[(df_result["multiple"] >= 5.0) & (df_result["multiple"] < 10.0)]
    print(f"📈 5-10倍股（接近门槛）: {len(df_5x)} 只\n")
    if not df_5x.empty:
        for i, (_, row) in enumerate(df_5x.iterrows(), 1):
            print(f"  #{i:2d}  {row['ts_code']:12s} {row['name']:<8s}  "
                  f"{row['total_return_pct']:>8.1f}%  "
                  f"{row['multiple']:>5.1f}x  "
                  f"年化 {row['cagr_pct']:>6.2f}%  "
                  f"{row['sector']}")
    
    # 全量统计
    print(f"\n📊 全量统计（{len(df_result)} 只有效标的）:")
    print(f"   平均倍数: {df_result['multiple'].mean():.2f}x")
    print(f"   中位数倍数: {df_result['multiple'].median():.2f}x")
    print(f"   正收益占比: {(df_result['multiple'] >= 1).sum()}/{len(df_result)} = {(df_result['multiple'] >= 1).mean()*100:.1f}%")
    print(f"   10倍以上: {len(df_10x)} 只 ({len(df_10x)/len(df_result)*100:.1f}%)")
    print(f"   5-10倍: {len(df_5x)} 只")
    print(f"   2-5倍: {(df_result['multiple'] >= 2).sum() - (df_result['multiple'] >= 5).sum()} 只")
    print(f"   1-2倍: {(df_result['multiple'] >= 1).sum() - (df_result['multiple'] >= 2).sum()} 只")
    print(f"   亏损: {(df_result['multiple'] < 1).sum()} 只")
    
    # 保存结果 CSV
    output_file = PROJECT_DIR / "output/ten_bagger_zz800_10y.csv"
    output_file.parent.mkdir(exist_ok=True)
    df_result.to_csv(output_file, index=False, encoding="utf-8-sig")
    print(f"\n💾 完整结果已保存: {output_file}")
    
    return df_result


if __name__ == "__main__":
    df = main()
