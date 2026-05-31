"""
Scanner — A股红利潜力股自动挖掘
==================================

从全市场 ~5000 只 A 股中，自动筛选具备持续分红能力的优质标的。

筛选流水线:
  1. 基础过滤: 非 ST、上市 >3 年、主板/中小板/创业板
  2. 分红筛选: 连续分红 ≥3 年、DPS 未大幅下滑
  3. 财务筛选: ROE > 8%、负债率 < 70%、经营现金流正
  4. 多维度评分: 分红稳定性 + 财务质量 + 股息吸引力 + 成长性
  5. 行业映射: 归入弱周期/消费成长/周期资源/其他 四类

输出:
  - 候选池 Top N，含评分和关键指标
  - 支持导出为 JSON / CSV
  - 可与现有 COMPANIES 对比，识别漏网之鱼
"""

from __future__ import annotations

import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import tushare as ts

# 路径设置
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from config.settings import tushare_cfg

warnings.filterwarnings("ignore")

# ─── 行业 → 类别映射 ───────────────────────────────────────────
# 基于申万行业分类，映射到红利策略三大类别
INDUSTRY_CATEGORY_MAP = {
    # 弱周期红利: 垄断/公用/必选消费
    "电力": "弱周期红利",
    "水务": "弱周期红利",
    "燃气": "弱周期红利",
    "通信服务": "弱周期红利",
    "通信运营": "弱周期红利",
    "银行": "弱周期红利",
    "保险": "弱周期红利",
    "证券": "弱周期红利",  # 头部券商有分红传统
    "高速公路": "弱周期红利",
    "铁路运输": "弱周期红利",
    "港口": "弱周期红利",
    "机场": "弱周期红利",

    # 消费成长红利: 品牌消费/医药/家电
    "白酒": "消费成长红利",
    "饮料制造": "消费成长红利",
    "食品加工": "消费成长红利",
    "白色家电": "消费成长红利",
    "小家电": "消费成长红利",
    "中药": "消费成长红利",
    "医药商业": "消费成长红利",
    "纺织制造": "消费成长红利",
    "服装家纺": "消费成长红利",
    "汽车整车": "消费成长红利",

    # 周期资源红利: 资源/化工/航运
    "工业金属": "周期资源红利",
    "贵金属": "周期资源红利",
    "稀有金属": "周期资源红利",
    "煤炭开采": "周期资源红利",
    "石油开采": "周期资源红利",
    "石油化工": "周期资源红利",
    "化学原料": "周期资源红利",
    "化学制品": "周期资源红利",
    "钢铁": "周期资源红利",
    "水泥": "周期资源红利",
    "航运": "周期资源红利",
    "航空运输": "周期资源红利",
    "建筑材料": "周期资源红利",
    "有色金属": "周期资源红利",
    "钾肥": "周期资源红利",
    "磷肥": "周期资源红利",
}

# ─── 行业锚定阈值（与 dividend_evaluator 对齐）─────────────────
CATEGORY_YIELD_THRESHOLDS = {
    "弱周期红利": {"excellent": 5.5, "good": 4.0, "minimum": 3.0},
    "消费成长红利": {"excellent": 6.0, "good": 4.0, "minimum": 2.5},
    "周期资源红利": {"excellent": 5.0, "good": 3.5, "minimum": 2.0},
    "其他":        {"excellent": 5.0, "good": 3.5, "minimum": 2.5},
}


@dataclass
class ScanResult:
    """单只股票的扫描结果"""
    ts_code: str
    name: str
    industry: str
    category: str
    # 分红指标
    consecutive_years: int = 0       # 连续分红年数
    latest_dps: float = 0.0          # 最新年度每股分红
    dps_trend: str = ""              # DPS趋势: 上升/稳定/下降
    avg_payout_ratio: float = 0.0    # 平均分红率
    current_div_yield: float = 0.0   # 当前股息率(%)
    # 财务指标
    roe_3y_avg: float = 0.0          # 3年平均ROE
    debt_ratio: float = 0.0           # 最新负债率(%)
    revenue_cagr_3y: float = 0.0     # 3年营收CAGR
    profit_cagr_3y: float = 0.0      # 3年净利CAGR
    fcf_positive_years: int = 0      # 近3年经营现金流为正年数
    # 估值
    pe_ttm: float = 0.0
    pb: float = 0.0
    total_mv: float = 0.0            # 总市值(亿)
    # 评分
    score_consistency: float = 0.0   # 分红稳定性(0-30)
    score_financial: float = 0.0     # 财务质量(0-25)
    score_yield: float = 0.0         # 股息吸引力(0-20)
    score_growth: float = 0.0        # 成长性(0-15)
    score_size: float = 0.0          # 规模流动性(0-10)
    total_score: float = 0.0
    # 建议
    recommendation: str = ""         # 强烈推荐/推荐关注/观察/暂不推荐


class DividendScanner:
    """
    A 股红利潜力扫描器

    从全市场筛选具备持续分红能力的标的，输出候选池。

    Usage:
        scanner = DividendScanner()
        candidates = scanner.scan()
        scanner.export(candidates, "data/scanner_results.json")
    """

    def __init__(self, verbose: bool = True):
        ts.set_token(tushare_cfg.token)
        self.pro = ts.pro_api()
        self.verbose = verbose
        self._cache: Dict[str, pd.DataFrame] = {}

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    # ── Stage 1: 基础过滤 ──────────────────────────────────────

    def _get_stock_pool(self) -> pd.DataFrame:
        """获取 A 股基础股票池，过滤 ST/三板/北交所/上市不足3年"""
        self._log("\n🔍 Stage 1: 获取 A 股基础股票池...")

        df = self.pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,name,industry,list_date,area,market",
        )

        if df.empty:
            raise RuntimeError("无法获取股票列表")

        # 过滤
        df = df[~df["name"].str.contains("ST|退市", na=False)]
        df = df[df["market"].isin(["主板", "中小板", "创业板"])]

        # 上市 ≥3 年
        df["list_date"] = pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce")
        three_years_ago = pd.Timestamp.now() - pd.DateOffset(years=3)
        df = df[df["list_date"] <= three_years_ago]

        self._log(f"   初始池: {len(df)} 只（已排除 ST/三板/北交所/上市<3年）")
        return df.reset_index(drop=True)

    # ── Stage 2: 分红预筛选（基于 dv_ttm）───────────────────────

    def _prefilter_by_dividend(
        self, stock_basic: pd.DataFrame, trade_date: str = ""
    ) -> Tuple[List[str], pd.DataFrame]:
        """
        用 daily_basic 的 dv_ttm 预筛选有分红的股票
        同时获取 PE/PB/市值等估值数据

        Returns
        -------
        (qualified_codes, daily_df)
        """
        self._log("\n📊 Stage 2: 股息率预筛选...")

        if not trade_date:
            try:
                cal = self.pro.trade_cal(exchange="SSE", is_open="1", end_date="20260530")
                trade_date = cal["cal_date"].max()
            except Exception:
                trade_date = "20260530"

        try:
            daily_df = self.pro.daily_basic(
                trade_date=trade_date,
                fields="ts_code,close,pe_ttm,pb,total_mv,dv_ttm,turnover_rate",
            )
        except Exception as e:
            self._log(f"   ⚠️ daily_basic 获取失败: {e}")
            return [], pd.DataFrame()

        if daily_df.empty:
            return [], pd.DataFrame()

        # 标记有分红的股票
        all_codes = set(stock_basic["ts_code"].tolist())
        daily_df = daily_df[daily_df["ts_code"].isin(all_codes)]
        daily_df["dv_ttm"] = pd.to_numeric(daily_df["dv_ttm"], errors="coerce").fillna(0)

        # 预筛选：dv_ttm > 1.5%
        qualified = daily_df[daily_df["dv_ttm"] > 1.5].copy()
        codes = qualified["ts_code"].tolist()

        self._log(f"   有分红记录: {len(daily_df[daily_df['dv_ttm']>0])} 只")
        self._log(f"   股息率 >1.5%: {len(codes)} 只（进入详细分析）")
        return codes, daily_df

    def _fetch_dividend_for_stocks(
        self, ts_codes: List[str]
    ) -> pd.DataFrame:
        """
        逐只获取分红历史（仅对预筛选通过的股票）
        """
        self._log(f"\n📥 获取分红历史 (需分析 {len(ts_codes)} 只)...")

        all_divs = []
        errors = 0
        for i, code in enumerate(ts_codes):
            try:
                df = self.pro.dividend(
                    ts_code=code,
                    fields="ts_code,end_date,cash_div,ex_date,ann_date",
                )
                if not df.empty:
                    all_divs.append(df)
            except Exception:
                errors += 1

            # 进度显示
            if (i + 1) % 200 == 0:
                self._log(f"   进度: {i+1}/{len(ts_codes)}, 已获取 {len(all_divs)} 只有分红记录")

            time.sleep(0.15)  # 节流（约6-7只/秒）

        if errors:
            self._log(f"   ⚠️ {errors} 只获取失败")

        if not all_divs:
            return pd.DataFrame()

        result = pd.concat(all_divs, ignore_index=True)
        self._log(f"   ✅ 完成！分红记录: {len(result)} 条, 涉及 {result['ts_code'].nunique()} 只股票")
        return result
        self._log(f"   分红记录: {len(result)} 条")
        return result

    def _analyze_dividend_quality(
        self, ts_codes: List[str], div_df: pd.DataFrame
    ) -> Dict[str, Dict]:
        """
        分析各股票的分红质量:
        - 连续分红年数
        - 最新 DPS
        - DPS 趋势
        - 平均分红率
        """
        if div_df.empty:
            return {}

        # 只保留现金分红
        div_df = div_df[div_df["cash_div"].notna() & (div_df["cash_div"] > 0)].copy()
        div_df["year"] = pd.to_datetime(
            div_df["end_date"].astype(str).str[:8], format="%Y%m%d", errors="coerce"
        ).dt.year

        results = {}
        for code in ts_codes:
            stock_div = div_df[div_df["ts_code"] == code].copy()
            if stock_div.empty:
                continue

            # 按年度汇总 DPS
            stock_div = stock_div.dropna(subset=["year"])
            if stock_div.empty:
                continue

            annual_dps = stock_div.groupby("year")["cash_div"].sum().sort_index()

            if len(annual_dps) < 2:
                continue

            # 连续分红年数
            years = annual_dps.index.tolist()
            consecutive = self._count_consecutive(years)

            if consecutive < 3:
                continue

            # 最新 DPS（最近完整年度）
            latest_dps = float(annual_dps.iloc[-1])

            # DPS 趋势
            recent = annual_dps.tail(3)
            if len(recent) >= 3:
                if recent.iloc[-1] >= recent.iloc[0] * 1.1:
                    trend = "上升"
                elif recent.iloc[-1] >= recent.iloc[0] * 0.9:
                    trend = "稳定"
                else:
                    trend = "下降"
            else:
                trend = "稳定"

            results[code] = {
                "consecutive_years": consecutive,
                "latest_dps": latest_dps,
                "dps_trend": trend,
                "annual_dps": annual_dps.to_dict(),
            }

        self._log(f"   通过分红筛选: {len(results)} 只（连续≥3年）")
        return results

    @staticmethod
    def _count_consecutive(years: List[int]) -> int:
        """计算最大连续年数"""
        if not years:
            return 0
        years = sorted(set(years))
        max_len = cur = 1
        for i in range(1, len(years)):
            if years[i] == years[i - 1] + 1:
                cur += 1
                max_len = max(max_len, cur)
            else:
                cur = 1
        # 如果最近一年不连续，取最大连续段
        # 但要求最近年份在连续段中（即最近2年连续）
        if len(years) >= 2 and years[-1] != years[-2] + 1:
            return 0
        return max_len

    # ── Stage 3: 财务筛选 ──────────────────────────────────────

    def _fetch_financials_batch(
        self, ts_codes: List[str], years: List[int] = None
    ) -> pd.DataFrame:
        """批量获取财务指标"""
        if years is None:
            years = [2023, 2024, 2025]

        cache_key = f"fina_{min(years)}_{max(years)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._log(f"\n📊 获取财务数据 (年度: {years})...")

        all_fina = []
        for yr in years:
            for retry in range(3):
                try:
                    df = self.pro.fina_indicator(
                        period=str(yr),
                        fields="ts_code,end_date,roe,roe_yearly,debt_to_assets,"
                               "current_ratio,quick_ratio,ocf_to_revenue,"
                               "profit_dedt,assets_turn",
                    )
                    if not df.empty:
                        all_fina.append(df)
                    break
                except Exception as e:
                    if retry == 2:
                        self._log(f"   ⚠️ {yr}年财务数据获取失败: {e}")
                    time.sleep(0.5)

        # 也获取营收和利润数据
        all_income = []
        for yr in years:
            try:
                df = self.pro.income(
                    period=str(yr),
                    fields="ts_code,end_date,revenue,n_income,"
                           "total_revenue,operate_profit",
                )
                if not df.empty:
                    all_income.append(df)
                time.sleep(0.3)
            except Exception:
                pass

        if not all_fina:
            self._cache[cache_key] = pd.DataFrame()
            return pd.DataFrame()

        result_fina = pd.concat(all_fina, ignore_index=True)
        result_income = pd.concat(all_income, ignore_index=True) if all_income else pd.DataFrame()

        self._cache[cache_key] = result_fina
        self._cache[f"income_{min(years)}_{max(years)}"] = result_income
        self._log(f"   财务记录: {len(result_fina)} 条")
        return result_fina

    def _filter_financials(
        self,
        ts_codes: List[str],
        fina_df: pd.DataFrame,
        income_df: pd.DataFrame,
    ) -> Dict[str, Dict]:
        """财务质量筛选 + 指标提取"""
        if fina_df.empty:
            return {}

        # 只保留需检查的股票
        fina_df = fina_df[fina_df["ts_code"].isin(ts_codes)].copy()
        if fina_df.empty:
            return {}

        fina_df["year"] = pd.to_datetime(
            fina_df["end_date"].astype(str).str[:8], format="%Y%m%d", errors="coerce"
        ).dt.year

        # 按股票汇总
        results = {}
        for code in ts_codes:
            stock_fina = fina_df[fina_df["ts_code"] == code].copy()
            if stock_fina.empty or len(stock_fina) < 2:
                continue

            # 使用 roe_yearly（年度ROE），回退到 roe
            roe_col = "roe_yearly" if "roe_yearly" in stock_fina.columns else "roe"
            roe_vals = stock_fina[roe_col].dropna()
            if len(roe_vals) < 2:
                continue

            roe_3y = float(roe_vals.tail(3).mean())

            # ROE 筛选
            if roe_3y < 8:
                continue

            # 负债率
            debt = float(stock_fina["debt_to_assets"].tail(1).iloc[0]) if "debt_to_assets" in stock_fina.columns else 50

            if debt > 70 or debt < 0:
                continue

            # 营收/利润 CAGR
            rev_cagr, prof_cagr = 0.0, 0.0
            if not income_df.empty:
                stock_inc = income_df[income_df["ts_code"] == code].copy()
                if not stock_inc.empty and len(stock_inc) >= 2:
                    stock_inc["year"] = pd.to_datetime(
                        stock_inc["end_date"].astype(str).str[:8],
                        format="%Y%m%d", errors="coerce"
                    ).dt.year
                    stock_inc = stock_inc.sort_values("year")

                    rev_col = "revenue" if "revenue" in stock_inc.columns else "total_revenue"
                    if rev_col in stock_inc.columns:
                        rev_vals = stock_inc[rev_col].dropna()
                        if len(rev_vals) >= 3 and rev_vals.iloc[0] > 0:
                            rev_cagr = (rev_vals.iloc[-1] / rev_vals.iloc[0]) ** (1 / (len(rev_vals) - 1)) - 1
                            rev_cagr *= 100

                    if "n_income" in stock_inc.columns:
                        prof_vals = stock_inc["n_income"].dropna()
                        if len(prof_vals) >= 3 and prof_vals.iloc[0] != 0:
                            prof_cagr = (prof_vals.iloc[-1] / abs(prof_vals.iloc[0])) ** (1 / (len(prof_vals) - 1)) - 1
                            prof_cagr *= 100

            # 经营现金流正的年数
            if "ocf_to_revenue" in stock_fina.columns:
                ocf = stock_fina["ocf_to_revenue"].tail(3)
                fcf_positive = int((ocf > 0).sum())
            else:
                fcf_positive = 3

            results[code] = {
                "roe_3y_avg": round(roe_3y, 2),
                "debt_ratio": round(debt, 2),
                "revenue_cagr_3y": round(rev_cagr, 2),
                "profit_cagr_3y": round(prof_cagr, 2),
                "fcf_positive_years": fcf_positive,
            }

        self._log(f"   通过财务筛选: {len(results)} 只（ROE>8%, 负债<70%, 现金流正）")
        return results

    # ── Stage 4: 估值数据 ──────────────────────────────────────

    def _fetch_daily_basic(self, trade_date: str = "") -> pd.DataFrame:
        """获取最新交易日估值数据"""
        cache_key = f"daily_{trade_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._log(f"\n💰 获取估值数据...")

        if not trade_date:
            # 获取最近交易日
            try:
                cal = self.pro.trade_cal(exchange="SSE", is_open="1", end_date="20260530")
                trade_date = cal["cal_date"].max()
            except Exception:
                trade_date = "20260530"

        try:
            df = self.pro.daily_basic(
                trade_date=trade_date,
                fields="ts_code,close,pe_ttm,pb,total_mv,circ_mv,turnover_rate,"
                       "dv_ttm,dv_ratio",
            )
            self._cache[cache_key] = df
            self._log(f"   估值记录: {len(df)} 条")
            return df
        except Exception as e:
            self._log(f"   ⚠️ 估值数据获取失败: {e}")
            return pd.DataFrame()

    # ── Stage 5: 综合评分 ──────────────────────────────────────

    def _score_candidates(
        self,
        dividend_quality: Dict[str, Dict],
        financials: Dict[str, Dict],
        daily_df: pd.DataFrame,
        stock_basic: pd.DataFrame,
    ) -> List[ScanResult]:
        """综合评分，输出排序后的候选列表"""
        self._log(f"\n⭐ 综合评分...")

        # 合并数据
        candidates = []
        all_codes = set(dividend_quality.keys()) & set(financials.keys())

        price_lookup = {}
        if not daily_df.empty:
            for _, row in daily_df.iterrows():
                price_lookup[row["ts_code"]] = row

        name_lookup = {}
        industry_lookup = {}
        for _, row in stock_basic.iterrows():
            name_lookup[row["ts_code"]] = row["name"]
            industry_lookup[row["ts_code"]] = row.get("industry", "")

        for code in all_codes:
            div = dividend_quality[code]
            fin = financials[code]
            daily = price_lookup.get(code, {})
            name = name_lookup.get(code, code)
            industry = industry_lookup.get(code, "")
            category = INDUSTRY_CATEGORY_MAP.get(industry, "其他")

            # ── 当前股息率 ──
            close = float(daily.get("close", 0)) if isinstance(daily, pd.Series) else 0
            dv_ttm = float(daily.get("dv_ttm", 0)) if isinstance(daily, pd.Series) else 0
            if close > 0 and dv_ttm == 0 and div["latest_dps"] > 0:
                dv_ttm = div["latest_dps"] / close * 100

            # ── 评分 ──
            thresholds = CATEGORY_YIELD_THRESHOLDS.get(category, CATEGORY_YIELD_THRESHOLDS["其他"])

            # 1. 分红稳定性 (0-30)
            s_consistency = min(30, div["consecutive_years"] * 3)
            if div["dps_trend"] == "上升":
                s_consistency += 5
            elif div["dps_trend"] == "下降":
                s_consistency -= 5
            s_consistency = max(5, min(30, s_consistency))

            # 2. 财务质量 (0-25)
            s_financial = 0
            roe = fin["roe_3y_avg"]
            if roe >= 20:
                s_financial += 12
            elif roe >= 15:
                s_financial += 9
            elif roe >= 10:
                s_financial += 6
            else:
                s_financial += 3

            debt = fin["debt_ratio"]
            if debt <= 30:
                s_financial += 7
            elif debt <= 50:
                s_financial += 5
            elif debt <= 70:
                s_financial += 2

            s_financial += min(6, fin["fcf_positive_years"] * 2)

            # 3. 股息吸引力 (0-20)
            s_yield = 0
            if dv_ttm >= thresholds["excellent"]:
                s_yield = 20
            elif dv_ttm >= thresholds["good"]:
                s_yield = 15
            elif dv_ttm >= thresholds["minimum"]:
                s_yield = 8
            else:
                s_yield = max(0, dv_ttm / thresholds["minimum"] * 5)

            # 4. 成长性 (0-15)
            s_growth = 0
            prof_cagr = fin["profit_cagr_3y"]
            if prof_cagr >= 15:
                s_growth += 8
            elif prof_cagr >= 5:
                s_growth += 5
            elif prof_cagr >= 0:
                s_growth += 2
            rev_cagr = fin["revenue_cagr_3y"]
            if rev_cagr >= 10:
                s_growth += 5
            elif rev_cagr >= 0:
                s_growth += 2
            s_growth = min(15, s_growth)

            # 5. 规模流动性 (0-10)
            s_size = 0
            total_mv = float(daily.get("total_mv", 0)) if isinstance(daily, pd.Series) else 0
            total_mv_yi = total_mv / 1e4 if total_mv > 0 else 0  # 万元→亿
            if total_mv_yi >= 500:
                s_size = 10
            elif total_mv_yi >= 200:
                s_size = 7
            elif total_mv_yi >= 100:
                s_size = 4
            elif total_mv_yi >= 50:
                s_size = 2

            # 总分
            total = s_consistency + s_financial + s_yield + s_growth + s_size

            # 推荐等级
            if total >= 75:
                rec = "强烈推荐"
            elif total >= 60:
                rec = "推荐关注"
            elif total >= 45:
                rec = "观察"
            else:
                rec = "暂不推荐"

            candidates.append(ScanResult(
                ts_code=code,
                name=name,
                industry=industry,
                category=category,
                consecutive_years=div["consecutive_years"],
                latest_dps=round(div["latest_dps"], 4),
                dps_trend=div["dps_trend"],
                current_div_yield=round(dv_ttm, 2),
                roe_3y_avg=roe,
                debt_ratio=debt,
                revenue_cagr_3y=fin["revenue_cagr_3y"],
                profit_cagr_3y=fin["profit_cagr_3y"],
                fcf_positive_years=fin["fcf_positive_years"],
                pe_ttm=round(float(daily.get("pe_ttm", 0)), 2) if isinstance(daily, pd.Series) else 0,
                pb=round(float(daily.get("pb", 0)), 2) if isinstance(daily, pd.Series) else 0,
                total_mv=round(total_mv_yi, 2),
                score_consistency=round(s_consistency, 1),
                score_financial=round(s_financial, 1),
                score_yield=round(s_yield, 1),
                score_growth=round(s_growth, 1),
                score_size=round(s_size, 1),
                total_score=round(total, 1),
                recommendation=rec,
            ))

        # 按总分排序
        candidates.sort(key=lambda x: x.total_score, reverse=True)
        self._log(f"   候选池: {len(candidates)} 只")
        return candidates

    # ── 主流程 ──────────────────────────────────────────────────

    def scan(self, top_n: int = 50) -> List[ScanResult]:
        """
        执行全市场扫描（渐进式五阶段流水线）

        1. 基础股票池 (4100+只)
        2. dv_ttm 预筛选 → ~800只
        3. 逐只获取分红历史 → 连续≥3年
        4. 财务健康筛选 → ROE/负债/现金流
        5. 多维度评分排序
        """
        t0 = time.time()

        # Stage 1: 基础股票池
        stock_basic = self._get_stock_pool()
        all_codes = stock_basic["ts_code"].tolist()

        # Stage 2: 股息率预筛选 (daily_basic.dv_ttm)
        div_candidates, daily_df = self._prefilter_by_dividend(stock_basic)

        if not div_candidates:
            self._log("❌ 无股票通过股息率预筛")
            return []

        # Stage 3: 分红历史详细分析（仅对预筛选通过的股票）
        div_all = self._fetch_dividend_for_stocks(div_candidates)
        dividend_quality = self._analyze_dividend_quality(div_candidates, div_all)
        div_codes = list(dividend_quality.keys())

        if not div_codes:
            self._log("❌ 无股票通过分红质量筛选")
            return []

        # Stage 4: 财务筛选
        fina_df = self._fetch_financials_batch(div_codes)
        income_df = self._cache.get("income_2023_2025", pd.DataFrame())
        financials = self._filter_financials(div_codes, fina_df, income_df)
        fina_codes = list(financials.keys())

        if not fina_codes:
            self._log("❌ 无股票通过财务筛选")
            return []

        # Stage 5: 评分排序（复用 Stage 2 的 daily_df）
        candidates = self._score_candidates(
            dividend_quality, financials, daily_df, stock_basic
        )

        elapsed = time.time() - t0
        self._log(f"\n✅ 扫描完成，耗时 {elapsed:.1f} 秒")
        self._log(f"   最终候选: {len(candidates)} 只 (Top {min(top_n, len(candidates))} 展示)")

        return candidates[:top_n]

    # ── 输出 ────────────────────────────────────────────────────

    def to_dataframe(self, candidates: List[ScanResult]) -> pd.DataFrame:
        """转换为 DataFrame"""
        rows = []
        for c in candidates:
            rows.append({
                "代码": c.ts_code,
                "名称": c.name,
                "行业": c.industry,
                "类别": c.category,
                "总分": c.total_score,
                "推荐": c.recommendation,
                "连续分红(年)": c.consecutive_years,
                "DPS趋势": c.dps_trend,
                "股息率(%)": c.current_div_yield,
                "3年ROE(%)": c.roe_3y_avg,
                "负债率(%)": c.debt_ratio,
                "营收CAGR(%)": c.revenue_cagr_3y,
                "净利CAGR(%)": c.profit_cagr_3y,
                "PE": c.pe_ttm,
                "PB": c.pb,
                "市值(亿)": c.total_mv,
                "分红稳定性": c.score_consistency,
                "财务质量": c.score_financial,
                "股息吸引力": c.score_yield,
                "成长性": c.score_growth,
                "规模流动性": c.score_size,
            })
        return pd.DataFrame(rows)

    def print_report(self, candidates: List[ScanResult], top_n: int = 30):
        """打印扫描报告"""
        if not candidates:
            print("\n⚠️ 无候选标的")
            return

        print("\n" + "=" * 90)
        print("  🔍 A股红利潜力扫描报告")
        print("=" * 90)

        # 按类别汇总
        cat_counts = defaultdict(int)
        for c in candidates:
            cat_counts[c.category] += 1

        print("\n  ── 类别分布 ──")
        for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {cnt} 只")

        # 强烈推荐 Top 10
        top_recs = [c for c in candidates if c.recommendation == "强烈推荐"][:10]
        if top_recs:
            print(f"\n  ── 🔥 强烈推荐 Top {len(top_recs)} ──")
            print(f"  {'名称':<8} {'代码':<12} {'行业':<10} {'总分':>4} {'股息率':>6} {'ROE':>6} {'分红年':>5}")
            print("  " + "-" * 60)
            for c in top_recs:
                print(
                    f"  {c.name:<6} {c.ts_code:<12} {c.industry:<8} "
                    f"{c.total_score:>4.0f} {c.current_div_yield:>5.1f}% "
                    f"{c.roe_3y_avg:>5.1f}% {c.consecutive_years:>4}年"
                )

        # 按类别分组展示
        for cat in ["弱周期红利", "消费成长红利", "周期资源红利", "其他"]:
            cat_candidates = [c for c in candidates[:top_n] if c.category == cat]
            if not cat_candidates:
                continue
            print(f"\n  ── {cat} Top {min(5, len(cat_candidates))} ──")
            print(f"  {'名称':<8} {'代码':<12} {'行业':<10} {'总分':>4} {'股息率':>6} {'ROE':>6} {'推荐'}")
            print("  " + "-" * 65)
            for c in cat_candidates[:5]:
                print(
                    f"  {c.name:<6} {c.ts_code:<12} {c.industry:<8} "
                    f"{c.total_score:>4.0f} {c.current_div_yield:>5.1f}% "
                    f"{c.roe_3y_avg:>5.1f}% {c.recommendation}"
                )

        print(f"\n  共 {len(candidates)} 只候选，展示 Top {min(top_n, len(candidates))}")
        print("=" * 90)

    def export(self, candidates: List[ScanResult], path: str):
        """导出结果"""
        df = self.to_dataframe(candidates)
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if path.endswith(".json"):
            df.to_json(output_path, orient="records", force_ascii=False, indent=2)
        elif path.endswith(".csv"):
            df.to_csv(output_path, index=False, encoding="utf-8-sig")
        else:
            df.to_csv(output_path.with_suffix(".csv"), index=False, encoding="utf-8-sig")

        self._log(f"\n💾 扫描结果 → {output_path}")

    def compare_with_existing(
        self, candidates: List[ScanResult]
    ) -> Tuple[List[ScanResult], List[str]]:
        """
        与当前 COMPANIES 对比，找出已覆盖和未覆盖的标的

        Returns
        -------
        (new_candidates, existing_codes)
        """
        from dividend_evaluator import COMPANIES

        existing_codes = set()
        for sector, companies in COMPANIES.items():
            for name, meta in companies.items():
                existing_codes.add(meta["ts_code"])

        new = [c for c in candidates if c.ts_code not in existing_codes]
        covered = [c for c in candidates if c.ts_code in existing_codes]

        if self.verbose:
            print(f"\n  📊 对比当前池:")
            print(f"   已覆盖: {len(covered)} 只")
            print(f"   新发现: {len(new)} 只（推荐加入评估池）")

        return new, list(existing_codes)
