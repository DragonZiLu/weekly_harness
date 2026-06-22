"""
中证红利低波动指数 H30269（红利低波50）选股引擎
====================================================

基于中证指数公司编制方案实现：
  样本空间：中证800（000906.SH）
  选股逻辑：
    1. 过去三年连续现金分红，每年股息率>0
    2. 支付率过滤：剔除 DPS/basic_eps 为负 + 前5%过高（仅保留[0, P95]）
    3. DPS增长过滤：过去三年每股股利增长率均>0
    4. 三年平均税后股息率降序 → Top 75
    5. 过去一年波动率升序 → Top 50
  加权方式：纯股息率加权，单股15%封顶（无行业上限）
  调仓频率：年度（12月第二个周五下一交易日）

区别于 930955（dividend_lowvol.py）：
  - 股息率用税后（cash_div_tax）而非税前
  - 新增支付率过滤 + DPS增长过滤
  - 年度调仓而非季度
  - Top 50 而非 Top 100
  - 纯股息率加权而非股息率/波动率
  - 15% 单股上限而非 10%+行业20%

继承 DividendLowvolEngine，覆盖 6 个方法 + 新建 2 个方法。

🔴 核心约束：两阶段模式
  Phase 1 (preload): 批量下载所有数据到磁盘（含 EPS）
  Phase 2 (select_basket): 纯读本地缓存，零API调用
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_INCOME_DIR = _DATA_DIR / "income"
_DIV_DIR = _DATA_DIR / "dividend_history"

# 导入父类
from dividend_lowvol import (
    DividendLowvolEngine,
    IndexWeightCache,
    _STOCK_BASIC_FILE,
    _PRICE_DIR,
    _VOL_CACHE_DIR,
    _DIV_DIR as _PARENT_DIV_DIR,
    _get_tushare_api,
    _nearest_trading_day,
    _load_trading_calendar,
)


# ============================================================================
# H30269Engine — 继承 DividendLowvolEngine
# ============================================================================

class H30269Engine(DividendLowvolEngine):
    """
    中证红利低波动指数 H30269（红利低波50）选股引擎。

    继承 DividendLowvolEngine（930955），覆盖差异方法。

    用法:
        engine = H30269Engine()
        engine.preload(download=True, rebalance_dates=[...])  # Phase 1
        basket = engine.select_basket("2020-12-14")            # Phase 2
    """

    def __init__(self, index_code: str = "000906.SH"):
        super().__init__(index_code=index_code)
        # H30269 专用：EPS 数据 {ts_code: {year: basic_eps}}
        self._eps_by_year: Dict[str, Dict[int, float]] = {}
        # 两份 DPS 数据 {ts_code: {year: DPS}}
        self._dps_pre_tax: Dict[str, Dict[int, float]] = {}   # 税前
        self._dps_after_tax: Dict[str, Dict[int, float]] = {}  # 税后

    # ————————————————————————————————————————————————
    # Phase 1: 扩展 preload（追加 EPS 下载步骤）
    # ————————————————————————————————————————————————

    def preload(self, download: bool = False, rebalance_dates: List[str] = None):
        """
        Phase 1 — 继承父类 preload，追加 EPS 下载步骤。
        """
        if self._preloaded:
            return

        self._all_rebalance_dates = rebalance_dates or []
        t0 = time.time()
        print("=" * 60)
        print("H30269Engine: Phase 1 — 预下载数据（含EPS）")
        print("=" * 60)

        # 1) 成分股权重（复用父类）
        print("\n[1/6] 加载 CSI 800 成分股权重...")
        if download:
            self._idx_cache.download()
        else:
            self._idx_cache.load()

        # 2) 股票基本信息（复用父类）
        print("\n[2/6] 加载 stock_basic...")
        self._load_stock_basic()

        # 3) 分红数据（H30269 覆盖版：双份 DPS）
        print("\n[3/6] 加载分红数据（税前+税后DPS）...")
        self._load_dividend_cache()

        # ★ 4) EPS 数据（H30269 新增）
        print("\n[4/6] 加载 EPS 数据...")
        self._load_eps_cache(download=download)

        # 5) 价格快照（复用父类）
        print("\n[5/6] 预加载价格快照...")
        if self._all_rebalance_dates:
            self._preload_prices(download=download)

        # 6) 波动率（复用父类）
        print("\n[6/6] 预计算波动率 → 磁盘缓存...")
        if self._all_rebalance_dates:
            self._preload_volatility(download=download)
        else:
            print("  ⚠️ 无调仓日列表，跳过波动率预计算")

        self._preloaded = True
        elapsed = time.time() - t0
        print(f"\n✅ Phase 1 完成 ({elapsed/60:.1f}min)")

        # —— Phase 1 数据质量自检 ——
        self._run_data_quality_checks()

    # ————————————————————————————————————————————————
    # 覆盖：_load_dividend_cache（双份 DPS）
    # ————————————————————————————————————————————————

    def _load_dividend_cache(self):
        """加载分红数据 → 预计算税前+税后两套 DPS（向量化）"""
        if not _DIV_DIR.exists():
            print(f"  ⚠️ 分红数据目录不存在: {_DIV_DIR}")
            return

        proc_priority = {"实施": 0, "股东大会通过": 1, "预案": 2}
        div_files = list(_DIV_DIR.glob("*.csv"))
        all_records = []

        for f in div_files:
            try:
                df = pd.read_csv(f, dtype={
                    "ts_code": str, "end_date": str, "ann_date": str,
                    "div_proc": str, "ex_date": str, "pay_date": str
                })
                if df.empty:
                    continue
                if "cash_div_tax" not in df.columns or "cash_div" not in df.columns:
                    continue
                all_records.append(df)
            except Exception:
                pass

        if not all_records:
            print(f"  ⚠️ 无有效分红文件")
            return

        div_all = pd.concat(all_records, ignore_index=True)

        # Tushare cash_div/cash_div_tax 已经是每股金额，直接使用
        div_all["cash_div"] = pd.to_numeric(div_all["cash_div"], errors="coerce").fillna(0)
        div_all["cash_div_tax"] = pd.to_numeric(div_all["cash_div_tax"], errors="coerce").fillna(0)

        div_all["dps_pre_tax"] = div_all["cash_div"]
        div_all["dps_after_tax"] = div_all["cash_div_tax"]
        div_all["year"] = div_all["end_date"].astype(str).str[:4].astype(int)

        # 去重：同一会计年度取进度最高的分红方案
        div_all["proc_rank"] = div_all["div_proc"].map(lambda x: proc_priority.get(str(x), 3))
        dedup = div_all.sort_values("proc_rank").drop_duplicates(
            subset=["ts_code", "end_date"], keep="first"
        )
        # 去除重复 end_date（保留第一条）
        dedup = dedup.drop_duplicates(subset=["ts_code", "year"], keep="first")

        # 聚合为年度 DPS（同一年可能多次分红，累加）
        annual_dps = dedup.groupby(["ts_code", "year"])[["dps_pre_tax", "dps_after_tax"]].sum()

        # 转为嵌套字典（O(1)查询）
        for code in annual_dps.index.get_level_values("ts_code").unique():
            pre_map = annual_dps.loc[code]["dps_pre_tax"]
            aft_map = annual_dps.loc[code]["dps_after_tax"]
            self._dps_pre_tax[code] = {
                yr: float(pre_map.loc[yr]) if yr in pre_map.index else 0.0
                for yr in pre_map.index
            }
            self._dps_after_tax[code] = {
                yr: float(aft_map.loc[yr]) if yr in aft_map.index else 0.0
                for yr in aft_map.index
            }
            # 同时填充父类的 _dps_by_year（兼容继承方法）
            self._dps_by_year[code] = self._dps_after_tax[code]

        print(f"  📂 分红缓存: {len(self._dps_after_tax)}只有效数据（税前+税后）")

    # ————————————————————————————————————————————————
    # 覆盖：_get_annual_dividends（返回税前 DPS）
    # ————————————————————————————————————————————————

    def _get_annual_dividends(self, ts_code: str, year: int, tax_type: str = "after") -> Optional[float]:
        """
        获取某年每股分红总额。

        Parameters
        ----------
        tax_type : "pre" 或 "after"
            "pre"  → pre_tax (用于支付率计算)
            "after" → after_tax (用于股息率计算)
        """
        if tax_type == "pre":
            source = self._dps_pre_tax
        else:
            source = self._dps_after_tax

        year_map = source.get(ts_code)
        if year_map is None:
            return None
        dps = year_map.get(year, 0.0)
        return dps if dps > 0 else 0.0

    # ————————————————————————————————————————————————
    # 覆盖：_calc_avg_dividend_yield（使用税后 DPS）
    # ————————————————————————————————————————————————

    def _calc_avg_dividend_yield(
        self, ts_code: str, date_str: str, years: int = 3
    ) -> Optional[float]:
        """
        计算过去 years 年平均税后股息率：DPS_tax_i / 年末股价_i
        """
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        check_years = list(range(latest_year - years + 1, latest_year + 1))

        annual_yields = []
        for yr in check_years:
            dps = self._get_annual_dividends(ts_code, yr, tax_type="after")
            if dps is None or dps <= 0:
                continue

            price_yr = self._get_stock_price(ts_code, f"{yr}-12-31")
            if price_yr is None or price_yr <= 0:
                price_yr = self._get_stock_price(ts_code, f"{yr}-06-30")

            if price_yr is None or price_yr <= 0:
                continue

            annual_yields.append(dps / price_yr * 100)

        if len(annual_yields) == 0:
            return None
        return sum(annual_yields) / len(annual_yields)

    # ————————————————————————————————————————————————
    # 新建：_load_eps_cache（从 fcf_financials/ 直接读取）
    # ————————————————————————————————————————————————

    def _load_eps_cache(self, download: bool = False):
        """加载 EPS 数据（basic_eps），从 data/fcf_financials/income_*_annual.csv 读取。

        零API调用 — 复用 FCF 引擎已有的财务数据缓存。
        """
        from pathlib import Path as _Path
        _FCF_DIR = _DATA_DIR / "fcf_financials"
        annual_files = sorted(_FCF_DIR.glob("income_*_annual.csv"))

        if not annual_files:
            print(f"  ⚠️ 无 income_annual 文件: {_FCF_DIR}")
            return

        print(f"  📂 发现 {len(annual_files)} 个年度 income 文件")

        self._eps_by_year = {}
        n_total = 0

        for f in annual_files:
            try:
                # 从文件名提取年份
                year = int(f.stem.split("_")[1])
            except (ValueError, IndexError):
                continue

            try:
                df = pd.read_csv(f, dtype={"ts_code": str})
                if df.empty or "basic_eps" not in df.columns:
                    continue

                df["basic_eps"] = pd.to_numeric(df["basic_eps"], errors="coerce")
                df = df[df["basic_eps"].notna() & (df["basic_eps"] != 0)]

                for _, row in df.iterrows():
                    code = str(row["ts_code"])
                    eps = float(row["basic_eps"])
                    if code not in self._eps_by_year:
                        self._eps_by_year[code] = {}
                    # 同一年可能有多条记录（B股等），取平均值或最后一条
                    if year not in self._eps_by_year[code]:
                        self._eps_by_year[code][year] = eps
                        n_total += 1
            except Exception:
                pass

        n_codes = len(self._eps_by_year)
        print(f"  📂 EPS 缓存: {n_codes}只, {n_total}条记录")
        if n_codes < 3000:
            print(f"  ⚠️ EPS 覆盖率偏低 ({n_codes}只)，支付率过滤可能大量无法计算")

    def _get_eps(self, ts_code: str, year: int) -> Optional[float]:
        """O(1) 查询 EPS"""
        return self._eps_by_year.get(ts_code, {}).get(year)

    # ————————————————————————————————————————————————
    # 支付率 & DPS 增长过滤（H30269 专用）
    # ————————————————————————————————————————————————

    def _calc_payout_ratio(self, ts_code: str, year: int) -> Optional[float]:
        """计算支付率 = DPS_pre / basic_eps"""
        dps = self._get_annual_dividends(ts_code, year, tax_type="pre")
        if dps is None or dps <= 0:
            return None
        eps = self._get_eps(ts_code, year)
        if eps is None or eps <= 0:
            return None
        return dps / eps

    def _check_dps_growth(self, ts_code: str, date_str: str) -> bool:
        """检查过去三年每股股利增长率是否 > 0（解读B：三年累计增长）

        官方原文：「过去三年的每股股利增长率」— 解读为累计增长率：
        即 Y/Y_3y_ago - 1 > 0，而非每年都比前一年增长。
        """
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        years = list(range(latest_year - 3, latest_year + 1))  # 需要4年算累计增长

        dps_values = []
        for yr in years:
            d = self._get_annual_dividends(ts_code, yr, tax_type="pre")
            if d is None or d <= 0:
                return False
            dps_values.append(d)

        # 解读B: 三年累计增长率 = Y4/Y1 - 1 > 0
        growth = dps_values[3] / dps_values[0] - 1
        return growth > 0

    def _check_consecutive_dividends_h30269(
        self, ts_code: str, date_str: str, years: int = 3
    ) -> bool:
        """检查过去 years 年每年是否有税后分红（H30269 使用税后）"""
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        check_years = list(range(latest_year - years + 1, latest_year + 1))
        for yr in check_years:
            dps = self._get_annual_dividends(ts_code, yr, tax_type="after")
            if dps is None or dps <= 0:
                return False
        return True

    # ————————————————————————————————————————————————
    # 覆盖：select_basket（完全不同的流程）
    # ————————————————————————————————————————————————

    def select_basket(
        self, date_str: str, previous_basket: Dict[str, Dict] = None,
        verbose: bool = True
    ) -> Dict[str, Dict]:
        """
        Phase 2 — H30269 选股流程（纯本地读盘，零API）：

        获取 CSI800 → 连续3年分红 → 支付率过滤 → DPS增长过滤
        → 税后股息率 Top 75 → 波动率 Top 50（含20%缓冲区）
        → 纯股息率加权 15% 封顶

        Parameters
        ----------
        previous_basket : 上期持仓，用于20%缓冲区（保留≥40只原样本）
        """
        if verbose:
            print(f"\n{'─'*50}")
            print(f"  [{date_str}] H30269 红利低波选股")
            print(f"{'─'*50}")

        # Step 1: 获取 CSI 800 成分股
        constituents = self._idx_cache.get_constituents(date_str)
        if verbose:
            print(f"  CSI800成分股: {len(constituents)}只")

        if not constituents:
            return {}

        # Step 2: 连续三年分红检查 + 股息率计算
        eligible = []
        n_no_div_data = 0
        n_no_consecutive = 0
        n_no_yield = 0

        for ts_code in constituents:
            # 分红数据检查
            if ts_code not in self._dps_after_tax:
                n_no_div_data += 1
                continue

            # 连续三年税后分红检查
            if not self._check_consecutive_dividends_h30269(ts_code, date_str):
                n_no_consecutive += 1
                continue

            # 计算三年平均税后股息率
            div_yield = self._calc_avg_dividend_yield(ts_code, date_str)
            if div_yield is None or div_yield <= 0:
                n_no_yield += 1
                continue

            info = self._stock_info_map.get(ts_code, {})
            eligible.append({
                "ts_code": ts_code,
                "name": info.get("name", ts_code),
                "industry": info.get("industry", ""),
                "div_yield_3y": round(div_yield, 4),
            })

        if verbose:
            print(f"  无分红数据: {n_no_div_data} | 无连续分红: {n_no_consecutive} | "
                  f"缺价/股息率: {n_no_yield}")
            print(f"  合格样本（分红过滤后）: {len(eligible)}只")

        # —— 股息率合理性检查 ——
        if eligible and verbose:
            yields = [s["div_yield_3y"] for s in eligible]
            print(f"  📊 股息率: min={min(yields):.2f}% median={np.median(yields):.2f}% "
                  f"max={max(yields):.2f}%")

        if not eligible:
            return {}

        # Step 3: 支付率过滤（剔除为负 + 剔除前 5% 过高）
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        payouts = []
        post_payout = []
        n_no_payout = 0
        n_neg_payout = 0

        for s in eligible:
            payout = self._calc_payout_ratio(s["ts_code"], latest_year)
            if payout is None:
                n_no_payout += 1
                post_payout.append((s, None))
            elif payout <= 0:
                n_neg_payout += 1
            else:
                payouts.append(payout)
                post_payout.append((s, payout))

        # 计算支付率 P95 阈值
        p95_threshold = np.percentile(payouts, 95) if payouts else float("inf")

        if verbose:
            print(f"  支付率过滤: 无法计算{n_no_payout} | 为负{n_neg_payout} | "
                  f"P95阈值={p95_threshold:.2f}")

        eligible_payout = []
        n_high_payout = 0
        for s, p in post_payout:
            if p is None:
                # 无法计算支付率的保留（后续EPS缺失时宽松处理）
                eligible_payout.append(s)
            elif p <= 0:
                continue  # 负支付率剔除
            elif p > p95_threshold:
                n_high_payout += 1  # 前5%过高剔除
            else:
                eligible_payout.append(s)

        if verbose:
            print(f"  支付率过滤后: {len(eligible_payout)}只 "
                  f"(剔除超额{n_high_payout})")

        if not eligible_payout:
            return {}

        # Step 4: DPS 增长过滤（解读B：三年累计增长率 > 0）
        eligible_growth = []
        n_no_growth = 0
        n_no_dps = 0
        for s in eligible_payout:
            ts = s["ts_code"]
            rebalance_year = int(date_str[:4])
            latest_year = rebalance_year - 1
            years = list(range(latest_year - 3, latest_year + 1))
            dps_vals = []
            all_positive = True
            for yr in years:
                d = self._get_annual_dividends(ts, yr, tax_type="pre")
                if d is None or d <= 0:
                    all_positive = False
                    break
                dps_vals.append(d)

            if not all_positive or len(dps_vals) < 4:
                n_no_dps += 1
                continue

            # 解读B：三年累计增长率 = Y4/Y1 - 1 > 0
            growth = dps_vals[3] / dps_vals[0] - 1
            if growth > 0:
                eligible_growth.append(s)
            else:
                n_no_growth += 1

        if verbose:
            print(f"  DPS增长过滤: 缺DPS{n_no_dps} | 非全正增长{n_no_growth} | "
                  f"通过{len(eligible_growth)}只")

        if not eligible_growth:
            return {}

        # Step 5: 税后股息率降序 → Top 75
        eligible_growth.sort(key=lambda x: x["div_yield_3y"], reverse=True)
        top_n_div = min(75, len(eligible_growth))
        div_top_75 = eligible_growth[:top_n_div]

        if verbose:
            print(f"  股息率 Top{top_n_div}: "
                  f"最高 {div_top_75[0]['div_yield_3y']:.2f}% "
                  f"最低 {div_top_75[-1]['div_yield_3y']:.2f}%")

        # Step 6: 波动率升序 → Top 50
        vol_data = self._load_vol_from_disk(date_str)
        n_vol_missing = 0
        for s in div_top_75:
            vol = vol_data.get(s["ts_code"])
            if vol is None or (isinstance(vol, float) and np.isnan(vol)):
                s["ann_vol"] = self.DEFAULT_VOL
                n_vol_missing += 1
            else:
                s["ann_vol"] = vol

        if verbose:
            vols = [s.get("ann_vol") for s in div_top_75 if s.get("ann_vol") != self.DEFAULT_VOL]
            print(f"  波动率数据: 缺失{n_vol_missing}只/{len(div_top_75)}只 "
                  f"({n_vol_missing/len(div_top_75)*100:.1f}%)")

        div_top_75.sort(key=lambda x: x.get("ann_vol", self.DEFAULT_VOL))
        top_n_final = min(50, len(div_top_75))
        selected = div_top_75[:top_n_final]

        if verbose:
            sel_vols = [s.get("ann_vol") for s in selected]
            print(f"  波动率 Top{top_n_final}: "
                  f"最低 {min(sel_vols):.1f}% 最高 {max(sel_vols):.1f}%")

        # Step 7: 20% 缓冲区（官方规则：每次调整样本比例 ≤ 20%）
        # 传入 eligible_payout（DPS过滤前）扩大补回搜索范围
        selected = self._apply_rebalance_buffer(
            selected, date_str, previous_basket, eligible_payout, verbose
        )

        # Step 8: 纯股息率加权 + 15% 单股上限
        selected = self._apply_dividend_weighting(selected, single_cap=0.15)

        # —— CP2: 篮子自检 ——
        self._run_basket_checks(selected, verbose)

        # Step 9: 转为返回格式
        result = {}
        for s in selected:
            result[s["ts_code"]] = {
                "ts_code": s["ts_code"],
                "name": s["name"],
                "industry": s.get("industry", ""),
                "div_yield_3y": s["div_yield_3y"],
                "ann_vol": s.get("ann_vol", self.DEFAULT_VOL),
                "weight": s["weight"],
            }

        # 记录换手
        if previous_basket and verbose:
            old_codes = set(previous_basket.keys())
            new_codes = set(result.keys())
            kept = old_codes & new_codes
            replaced = old_codes - new_codes
            added = new_codes - old_codes
            print(f"  📊 换手: 保留{len(kept)}只 | 调出{len(replaced)}只 | 调入{len(added)}只 "
                  f"| 换手率={len(replaced)/max(len(old_codes),1)*100:.0f}%")

        if verbose:
            total_w = sum(s["weight"] for s in selected)
            avg_yield = np.mean([s["div_yield_3y"] for s in selected])
            avg_vol = np.mean([s.get("ann_vol") or self.DEFAULT_VOL for s in selected])
            print(f"  ✅ 最终持仓: {len(selected)}只, 权重合计={total_w:.4f}, "
                  f"平均股息率={avg_yield:.2f}%, 平均波动率={avg_vol:.1f}%")

        return result

    # ————————————————————————————————————————————————
    # 新建：_calc_single_year_dividend_yield（原样本保留条件）
    # ————————————————————————————————————————————————

    def _calc_single_year_dividend_yield(
        self, ts_code: str, date_str: str, lookback_year: int = None
    ) -> Optional[float]:
        """计算最近一个会计年度的税后股息率：DPS / 年末股价。

        用于原样本保留条件检查（股息率 > 0.5%）。
        """
        rebalance_year = int(date_str[:4])
        yr = lookback_year if lookback_year is not None else (rebalance_year - 1)

        dps = self._get_annual_dividends(ts_code, yr, tax_type="after")
        if dps is None or dps <= 0:
            return None

        price_yr = self._get_stock_price(ts_code, f"{yr}-12-31")
        if price_yr is None or price_yr <= 0:
            price_yr = self._get_stock_price(ts_code, f"{yr}-06-30")
        if price_yr is None or price_yr <= 0:
            return None

        return dps / price_yr * 100

    # ————————————————————————————————————————————————
    # 新建：_apply_rebalance_buffer（官方 20% 缓冲区规则）
    # ————————————————————————————————————————————————

    def _apply_rebalance_buffer(
        self,
        selected: List[Dict],
        date_str: str,
        previous_basket: Dict[str, Dict],
        search_pool: List[Dict],
        verbose: bool = True,
    ) -> List[Dict]:
        """
        官方规则：每次调整样本比例一般不超过 20%（即保留 ≥40 只原样本）。

        逻辑：
        1. 若无上期持仓（首期），直接返回 selected
        2. 检查 selected 中保留了上期多少只
        3. 如果 ≥40 只 → 直接用 selected
        4. 如果 < 40 只 → 从 search_pool 中找合格原样本补回
        5. 原样本满足保留条件（最近年度股息率 > 0.5%）即可补回，
           不受 DPS 增长过滤限制（官方规则只要求这三个条件）

        Parameters
        ----------
        selected : 波动率选出的 Top 50（已按波动率升序排序）
        previous_basket : 上期持仓 {ts_code: info}
        search_pool : 候选池（eligible_payout，DPS过滤前，最大化补回范围）
        """
        if not previous_basket or len(previous_basket) == 0:
            return selected  # 首期，无缓冲

        old_codes = set(previous_basket.keys())
        fresh_codes = set(s["ts_code"] for s in selected)
        overlap = old_codes & fresh_codes
        n_kept = len(overlap)
        n_dropped = len(old_codes - fresh_codes)

        if n_kept >= 40:
            if verbose:
                print(f"  🔒 缓冲区: 保留{n_kept}只原样本 ≥ 40 → 无需补回")
            return selected

        # —— 需要补回原样本 ——
        n_need = 40 - n_kept
        if verbose:
            print(f"  ⚠️ 缓冲区: 仅保留{n_kept}只原样本，需补回{n_need}只")

        # 在 search_pool 中找合格原样本
        # 建立 search_pool 索引 {code: info}（避免重复查询）
        pool_map = {s["ts_code"]: s for s in search_pool}
        dropped_codes = old_codes - fresh_codes

        qualified_old = []
        vol_data = self._load_vol_from_disk(date_str)

        for code in dropped_codes:
            if code not in pool_map:
                continue  # 不在候选池中
            pool_entry = pool_map[code]

            # 检查原样本保留条件：最近年度税后股息率 > 0.5%
            single_yr_yield = self._calc_single_year_dividend_yield(code, date_str)
            if single_yr_yield is None or single_yr_yield <= 0.5:
                continue

            # 构建补回标的（补充波动率）
            vol = vol_data.get(code)
            if vol is None or (isinstance(vol, float) and np.isnan(vol)):
                vol = self.DEFAULT_VOL

            qualified_old.append({
                "ts_code": code,
                "name": pool_entry.get("name", code),
                "industry": pool_entry.get("industry", ""),
                "div_yield_3y": pool_entry.get("div_yield_3y", single_yr_yield),
                "ann_vol": vol,
            })

        if not qualified_old:
            if verbose:
                print(f"  ⚠️ 无合格原样本可补回（{len(dropped_codes)}只调出标的均不满足股息率>0.5%）")
            return selected

        if verbose:
            print(f"  📋 search_pool中合格原样本: {len(qualified_old)}只 "
                  f"(从{len(dropped_codes)}只调出标的中筛选)")

        # 按波动率排序合格原样本（优先补回低波动的）
        qualified_old.sort(key=lambda x: x.get("ann_vol", self.DEFAULT_VOL))
        bring_back = qualified_old[:n_need]

        # 找出 selected 中的"新标的"（非原样本），按波动率降序排列
        new_entries = [s for s in selected if s["ts_code"] not in old_codes]
        new_entries.sort(key=lambda x: x.get("ann_vol", self.DEFAULT_VOL), reverse=True)
        to_remove = set(s["ts_code"] for s in new_entries[:len(bring_back)])

        # 替换
        selected_updated = [s for s in selected if s["ts_code"] not in to_remove]
        selected_updated.extend(bring_back)
        selected_updated.sort(key=lambda x: x.get("ann_vol", self.DEFAULT_VOL))
        selected_updated = selected_updated[:50]

        if verbose:
            new_overlap = old_codes & set(s["ts_code"] for s in selected_updated)
            print(f"  🔒 补回后: 保留{len(new_overlap)}只原样本 "
                  f"(补回{len(bring_back)}只: {[s['ts_code'] for s in bring_back]})")

        return selected_updated

    # ————————————————————————————————————————————————
    # 新建：_apply_dividend_weighting（纯股息率加权 + 15% 封顶）
    # ————————————————————————————————————————————————

    def _apply_dividend_weighting(
        self,
        stocks: List[Dict],
        single_cap: float = 0.15,
        max_iter: int = 200,
    ) -> List[Dict]:
        """
        纯股息率加权 + 单股上限迭代封顶（无行业上限）。

        流程：
          1) 权重 = div_yield_i / Σ div_yield
          2) 迭代：超标→封顶→溢出分配给未超标标的（按当前权重比例）
          3) 归一化
        """
        if not stocks:
            return stocks

        n = len(stocks)

        # 初始权重 = 股息率加权
        scores = [max(s.get("div_yield_3y", 0) or 0, 0.01) for s in stocks]  # guard
        total_score = sum(scores)
        if total_score <= 0:
            w = 1.0 / n
            for s in stocks:
                s["weight"] = round(w, 6)
            return stocks

        weights = [s / total_score for s in scores]

        # 迭代封顶
        for _ in range(max_iter):
            overflow = sum(w - single_cap for w in weights if w > single_cap)
            if overflow < 1e-9:
                break
            capped = [min(w, single_cap) for w in weights]
            below_cap = [c for c in capped if c < single_cap]
            below_sum = sum(below_cap)
            if below_sum <= 0:
                break
            weights = [
                min(c + overflow * (c / below_sum), single_cap)
                if c < single_cap else single_cap
                for c in capped
            ]

        # 归一化
        tw = sum(weights)
        if tw > 0:
            weights = [w / tw for w in weights]

        for s, w in zip(stocks, weights):
            s["weight"] = round(w, 6)

        return stocks

    # ————————————————————————————————————————————————
    # 覆盖：_run_data_quality_checks（追加 EPS 覆盖率）
    # ————————————————————————————————————————————————

    def _run_data_quality_checks(self):
        """Phase 1 数据质量自检（D1-D6，追加 EPS 覆盖率）"""
        print("\n" + "=" * 60)
        print("📋 H30269 数据质量自检")
        print("=" * 60)

        # D1: 成分股权重（复用父类逻辑）
        weights = self._idx_cache._weights
        if weights is not None and not weights.empty:
            n_records = len(weights)
            print(f"  ✅ D1 成分股权重: {n_records}条记录")
            if n_records < 10000:
                print(f"  ⚠️ D1警告: 成分股权重记录较少 ({n_records}条)")
        else:
            print(f"  ❌ D1失败: 成分股权重文件为空")

        # D2: 分红覆盖率
        n_div = len(self._dps_after_tax)
        print(f"  {'✅' if n_div >= 1500 else '⚠️'} D2 分红数据: {n_div}只有分红记录")

        # D3: EPS 覆盖率
        eps_codes = len(self._eps_by_year)
        print(f"  {'✅' if eps_codes >= 3000 else '⚠️'} D3 EPS 数据: {eps_codes}只有 EPS 记录")
        if eps_codes < 3000:
            print(f"     ⚠️ EPS 覆盖率偏低，支付率过滤可能大量无法计算")

        # D4: 波动率覆盖率
        if self._all_rebalance_dates:
            vol_data = self._load_vol_from_disk(self._all_rebalance_dates[0])
            if vol_data:
                valid = sum(1 for v in vol_data.values() if v is not None and not (
                    isinstance(v, float) and np.isnan(v)))
                ratio = valid / len(vol_data) * 100
                print(f"  {'✅' if ratio >= 80 else '⚠️'} D4 波动率覆盖率: "
                      f"{valid}/{len(vol_data)} = {ratio:.1f}%")
            else:
                print(f"  ⚠️ D4: 波动率缓存为空")
        else:
            print(f"  ⚠️ D4: 无调仓日列表")

        # D5: 价格覆盖率
        if self._all_rebalance_dates:
            first_date = self._all_rebalance_dates[0]
            constituents = self._idx_cache.get_constituents(first_date)
            if constituents:
                prices = self._load_prices_from_cache(first_date)
                has_price = sum(1 for c in constituents if c in prices)
                ratio = has_price / len(constituents) * 100
                print(f"  {'✅' if ratio >= 95 else '⚠️'} D5 价格覆盖率({first_date}): "
                      f"{has_price}/{len(constituents)} = {ratio:.1f}%")

        # D6: 行业覆盖率
        if self._all_rebalance_dates:
            first_date = self._all_rebalance_dates[0]
            constituents = self._idx_cache.get_constituents(first_date)
            if constituents:
                has_ind = sum(1 for c in constituents
                             if self._stock_info_map.get(c, {}).get("industry", ""))
                ratio = has_ind / len(constituents) * 100
                print(f"  {'✅' if ratio >= 90 else '⚠️'} D6 行业覆盖率: "
                      f"{has_ind}/{len(constituents)} = {ratio:.1f}%")

        print("=" * 60)

    # ————————————————————————————————————————————————
    # 覆盖：_run_basket_checks（H30269：50 只 + 15% 上限）
    # ————————————————————————————————————————————————

    def _run_basket_checks(self, selected: List[Dict], verbose: bool = True):
        """选股后的篮子自检（CP2：50只/权重和=1/单股≤15%）"""
        if not selected or not verbose:
            return

        n = len(selected)
        weights = [s.get("weight", 0) for s in selected]
        max_w = max(weights) if weights else 0
        total_w = sum(weights) if weights else 0

        # 篮子数量
        if n < 50:
            status_icon = "⚠️" if n >= 40 else "❌"
            print(f"  {status_icon} CP2 篮子数量: {n}只 (目标50只)")
        else:
            print(f"  ✅ CP2 篮子数量: {n}只")

        # 单股权重上限
        if max_w > 0.155:
            print(f"  ⚠️ CP2警告: 单股权重{max_w:.4f} > 15%上限")
        else:
            print(f"  ✅ CP2 单股权重上限: max={max_w:.4f}")

        # 权重合计
        if abs(total_w - 1.0) > 0.01:
            print(f"  ⚠️ CP2警告: 权重合计{total_w:.4f} ≠ 1.0")
        else:
            print(f"  ✅ CP2 权重合计: {total_w:.4f}")

        # 全部通过
        if (n >= 50 and max_w <= 0.155 and abs(total_w - 1.0) <= 0.01):
            print(f"  ✅ CP2 全部通过")
        elif n >= 40 and max_w <= 0.155 and abs(total_w - 1.0) <= 0.01:
            print(f"  ⚠️ CP2 基本通过（数量略低于50）")

    # ————————————————————————————————————————————————
    # 行业暴露分析（H30269 专用，无行业上限）
    # ————————————————————————————————————————————————

    def _report_sector_exposure(self, selected: List[Dict]) -> Dict[str, float]:
        """输出行业暴露分布（仅供分析参考，不执行封顶）"""
        ind_w: Dict[str, float] = {}
        for s in selected:
            ind = s.get("industry", "") or "其他"
            ind_w[ind] = ind_w.get(ind, 0) + s.get("weight", 0)
        return dict(sorted(ind_w.items(), key=lambda x: x[1], reverse=True))


# ============================================================================
# 测试入口
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  H30269 红利低波50 选股引擎 — 测试")
    print("=" * 60)

    engine = H30269Engine(index_code="000906.SH")
    engine.preload(download=False)

    test_dates = ["2018-12-17", "2020-12-14", "2025-12-15"]
    for d in test_dates:
        try:
            basket = engine.select_basket(d)
            print(f"\n  结果: {len(basket)}只")
            if basket:
                top3 = list(basket.items())[:3]
                for code, info in top3:
                    print(f"    {code} {info['name']}: 股息率={info['div_yield_3y']:.2f}% "
                          f"波动率={info.get('ann_vol', 'N/A')} 权重={info['weight']:.4f}")
                # 行业暴露
                exposure = engine._report_sector_exposure(list(basket.values()))
                print(f"    行业暴露: {exposure}")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            import traceback
            traceback.print_exc()
