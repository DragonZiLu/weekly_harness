#!/usr/bin/env python3
"""
100 Baggers 打分系统历史回测
===================================
站在 2023年6月 的视角，对中证800+中证1000成分股用 SQGLP 框架打分，
追踪这些股票到 2026年6月 的实际收益，评估打分系统的预测能力（precision/recall）。

回测参数：
  - 评分时间点：2023-06-15（可见数据截止2022年年报）
  - 收益区间：2023-06-15 → 2026-06-15（整3年，含股息复投）
  - 股票池：中证800 + 中证1000 成分股（约1800只）
  - 10倍定义：后复权价格涨幅 ≥ 900%

防前视偏差铁律：
  1. 成分股使用 2023年6月 时点的指数成分
  2. 财务数据仅用 2019-2022 年报（2023年6月时可见）
  3. 剔除 2023年6月之后上市的标的
  4. 估值数据使用 2023年6月 时点的市场数据

用法：
  python backtest_baggers_scoring.py
  python backtest_baggers_scoring.py --skip-financial-fetch  # 跳过财务数据拉取（使用缓存）
"""

import sys
import os
import time
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Set, Tuple

import pandas as pd
import numpy as np

# ── 路径初始化 ────────────────────────────────────────────
_PROJ = Path(__file__).parent
sys.path.insert(0, str(_PROJ))

from config.settings import tushare_cfg, DATA_DIR

# 注入历史 CFG 后再 import find_baggers（让 score_stock 使用历史参数）
import find_baggers as baggers
HISTORICAL_CFG = {
    **baggers.CFG,
    "data_years": [2019, 2020, 2021, 2022],  # 2023年6月可见的最新4个年报
}
baggers.CFG = HISTORICAL_CFG  # 猴子补丁：覆盖模块级 CFG

try:
    import tushare as ts
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    sys.exit(1)

# ── 常量 ──────────────────────────────────────────────────
START_DATE = "2023-06-15"
END_DATE = "2026-06-15"
CLIFF_DATE = "20230630"  # IPO 截止日期，晚于此日期上市的剔除

CACHE_DIR = DATA_DIR / "baggers"
CACHE_DIR.mkdir(exist_ok=True, parents=True)

OUTPUT_DIR = _PROJ / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

ADJ_CACHE_DIR = DATA_DIR / "adj_close_cache"
INDEX_WEIGHTS_DIR = DATA_DIR / "index_weights"

CSV_OUTPUT = OUTPUT_DIR / "baggers_backtest_202306.csv"
REPORT_OUTPUT = _PROJ / "docs" / "2026-06-24_baggers_scoring_backtest.md"

# 指数代码映射
INDEX_MAP = {
    "zz800": "000906.SH",
    "zz1000": "000852.SH",
}


# ══════════════════════════════════════════════════════════════
# 第 1 阶段：数据准备
# ══════════════════════════════════════════════════════════════

def init_pro():
    """初始化 Tushare Pro"""
    if not tushare_cfg.token:
        print("❌ TUSHARE_TOKEN 未配置")
        sys.exit(1)
    return ts.pro_api(tushare_cfg.token)


def safe_call(fn, *args, retries=3, **kwargs):
    """带重试的安全 API 调用"""
    for i in range(retries):
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as e:
            if i < retries - 1:
                wait = 2 ** (i + 1)
                print(f"  ⏳ 重试 {i+1}/{retries}，等待 {wait}s... ({e})")
                time.sleep(wait)
            else:
                print(f"  ⚠️ API 调用最终失败: {e}")
                return None


def get_index_constituents_historical(pro, index_code: str, target_date: str = "20230615") -> Set[str]:
    """
    获取历史时点的指数成分股。
    使用 Tushare index_weight API，传入 target_date 前后区间，
    取不晚于 target_date 的最新一期调整。
    """
    cache_path = CACHE_DIR / f"index_{index_code}_{target_date[:6]}.csv"
    if cache_path.exists():
        print(f"  📂 使用缓存成分股: {cache_path}")
        df = pd.read_csv(cache_path)
        return set(df["con_code"].tolist())

    print(f"  🌐 获取 {index_code} 成分股 (target: {target_date})...")

    # 拉取覆盖目标日期的范围（往前6个月覆盖最近一次调整）
    start = (datetime.strptime(target_date, "%Y%m%d") - timedelta(days=210)).strftime("%Y%m%d")
    end = (datetime.strptime(target_date, "%Y%m%d") + timedelta(days=30)).strftime("%Y%m%d")

    df = safe_call(pro.index_weight, index_code=index_code,
                   start_date=start, end_date=end)
    if df is None or df.empty:
        print(f"  ⚠️ 无法获取 {index_code} 成分股数据")
        return set()

    df["trade_date"] = df["trade_date"].astype(str)
    available = sorted(df["trade_date"].unique())

    # 取不晚于 target_date 的最新时点
    valid = [d for d in available if d <= target_date]
    if not valid:
        print(f"  ⚠️ 无 target_date={target_date} 之前的数据")
        return set()

    latest = valid[-1]
    constituents = set(df[df["trade_date"] == latest]["con_code"].unique())
    print(f"  📅 使用成分股日期: {latest}, 成分股数: {len(constituents)}")

    pd.DataFrame({"con_code": list(constituents)}).to_csv(cache_path, index=False)
    return constituents


def load_stock_basic() -> pd.DataFrame:
    """加载全A股基本信息（含 list_date 用于过滤 IPO）"""
    f = DATA_DIR / "stock_basic.csv"
    if f.exists():
        return pd.read_csv(f, dtype={"ts_code": str})
    return pd.DataFrame()


def filter_by_list_date(pool: Set[str], basic_df: pd.DataFrame) -> Set[str]:
    """剔除 2023-06-30 之后上市的标的"""
    if basic_df.empty:
        return pool
    basic_df = basic_df[basic_df["ts_code"].isin(pool)]
    before = len(pool)
    # list_date 格式可能是 int 或 str
    basic_df["list_date_str"] = basic_df["list_date"].astype(str).str.strip()
    valid_codes = set(basic_df[basic_df["list_date_str"] <= CLIFF_DATE]["ts_code"].tolist())
    removed = before - len(valid_codes)
    if removed > 0:
        print(f"  🚫 剔除 {removed} 只 {CLIFF_DATE} 后上市的标的")
    return valid_codes


def find_nearest_trade_date(pro, target: str) -> str:
    """找到 target 当天或之前最近的交易日"""
    # 尝试 target 当天
    df = safe_call(pro.trade_cal, exchange="SSE", start_date=target, end_date=target)
    if df is not None and not df.empty:
        row = df.iloc[0]
        if row["is_open"] == 1:
            return target
        # 如果当天非交易日，读 pretrade_date
        if "pretrade_date" in row and pd.notna(row["pretrade_date"]):
            return str(row["pretrade_date"])

    # 向前回溯
    for offset in range(1, 15):
        d = (datetime.strptime(target, "%Y%m%d") - timedelta(days=offset)).strftime("%Y%m%d")
        df = safe_call(pro.trade_cal, exchange="SSE", start_date=d, end_date=d)
        if df is not None and not df.empty and df.iloc[0]["is_open"] == 1:
            return d
    return target


def get_daily_basic_at_date(pro, trade_date: str) -> pd.DataFrame:
    """获取指定交易日的市场估值数据"""
    cache_path = CACHE_DIR / f"daily_basic_{trade_date}.csv"
    if cache_path.exists():
        print(f"  📂 使用缓存估值数据: {cache_path}")
        return pd.read_csv(cache_path)

    print(f"  🌐 获取估值数据 (trade_date={trade_date})...")
    df = safe_call(pro.daily_basic, trade_date=trade_date,
                   fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
    if df is not None and not df.empty:
        df.to_csv(cache_path, index=False)
    return df if df is not None else pd.DataFrame()


def get_fina_indicator_batch_historical(pro, year: int) -> pd.DataFrame:
    """
    批量获取历史年度财务指标。
    复用 find_baggers.py 的相同逻辑，但要拉取更早的年份（2019-2021）。
    """
    cache_path = CACHE_DIR / f"fina_indicator_{year}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        if "ann_date" in df.columns:
            df = df.sort_values("ann_date", ascending=False).drop_duplicates("ts_code", keep="first")
        else:
            df = df.drop_duplicates("ts_code", keep="first")
        return df

    print(f"  🌐 批量获取财务指标 ({year}年报)...")
    df = safe_call(
        pro.fina_indicator_vip,
        period=f"{year}1231",
        fields="ts_code,ann_date,end_date,roe,roa,netprofit_yoy,or_yoy,dt_eps,"
               "grossprofit_margin,netprofit_margin,assets_turn,inv_turn,"
               "current_ratio,quick_ratio,cash_ratio,debt_to_assets,"
               "op_yoy,ebit_yoy,fcff,ocf_to_or"
    )
    if df is not None and not df.empty:
        if "ann_date" in df.columns:
            df = df.sort_values("ann_date", ascending=False).drop_duplicates("ts_code", keep="first")
        else:
            df = df.drop_duplicates("ts_code", keep="first")
        df.to_csv(cache_path, index=False)
    return df if df is not None else pd.DataFrame()


# ══════════════════════════════════════════════════════════════
# 第 2 阶段：历史时点评分
# ══════════════════════════════════════════════════════════════

def run_historical_scoring(
    pool: Set[str],
    basic_map: Dict[str, Dict],
    market_map: Dict[str, Dict],
    fina_by_year: Dict[int, Dict[str, Dict]],
) -> List[Dict]:
    """
    对股票池中的每只股票，用 2023-06 时点的数据执行 SQGLP 打分。
    直接调用 find_baggers.score_stock()（CFG 已在上方猴子补丁覆盖为历史年份）。
    """
    print(f"\n🎯 开始对 {len(pool)} 只成分股打分...")
    scores = []
    missing_count = 0

    for i, ts_code in enumerate(sorted(pool)):
        basic_info = basic_map.get(ts_code, {})
        # 清理 NaN 值：score_stock 中 industry 需要是可迭代字符串
        basic_info = {k: ("" if pd.isna(v) else v) for k, v in basic_info.items()}
        market_data = market_map.get(ts_code, {})

        # 汇整各年度财务数据
        fina_data = {yr: fina_by_year.get(yr, {}).get(ts_code, {}) for yr in HISTORICAL_CFG["data_years"]}

        # 检查财务数据覆盖率
        covered_years = sum(1 for yr, d in fina_data.items() if d)
        if covered_years < 2:
            missing_count += 1
            continue

        score = baggers.score_stock(ts_code, basic_info, fina_data, market_data)
        scores.append(score)

        if (i + 1) % 100 == 0:
            print(f"  已打分 {i+1}/{len(pool)} 只...")

    print(f"  打分完成，有效打分: {len(scores)} 只，跳过(数据不足): {missing_count} 只")
    return scores


# ══════════════════════════════════════════════════════════════
# 第 3 阶段：3 年收益追踪
# ══════════════════════════════════════════════════════════════

def get_adj_close_series(ts_code: str) -> Optional[pd.DataFrame]:
    """获取后复权价序列"""
    cache_file = ADJ_CACHE_DIR / f"{ts_code}.csv"
    if not cache_file.exists():
        return None
    try:
        df = pd.read_csv(cache_file)
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
        df = df.sort_values("trade_date")
        return df[["trade_date", "adj_close"]]
    except Exception:
        return None


def find_nearest_price(df: pd.DataFrame, target_date: str, direction: str = "forward") -> Tuple[Optional[pd.Timestamp], Optional[float]]:
    """
    在价格序列中找最接近 target_date 的交易日价格。
    direction='forward' → target 之后第一个交易日（买入）
    direction='backward' → target 之前最后一个交易日（卖出）
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


def calculate_returns(scores: List[Dict]) -> List[Dict]:
    """
    为每只股票计算 2023-06 → 2026-06 的总收益率。
    使用后复权价格，含股息复投效应。
    """
    print(f"\n📈 计算 {len(scores)} 只股票的 3 年收益...")
    results = []
    missing_price = 0

    for i, s in enumerate(scores):
        ts_code = s["ts_code"]
        df = get_adj_close_series(ts_code)

        if df is None:
            missing_price += 1
            continue

        start_dt, start_price = find_nearest_price(df, START_DATE, "forward")
        end_dt, end_price = find_nearest_price(df, END_DATE, "backward")

        if start_price is None or end_price is None or start_price <= 0:
            missing_price += 1
            continue

        total_return = end_price / start_price - 1
        multiple = end_price / start_price
        is_10bagger = total_return >= 9.0  # 10倍 = 900%+

        result = {
            **s,
            "start_date": str(start_dt.date()) if hasattr(start_dt, 'date') else str(start_dt)[:10],
            "start_price": round(start_price, 2),
            "end_date": str(end_dt.date()) if hasattr(end_dt, 'date') else str(end_dt)[:10],
            "end_price": round(end_price, 2),
            "total_return_pct": round(total_return * 100, 1),
            "multiple": round(multiple, 2),
            "is_10bagger": is_10bagger,
        }
        results.append(result)

        if (i + 1) % 200 == 0:
            print(f"  已计算 {i+1}/{len(scores)} 只...")

    print(f"  收益计算完成，有效标的: {len(results)} 只，缺价格数据: {missing_price} 只")
    return results


# ══════════════════════════════════════════════════════════════
# 第 4 阶段：Precision/Recall 分析
# ══════════════════════════════════════════════════════════════

def analyze_precision_recall(results: List[Dict]) -> str:
    """计算并输出 precision/recall 分析报告"""
    df = pd.DataFrame(results)

    total = len(df)
    total_10x = df["is_10bagger"].sum()
    pct_10x = total_10x / total * 100 if total > 0 else 0

    print(f"\n{'='*60}")
    print(f"📊 Precision / Recall 分析")
    print(f"{'='*60}")
    print(f"  总标的数: {total}")
    print(f"  10倍股: {total_10x} 只 ({pct_10x:.1f}%)")

    # ── 按 Layer 层级分析 ──
    layers = [
        ("全部标的", df["layer1_pass"].notna(), "有效评分"),
        ("Layer 1 通过", df["layer1_pass"] == True, "无硬性缺陷"),
        ("Layer 2 通过 (≥40分)", df["layer2_pass"] == True, "分数≥40"),
        ("Layer 3 通过 (≥55分)", df["layer3_pass"] == True, "高分+估值合理"),
    ]

    print(f"\n{'Layer/阈值':<25s} {'标的数':>6s} {'10倍股':>6s} {'Precision':>10s} {'Recall':>10s} {'F1':>10s}")
    print("-" * 70)

    for name, mask, desc in layers:
        subset = df[mask]
        n = len(subset)
        n_10x = subset["is_10bagger"].sum()
        precision = n_10x / n * 100 if n > 0 else 0
        recall = n_10x / total_10x * 100 if total_10x > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        print(f"  {name:<23s} {n:>6d} {n_10x:>6d} {precision:>9.1f}% {recall:>9.1f}% {f1:>9.1f}%")

    # ── 按分数阈值分析 ──
    thresholds = [80, 70, 60, 50, 40, 30]
    print(f"\n{'分数阈值':<12s} {'标的数':>6s} {'10倍股':>6s} {'Precision':>10s} {'Recall':>10s} {'F1':>10s}")
    print("-" * 60)

    for thresh in thresholds:
        subset = df[df["total_score"] >= thresh]
        n = len(subset)
        n_10x = subset["is_10bagger"].sum()
        precision = n_10x / n * 100 if n > 0 else 0
        recall = n_10x / total_10x * 100 if total_10x > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        print(f"  ≥{thresh:<10d} {n:>6d} {n_10x:>6d} {precision:>9.1f}% {recall:>9.1f}% {f1:>9.1f}%")

    # ── 10倍股分项得分 vs 非10倍股 ──
    print(f"\n{'维度':<12s} {'10倍股均值':>10s} {'非10倍股均值':>12s} {'差异':>8s}")
    print("-" * 50)
    df_10x = df[df["is_10bagger"]]
    df_non = df[~df["is_10bagger"]]

    for col, label in [("total_score", "总分"), ("score_S", "S-销售增长"),
                        ("score_Q", "Q-质量"), ("score_G", "G-盈利增长"),
                        ("score_L", "L-赛道"), ("score_P", "P-估值"),
                        ("score_size", "市值加分"), ("roe_avg", "ROE均值(%)"),
                        ("rev_growth_avg", "营收增速(%)")]:
        v10 = df_10x[col].mean() if not df_10x.empty else 0
        vn = df_non[col].mean() if not df_non.empty else 0
        diff = v10 - vn
        if col in ("roe_avg", "rev_growth_avg"):
            print(f"  {label:<12s} {v10*100:>9.1f}% {vn*100:>11.1f}% {diff*100:>+7.1f}pp")
        else:
            print(f"  {label:<12s} {v10:>10.1f} {vn:>12.1f} {diff:>+8.1f}")

    # ── 漏网之鱼：低分但成为10倍股 ──
    fn_stocks = df[(df["is_10bagger"]) & (df["total_score"] < 50)].sort_values("total_score", ascending=True)
    print(f"\n{'='*60}")
    print(f"🔍 漏网之鱼分析：得分 < 50 但成了 10倍股 ({len(fn_stocks)} 只)")
    print(f"{'='*60}")
    if not fn_stocks.empty:
        for _, r in fn_stocks.iterrows():
            name = r.get("name", "?")
            print(f"  {r['ts_code']} {name:<8s} 总分:{r['total_score']:.0f} "
                  f"S:{r['score_S']:.0f} Q:{r['score_Q']:.0f} G:{r['score_G']:.0f} "
                  f"L:{r['score_L']:.0f} P:{r['score_P']:.0f} 收益:{r['total_return_pct']:.0f}% "
                  f"原因:{r.get('reject_reason', '?')}")

    # ── 高分未成10倍股 ──
    fp_stocks = df[(~df["is_10bagger"]) & (df["layer3_pass"])].sort_values("total_score", ascending=False)
    print(f"\n{'='*60}")
    print(f"⚠️ 假阳性分析：Layer3 通过但未成 10倍股 ({len(fp_stocks)} 只)")
    print(f"{'='*60}")
    if not fp_stocks.empty:
        for _, r in fp_stocks.head(15).iterrows():
            name = r.get("name", "?")
            print(f"  {r['ts_code']} {name:<8s} 总分:{r['total_score']:.0f} 收益:{r['total_return_pct']:.0f}% "
                  f"倍数:{r['multiple']:.1f}x PE:{r.get('pe_ttm','?')} "
                  f"ROE:{r.get('roe_avg',0)*100 if pd.notna(r.get('roe_avg')) else 0:.1f}%")

    return df


# ══════════════════════════════════════════════════════════════
# 报告生成
# ══════════════════════════════════════════════════════════════

def generate_report(df: pd.DataFrame):
    """生成 Markdown 回测分析报告"""
    total = len(df)
    total_10x = df["is_10bagger"].sum()

    # 各层级统计
    l1 = df["layer1_pass"].sum()
    l2 = df["layer2_pass"].sum()
    l3 = df["layer3_pass"].sum()
    l3_10x = df[df["layer3_pass"]]["is_10bagger"].sum()
    l3_precision = l3_10x / l3 * 100 if l3 > 0 else 0
    l3_recall = l3_10x / total_10x * 100 if total_10x > 0 else 0

    # 10倍股详情
    df_10x = df[df["is_10bagger"]].sort_values("total_return_pct", ascending=False)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    md = f"""# 100 Baggers 打分系统回溯验证报告

> 生成时间：{now_str}  
> 评分时间点：{START_DATE}（可用数据截止 2022 年报）  
> 收益区间：{START_DATE} → {END_DATE}（整 3 年，含股息复投）  
> 股票池：中证800 + 中证1000 成分股  
> 执行脚本：`backtest_baggers_scoring.py`

---

## 一、实验设计

**问题**：当前这套 SQGLP 打分体系，如果站在 2023 年 6 月的时点回头看，能否识别出后续 3 年成为 10 倍股的标的？

**回测逻辑**：
1. 获取 2023 年 6 月中证 800 + 中证 1000 的成分股（剔除后上市标的）
2. 用当时可见的 2019-2022 年报数据 + 2023 年 6 月估值数据，逐只打分
3. 追踪这组股票到 2026 年 6 月的后复权收益
4. 计算打分系统在不同阈值下的 precision / recall

**防前视偏差措施**：
- 成分股使用 2023 年 6 月时点的指数成分 ✅
- 财务数据仅用 2019-2022 年度年报 ✅
- 剔除 2023 年 6 月之后上市的标的 ✅
- 估值数据使用 2023 年 6 月时点的 PE/PB/市值 ✅

---

## 二、总体统计

| 指标 | 数值 |
|------|------|
| 有效评分的成分股 | {total} 只 |
| 三年 10 倍股 | **{total_10x} 只** ({total_10x/total*100:.1f}%) |
| Layer 1 通过（无硬性缺陷） | {l1} 只 ({l1/total*100:.1f}%) |
| Layer 2 通过（≥40 分） | {l2} 只 ({l2/total*100:.1f}%) |
| Layer 3 通过（≥55 分 + 估值合理） | {l3} 只 ({l3/total*100:.1f}%) |

---

## 三、Precision / Recall 矩阵

### 按层级

| 层级 | 标的数 | 10倍股 | Precision | Recall | F1 |
|------|-------|--------|-----------|--------|-----|
| 全部标的 | {total} | {total_10x} | {total_10x/total*100:.1f}% | 100.0% | — |
"""

    # Layer 统计
    for label, mask, desc in [
        ("Layer 1 通过", df["layer1_pass"] == True, "无硬性缺陷"),
        ("Layer 2 通过 (≥40分)", df["layer2_pass"] == True, "分数≥40"),
        ("Layer 3 通过 (≥55分)", df["layer3_pass"] == True, "高分+估值合理"),
    ]:
        subset = df[mask]
        n = len(subset)
        n_10x = subset["is_10bagger"].sum()
        prec = n_10x / n * 100 if n > 0 else 0
        rec = n_10x / total_10x * 100 if total_10x > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        md += f"| {label} | {n} | {n_10x} | {prec:.1f}% | {rec:.1f}% | {f1:.1f}% |\n"

    # 按分数阈值
    md += f"""
### 按分数阈值

| 阈值 | 标的数 | 10倍股 | Precision | Recall | F1 |
|------|-------|--------|-----------|--------|-----|
"""
    for thresh in [80, 70, 60, 50, 40, 30]:
        subset = df[df["total_score"] >= thresh]
        n = len(subset)
        n_10x = subset["is_10bagger"].sum()
        prec = n_10x / n * 100 if n > 0 else 0
        rec = n_10x / total_10x * 100 if total_10x > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        md += f"| ≥{thresh} | {n} | {n_10x} | {prec:.1f}% | {rec:.1f}% | {f1:.1f}% |\n"

    # 分项得分对比
    md += f"""
---

## 四、10倍股 vs 非10倍股 分项得分对比

| 维度 | 10倍股均值 | 非10倍股均值 | 差异 |
|------|-----------|-------------|------|
"""
    df_10x = df[df["is_10bagger"]]
    df_non = df[~df["is_10bagger"]]

    for col, label in [("total_score", "总分"), ("score_S", "S-销售增长"),
                        ("score_Q", "Q-质量"), ("score_G", "G-盈利增长"),
                        ("score_L", "L-赛道"), ("score_P", "P-估值"),
                        ("score_size", "市值加分"), ("roe_avg", "ROE均值"),
                        ("rev_growth_avg", "营收增速")]:
        v10 = df_10x[col].mean() if not df_10x.empty else 0
        vn = df_non[col].mean() if not df_non.empty else 0
        diff = v10 - vn
        if col in ("roe_avg", "rev_growth_avg"):
            md += f"| {label} | {v10*100:.1f}% | {vn*100:.1f}% | {diff*100:+.1f}pp |\n"
        else:
            md += f"| {label} | {v10:.1f} | {vn:.1f} | {diff:+.1f} |\n"

    # 10倍股列表
    md += f"""
---

## 五、10 倍股完整列表（{total_10x} 只）

| # | 代码 | 名称 | 行业 | 总分 | S | Q | G | L | P | 收益% | 倍数 | ROE% | 营收增速% |
|---|------|------|------|------|---|---|---|---|---|---|-------|------|------|----------|
"""
    for i, (_, r) in enumerate(df_10x.iterrows(), 1):
        name = r.get("name", "?")
        roe_str = f"{r['roe_avg']*100:.1f}" if pd.notna(r.get('roe_avg')) else "-"
        rev_str = f"{r['rev_growth_avg']*100:.1f}" if pd.notna(r.get('rev_growth_avg')) else "-"
        pe_str = f"{r.get('pe_ttm',0):.0f}" if pd.notna(r.get('pe_ttm')) else "-"
        md += (f"| {i} | {r['ts_code']} | {name} | {r.get('industry','?')} | "
               f"{r['total_score']:.0f} | {r['score_S']:.0f} | {r['score_Q']:.0f} | "
               f"{r['score_G']:.0f} | {r['score_L']:.0f} | {r['score_P']:.0f} | "
               f"{r['total_return_pct']:.0f}% | {r['multiple']:.1f}x | {roe_str} | {rev_str} |\n")

    # 漏网之鱼
    fn_stocks = df[(df["is_10bagger"]) & (df["total_score"] < 50)].sort_values("total_score", ascending=True)
    md += f"""
---

## 六、漏网之鱼：得分 < 50 但成了 10 倍股（{len(fn_stocks)} 只）

这些是打分系统未能识别的高收益标的，分析共性以反哺框架。

"""
    if not fn_stocks.empty:
        md += "| 代码 | 名称 | 总分 | S | Q | G | L | P | 收益% | 拒绝原因 |\n"
        md += "|------|------|------|---|---|---|---|---|-------|----------|\n"
        for _, r in fn_stocks.iterrows():
            name = r.get("name", "?")
            md += (f"| {r['ts_code']} | {name} | {r['total_score']:.0f} | "
                   f"{r['score_S']:.0f} | {r['score_Q']:.0f} | {r['score_G']:.0f} | "
                   f"{r['score_L']:.0f} | {r['score_P']:.0f} | "
                   f"{r['total_return_pct']:.0f}% | {r.get('reject_reason','?')} |\n")
    else:
        md += "_无漏网之鱼_\n"

    md += f"""
---

## 七、结论与改进建议

### 7.1 核心指标总结

| 指标 | 值 | 解读 |
|------|-----|------|
| 3年10倍股占比 | {total_10x/total*100:.1f}% | 整个池子中 10 倍股的自然概率 |
| Layer3 Precision | {l3_precision:.1f}% | Layer3 通过者中 10 倍股占比 |
| Layer3 Recall | {l3_recall:.1f}% | 全体 10 倍股中被 Layer3 捕获的比例 |
| Layer3 命中率提升 | {l3_precision - total_10x/total*100:.1f}pp | 相对随机选股的提升幅度 |

### 7.2 框架有效性评估

- 打分系统是否有效？→ 看 Layer3 Precision 是否显著高于自然概率
- 漏网之鱼特征？→ 是否有共性的被拒原因可以优化
- 最优截断阈值？→ F1 最高的分数门槛在哪里

### 7.3 改进方向

基于回溯结果，对框架的可能改进：
- 漏网之鱼的共性拒因 → 是否需要放宽或调整某些硬性门槛
- 假阳性的共性特征 → 是否需要增加新的过滤条件
- 分项得分哪个维度预测力最强 → 调整权重

---
*报告由 `backtest_baggers_scoring.py` 自动生成。*
*数据截止：2026-06-24，财务数据基于 Tushare fina_indicator_vip。*
"""

    REPORT_OUTPUT.parent.mkdir(exist_ok=True, parents=True)
    REPORT_OUTPUT.write_text(md, encoding="utf-8")
    print(f"\n📝 报告已保存至: {REPORT_OUTPUT}")


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="100 Baggers 打分系统历史回测")
    parser.add_argument("--skip-financial-fetch", action="store_true",
                        help="跳过财务数据 API 拉取（使用已有缓存）")
    args = parser.parse_args()

    # ── 初始化 ────────────────────────────────────────────
    print("=" * 60)
    print("🔍 100 Baggers 打分系统 — 历史回测验证")
    print(f"   评分时点: {START_DATE} → 收益截止: {END_DATE}")
    print(f"   股票池: 中证800 + 中证1000")
    print(f"   财务数据: {HISTORICAL_CFG['data_years']} 年报")
    print("=" * 60)

    pro = init_pro()
    print("✅ Tushare Pro 连接成功")

    # ── 第 1 阶段：数据准备 ────────────────────────────────
    print("\n" + "─" * 50)
    print("📋 第 1 阶段：数据准备")

    # 1a. 获取历史成分股
    constituents_800 = get_index_constituents_historical(pro, INDEX_MAP["zz800"], "20230615")
    constituents_1000 = get_index_constituents_historical(pro, INDEX_MAP["zz1000"], "20230615")
    all_constituents = constituents_800 | constituents_1000
    print(f"📊 合并成分股: ZZ800={len(constituents_800)} + ZZ1000={len(constituents_1000)} → 去重={len(all_constituents)} 只")

    # 1b. 加载 stock_basic + 过滤 IPO
    basic_df = load_stock_basic()
    if not basic_df.empty:
        basic_map = basic_df.set_index("ts_code").to_dict(orient="index")
    else:
        basic_map = {}

    # 过滤后上市的
    all_constituents = filter_by_list_date(all_constituents, basic_df)
    print(f"📊 过滤 IPO 后成分股: {len(all_constituents)} 只")

    # 1c. 获取市场估值数据（2023-06 时点）
    trade_date_202306 = find_nearest_trade_date(pro, "20230615")
    print(f"📅 最近交易日: {trade_date_202306}")
    daily_df = get_daily_basic_at_date(pro, trade_date_202306)
    market_map = daily_df.set_index("ts_code").to_dict(orient="index") if not daily_df.empty else {}
    print(f"📊 估值数据覆盖: {len(market_map)} 只")

    # 1d. 获取财务数据（2019-2022 年报）
    print("\n📈 获取财务数据 (2019-2022 年报)...")
    fina_by_year = {}
    for yr in HISTORICAL_CFG["data_years"]:
        if args.skip_financial_fetch:
                cache_path = CACHE_DIR / f"fina_indicator_{yr}.csv"
                if cache_path.exists():
                    df = pd.read_csv(cache_path)
                    if not df.empty:
                        # 去重
                        if "ann_date" in df.columns:
                            df = df.sort_values("ann_date", ascending=False).drop_duplicates("ts_code", keep="first")
                        else:
                            df = df.drop_duplicates("ts_code", keep="first")
                        fina_by_year[yr] = df.set_index("ts_code").to_dict(orient="index")
                        print(f"  📂 {yr}年报：缓存 {len(fina_by_year[yr])} 条")
                    else:
                        fina_by_year[yr] = {}
                else:
                    fina_by_year[yr] = {}
        else:
            df = get_fina_indicator_batch_historical(pro, yr)
            if not df.empty:
                fina_by_year[yr] = df.set_index("ts_code").to_dict(orient="index")
                print(f"  ✅ {yr}年报：{len(fina_by_year[yr])} 条记录")
            else:
                fina_by_year[yr] = {}
                print(f"  ⚠️ {yr}年报：无数据")
        time.sleep(0.35)  # API 限速

    # ── 第 2 阶段：历史时点评分 ────────────────────────────
    print("\n" + "─" * 50)
    print("🎯 第 2 阶段：历史时点评分")
    scores = run_historical_scoring(all_constituents, basic_map, market_map, fina_by_year)

    # ── 第 3 阶段：3 年收益追踪 ────────────────────────────
    print("\n" + "─" * 50)
    print("📈 第 3 阶段：3 年收益追踪")
    results = calculate_returns(scores)

    # 保存 CSV
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values("total_return_pct", ascending=False)
    df_results.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")
    print(f"\n💾 详细结果已保存: {CSV_OUTPUT}")

    # ── 第 4 阶段：Precision/Recall 分析 ────────────────────
    print("\n" + "─" * 50)
    print("📊 第 4 阶段：Precision/Recall 分析")
    df_analyzed = analyze_precision_recall(results)

    # ── 生成报告 ─────────────────────────────────────────
    print("\n" + "─" * 50)
    print("📝 生成 Markdown 报告...")
    generate_report(df_analyzed)

    print(f"\n✅ 回测全流程完成！")
    print(f"   CSV: {CSV_OUTPUT}")
    print(f"   报告: {REPORT_OUTPUT}")


if __name__ == "__main__":
    main()
