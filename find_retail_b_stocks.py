#!/usr/bin/env python3
"""
散户乙估值法筛选：ROE/PB > 10% 且 股息率 > 7%
在中证800成分股中，寻找符合"一眼定胖瘦"标准的标的。
"""
import sys
import time
import warnings
from pathlib import Path

import pandas as pd
import numpy as np
import tushare as ts

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg

warnings.filterwarnings("ignore")

pro = ts.pro_api(tushare_cfg.token)

# ─── 1. 获取中证800最新成分股 ─────────────────────────────
print("=" * 80)
print("【散户乙估值法】中证800成分股筛选")
print("  条件: ROE / PB > 10%  且  股息率(TTM) > 7%")
print("=" * 80)

idx_file = _PROJECT_ROOT / "data" / "index_weights" / "index_weight_000906.SH.csv"
if not idx_file.exists():
    print("❌ 中证800成分股缓存不存在，请先下载")
    sys.exit(1)

df_idx = pd.read_csv(idx_file, dtype={"con_code": str, "trade_date": str})
df_idx["trade_date"] = pd.to_datetime(df_idx["trade_date"])
latest_idx_date = df_idx["trade_date"].max()
constituents = sorted(df_idx[df_idx["trade_date"] == latest_idx_date]["con_code"].unique().tolist())
print(f"\n📊 中证800最新成分股快照日期: {latest_idx_date.strftime('%Y-%m-%d')}")
print(f"📊 成分股数量: {len(constituents)}")

# ─── 2. 获取股票名称 ─────────────────────────────────────
basic_path = _PROJECT_ROOT / "data" / "stock_basic.csv"
if basic_path.exists():
    basic = pd.read_csv(basic_path, dtype={"ts_code": str})
    name_map = dict(zip(basic["ts_code"], basic["name"]))
    industry_map = dict(zip(basic["ts_code"], basic["industry"]))
else:
    # 从 tushare 拉取
    print("  正在拉取 stock_basic...")
    basic = pro.stock_basic(exchange='', list_status='L',
                            fields='ts_code,name,industry,list_date')
    name_map = dict(zip(basic["ts_code"], basic["name"]))
    industry_map = dict(zip(basic["ts_code"], basic["industry"]))

# ─── 3. 批量获取 daily_basic（PB、股息率） ──────────────
print("\n📡 拉取 daily_basic（PB、股息率 TTM）...")

# 先找最近交易日
try:
    trade_cal = pro.trade_cal(exchange='SSE', start_date='20260601', end_date='20260714',
                              is_open='1')
    latest_trade = trade_cal['cal_date'].max()
except:
    latest_trade = '20260711'

print(f"  最新交易日: {latest_trade}")

# 分批拉取（tushare 单次最多返回约 5000 条，但 daily_basic 全市场一次就够了）
daily_df = None
for attempt in range(3):
    try:
        daily_df = pro.daily_basic(
            trade_date=latest_trade,
            fields="ts_code,close,pe_ttm,pb,total_mv,dv_ttm,turnover_rate"
        )
        if daily_df is not None and len(daily_df) > 100:
            break
    except Exception as e:
        print(f"  重试 {attempt+1}/3: {e}")
        time.sleep(2)

if daily_df is None or daily_df.empty:
    print("❌ 无法获取 daily_basic 数据")
    sys.exit(1)

print(f"  获取到 {len(daily_df)} 条估值数据")

# 过滤到中证800成分
daily_zz800 = daily_df[daily_df["ts_code"].isin(constituents)].copy()
print(f"  中证800成分匹配: {len(daily_zz800)} 条")

# ─── 4. 获取 ROE（从 fina_indicator） ────────────────────
print("\n📡 拉取 ROE 数据...")

# 尝试从本地缓存读取
roe_map = {}
roe_cache_file = _PROJECT_ROOT / "data" / "roe_latest.csv"

if roe_cache_file.exists():
    print("  从本地缓存读取 ROE...")
    roe_df = pd.read_csv(roe_cache_file, dtype={"ts_code": str})
    for _, row in roe_df.iterrows():
        roe_map[row["ts_code"]] = row.get("roe", np.nan)
    print(f"  缓存覆盖: {len(roe_map)} 只")
else:
    # 分批次从 tushare 拉取（fina_indicator 不支持批量 ts_code，需要逐只或分批）
    # 尝试用 2025 年报数据
    print("  从 tushare 拉取最新 ROE（可能需要几分钟）...")

    # 策略：先尝试拉取最新一个季度的 fina_indicator
    batch_size = 100
    for i in range(0, len(constituents), batch_size):
        batch = constituents[i:i + batch_size]
        ts_codes_str = ",".join(batch)
        for attempt in range(3):
            try:
                # fina_indicator 按公告日期拉取最新数据
                fina = pro.fina_indicator(
                    ts_code=ts_codes_str,
                    period='20251231',  # 2025年报
                    fields="ts_code,roe"
                )
                time.sleep(0.3)
                if fina is not None and not fina.empty:
                    for _, row in fina.iterrows():
                        code = row["ts_code"]
                        if code not in roe_map or not pd.isna(row.get("roe")):
                            roe_map[code] = row.get("roe", np.nan)
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                else:
                    print(f"    ⚠️ 批次 {i//batch_size+1} 失败: {e}")

        if (i // batch_size + 1) % 2 == 0:
            print(f"    进度: {min(i+batch_size, len(constituents))}/{len(constituents)}")

    # 如果没有2025年报，尝试最新一期
    if len(roe_map) < len(constituents) * 0.5:
        print("  2025年报覆盖率不足，尝试最新季度...")
        missing = [c for c in constituents if c not in roe_map]
        for i in range(0, len(missing), batch_size):
            batch = missing[i:i + batch_size]
            ts_codes_str = ",".join(batch)
            try:
                fina = pro.fina_indicator(
                    ts_code=ts_codes_str,
                    fields="ts_code,roe,ann_date,end_date"
                )
                time.sleep(0.3)
                if fina is not None and not fina.empty:
                    # 取每个股票的最新一期
                    fina = fina.sort_values("end_date").groupby("ts_code").last().reset_index()
                    for _, row in fina.iterrows():
                        code = row["ts_code"]
                        roe_map[code] = row.get("roe", np.nan)
            except:
                pass

    print(f"  ROE 覆盖: {len(roe_map)}/{len(constituents)}")

    # 缓存
    pd.DataFrame([{"ts_code": k, "roe": v} for k, v in roe_map.items()]).to_csv(
        roe_cache_file, index=False)
    print(f"  已缓存到 {roe_cache_file}")

# ─── 5. 合并数据，计算 ROE/PB ─────────────────────────────
print("\n🔍 筛选条件: ROE/PB > 10% 且 股息率 > 7%")
print("-" * 80)

results = []
for _, row in daily_zz800.iterrows():
    code = row["ts_code"]
    pb = row.get("pb")
    dv_ttm = row.get("dv_ttm")
    roe = roe_map.get(code)

    if pb is None or pd.isna(pb) or pb <= 0:
        continue
    if dv_ttm is None or pd.isna(dv_ttm):
        continue
    if roe is None or pd.isna(roe):
        continue

    roe_pb_ratio = roe / pb  # ROE(已为%) / PB，阈值为10

    name = name_map.get(code, "?")
    industry = industry_map.get(code, "?")

    results.append({
        "代码": code,
        "名称": name,
        "行业": industry,
        "ROE(%)": round(roe, 2),
        "PB": round(pb, 3),
        "ROE/PB(%)": round(roe_pb_ratio, 2),
        "股息率(%)": round(dv_ttm, 2),
        "PE_TTM": round(row.get("pe_ttm", np.nan), 2) if not pd.isna(row.get("pe_ttm")) else None,
        "总市值(亿)": round(row.get("total_mv", 0) / 10000, 2) if not pd.isna(row.get("total_mv")) else None,
    })

df_result = pd.DataFrame(results)

# 筛选符合条件
df_pass = df_result[(df_result["ROE/PB(%)"] > 10) & (df_result["股息率(%)"] > 7)].copy()
df_pass = df_pass.sort_values("ROE/PB(%)", ascending=False)

# ─── 6. 输出结果 ─────────────────────────────────────────
print(f"\n📊 数据覆盖: {len(df_result)}/{len(constituents)} 只有完整数据")
print(f"\n✅ 符合「散户乙」标准的标的: {len(df_pass)} 只\n")

if len(df_pass) > 0:
    print(f"{'代码':<12} {'名称':<8} {'行业':<10} {'ROE%':>7} {'PB':>7} {'ROE/PB%':>9} {'股息率%':>8} {'PE_TTM':>8} {'市值(亿)':>10}")
    print("-" * 95)
    for _, r in df_pass.iterrows():
        pe_str = f"{r['PE_TTM']:.1f}" if r['PE_TTM'] is not None and not pd.isna(r['PE_TTM']) else "N/A"
        mv_str = f"{r['总市值(亿)']:.0f}" if r['总市值(亿)'] is not None and not pd.isna(r['总市值(亿)']) else "N/A"
        print(f"{r['代码']:<12} {r['名称']:<8} {r['行业']:<10} "
              f"{r['ROE(%)']:>7.2f} {r['PB']:>7.3f} {r['ROE/PB(%)']:>9.2f} "
              f"{r['股息率(%)']:>8.2f} {pe_str:>8} {mv_str:>10}")
else:
    print("⚠️ 当前中证800内没有同时满足两个条件的标的。")
    print("\n放宽条件观察：")

    # 接近的
    df_near = df_result[
        ((df_result["ROE/PB(%)"] > 8) & (df_result["股息率(%)"] > 5))
    ].sort_values(["ROE/PB(%)", "股息率(%)"], ascending=False)
    print(f"  ROE/PB>8% 且 股息率>5%: {len(df_near)} 只")
    if len(df_near) > 0:
        print(f"\n  {'代码':<12} {'名称':<8} {'行业':<10} {'ROE%':>7} {'PB':>7} {'ROE/PB%':>9} {'股息率%':>8}")
        print("  " + "-" * 75)
        for _, r in df_near.head(15).iterrows():
            print(f"  {r['代码']:<12} {r['名称']:<8} {r['行业']:<10} "
                  f"{r['ROE(%)']:>7.2f} {r['PB']:>7.3f} {r['ROE/PB(%)']:>9.2f} "
                  f"{r['股息率(%)']:>8.2f}")

    # 单独看股息率 > 7%（不管ROE/PB）
    high_div = df_result[df_result["股息率(%)"] > 7].sort_values("股息率(%)", ascending=False)
    print(f"\n  仅股息率>7%: {len(high_div)} 只")
    if len(high_div) > 0:
        for _, r in high_div.head(10).iterrows():
            print(f"    {r['代码']} {r['名称']:<8} ROE/PB={r['ROE/PB(%)']:.2f}% 股息率={r['股息率(%)']:.2f}% PB={r['PB']:.3f}")

    # 单独看 ROE/PB > 10%（不管股息率）
    high_roe_pb = df_result[df_result["ROE/PB(%)"] > 10].sort_values("ROE/PB(%)", ascending=False)
    print(f"\n  仅ROE/PB>10%: {len(high_roe_pb)} 只")
    if len(high_roe_pb) > 0:
        for _, r in high_roe_pb.head(10).iterrows():
            print(f"    {r['代码']} {r['名称']:<8} ROE/PB={r['ROE/PB(%)']:.2f}% 股息率={r['股息率(%)']:.2f}% ROE={r['ROE(%)']:.2f}%")

print("\n" + "=" * 80)
print("筛选完成")
