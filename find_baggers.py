#!/usr/bin/env python3
"""
100 Baggers 三层筛选器
基于 Christopher Mayer《100 Baggers》的 SQGLP 框架

筛选逻辑：
  Layer 1 (量化粗筛)：市值、销售增长、ROE、自由现金流
  Layer 2 (质性复筛)：行业赛道、主人翁得分、护城河信号
  Layer 3 (估值确认)：PEG、FCF收益率、起始PE相对合理性

用法：
  python find_baggers.py                     # 对中证800做筛选（默认）
  python find_baggers.py --pool zz800        # 仅筛中证800
  python find_baggers.py --pool zz500        # 仅筛中证500
  python find_baggers.py --pool hs300        # 仅筛沪深300
  python find_baggers.py --pool custom       # 使用 data/baggers_custom_pool.txt 自定义股票池
  python find_baggers.py --ts 600519.SH      # 对单只股票打分
  python find_baggers.py --max-mktcap 1000   # 放宽市值上限至1000亿

输出：
  output/baggers_candidates_YYYYMMDD.md        候选名单 Markdown
  output/baggers_scores_YYYYMMDD.csv           详细打分 CSV

迁移自：dayOne/06_工具与代码/tushare_tools/baggers_screener.py
仓库：weekly_harness（统一 Tushare 配置 + data/ 复用）
"""

import sys
import os
import json
import time
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import pandas as pd

# ── 路径初始化（weekly_harness 风格）─────────────────────────
_PROJ = Path(__file__).parent
sys.path.insert(0, str(_PROJ))

from config.settings import tushare_cfg, DATA_DIR

try:
    import tushare as ts
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    sys.exit(1)

# ── 目录设置 ───────────────────────────────────────────────
CACHE_DIR = DATA_DIR / "baggers"
CACHE_DIR.mkdir(exist_ok=True, parents=True)

OUTPUT_DIR = _PROJ / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

CUSTOM_POOL_FILE = DATA_DIR / "baggers_custom_pool.txt"

TODAY = datetime.now().strftime("%Y%m%d")
SCORE_CSV = OUTPUT_DIR / f"baggers_scores_{TODAY}.csv"
REPORT_MD = OUTPUT_DIR / f"baggers_candidates_{TODAY}.md"

# ── 筛选参数（可调节） ─────────────────────────────────────
CFG = {
    # Layer 1 硬门槛
    "max_mktcap_亿":     500,    # 市值上限（亿元），起点要小
    "min_mktcap_亿":     5,      # 市值下限（亿元），排除壳公司
    "min_rev_growth_3y": 0.12,   # 近3年平均营收增速下限 (12%)
    "min_roe_avg_3y":    0.12,   # 近3年平均ROE下限 (12%)
    "min_fcf_positive":  True,   # 近2年自由现金流均为正
    "max_debt_ratio":    0.65,   # 资产负债率上限

    # Layer 3 估值门槛（软门槛，失分不否决）
    "max_peg":           2.5,    # PEG 上限
    "min_fcf_yield":     0.02,   # FCF 收益率下限 (2%)
    "max_pe_ttm":        80,     # PE(TTM) 上限（超过此值直接扣分）

    # 数据获取年份范围
    "data_years": [2022, 2023, 2024, 2025],  # 取最近4年年报

    # API 限速
    "sleep_per_stock": 0.35,   # 每只股票请求间隔（秒）
}

# ── 行业赛道白名单（高产100倍股行业） ─────────────────────
HIGH_QUALITY_INDUSTRIES = {
    # 软件/互联网/云
    "计算机", "软件", "互联网", "信息技术", "半导体", "电子",
    # 医疗/生物
    "医疗器械", "生物医药", "医药", "医疗",
    # 消费/品牌
    "食品饮料", "白酒", "消费", "品牌",
    # 工业/专精特新
    "工业", "机械", "自动化", "精密制造",
    # 新能源/新材料
    "新能源", "储能", "材料",
}

# 低产行业（扣分）
LOW_QUALITY_INDUSTRIES = {
    "钢铁", "煤炭", "石油", "化工", "建筑", "房地产",
    "航空", "银行", "保险", "券商", "基础设施",
}


# ══════════════════════════════════════════════════════════
# 数据获取层
# ══════════════════════════════════════════════════════════

def init_pro():
    """初始化 Tushare Pro"""
    if not tushare_cfg.token:
        print("❌ TUSHARE_TOKEN 未配置")
        sys.exit(1)
    pro = ts.pro_api(tushare_cfg.token)
    return pro


def safe_call(fn, *args, retries=3, **kwargs):
    """带重试的安全 API 调用"""
    for i in range(retries):
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as e:
            if i < retries - 1:
                time.sleep(2 ** i)
            else:
                return None


def get_latest_trade_date(pro) -> str:
    """获取最近的交易日（向前回溯10天）"""
    for offset in range(10):
        d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        df = safe_call(pro.trade_cal, exchange="SSE", start_date=d, end_date=d)
        if df is not None and not df.empty and df.iloc[0]["is_open"] == 1:
            return d
    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


def get_stock_pool(pro, pool: str) -> pd.DataFrame:
    """
    获取股票池
    pool: 'hs300' | 'zz500' | 'zz800' | 'ashare' | 'custom'
    优先使用已有的 index_weight 缓存（weekly_harness 下载的），
    其次从 Tushare API 获取。
    """
    cache_path = CACHE_DIR / f"pool_{pool}.csv"
    if cache_path.exists() and (datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)).days < 3:
        print(f"  📂 使用缓存股票池: {cache_path}")
        return pd.read_csv(cache_path)

    print(f"  🌐 从 Tushare 获取股票池: {pool}")

    # 尝试复用 weekly_harness 已有的 index_weight 缓存
    idx_dir = _PROJ / "data" / "index_weights"
    idx_map = {"hs300": "399300.SZ", "zz500": "399905.SZ", "zz800": "000906.SH", "zz1000": "000852.SH"}
    
    df = None

    if pool in idx_map:
        idx_code = idx_map[pool]
        # 优先读取已有缓存
        idx_cache = idx_dir / f"index_weight_{idx_code}.csv"
        if idx_cache.exists():
            print(f"  📂 使用已有指数权重缓存: {idx_cache}")
            df = pd.read_csv(idx_cache, dtype={"con_code": str, "trade_date": str})
            latest_date = df["trade_date"].max()
            df = df[df["trade_date"] == latest_date]
            df = df[["con_code"]].rename(columns={"con_code": "ts_code"}).drop_duplicates()
            print(f"  📅 成分股日期: {latest_date}, 共 {len(df)} 只")
        else:
            # API 获取（需要同时传 start_date 和 end_date）
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            
            if pool in ("hs300", "zz800"):
                df300 = safe_call(pro.index_weight, index_code="399300.SZ",
                                  start_date=start_date, end_date=end_date)
                if df300 is not None and not df300.empty:
                    df300 = df300[["con_code"]].rename(columns={"con_code": "ts_code"}).drop_duplicates()
                    df = df300
            
            if pool in ("zz500", "zz800"):
                df500 = safe_call(pro.index_weight, index_code="399905.SZ",
                                  start_date=start_date, end_date=end_date)
                if df500 is not None and not df500.empty:
                    df500 = df500[["con_code"]].rename(columns={"con_code": "ts_code"}).drop_duplicates()
                    if pool == "zz800" and df is not None:
                        df = pd.concat([df, df500]).drop_duplicates()
                    else:
                        df = df500
            
            if pool == "zz1000":
                df1000 = safe_call(pro.index_weight, index_code="000852.SH",
                                   start_date=start_date, end_date=end_date)
                if df1000 is not None and not df1000.empty:
                    df = df1000[["con_code"]].rename(columns={"con_code": "ts_code"}).drop_duplicates()
    
    elif pool == "custom":
        if not CUSTOM_POOL_FILE.exists():
            print(f"❌ 未找到 {CUSTOM_POOL_FILE}，请创建文件并每行填写一个股票代码（如 600519.SH）")
            sys.exit(1)
        codes = [l.strip() for l in CUSTOM_POOL_FILE.read_text().splitlines() if l.strip()]
        df = pd.DataFrame({"ts_code": codes})
    else:  # ashare - 全A股（谨慎使用，数量巨大）
        df = safe_call(pro.stock_basic, exchange="", list_status="L",
                       fields="ts_code,name,industry,market,list_date")

    if df is None or df.empty:
        print(f"❌ 无法获取股票池: {pool}")
        return pd.DataFrame()

    df.to_csv(cache_path, index=False)
    return df


def get_daily_basic_batch(pro, trade_date: str) -> pd.DataFrame:
    """批量获取所有A股日线基本面指标（一次性拉取，效率高）
    如果当天数据尚未更新（盘中），自动回退到前一个有效交易日。
    """
    # 先检查缓存
    cache_path = CACHE_DIR / f"daily_basic_{trade_date}.csv"
    if cache_path.exists():
        return pd.read_csv(cache_path)

    # 尝试获取，若当天数据为空（盘中），向前回溯最多5天
    for offset in range(5):
        try_date = trade_date
        if offset > 0:
            try_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=offset)).strftime("%Y%m%d")
        
        # 检查是否是非交易日
        cal = safe_call(pro.trade_cal, exchange="SSE", start_date=try_date, end_date=try_date)
        if cal is None or cal.empty or cal.iloc[0]["is_open"] != 1:
            continue

        cache_path = CACHE_DIR / f"daily_basic_{try_date}.csv"
        if cache_path.exists():
            return pd.read_csv(cache_path)

        print(f"  🌐 批量获取日线指标 ({try_date})...")
        df = safe_call(pro.daily_basic, trade_date=try_date,
                       fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
        if df is not None and not df.empty:
            df.to_csv(cache_path, index=False)
            return df

    print("  ⚠️  无法获取任何有效日线指标数据")
    return pd.DataFrame()


def get_fina_indicator_batch(pro, year: int) -> pd.DataFrame:
    """
    批量获取财务指标（ROE、营收增长等）
    使用 fina_indicator_vip 接口（不传 ts_code 即可批量拉一年数据）
    注：fina_indicator 现在必须传 ts_code，批量场景需用 fina_indicator_vip
    """
    cache_path = CACHE_DIR / f"fina_indicator_{year}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        # 缓存也可能有重复（旧版本写入的），这里做一次安全去重
        if df["ts_code"].duplicated().any():
            if "ann_date" in df.columns:
                df = df.sort_values("ann_date", ascending=False).drop_duplicates("ts_code", keep="first")
            else:
                df = df.drop_duplicates("ts_code", keep="first")
        return df

    print(f"  🌐 批量获取财务指标 ({year}年报)...")
    # 年报期末日期为 YYYY1231
    df = safe_call(
        pro.fina_indicator_vip,
        period=f"{year}1231",
        fields="ts_code,ann_date,end_date,roe,roa,netprofit_yoy,or_yoy,dt_eps,grossprofit_margin,netprofit_margin,assets_turn,inv_turn,current_ratio,quick_ratio,cash_ratio,debt_to_assets,op_yoy,ebit_yoy,fcff,ocf_to_or"
    )
    if df is not None and not df.empty:
        # 去重：同一 ts_code 可能有多条（修正公告），保留最新 ann_date
        if "ann_date" in df.columns:
            df = df.sort_values("ann_date", ascending=False).drop_duplicates("ts_code", keep="first")
        else:
            df = df.drop_duplicates("ts_code", keep="first")
        df.to_csv(cache_path, index=False)
    return df if df is not None else pd.DataFrame()


def get_cashflow_batch(pro, year: int) -> pd.DataFrame:
    """批量获取现金流数据（用于FCF计算）"""
    cache_path = CACHE_DIR / f"cashflow_{year}.csv"
    if cache_path.exists():
        return pd.read_csv(cache_path)

    print(f"  🌐 批量获取现金流 ({year}年报)...")
    df = safe_call(
        pro.cashflow,
        start_date=f"{year}0101",
        end_date=f"{year+1}0630",
        fields="ts_code,ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta,free_cashflow"
    )
    if df is not None and not df.empty:
        # 只保留年报
        df = df[df["end_date"].astype(str).str.endswith("1231")]
        df = df[df["end_date"].astype(str).str.startswith(str(year))]
        df = df.sort_values("ann_date", ascending=False).drop_duplicates("ts_code")
        df.to_csv(cache_path, index=False)
    return df if df is not None else pd.DataFrame()


def get_stock_basic_all(pro) -> pd.DataFrame:
    """获取全A股基本信息（名称、行业、上市日期）"""
    cache_path = CACHE_DIR / "stock_basic_all.csv"
    if cache_path.exists() and (datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)).days < 7:
        return pd.read_csv(cache_path)
    print("  🌐 获取全A股基本信息...")
    df = safe_call(pro.stock_basic, exchange="", list_status="L",
                   fields="ts_code,name,industry,market,list_date,area")
    if df is not None and not df.empty:
        df.to_csv(cache_path, index=False)
    return df if df is not None else pd.DataFrame()


# ══════════════════════════════════════════════════════════
# 打分引擎
# ══════════════════════════════════════════════════════════

def score_stock(ts_code: str, basic_info: dict, fina_data: dict, market_data: dict) -> dict:
    """
    对单只股票按照 100 Baggers SQGLP 框架打分
    返回：得分明细 + 总分 + 通过层数
    """
    result = {
        "ts_code": ts_code,
        "name": basic_info.get("name", ""),
        "industry": basic_info.get("industry", ""),
        "mktcap_亿": round(market_data.get("total_mv", 0) / 10000, 1),  # 万元→亿元
        # 原始指标
        "roe_avg": None,
        "rev_growth_avg": None,
        "fcf_positive_count": 0,
        "debt_ratio_latest": None,
        "pe_ttm": market_data.get("pe_ttm"),
        "pb": market_data.get("pb"),
        "ps_ttm": market_data.get("ps_ttm"),
        "fcf_yield": None,
        "peg": None,
        # 各维度得分
        "score_S": 0,   # Sales Growth
        "score_Q": 0,   # Quality (ROE + FCF)
        "score_G": 0,   # Growth (EPS增长)
        "score_L": 0,   # Longevity (行业赛道)
        "score_P": 0,   # Price (估值)
        "score_size": 0,  # 市值加分
        "total_score": 0,
        "layer1_pass": False,
        "layer2_pass": False,
        "layer3_pass": False,
        "reject_reason": "",
    }

    years = CFG["data_years"]
    reject_reasons = []

    # ── 市值检查 ────────────────────────────────────────
    mktcap = result["mktcap_亿"]
    if mktcap < CFG["min_mktcap_亿"]:
        result["reject_reason"] = f"市值过小({mktcap:.0f}亿)"
        return result
    if mktcap > CFG["max_mktcap_亿"]:
        result["reject_reason"] = f"市值过大({mktcap:.0f}亿)"
        return result

    # 市值得分（越小越好，潜力越大）
    if mktcap <= 50:
        result["score_size"] = 15
    elif mktcap <= 100:
        result["score_size"] = 10
    elif mktcap <= 200:
        result["score_size"] = 5
    else:
        result["score_size"] = 2

    # ── S：销售增长得分（满分25） ────────────────────────
    rev_growths = []
    latest_rev_growth = None
    for yr in years:
        row = fina_data.get(yr, {})
        g = row.get("or_yoy")  # 营收同比增长率（%）
        if g is not None and not pd.isna(g):
            growth_dec = g / 100.0  # 转换为小数
            rev_growths.append(growth_dec)
            if yr == max(years):
                latest_rev_growth = growth_dec

    # 硬性过滤：最近一年营收不能下降（排除均值掩盖断崖下跌的标的，如嘉益股份）
    if latest_rev_growth is not None and latest_rev_growth < 0:
        result["reject_reason"] = f"最近一年营收下降({latest_rev_growth:.1%})"
        result["rev_growth_avg"] = round(sum(rev_growths) / len(rev_growths), 4) if rev_growths else None
        return result

    if len(rev_growths) >= 2:
        avg_rev_growth = sum(rev_growths) / len(rev_growths)
        result["rev_growth_avg"] = round(avg_rev_growth, 4)

        if avg_rev_growth < CFG["min_rev_growth_3y"]:
            reject_reasons.append(f"营收增速不足({avg_rev_growth:.1%})")
        
        if avg_rev_growth >= 0.30:
            result["score_S"] = 25
        elif avg_rev_growth >= 0.20:
            result["score_S"] = 20
        elif avg_rev_growth >= 0.15:
            result["score_S"] = 15
        elif avg_rev_growth >= 0.12:
            result["score_S"] = 10
        elif avg_rev_growth >= 0.08:
            result["score_S"] = 5
        else:
            result["score_S"] = 0

    # ── Q：企业质量得分（满分25） ────────────────────────
    roes = []
    for yr in years:
        row = fina_data.get(yr, {})
        roe = row.get("roe")
        if roe is not None and not pd.isna(roe):
            roes.append(roe / 100.0)

    fcf_positives = 0
    for yr in years[-2:]:  # 最近2年
        row = fina_data.get(yr, {})
        fcf = row.get("fcff")  # 企业自由现金流
        ocf_or = row.get("ocf_to_or")  # OCF/营收比
        if fcf is not None and not pd.isna(fcf) and fcf > 0:
            fcf_positives += 1
        elif ocf_or is not None and not pd.isna(ocf_or) and ocf_or > 0:
            fcf_positives += 1

    result["fcf_positive_count"] = fcf_positives

    # 硬性过滤：OCF/营收比检查（排除有利润没现金的假成长，如博俊科技）
    # 要求：近4年平均 ocf_to_or ≥ 3%，且最近一年为正
    ocf_or_ratios = []
    for yr in years:
        row = fina_data.get(yr, {})
        val = row.get("ocf_to_or")
        if val is not None and not pd.isna(val):
            ocf_or_ratios.append(val / 100.0 if abs(val) > 1 else val)  # 标准化为小数

    if len(ocf_or_ratios) >= 2:
        avg_ocf_or = sum(ocf_or_ratios) / len(ocf_or_ratios)
        latest_ocf_or = ocf_or_ratios[-1] if ocf_or_ratios else 0
        if avg_ocf_or < 0.03 or latest_ocf_or < 0:
            reject_reasons.append(f"现金流质量差(平均OCF/营收={avg_ocf_or:.1%})")

    if len(roes) >= 2:
        avg_roe = sum(roes) / len(roes)
        result["roe_avg"] = round(avg_roe, 4)

        if avg_roe < CFG["min_roe_avg_3y"]:
            reject_reasons.append(f"ROE不足({avg_roe:.1%})")

        roe_score = 0
        if avg_roe >= 0.25:
            roe_score = 15
        elif avg_roe >= 0.20:
            roe_score = 12
        elif avg_roe >= 0.15:
            roe_score = 9
        elif avg_roe >= 0.12:
            roe_score = 6
        else:
            roe_score = 0

        fcf_score = min(10, fcf_positives * 5)
        result["score_Q"] = roe_score + fcf_score

    # 资产负债率检查
    latest_yr = max(years)
    debt_ratio = fina_data.get(latest_yr, {}).get("debt_to_assets")
    if debt_ratio is not None and not pd.isna(debt_ratio):
        result["debt_ratio_latest"] = round(debt_ratio / 100.0, 4)
        if result["debt_ratio_latest"] > CFG["max_debt_ratio"]:
            reject_reasons.append(f"负债率过高({result['debt_ratio_latest']:.1%})")

    # ── G：盈利增长得分（满分20） ────────────────────────
    profit_growths = []
    for yr in years:
        row = fina_data.get(yr, {})
        g = row.get("netprofit_yoy")  # 净利润同比增长率（%）
        if g is not None and not pd.isna(g):
            profit_growths.append(g / 100.0)

    if len(profit_growths) >= 2:
        avg_profit_growth = sum(profit_growths) / len(profit_growths)
        if avg_profit_growth >= 0.30:
            result["score_G"] = 20
        elif avg_profit_growth >= 0.20:
            result["score_G"] = 15
        elif avg_profit_growth >= 0.15:
            result["score_G"] = 12
        elif avg_profit_growth >= 0.10:
            result["score_G"] = 8
        elif avg_profit_growth >= 0.0:
            result["score_G"] = 4
        else:
            result["score_G"] = 0

    # ── L：行业赛道得分（满分15） ────────────────────────
    industry = result["industry"] or ""
    industry_score = 8  # 中性行业得8分
    for hi in HIGH_QUALITY_INDUSTRIES:
        if hi in industry:
            industry_score = 15
            break
    for li in LOW_QUALITY_INDUSTRIES:
        if li in industry:
            industry_score = 2
            break
    result["score_L"] = industry_score

    # ── P：估值得分（满分15） ────────────────────────────
    pe = result["pe_ttm"]
    valuation_score = 0
    if pe is not None and not pd.isna(pe) and pe > 0:
        if pe <= CFG["max_pe_ttm"]:
            # PEG近似 = PE / (rev_growth * 100)
            if result["rev_growth_avg"] and result["rev_growth_avg"] > 0:
                peg = pe / (result["rev_growth_avg"] * 100)
                result["peg"] = round(peg, 2)
                if peg <= 0.5:
                    valuation_score = 15
                elif peg <= 1.0:
                    valuation_score = 12
                elif peg <= 1.5:
                    valuation_score = 9
                elif peg <= 2.0:
                    valuation_score = 6
                elif peg <= CFG["max_peg"]:
                    valuation_score = 3
                else:
                    valuation_score = 0
                    reject_reasons.append(f"PEG过高({peg:.1f})")
            else:
                valuation_score = 5  # 无法计算PEG，给中性分
        else:
            valuation_score = 0
            reject_reasons.append(f"PE过高({pe:.0f}x)")

    result["score_P"] = valuation_score

    # ── 汇总得分 ─────────────────────────────────────────
    total = (result["score_S"] + result["score_Q"] + result["score_G"] +
             result["score_L"] + result["score_P"] + result["score_size"])
    result["total_score"] = total

    # ── 层级通过判断 ────────────────────────────────────
    result["layer1_pass"] = (len(reject_reasons) == 0)  # 无硬性拒绝理由
    result["layer2_pass"] = result["layer1_pass"] and total >= 40
    result["layer3_pass"] = result["layer2_pass"] and total >= 55 and result["score_P"] >= 6
    result["reject_reason"] = "; ".join(reject_reasons) if reject_reasons else "✅通过"

    return result


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

def run_screen(pro, pool: str = "zz800") -> pd.DataFrame:
    """运行全池筛选"""
    print("\n" + "="*60)
    print(f"🔍 100 Baggers 筛选器 — 股票池: {pool}")
    print("="*60)

    # 1. 获取股票池
    print("\n📋 Step 1: 获取股票池...")
    pool_df = get_stock_pool(pro, pool)
    if pool_df.empty:
        print("❌ 股票池为空，退出")
        return pd.DataFrame()
    print(f"  股票池共 {len(pool_df)} 只")

    # 2. 批量获取日线基本面指标
    print("\n📊 Step 2: 获取市场估值数据...")
    trade_date = get_latest_trade_date(pro)
    print(f"  最近交易日: {trade_date}")
    daily_df = get_daily_basic_batch(pro, trade_date)

    # 3. 批量获取财务指标（各年度）
    print("\n📈 Step 3: 获取各年度财务指标...")
    fina_by_year = {}
    for yr in CFG["data_years"]:
        df = get_fina_indicator_batch(pro, yr)
        if not df.empty:
            fina_by_year[yr] = df.set_index("ts_code").to_dict(orient="index")
        print(f"  {yr}年报：{len(df)} 条记录")

    # 4. 获取股票基本信息
    print("\n🏢 Step 4: 获取股票基本信息...")
    basic_df = get_stock_basic_all(pro)
    basic_map = {}
    if not basic_df.empty:
        basic_map = basic_df.set_index("ts_code").to_dict(orient="index")

    # 5. 市场数据建立映射
    market_map = {}
    if not daily_df.empty:
        market_map = daily_df.set_index("ts_code").to_dict(orient="index")

    # 6. 逐只股票打分
    print(f"\n🎯 Step 5: 对 {len(pool_df)} 只股票打分...")
    scores = []
    for i, row in pool_df.iterrows():
        ts_code = row["ts_code"]

        basic_info = basic_map.get(ts_code, {})
        market_data = market_map.get(ts_code, {})

        # 汇整各年度财务数据
        fina_data = {}
        for yr, yr_map in fina_by_year.items():
            fina_data[yr] = yr_map.get(ts_code, {})

        score = score_stock(ts_code, basic_info, fina_data, market_data)
        scores.append(score)

        if (i + 1) % 50 == 0:
            print(f"  已处理 {i+1}/{len(pool_df)} 只...")

    scores_df = pd.DataFrame(scores)
    scores_df = scores_df.sort_values("total_score", ascending=False)

    # 保存详细打分 CSV
    scores_df.to_csv(SCORE_CSV, index=False, encoding="utf-8-sig")
    print(f"\n✅ 打分完成，结果已保存至: {SCORE_CSV}")

    return scores_df


def generate_report(scores_df: pd.DataFrame):
    """生成 Markdown 候选名单报告"""
    if scores_df.empty:
        return

    layer3 = scores_df[scores_df["layer3_pass"]].head(20)
    layer2 = scores_df[scores_df["layer2_pass"] & ~scores_df["layer3_pass"]].head(30)
    layer1 = scores_df[scores_df["layer1_pass"] & ~scores_df["layer2_pass"]].head(40)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    md = f"""# 100 Baggers 候选名单
> 生成时间：{now_str}  
> 筛选框架：Christopher Mayer《100 Baggers》SQGLP 框架  
> 数据来源：Tushare Pro  
> 执行脚本：`find_baggers.py`（weekly_harness）

---

## 筛选参数

| 参数 | 值 |
|------|----|
| 市值区间 | {CFG['min_mktcap_亿']}亿 ~ {CFG['max_mktcap_亿']}亿 |
| 营收平均增速（近{len(CFG['data_years'])}年） | ≥ {CFG['min_rev_growth_3y']:.0%} |
| ROE平均值（近{len(CFG['data_years'])}年） | ≥ {CFG['min_roe_avg_3y']:.0%} |
| 最大PE(TTM) | ≤ {CFG['max_pe_ttm']}x |
| 最大PEG | ≤ {CFG['max_peg']} |
| 资产负债率 | ≤ {CFG['max_debt_ratio']:.0%} |

---

## 🏆 第三层：高确信候选（{len(layer3)} 只）
> 通过所有三层筛选，总分 ≥ 55，估值合理，值得深度研究

"""
    if not layer3.empty:
        md += "| 股票代码 | 名称 | 行业 | 市值(亿) | 总分 | S增长 | Q质量 | G盈利 | L赛道 | P估值 | ROE均值 | 营收增速 | PEG | PE(TTM) |\n"
        md += "|---------|------|------|---------|------|-------|-------|-------|-------|-------|--------|---------|-----|--------|\n"
        for _, r in layer3.iterrows():
            roe_str = f"{r['roe_avg']:.1%}" if pd.notna(r['roe_avg']) else "-"
            rev_str = f"{r['rev_growth_avg']:.1%}" if pd.notna(r['rev_growth_avg']) else "-"
            peg_str = f"{r['peg']:.1f}" if pd.notna(r['peg']) else "-"
            pe_str = f"{r['pe_ttm']:.0f}x" if pd.notna(r['pe_ttm']) else "-"
            md += (f"| {r['ts_code']} | {r['name']} | {r['industry']} | "
                   f"{r['mktcap_亿']:.0f} | **{r['total_score']}** | "
                   f"{r['score_S']} | {r['score_Q']} | {r['score_G']} | {r['score_L']} | {r['score_P']} | "
                   f"{roe_str} | {rev_str} | {peg_str} | {pe_str} |\n")
    else:
        md += "_本次无满足条件的候选_\n"

    md += f"""
---

## 🥈 第二层：值得关注候选（{len(layer2)} 只）
> 通过 Layer1+2，总分 ≥ 40，但估值偏高或数据不全，可持续跟踪

"""
    if not layer2.empty:
        md += "| 股票代码 | 名称 | 行业 | 市值(亿) | 总分 | ROE均值 | 营收增速 | PEG | 备注 |\n"
        md += "|---------|------|------|---------|------|--------|---------|-----|------|\n"
        for _, r in layer2.iterrows():
            roe_str = f"{r['roe_avg']:.1%}" if pd.notna(r['roe_avg']) else "-"
            rev_str = f"{r['rev_growth_avg']:.1%}" if pd.notna(r['rev_growth_avg']) else "-"
            peg_str = f"{r['peg']:.1f}" if pd.notna(r['peg']) else "-"
            md += (f"| {r['ts_code']} | {r['name']} | {r['industry']} | "
                   f"{r['mktcap_亿']:.0f} | **{r['total_score']}** | "
                   f"{roe_str} | {rev_str} | {peg_str} | {r['reject_reason']} |\n")
    else:
        md += "_本次无满足条件的候选_\n"

    md += f"""
---

## 📋 第一层：初步通过（前40只，按总分排序）

"""
    if not layer1.empty:
        md += "| 股票代码 | 名称 | 行业 | 市值(亿) | 总分 | 拒绝原因 |\n"
        md += "|---------|------|------|---------|------|----------|\n"
        for _, r in layer1.iterrows():
            md += (f"| {r['ts_code']} | {r['name']} | {r['industry']} | "
                   f"{r['mktcap_亿']:.0f} | {r['total_score']} | {r['reject_reason']} |\n")

    md += """
---

## 📌 下一步行动

### 对第三层候选的深度研究清单
对每只 Layer 3 候选，依次完成：

- [ ] **阅读最近3年年报**：关注管理层讨论章节（MD&A）
- [ ] **验证主人翁要素**：查代理声明（Proxy），CEO持股比例和历史变化
- [ ] **护城河访谈测试**：价格测试、竞争测试、时间测试
- [ ] **资本配置回溯**：过去5年自由现金流去向
- [ ] **使用 V2.1 模板**：完成通用个股深度分析报告
- [ ] **计算内在价值**：DCF / 反推隐含增长率
- [ ] **写投资备忘录**：明确买入逻辑 + 卖出触发条件

---

## 评分体系说明

| 维度 | 满分 | 说明 |
|------|------|------|
| S 销售增长 | 25 | 近4年平均营收增速（≥30%满分） |
| Q 企业质量 | 25 | ROE(15分) + FCF正向(10分) |
| G 盈利增长 | 20 | 近4年平均净利润增速 |
| L 赛道持久性 | 15 | 行业赛道质量（高产→15分，低产→2分） |
| P 估值合理性 | 15 | PEG（≤0.5→15分，>2.5→0分） |
| 市值加分 | 15 | 市值越小潜力越大（≤50亿→15分） |
| **总分** | **115** | 建议深研门槛 ≥ 55分 |

"""

    REPORT_MD.write_text(md, encoding="utf-8")
    print(f"📝 候选名单报告已保存至: {REPORT_MD}")


def score_single_stock(pro, ts_code: str):
    """对单只股票进行详细打分并输出报告"""
    print(f"\n🎯 对 {ts_code} 进行 100 Baggers 评估...\n")

    trade_date = get_latest_trade_date(pro)
    daily_df = get_daily_basic_batch(pro, trade_date)

    fina_by_year = {}
    for yr in CFG["data_years"]:
        df = get_fina_indicator_batch(pro, yr)
        if not df.empty:
            fina_by_year[yr] = df.set_index("ts_code").to_dict(orient="index")

    basic_df = get_stock_basic_all(pro)
    basic_map = basic_df.set_index("ts_code").to_dict(orient="index") if not basic_df.empty else {}
    market_map = daily_df.set_index("ts_code").to_dict(orient="index") if not daily_df.empty else {}

    fina_data = {yr: yr_map.get(ts_code, {}) for yr, yr_map in fina_by_year.items()}

    score = score_stock(ts_code, basic_map.get(ts_code, {}), fina_data, market_map.get(ts_code, {}))

    # 打印详细报告
    print("=" * 50)
    print(f"股票: {score['ts_code']} | {score['name']} | {score['industry']}")
    print(f"市值: {score['mktcap_亿']}亿 | PE(TTM): {score['pe_ttm']} | PB: {score['pb']}")
    print("-" * 50)
    print(f"  S 销售增长得分:  {score['score_S']:>3}/25  (营收均增速: {score['rev_growth_avg']:.1%})" if score['rev_growth_avg'] else f"  S 销售增长得分:  {score['score_S']:>3}/25")
    print(f"  Q 企业质量得分:  {score['score_Q']:>3}/25  (ROE均值: {score['roe_avg']:.1%}, FCF正向: {score['fcf_positive_count']}/2)" if score['roe_avg'] else f"  Q 企业质量得分:  {score['score_Q']:>3}/25")
    print(f"  G 盈利增长得分:  {score['score_G']:>3}/20")
    print(f"  L 赛道持久得分:  {score['score_L']:>3}/15  (行业: {score['industry']})")
    print(f"  P 估值合理得分:  {score['score_P']:>3}/15  (PEG: {score['peg']})")
    print(f"  + 市值小公司:  +{score['score_size']:>2}  ({score['mktcap_亿']}亿)")
    print("-" * 50)
    print(f"  总分: {score['total_score']}/115")
    print(f"  通过 Layer1: {'✅' if score['layer1_pass'] else '❌'}")
    print(f"  通过 Layer2: {'✅' if score['layer2_pass'] else '❌'}")
    print(f"  通过 Layer3: {'✅' if score['layer3_pass'] else '❌'}")
    print(f"  备注: {score['reject_reason']}")
    print("=" * 50)

    # 给出建议
    total = score["total_score"]
    if score["layer3_pass"]:
        print("\n🏆 建议：进入深度研究候选池，使用 V2.1 模板展开完整分析")
    elif score["layer2_pass"]:
        print("\n🥈 建议：持续跟踪，关注估值改善或增速加速的催化剂")
    elif score["layer1_pass"]:
        print("\n📋 建议：初步通过，但有硬性缺陷，保持观察仓")
    else:
        print(f"\n❌ 建议：当前不满足100 Baggers标准，拒绝原因：{score['reject_reason']}")


# ══════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="100 Baggers 三层筛选器（基于 SQGLP 框架）")
    parser.add_argument("--pool", default="zz800",
                        choices=["hs300", "zz500", "zz800", "zz1000", "ashare", "custom"],
                        help="股票池选择（默认: zz800）")
    parser.add_argument("--ts", default=None,
                        help="单只股票代码（如 600519.SH），跳过全池筛选，直接评估")
    parser.add_argument("--max-mktcap", type=float, default=None,
                        help="覆盖市值上限（亿元）")
    parser.add_argument("--min-rev-growth", type=float, default=None,
                        help="覆盖营收增速下限（如 0.15 代表 15%%）")
    args = parser.parse_args()

    # 覆盖参数
    if args.max_mktcap:
        CFG["max_mktcap_亿"] = args.max_mktcap
    if args.min_rev_growth:
        CFG["min_rev_growth_3y"] = args.min_rev_growth

    pro = None
    try:
        pro = init_pro()
        print("✅ Tushare Pro 连接成功")
    except Exception as e:
        print(f"❌ API 初始化失败: {e}")
        sys.exit(1)

    if args.ts:
        # 单只股票评估模式
        score_single_stock(pro, args.ts)
    else:
        # 全池筛选模式
        scores_df = run_screen(pro, pool=args.pool)
        if not scores_df.empty:
            generate_report(scores_df)

            # 统计摘要
            total = len(scores_df)
            l1 = scores_df["layer1_pass"].sum()
            l2 = scores_df["layer2_pass"].sum()
            l3 = scores_df["layer3_pass"].sum()
            print(f"\n📊 筛选摘要")
            print(f"  总计：{total} 只")
            print(f"  Layer 1 通过（无硬性缺陷）：{l1} 只 ({l1/total:.1%})")
            print(f"  Layer 2 通过（得分≥40）：{l2} 只 ({l2/total:.1%})")
            print(f"  Layer 3 通过（得分≥55，估值合理）：{l3} 只 ({l3/total:.1%})")
            print(f"\n📂 输出文件：")
            print(f"  详细打分：{SCORE_CSV}")
            print(f"  候选名单：{REPORT_MD}")


if __name__ == "__main__":
    main()
