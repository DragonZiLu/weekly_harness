"""
中证全指自由现金流指数（932365）选股模块
===========================================

基于中证指数公司编制方案实现：
  样本空间：中证全指（000985.SH）
  选样方法：
    1. 剔除金融和房地产行业（CSI一级行业）
    2. 过去一年日均成交金额前80%
    3. 自由现金流 > 0 且 企业价值 > 0
    4. 连续5年经营活动现金流量净额为正
    5. 盈利质量排名前80%
    6. 按自由现金流率从高到低选取前100只
  加权方式：自由现金流加权，单样本权重不超过10%
  调仓频率：每季度（3/6/9/12月第二个星期五的下一交易日）
  基日：2013-12-31，基点：1000点

数据获取策略：
  使用 tushare fina_indicator / cashflow / balancesheet / income 批量拉取
  按 end_date 查询全市场数据（一次请求拉全量），缓存为 CSV
  避免逐只查询导致 API 限流

使用方式：
  from weekly_harness.fcf_universe import FcfUniverse

  uni = FcfUniverse()
  uni.preload_all()  # 预加载全部财务数据 + 成分股数据

  # 查询 2020-06-30 时的 FCF Top 100 标的
  basket = uni.get_fcf_basket("2020-06-30", top_n=100)
  # 返回: {ts_code: {name, fcf, ev, fcf_yield, sector, industry, ...}}
"""

from __future__ import annotations

import os
import sys
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import math
import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data" / "fcf_financials"
_INDEX_WEIGHTS_DIR = _PROJECT_ROOT / "data" / "index_weights"


def _get_tushare_api():
    sys.path.insert(0, str(_PROJECT_ROOT))
    import tushare as ts
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


# ─── 金融/房地产行业剔除 ────────────────────────────────────────
# 规则原文：「按照中证一级行业分类，不属于金融或地产行业」
#
# tushare stock_basic.industry 使用申万行业分类，非中证行业分类。
# 无法直接获取中证一级行业，因此采用双重防护：
#   1. 显式剔除已知的申万金融/地产行业（如下）
#   2. 关键词兜底：行业名含"金融""银行""证券""保险""地产""房产"等也剔除
#
# 注意：申万→中证的映射并非完全一一对应，可能存在错杀或漏杀。
# 严格复刻需要中证官方的一级行业分类数据。
_EXCLUDED_INDUSTRIES = {
    # 申万一级：银行
    "银行",
    # 申万一级：非银金融（含子行业）
    "证券", "保险", "多元金融",
    # 申万二级：多元金融下的子类
    "信托", "期货", "融资租赁", "金融控股", "资产管理",
    # 申万一级：房地产（含子行业）
    "房地产开发", "房地产服务",
    # 申万旧版房地产子行业
    "全国地产", "区域地产", "房产服务", "园区开发",
    # —— CSI 中证一级金融行业在申万的常见额外映射 ——
    "投资银行", "券商", "典当", "期货经纪",
    "金融信息服务", "综合金融", "再保险", "保险经纪",
    # —— CSI 房地产相关额外映射 ——
    "房地产经纪", "物业管理",
}

# 关键词兜底：行业名包含任一关键词即视为金融/地产
_EXCLUDED_INDUSTRY_KEYWORDS = [
    "金融", "银行", "证券", "保险", "地产", "房产",
    "信托", "期货", "租赁",  # 融资租赁、金融租赁
    "投行", "基金", "典当",
]

def _is_financial_or_real_estate(industry: str) -> bool:
    """判断该行业是否属于金融或房地产（CSI一级行业判定）。"""
    industry = str(industry).strip()
    if not industry:
        return False
    # 显式匹配
    if industry in _EXCLUDED_INDUSTRIES:
        return True
    # 关键词兜底
    for kw in _EXCLUDED_INDUSTRY_KEYWORDS:
        if kw in industry:
            return True
    return False


# ─── 行业映射（Shenwan → CSI 大类）────────────────────────────
INDUSTRY_TO_SECTOR: Dict[str, str] = {
    "银行": "金融", "多元金融": "金融", "证券": "金融", "保险": "金融",
    "全国地产": "房地产", "区域地产": "房地产", "房产服务": "房地产", "园区开发": "房地产",
    "水力发电": "公用事业", "火力发电": "公用事业", "新型电力": "公用事业",
    "供气供热": "公用事业", "水务": "公用事业", "环境保护": "公用事业",
    "电信运营": "电信业务", "通信设备": "电信业务",
    "白酒": "主要消费", "啤酒": "主要消费", "食品": "主要消费",
    "乳制品": "主要消费", "软饮料": "主要消费", "种植业": "主要消费",
    "农业综合": "主要消费", "百货": "主要消费", "服饰": "可选消费",
    "家用电器": "可选消费", "家居用品": "可选消费", "汽车整车": "可选消费",
    "汽车配件": "可选消费", "摩托车": "可选消费", "文教休闲": "可选消费",
    "广告包装": "可选消费", "日用化工": "可选消费",
    "中成药": "医药卫生", "化学制药": "医药卫生", "医药商业": "医药卫生",
    "医疗保健": "医药卫生", "生物制药": "医药卫生",
    "煤炭开采": "能源", "石油开采": "能源", "石油加工": "能源",
    "铝": "原材料", "铜": "原材料", "特种钢": "原材料", "普钢": "原材料",
    "其他建材": "原材料", "水泥": "原材料", "农药化肥": "原材料",
    "化工原料": "原材料", "染料涂料": "原材料",
    "建筑工程": "工业", "专用机械": "工业", "工程机械": "工业",
    "电气设备": "工业", "运输设备": "工业", "铁路": "工业",
    "路桥": "工业", "港口": "工业", "水运": "工业", "仓储物流": "工业",
    "出版业": "可选消费", "互联网": "信息技术", "IT设备": "信息技术",
    "软件服务": "信息技术", "元器件": "信息技术",
}


# ══════════════════════════════════════════════════════════════════
# 财务数据下载器 & 缓存
# ══════════════════════════════════════════════════════════════════

class FinancialDataCache:
    """
    批量拉取并缓存 A 股全市场年度财务数据。

    每次按 end_date (如 20231231) 拉取全市场 cashflow / balancesheet / income，
    存为 CSV，后续回测直接读取缓存。

    数据项：
      - cashflow: n_cashflow_act (经营CF), c_pay_acq_const_fiolta (购建固定资产等)
      - balancesheet: total_liab (总负债), money_cap (货币资金), total_assets (总资产)
      - income: operate_profit (营业利润，tushare官方字段)
      - fina_indicator: 盈利质量相关的辅助指标
    """

    def __init__(self):
        self._cashflow: Optional[pd.DataFrame] = None
        self._balance: Optional[pd.DataFrame] = None
        self._income: Optional[pd.DataFrame] = None
        self._cashflow_q: Optional[pd.DataFrame] = None  # 季度现金流
        self._balance_q: Optional[pd.DataFrame] = None  # 季度资产负债表
        self._income_q: Optional[pd.DataFrame] = None  # 季度利润表
        self._fina: Optional[pd.DataFrame] = None
        self._stock_basic: Optional[pd.DataFrame] = None
        self._loaded = False
        # ── 快速查找索引（避免每次 DataFrame filter） ──
        self._cf_idx: Optional[Dict] = None   # {(ts_code, end_date8): row}
        self._bs_idx: Optional[Dict] = None
        self._inc_idx: Optional[Dict] = None
        self._cfq_idx: Optional[Dict] = None  # 季度
        self._bsq_idx: Optional[Dict] = None
        self._incq_idx: Optional[Dict] = None

    def download_all(self, start_year: int = 2015, end_year: int = 2025):
        """
        批量下载全市场财务数据（委托 download_fcf_financials.py）。
        
        注意：tushare pro 的 cashflow/balancesheet/income 接口不支持仅按 end_date
        批量查询，需要逐只股票下载。download_fcf_financials.py 会按 CSI 全指成分股
        （剔除金融/地产）下载指定年份的年度财务数据。

        Parameters
        ----------
        start_year : 起始年份（含）
        end_year : 截止年份（含）
        """
        import subprocess
        script = _PROJECT_ROOT / "download_fcf_financials.py"
        
        # download_fcf_financials.py 内部定义了 years = range(2015, 2026)，直接调用即可
        print(f"\n📥 调用 download_fcf_financials.py 下载财务数据...")
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(_PROJECT_ROOT),
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"  ⚠️ 下载脚本返回非零退出码: {result.returncode}")

    def load_from_cache(self):
        """从本地 CSV 加载已缓存的财务数据"""
        if self._loaded:
            return

        cf_dfs, bs_dfs, inc_dfs, fina_dfs = [], [], [], []

        for year in range(2010, 2027):
            cf_p = _DATA_DIR / f"cashflow_{year}.csv"
            bs_p = _DATA_DIR / f"balance_{year}.csv"
            inc_p = _DATA_DIR / f"income_{year}.csv"
            fina_p = _DATA_DIR / f"fina_{year}.csv"

            if cf_p.exists():
                cf_dfs.append(pd.read_csv(cf_p, dtype={"ts_code": str, "ann_date": str,
                                                         "f_ann_date": str, "end_date": str}))
            if bs_p.exists():
                bs_dfs.append(pd.read_csv(bs_p, dtype={"ts_code": str, "ann_date": str,
                                                         "f_ann_date": str, "end_date": str}))
            if inc_p.exists():
                inc_dfs.append(pd.read_csv(inc_p, dtype={"ts_code": str, "ann_date": str,
                                                          "f_ann_date": str, "end_date": str}))
            if fina_p.exists():
                fina_dfs.append(pd.read_csv(fina_p, dtype={"ts_code": str, "ann_date": str,
                                                            "end_date": str}))

        self._cashflow = pd.concat(cf_dfs, ignore_index=True).copy() if cf_dfs else pd.DataFrame()
        self._balance = pd.concat(bs_dfs, ignore_index=True).copy() if bs_dfs else pd.DataFrame()
        self._income = pd.concat(inc_dfs, ignore_index=True).copy() if inc_dfs else pd.DataFrame()
        self._fina = pd.concat(fina_dfs, ignore_index=True).copy() if fina_dfs else pd.DataFrame()

        # ── 加载季度数据（Q1/Q2/Q3）用于 TTM 计算 ──
        # TTM 公式需要上年同期数据，如 2013Q2 TTM 需要 2012Q2
        # 首次调仓为 2013-03，TTM ref=2012Q3，需要 2011Q3
        cfq_dfs, bsq_dfs, incq_dfs = [], [], []
        for year in range(2011, 2027):
            for q in ["Q1", "Q2", "Q3"]:
                cfq_p = _DATA_DIR / f"cashflow_{year}{q}.csv"
                bsq_p = _DATA_DIR / f"balance_{year}{q}.csv"
                incq_p = _DATA_DIR / f"income_{year}{q}.csv"
                if cfq_p.exists():
                    cfq_dfs.append(pd.read_csv(cfq_p, dtype={"ts_code": str, "ann_date": str, "end_date": str}))
                if bsq_p.exists():
                    bsq_dfs.append(pd.read_csv(bsq_p, dtype={"ts_code": str, "ann_date": str, "end_date": str}))
                if incq_p.exists():
                    incq_dfs.append(pd.read_csv(incq_p, dtype={"ts_code": str, "ann_date": str, "end_date": str}))

        self._cashflow_q = pd.concat(cfq_dfs, ignore_index=True).copy() if cfq_dfs else pd.DataFrame()
        self._balance_q = pd.concat(bsq_dfs, ignore_index=True).copy() if bsq_dfs else pd.DataFrame()
        self._income_q = pd.concat(incq_dfs, ignore_index=True).copy() if incq_dfs else pd.DataFrame()

        # 加载 stock_basic
        try:
            pro = _get_tushare_api()
            self._stock_basic = pro.stock_basic(
                fields="ts_code,name,industry,list_date,list_status"
            )
            time.sleep(0.3)
        except Exception:
            self._stock_basic = pd.DataFrame()

        # ── 预转换 end_date 为整数，加速后续过滤（避免 .str[:8] 切片瓶颈） ──
        for df_attr in ("_cashflow", "_balance", "_income",
                        "_cashflow_q", "_balance_q", "_income_q", "_fina"):
            df = getattr(self, df_attr, None)
            if df is not None and not df.empty and "end_date" in df.columns:
                df["_end_date_int"] = df["end_date"].astype(str).str[:8].astype(int)

        self._loaded = True

        # ── 构建快速查找索引 ──
        self._cf_idx = self._build_lookup_index(self._cashflow)
        self._bs_idx = self._build_lookup_index(self._balance)
        self._inc_idx = self._build_lookup_index(self._income)
        self._cfq_idx = self._build_lookup_index(self._cashflow_q, multi=True)
        self._bsq_idx = self._build_lookup_index(self._balance_q, multi=True)
        self._incq_idx = self._build_lookup_index(self._income_q, multi=True)

        print(f"  📂 财务数据缓存: cashflow {len(self._cashflow)}条, "
              f"balance {len(self._balance)}条, income {len(self._income)}条, "
              f"fina {len(self._fina)}条")
        n_cfq = len(self._cashflow_q) if self._cashflow_q is not None else 0
        n_bsq = len(self._balance_q) if self._balance_q is not None else 0
        n_incq = len(self._income_q) if self._income_q is not None else 0
        print(f"  📂 季度数据缓存: cashflow_q {n_cfq}条, "
              f"balance_q {n_bsq}条, income_q {n_incq}条")

    def _build_lookup_index(self, df: Optional[pd.DataFrame],
                            multi: bool = False) -> Optional[Dict]:
        """构建快速查找索引 {(ts_code, end_date8): row_or_list}
        
        multi=True 时，一个 key 可能对应多行（同一公司同一报告期多次修正），
        返回 list；否则只保留最新一行（ann_date 最大的）。
        """
        if df is None or df.empty:
            return None
        idx: Dict = {}
        for _, row in df.iterrows():
            key = (str(row["ts_code"]), str(row["end_date"])[:8])
            if multi:
                idx.setdefault(key, []).append(row)
            else:
                # 保留 ann_date 最大的
                if key not in idx:
                    idx[key] = row
                else:
                    existing = idx[key]
                    if str(row.get("ann_date", "")) > str(existing.get("ann_date", "")):
                        idx[key] = row
        return idx

    def _lookup_row(self, index: Optional[Dict], ts_code: str, period8: str,
                    latest: bool = True) -> Optional[pd.Series]:
        """从索引中查找一行数据。
        
        latest=True: 多行时取 ann_date 最大的（已排序）
        """
        if index is None:
            return None
        key = (ts_code, period8)
        val = index.get(key)
        if val is None:
            return None
        if isinstance(val, list):
            if not val:
                return None
            if latest:
                # 按 ann_date 排序取最新
                return sorted(val, key=lambda r: str(r.get("ann_date", "")))[-1]
            return val[0]
        return val

    def get_annual_financials(
        self, ts_code: str, year: int
    ) -> Dict[str, Optional[float]]:
        """
        获取某只股票某年度的年报财务数据。

        Returns
        -------
        dict with keys: oper_cf, capex, total_liab, money_cap,
                        total_assets, oper_profit, ann_date
        无数据返回 None 对应值
        """
        result = {
            "oper_cf": None, "capex": None, "total_liab": None,
            "money_cap": None, "total_assets": None,
            "oper_profit": None,  # 来自 income 表的 operate_profit 字段
            "ann_date": None,
        }
        end_date = f"{year}1231"

        # ── Cashflow（优先使用索引） ──
        cf = self._lookup_row(self._cf_idx, ts_code, end_date)
        if cf is None and self._cashflow is not None and not self._cashflow.empty:
            cf_rows = self._cashflow[
                (self._cashflow["ts_code"] == ts_code)
                & (self._cashflow["_end_date_int"] == int(end_date))
            ]
            if not cf_rows.empty:
                cf = cf_rows.sort_values("ann_date").iloc[-1]
        if cf is not None:
            result["oper_cf"] = float(cf["n_cashflow_act"]) if pd.notna(cf.get("n_cashflow_act")) else None
            result["capex"] = float(cf["c_pay_acq_const_fiolta"]) if pd.notna(cf.get("c_pay_acq_const_fiolta")) else None
            result["ann_date"] = str(cf.get("ann_date", ""))
            result["ann_date_cf"] = str(cf.get("ann_date", ""))

        # ── Balance Sheet ──
        bs = self._lookup_row(self._bs_idx, ts_code, end_date)
        if bs is None and self._balance is not None and not self._balance.empty:
            bs_rows = self._balance[
                (self._balance["ts_code"] == ts_code)
                & (self._balance["_end_date_int"] == int(end_date))
            ]
            if not bs_rows.empty:
                bs = bs_rows.sort_values("ann_date").iloc[-1]
        if bs is not None:
            result["total_liab"] = float(bs["total_liab"]) if pd.notna(bs.get("total_liab")) else None
            result["money_cap"] = float(bs["money_cap"]) if pd.notna(bs.get("money_cap")) else None
            result["total_assets"] = float(bs["total_assets"]) if pd.notna(bs.get("total_assets")) else None
            result["ann_date_bs"] = str(bs.get("ann_date", ""))

        # ── Income ──
        inc = self._lookup_row(self._inc_idx, ts_code, end_date)
        if inc is None and self._income is not None and not self._income.empty:
            inc_rows = self._income[
                (self._income["ts_code"] == ts_code)
                & (self._income["_end_date_int"] == int(end_date))
            ]
            if not inc_rows.empty:
                inc = inc_rows.sort_values("ann_date").iloc[-1]
        if inc is not None:
            # 优先使用官方 operate_profit 字段
            if "operate_profit" in inc.index:
                val = inc.get("operate_profit")
                if pd.notna(val):
                    result["oper_profit"] = float(val)
            # 回退：从 revenue, oper_cost 等子科目自行计算
            if result["oper_profit"] is None:
                revenue = float(inc["revenue"]) if pd.notna(inc.get("revenue")) else None
                oper_cost = float(inc["oper_cost"]) if pd.notna(inc.get("oper_cost")) else None
                biz_tax = float(inc.get("biz_tax_surchg")) if pd.notna(inc.get("biz_tax_surchg")) else None
                sell_exp = float(inc.get("sell_exp")) if pd.notna(inc.get("sell_exp")) else None
                admin_exp = float(inc.get("admin_exp")) if pd.notna(inc.get("admin_exp")) else None
                fin_exp = float(inc.get("fin_exp")) if pd.notna(inc.get("fin_exp")) else None
                invest_inc = float(inc.get("invest_income")) if pd.notna(inc.get("invest_income")) else None
                if all(v is not None for v in [revenue, oper_cost, biz_tax, sell_exp, admin_exp, fin_exp]):
                    op = revenue - oper_cost - biz_tax - sell_exp - admin_exp - fin_exp
                    if invest_inc is not None:
                        op += invest_inc
                    result["oper_profit"] = op
            result["ann_date_inc"] = str(inc.get("ann_date", ""))

        return result

    def get_ttm_financials(
        self, ts_code: str, ref_period: str
    ) -> Dict[str, Optional[float]]:
        """
        获取 TTM（Trailing Twelve Months）财务数据。

        A股财报为累计数：Q1=1-3月，Q2=1-6月，Q3=1-9月，年报=1-12月
        TTM算法：
          - 若 ref_period 为年报(1231)：直接返回年报数据
          - 若 ref_period 为Q1(0331)：TTM = 去年年报 - 去年Q1 + 今年Q1
          - 若 ref_period 为Q2(0630)：TTM = 去年年报 - 去年Q2 + 今年Q2
          - 若 ref_period 为Q3(0930)：TTM = 去年年报 - 去年Q3 + 今年Q3

        Parameters
        ----------
        ref_period : 参考报告期，如 '20240331', '20240630', '20240930', '20231231'

        Returns
        -------
        dict with keys: oper_cf, capex, total_liab, money_cap,
                        total_assets, oper_profit, ann_date
        流量项(oper_cf/capex/oper_profit)为TTM值，时点项(total_liab/money_cap/total_assets)为期末值
        """
        ref_year = int(ref_period[:4])
        ref_month = int(ref_period[4:6])

        # 年报直接返回
        if ref_month == 12:
            return self.get_annual_financials(ts_code, ref_year)

        # 确定上年同期和本年累计
        prev_year = ref_year - 1
        prev_period = f"{prev_year}{ref_period[4:]}"  # 去年同期
        prev_annual = f"{prev_year}1231"  # 去年年报

        result = {
            "oper_cf": None, "capex": None, "total_liab": None,
            "money_cap": None, "total_assets": None,
            "oper_profit": None, "ann_date": None,
        }

        # ── 获取三个期间的数据（优先使用索引） ──
        def _get_period_data(idx, df, period_str):
            """从索引或DataFrame中获取某报告期数据"""
            # 优先从索引查找（O(1)）
            row = self._lookup_row(idx, ts_code, period_str)
            if row is not None:
                return row
            # 回退到 DataFrame filter
            if df is None or df.empty:
                return None
            rows = df[
                (df["ts_code"] == ts_code)
                & (df["_end_date_int"] == int(period_str))
            ]
            if rows.empty:
                return None
            return rows.sort_values("ann_date").iloc[-1]

        # Cashflow TTM
        cur_cf = _get_period_data(self._cfq_idx, self._cashflow_q, ref_period)
        prev_cf_q = _get_period_data(self._cfq_idx, self._cashflow_q, prev_period)
        prev_cf_ann = _get_period_data(self._cf_idx, self._cashflow, prev_annual)

        if cur_cf is not None and prev_cf_ann is not None and prev_cf_q is not None:
            try:
                ocf_cur = float(cur_cf["n_cashflow_act"])
                ocf_prev_q = float(prev_cf_q["n_cashflow_act"])
                ocf_prev_ann = float(prev_cf_ann["n_cashflow_act"])
                if pd.notna(ocf_cur) and pd.notna(ocf_prev_q) and pd.notna(ocf_prev_ann):
                    result["oper_cf"] = ocf_prev_ann - ocf_prev_q + ocf_cur

                capex_cur = float(cur_cf["c_pay_acq_const_fiolta"])
                capex_prev_q = float(prev_cf_q["c_pay_acq_const_fiolta"])
                capex_prev_ann = float(prev_cf_ann["c_pay_acq_const_fiolta"])
                if pd.notna(capex_cur) and pd.notna(capex_prev_q) and pd.notna(capex_prev_ann):
                    result["capex"] = capex_prev_ann - capex_prev_q + capex_cur

                # ann_date 取三个期间中最晚的
                dates = []
                for r in [cur_cf, prev_cf_q, prev_cf_ann]:
                    d = str(r.get("ann_date", ""))
                    if d and d not in ("nan", "None"):
                        dates.append(d)
                result["ann_date"] = max(dates) if dates else ""
                result["ann_date_cf"] = str(cur_cf.get("ann_date", ""))
            except (ValueError, TypeError):
                pass
        elif cur_cf is not None and prev_cf_ann is not None:
            # ⚠️ TTM回退：缺少上年同期季度数据时，用年报近似
            # 当上年Q3累计数据不可用时，回退到最近年报数据
            # 这不是精确的TTM，但比完全缺失好
            # 常见于：大市值蓝筹（如中国石油601857）季度数据下载不完整
            try:
                # 尝试直接用当期累计值作为近似（仅适用于年报口径或近年底的Q3）
                # 对于Q3(0930)：TTM ≈ 当年Q3累计 + (上年年报 - 上年Q3累计)
                # 如果上年Q3累计不可用，回退到上年年报数据
                ann_fallback = prev_cf_ann
                ocf_cur_val = float(cur_cf["n_cashflow_act"])
                ocf_ann_val = float(ann_fallback["n_cashflow_act"])
                if pd.notna(ocf_cur_val) and pd.notna(ocf_ann_val):
                    # 无法精确计算TTM，使用上年年报作为近似
                    result["oper_cf"] = ocf_ann_val
                    result["_ttm_fallback"] = "prev_annual_no_q3"

                capex_cur_val = float(cur_cf["c_pay_acq_const_fiolta"])
                capex_ann_val = float(ann_fallback["c_pay_acq_const_fiolta"])
                if pd.notna(capex_cur_val) and pd.notna(capex_ann_val):
                    result["capex"] = capex_ann_val
            except (ValueError, TypeError):
                pass

        # Balance Sheet（时点数据，直接用当期）
        cur_bs = _get_period_data(self._bsq_idx, self._balance_q, ref_period)
        if cur_bs is not None:
            try:
                result["total_liab"] = float(cur_bs["total_liab"]) if pd.notna(cur_bs.get("total_liab")) else None
                result["money_cap"] = float(cur_bs["money_cap"]) if pd.notna(cur_bs.get("money_cap")) else None
                result["total_assets"] = float(cur_bs["total_assets"]) if pd.notna(cur_bs.get("total_assets")) else None
                result["ann_date_bs"] = str(cur_bs.get("ann_date", ""))
            except (ValueError, TypeError):
                pass

        # Income TTM
        cur_inc = _get_period_data(self._incq_idx, self._income_q, ref_period)
        prev_inc_q = _get_period_data(self._incq_idx, self._income_q, prev_period)
        prev_inc_ann = _get_period_data(self._inc_idx, self._income, prev_annual)

        if cur_inc is not None and prev_inc_ann is not None and prev_inc_q is not None:
            try:
                op_cur = float(cur_inc.get("operate_profit", cur_inc.get("oper_profit", float("nan"))))
                op_prev_q = float(prev_inc_q.get("operate_profit", prev_inc_q.get("oper_profit", float("nan"))))
                op_prev_ann = float(prev_inc_ann.get("operate_profit", prev_inc_ann.get("oper_profit", float("nan"))))
                if pd.notna(op_cur) and pd.notna(op_prev_q) and pd.notna(op_prev_ann):
                    result["oper_profit"] = op_prev_ann - op_prev_q + op_cur
                result["ann_date_inc"] = str(cur_inc.get("ann_date", ""))
            except (ValueError, TypeError):
                pass

        return result

    def get_bs_ann_date(self, ts_code: str, year: int) -> str:
        """获取资产负债表公告日（用于 as-of 时间校验）"""
        end_date = f"{year}1231"
        bs = self._lookup_row(self._bs_idx, ts_code, end_date)
        if bs is not None:
            return str(bs.get("ann_date", ""))
        if self._balance is not None and not self._balance.empty:
            bs_rows = self._balance[
                (self._balance["ts_code"] == ts_code)
                & (self._balance["_end_date_int"] == int(end_date))
            ]
            if not bs_rows.empty:
                bs = bs_rows.sort_values("ann_date").iloc[-1]
                return str(bs.get("ann_date", ""))
        return ""

    def get_inc_ann_date(self, ts_code: str, year: int) -> str:
        """获取利润表公告日（用于 as-of 时间校验）"""
        end_date = f"{year}1231"
        inc = self._lookup_row(self._inc_idx, ts_code, end_date)
        if inc is not None:
            return str(inc.get("ann_date", ""))
        if self._income is not None and not self._income.empty:
            inc_rows = self._income[
                (self._income["ts_code"] == ts_code)
                & (self._income["_end_date_int"] == int(end_date))
            ]
            if not inc_rows.empty:
                inc = inc_rows.sort_values("ann_date").iloc[-1]
                return str(inc.get("ann_date", ""))
        return ""

    def check_5yr_positive_ocf(self, ts_code: str, base_year: int,
                                start_year: Optional[int] = None,
                                ref_period: Optional[str] = None,
                                strict: bool = True) -> bool:
        """
        检查连续5个年份经营现金流是否均为正。

        严格模式 (strict=True)：要求 base_year-4 到 base_year 共 5 个年份均有数据且 OCF > 0。
        - 数据缺失的年份视为不通过（不再跳过）
        - OCF 为负或为零的年份视为不通过
        - 上市不足5年的标的，5年窗口中缺失的年份（上市前）视为不通过

        宽松模式 (strict=False)：允许跳过缺失年份，上市不足5年仅检查已有年份。
        - 数据缺失的年份跳过（continue）
        - OCF 为负或为零的年份视为不通过
        - 若指定 ref_period，则使用 TTM 口径计算 OCF；否则使用年报口径
        """
        if start_year is None:
            start_year = base_year - 4
        first_year = min(start_year, base_year)
        last_year = base_year
        if last_year < first_year:
            return False
        for year in range(first_year, last_year + 1):
            if ref_period is not None:
                rp_suffix = ref_period[4:]  # e.g. '0930'
                if rp_suffix == '1231':
                    fin = self.get_annual_financials(ts_code, year)
                    ocf = fin["oper_cf"]
                else:
                    this_period = f'{year}{rp_suffix}'
                    fin = self.get_ttm_financials(ts_code, this_period)
                    ocf = fin["oper_cf"]
                    if ocf is None:
                        fin = self.get_annual_financials(ts_code, year)
                        ocf = fin["oper_cf"]
            else:
                fin = self.get_annual_financials(ts_code, year)
                ocf = fin["oper_cf"]
            if strict:
                # 严格模式：数据缺失视为不通过
                if ocf is None:
                    return False
            else:
                # 宽松模式：数据缺失跳过
                if ocf is None:
                    continue
            if ocf <= 0:
                return False
        return True


# ══════════════════════════════════════════════════════════════════
# CSI 全指成分股权重缓存
# ══════════════════════════════════════════════════════════════════

class IndexWeightCache:
    """CSI 全指（000985.SH）成分股权重缓存"""

    def __init__(self, index_code: str = "000985.SH"):
        self.index_code = index_code
        self._weights: Optional[pd.DataFrame] = None

    def download(self):
        """分年段下载全量成分股权重"""
        _INDEX_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
        cached = _INDEX_WEIGHTS_DIR / f"index_weight_{self.index_code}.csv"

        if cached.exists():
            self._weights = pd.read_csv(cached, dtype={"con_code": str, "trade_date": str})
            print(f"  📂 成分股权重缓存: {len(self._weights)} 条")
            return

        pro = _get_tushare_api()
        dfs = []
        year_ranges = [
            ("20140101", "20161231"),
            ("20170101", "20191231"),
            ("20200101", "20221231"),
            ("20230101", "20251231"),
        ]
        for s, e in year_ranges:
            try:
                df = pro.index_weight(index_code=self.index_code, start_date=s, end_date=e)
                time.sleep(0.5)
                if df is not None and not df.empty:
                    dfs.append(df)
                    print(f"  ✅ 成分股 {s[:4]}-{e[:4]}: {len(df)} 条")
            except Exception as ex:
                print(f"  ❌ 成分股 {s[:4]}-{e[:4]}: {ex}")
                time.sleep(1)

        if dfs:
            self._weights = pd.concat(dfs, ignore_index=True)
            self._weights["trade_date"] = self._weights["trade_date"].astype(str)
            self._weights.to_csv(cached, index=False)
        else:
            self._weights = pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"])

    def load(self):
        """从缓存加载"""
        cached = _INDEX_WEIGHTS_DIR / f"index_weight_{self.index_code}.csv"
        if cached.exists():
            self._weights = pd.read_csv(cached, dtype={"con_code": str, "trade_date": str})
            return
        self.download()

    def get_constituents(self, date_str: str) -> List[str]:
        """获取 date_str 时最新成分股列表

        对齐932368编制方案：指数样本空间为中证800指数成分股，
        使用调仓日当期的成分股（即半年调整后的新成分股）。

        CSI800 每年6月和12月进行成分股调整，调整生效日通常在
        月中第二个星期五后的下一交易日。932368 在12月/6月调仓时
        使用调整后的新成分股，因此我们需要使用调仓月月末的快照，
        而非调仓日前的上一个月末快照。

        ⚠️ 经与932368官方成分交叉验证发现：
        若使用前向填充（prior <= date），12月调仓日会取11月末快照，
        遗漏当月新调入的成分股，导致Recall偏低约10%（5/50）。
        修正：对6月/12月调仓月，取当月月末及之后的快照。
        """
        if self._weights is None or self._weights.empty:
            return []
        d = date_str.replace("-", "")
        dates = sorted(self._weights["trade_date"].unique())

        # 判断是否为调仓月（3/6/9/12月）
        rebalance_month = d[4:6]  # '03', '06', '09', '12'
        rebalance_year = d[:4]

        if rebalance_month in ('06', '12'):
            # 半年调整月：使用当月或之后的快照（取调整后的新成分股）
            # 找到当月月末的快照（含当月最后一天）
            month_prefix = f"{rebalance_year}{rebalance_month}"
            same_month = [x for x in dates if x[:6] == month_prefix]
            if same_month:
                snap = same_month[-1]  # 月末快照
            else:
                # 当月无快照，取之后最近的
                later = [x for x in dates if x > d]
                if later:
                    snap = later[0]
                else:
                    prior = [x for x in dates if x <= d]
                    snap = prior[-1] if prior else dates[0]
        else:
            # 非调整月：使用前向填充（最新 <= date 的快照）
            prior = [x for x in dates if x <= d]
            if not prior:
                snap = dates[0]
            else:
                snap = prior[-1]

        members = self._weights[self._weights["trade_date"] == snap]["con_code"].tolist()
        return members


# ══════════════════════════════════════════════════════════════════
# FCF 选股引擎
# ══════════════════════════════════════════════════════════════════

class FcfUniverse:
    """
    中证全指自由现金流指数选股引擎

    每期调仓日执行：
      1. 获取 CSI 全指成分股
      2. 剔除金融/房地产行业
      3. 获取最近可用年报财务数据
      4. 计算 FCF、EV、FCF 率、盈利质量
      5. 过滤：FCF>0, EV>0, 连续5年 OCF>0, 盈利质量前80%
      6. 按 FCF 率降序选 Top N
    """

    INDEX_CODE = "000985.SH"  # 默认中证全指

    def __init__(self, index_code: str = "000985.SH", strict_ocf: bool = True):
        self.index_code = index_code
        self.strict_ocf = strict_ocf
        self._fin_cache = FinancialDataCache()
        self._idx_cache = IndexWeightCache(index_code)
        self._stock_basic: Optional[pd.DataFrame] = None
        self._preloaded = False
        self._data_quality_warnings: List[str] = []

    def preload_all(self, download: bool = False):
        """
        预加载所有数据。

        Parameters
        ----------
        download : 是否先下载原始数据（首次需要）
        """
        if self._preloaded:
            return

        if download:
            self._fin_cache.download_all()
            self._idx_cache.download()

        self._fin_cache.load_from_cache()
        self._idx_cache.load()
        self._stock_basic = self._fin_cache._stock_basic
        self._preloaded = True

    def _get_available_report_year(self, date_str: str, ts_code: str) -> int:
        """
        获取 date_str 时，ts_code 最新可用的年报年份。

        严格按公告日（ann_date）逐只判断，且要求三张表（现金流、资产负债、利润）
        的 ann_date 均 <= date_str 才认定该年报可用。

        这避免了未来函数：
        - 部分公司利润表、资产负债表可能晚于现金流表公告
        - 仅使用现金流表 ann_date 可能导致使用了尚未公告的利润表数据
        - 全表 as-of 校验 = 所有用到字段的公告日均在调仓日之前
        """
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")

        for year in range(dt.year - 1, dt.year - 4, -1):
            # 获取三张表的 ann_date，分别判断
            all_ann_ok = True
            for table_key in ["cashflow", "balance", "income"]:
                # 获取该表格该年份的数据
                if table_key == "cashflow":
                    fin = self._fin_cache.get_annual_financials(ts_code, year)
                    ann_str = fin.get("ann_date", "")
                elif table_key == "balance":
                    ann_str = self._fin_cache.get_bs_ann_date(ts_code, year)
                else:  # income
                    ann_str = self._fin_cache.get_inc_ann_date(ts_code, year)

                if not ann_str or ann_str in ("nan", "None", ""):
                    all_ann_ok = False
                    break
                try:
                    ann_dt = datetime.strptime(str(ann_str)[:8], "%Y%m%d")
                    if ann_dt > dt:
                        all_ann_ok = False
                        break
                except (ValueError, TypeError):
                    all_ann_ok = False
                    break

            if all_ann_ok:
                return year

        return dt.year - 2

    def _calc_fcf(self, oper_cf: Optional[float], capex: Optional[float]) -> Optional[float]:
        """计算自由现金流"""
        if oper_cf is None or capex is None:
            return None
        return oper_cf - capex

    def _calc_ev(
        self,
        total_mv: Optional[float],
        total_liab: Optional[float],
        money_cap: Optional[float],
    ) -> Optional[float]:
        """计算企业价值 EV = 总市值 + 总负债 - 货币资金

        ⚠️ 对齐932368编制方案：EV = 总市值 + 总负债 - 现金
        旧版使用流通市值(circ_mv)，导致中国移动/中国电信等
        流通市值远小于总市值的标的FCF Yield偏高，排名虚高。
        修正为使用总市值(total_mv)，与932368官方一致。

        单位说明：
          - total_mv  来自 daily_basic.total_mv（总市值），单位：万元
          - total_liab 来自 balancesheet，单位：元
          - money_cap  来自 balancesheet，单位：元
          统一换算：total_mv × 10,000 → 元，再与财报数据相加减。
        """
        if total_mv is None or total_liab is None or money_cap is None:
            return None
        return total_mv * 10_000 + total_liab - money_cap

    def _calc_fcf_yield(self, fcf: Optional[float], ev: Optional[float]) -> Optional[float]:
        """计算自由现金流率 = FCF / EV"""
        if fcf is None or ev is None or ev <= 0:
            return None
        return fcf / ev

    def _calc_profit_quality(
        self, oper_cf: Optional[float], oper_profit: Optional[float], total_assets: Optional[float]
    ) -> Optional[float]:
        """计算盈利质量 = (经营CF - 营业利润) / 总资产
        
        oper_profit 由 income 表子科目自行计算：
        营业收入 - 营业成本 - 税金 - 销售费 - 管理费 - 财务费 + 投资收益
        """
        if oper_cf is None or oper_profit is None or total_assets is None or total_assets <= 0:
            return None
        return (oper_cf - oper_profit) / total_assets

    @staticmethod
    def _apply_capped_redistribution(
        raw_weights: Dict[str, float], cap: float = 0.10,
        allow_cash: bool = False,
    ) -> Dict[str, float]:
        """
        迭代 capped redistribution：确保最终权重均 ≤ cap。

        算法：
        1. 将 raw_weights 归一化到总和为 1.0
        2. 找出超过 cap 的标的，将其固定为 cap，标记为已封顶
        3. 将剩余权重空间（1.0 - 已封顶总和）在未封顶标的中按原始权重比例分配
        4. 重复 2-3，直到没有任何标的超过 cap
        5. 若 allow_cash=True 且 n*cap < 1.0，允许剩余现金（不强制满仓）

        边角情况：
        - 若所有标的均超过 cap（FCF 极度集中），允许突破 cap，等权分配
        - 若 allow_cash=True 且标的太少无法满仓（n*cap < 1.0），跳过最终归一化

        Parameters
        ----------
        allow_cash : 当标的数量不足以在 cap 限制下满仓时（n*cap < 1.0），
                     是否允许保留现金（不强制归一化到 1.0）。
        """
        n = len(raw_weights)
        if n == 0:
            return {}

        total_raw = sum(raw_weights.values())
        if total_raw <= 0:
            return {k: 1.0 / n for k in raw_weights}

        # 检查数学可行性：n 只标的 × cap 上限能否达到 100%
        _feasible = n * cap >= 1.0

        # 初始归一化
        weights = {k: v / total_raw for k, v in raw_weights.items()}
        capped_set: set = set()

        for _iteration in range(n):  # 最多 n 次迭代
            violations = {
                k: v for k, v in weights.items()
                if k not in capped_set and v > cap + 1e-10
            }
            if not violations:
                break

            for k in violations:
                capped_set.add(k)
                weights[k] = cap

            # 剩余空间 = 1.0 - 已封顶总和
            used_by_capped = sum(weights[k] for k in capped_set)
            remaining_space = 1.0 - used_by_capped

            if remaining_space <= 1e-10:
                break

            uncapped = {k: v for k, v in weights.items() if k not in capped_set}
            if not uncapped:
                break

            uncapped_total = sum(uncapped.values())
            if uncapped_total <= 1e-10:
                break

            # 将剩余空间按比例分配给未封顶标的
            scale = remaining_space / uncapped_total
            for k in uncapped:
                weights[k] *= scale

        # 边角：所有标的均被截断但总权重 < 1.0
        if len(capped_set) == n and abs(sum(weights.values()) - 1.0) > 1e-8:
            total_w = sum(weights.values())
            if total_w > 0:
                for k in weights:
                    weights[k] /= total_w

        # 最终确保无溢出
        for k in weights:
            weights[k] = min(weights[k], cap)

        # 最终归一化（仅在数学可行时执行：n*cap >= 1.0）
        if _feasible:
            total_w = sum(weights.values())
            if total_w > 1e-10:
                weights = {k: v / total_w for k, v in weights.items()}

        # 再次确保无溢出（归一化可能引入微幅超标）
        for k in weights:
            weights[k] = min(weights[k], cap)

        return weights

    def _apply_turnover_filter(
        self, date_str: str, ts_codes: List[str], verbose: bool = False
    ) -> Tuple[List[str], Dict[str, float]]:
        """
        成交额过滤：保留过去一年日均成交金额排名前 80% 的标的。

        方案要求：「过去一年日均成交金额排名位于样本空间前 80%」
        严格实现：拉取过去约 252 个交易日 daily 的 amount 数据，
        按股票求日均值后排名，取前 80%。

        Returns
        -------
        (filtered_codes, mv_map)
            filtered_codes: 通过成交额过滤的代码列表
            mv_map: {ts_code: total_mv} — 同时获取的总市值，供后续 EV 计算复用
        """
        if not ts_codes:
            return [], {}

        code_set = set(ts_codes)
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")

        # 回推 ~400 个自然日（覆盖约 252 个交易日）
        lookback_start = dt - timedelta(days=400)

        pro = _get_tushare_api()
        daily_dfs = []

        # 按季度批次拉取 daily 数据（避免单次拉取过大触发限制）
        current = lookback_start
        while current < dt:
            batch_end = min(
                current + timedelta(days=92),  # ~一个季度
                dt,
            )
            s = current.strftime("%Y%m%d")
            e = batch_end.strftime("%Y%m%d")
            if s > e:
                break
            try:
                df = pro.daily(
                    start_date=s,
                    end_date=e,
                    fields="ts_code,trade_date,amount",
                )
                time.sleep(0.35)
                if df is not None and not df.empty:
                    daily_dfs.append(df)
            except Exception:
                time.sleep(1)
            current = batch_end + timedelta(days=1)

        if not daily_dfs:
            if verbose:
                print(f"  [FCF] ⚠️ 成交额: daily API 无数据，跳过流动性过滤！"
                      f"（共 {len(ts_codes)} 只候选标的全部通过，但市值数据缺失，"
                      f"后续 EV 计算将逐只补拉）")
            # 记录数据质量警告到实例（供外部检查）
            self._data_quality_warnings.append(
                f"{date_str}: daily API 无成交额数据，跳过流动性过滤"
            )
            return ts_codes, {}

        daily_all = pd.concat(daily_dfs, ignore_index=True)
        daily_all["ts_code"] = daily_all["ts_code"].astype(str)
        daily_all["amount"] = pd.to_numeric(daily_all["amount"], errors="coerce")

        # 仅保留成分股
        in_universe = daily_all[daily_all["ts_code"].isin(code_set)].copy()

        if in_universe.empty:
            if verbose:
                print(f"  [FCF] ⚠️ 成交额: 成分股在该时间段无成交数据，"
                      f"跳过流动性过滤（{len(code_set)} 只候选全部通过）")
            self._data_quality_warnings.append(
                f"{date_str}: 成分股无成交数据，跳过流动性过滤"
            )
            return ts_codes, {}

        # 计算每只股票的日均成交金额
        avg_amount = in_universe.groupby("ts_code")["amount"].mean().sort_values(ascending=False)

        # 取前 80%：前 80% = 排名前 80%（即剔除最后 20%的低成交量股票）
        # 注意：sort_values(ascending=False) 已将成交额从高到低排序，
        #       取前 80% 即保留成交量较大的标的，符合指数编制要求
        cutoff_idx = max(1, int(len(avg_amount) * 0.80))
        passed_codes = avg_amount.iloc[:cutoff_idx].index.tolist()

        # 获取总市值用于后续 EV 计算
        mv_map: Dict[str, float] = {}
        # 复用成交额过滤阶段的 daily 数据获取总市值
        self._batch_fetch_mv_from_daily_basic(pro, date_str, passed_codes, mv_map)

        if verbose:
            cutoff_amt = avg_amount.iloc[cutoff_idx - 1] if cutoff_idx <= len(avg_amount) else 0
            print(f"  [FCF] 成交额过滤: {len(avg_amount)} 只有效, "
                  f"cutoff=日均{cutoff_amt/1e8:.1f}亿元, "
                  f"通过 {len(passed_codes)} 只, 剔除 {len(avg_amount)-len(passed_codes)} 只")

        return passed_codes, mv_map

    _DAILY_BASIC_CACHE_DIR = _DATA_DIR / "daily_basic_cache"

    @staticmethod
    def _load_daily_basic_from_cache(date_key: str) -> Optional[pd.DataFrame]:
        """从本地缓存加载 daily_basic 数据，避免每次 API 调用。
        date_key: YYYYMMDD 格式
        """
        cache_dir = FcfUniverse._DAILY_BASIC_CACHE_DIR
        if not cache_dir.exists():
            return None
        cache_file = cache_dir / f"daily_basic_{date_key}.csv"
        if cache_file.exists():
            try:
                return pd.read_csv(cache_file, dtype={"ts_code": str})
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_mv_from_daily_basic_df(df: pd.DataFrame, code_set: set, mv_map: Dict[str, float],
                                          result_map: Optional[Dict[str, float]] = None) -> int:
        """从 daily_basic DataFrame 中提取市值数据。
        返回填充的数量。
        """
        filled = 0
        target = result_map if result_map is not None else mv_map
        for _, row in df.iterrows():
            code = str(row["ts_code"])
            if code not in code_set:
                continue
            if code not in target:
                mv = row.get("total_mv")
                if pd.notna(mv) and float(mv) > 0:
                    target[code] = float(mv)
                    filled += 1
            circ_key = f"{code}_circ"
            if circ_key not in target:
                cmv = row.get("circ_mv")
                if pd.notna(cmv) and float(cmv) > 0:
                    target[circ_key] = float(cmv)
        return filled

    def _batch_fetch_mv_from_daily_basic(
        self, pro, date_str: str, ts_codes: List[str], mv_map: Dict[str, float]
    ) -> None:
        """
        通过 daily_basic 补充总市值与流通市值数据。
        直接修改传入的 mv_map：{code: total_mv, f"{code}_circ": circ_mv}

        优先从本地缓存 (data/fcf_financials/daily_basic_cache/) 读取，
        仅在缓存缺失时回退到 tushare API，大幅减少 API 调用。
        """
        from datetime import datetime as dt_dt, timedelta as dt_td

        code_set = set(ts_codes)
        remaining = [c for c in ts_codes if c not in mv_map]
        if not remaining:
            return

        date_key = date_str.replace("-", "")
        base = dt_dt.strptime(date_key, "%Y%m%d")

        # 优先从本地缓存读取
        for delta in range(6):
            d = (base - dt_td(days=delta)).strftime("%Y%m%d")
            cached_df = self._load_daily_basic_from_cache(d)
            if cached_df is not None and not cached_df.empty:
                self._extract_mv_from_daily_basic_df(cached_df, code_set, mv_map)
                if all(c in mv_map for c in remaining):
                    return  # 全部命中，无需 API

        # 缓存未完全命中，回退到 API（仅取缺失的）
        for delta in range(6):
            d = (base - dt_td(days=delta)).strftime("%Y%m%d")
            # 再次检查缓存（可能在上面已下载）
            cached_df = self._load_daily_basic_from_cache(d)
            if cached_df is not None and not cached_df.empty:
                self._extract_mv_from_daily_basic_df(cached_df, code_set, mv_map)
                if all(c in mv_map for c in remaining):
                    return
            try:
                df = pro.daily_basic(
                    trade_date=d,
                    fields="ts_code,total_mv,circ_mv",
                )
                time.sleep(0.3)
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        code = str(row["ts_code"])
                        if code in code_set:
                            if code not in mv_map:
                                mv = row.get("total_mv")
                                if pd.notna(mv) and float(mv) > 0:
                                    mv_map[code] = float(mv)
                            # 同时存流通市值（用于 EV 计算）
                            circ_key = f"{code}_circ"
                            if circ_key not in mv_map:
                                cmv = row.get("circ_mv")
                                if pd.notna(cmv) and float(cmv) > 0:
                                    mv_map[circ_key] = float(cmv)
                    if sum(1 for c in remaining if c in mv_map) >= len(remaining):
                        break
            except Exception:
                time.sleep(1)

    def get_fcf_basket(
        self,
        date_str: str,
        top_n: int = 100,
        verbose: bool = False,
        use_ttm: bool = False,
        use_ocf_profit_filter: bool = False,
    ) -> Dict[str, Dict]:
        """
        获取 date_str 时的 FCF 精选股票池。

        Parameters
        ----------
        date_str : 调仓日期 YYYY-MM-DD
        top_n : 选取前 N 只
        verbose : 是否打印筛选过程
        use_ttm : 是否使用 TTM 数据（官方编制方案要求"过去一年"=TTM）
        use_ocf_profit_filter : 是否启用 OCF/营业利润 > 1.0 二维质量过滤
                  若季度数据不可用则自动回退到年报

        Returns
        -------
        {ts_code: {name, fcf, ev, fcf_yield, sector, industry, weight, ...}}
          其中 weight 为 FCF 加权权重（归一化，上限10%）
        """
        if not self._preloaded:
            self.preload_all()

        # ── Step 1: 获取成分股 ──
        constituents = self._idx_cache.get_constituents(date_str)
        if verbose:
            print(f"  [FCF] Step1: {self.index_code} 成分股 {len(constituents)} 只")

        if not constituents:
            return {}

        # ── Step 2: 成交额过滤（过去一年日均成交金额前 80%）──
        # 注意：CSI 300/800 成分股已通过流动性筛选，且 932366/932368 实际指数
        # 编制方案中无成交额过滤条款 → 跳过此过滤
        # 对全指(000985)仍需保留，因为样本空间包含大量低流动性标的
        if self.index_code in ("000300.SH", "000300.CSI", "000906.SH", "000906.CSI"):
            passed_turnover = constituents
            mv_map = {}
            if verbose:
                print(f"  [FCF] Step2: {self.index_code} 跳过成交额过滤"
                      f"（成分股本身已通过流动性筛选，编制方案无此条款）")
        else:
            passed_turnover, mv_map = self._apply_turnover_filter(
                date_str, constituents, verbose=verbose
            )

        if not passed_turnover:
            if verbose:
                print(f"  [FCF] Step2: 成交额过滤后无标的，终止")
            return {}

        # ── Step 3: 获取全样本空间财务数据（含金融地产）──
        # 关键修复：盈利质量cutoff应在全样本空间（含金融地产）上计算，
        # 而非行业过滤后子集。官方方案原文：
        #   「盈利质量由高到低排名位于样本空间前80%」
        # "样本空间"= CSI全指经可投资性筛选后的全部股票
        if self._stock_basic is not None and not self._stock_basic.empty:
            info_map = self._stock_basic.set_index("ts_code").to_dict("index")
        else:
            info_map = {}

        # ── 确定参考报告期 ──
        # 932365 每季度调仓，官方用"过去一年"=TTM
        # 调仓月 → 最新可用季报：
        #   3月调仓 → 用上年Q3(0930)，Q4年报尚未公告
        #   6月调仓 → 用当年Q1(0331)
        #   9月调仓 → 用当年Q2(0630)
        #   12月调仓 → 用当年Q3(0930)
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        rebalance_month = dt.month
        if use_ttm:
            if 1 <= rebalance_month <= 3:
                # 3月调仓：上年Q3
                ref_period = f"{dt.year - 1}0930"
                ref_year = dt.year - 1
            elif 4 <= rebalance_month <= 6:
                # 6月调仓：当年Q1
                ref_period = f"{dt.year}0331"
                ref_year = dt.year
            elif 7 <= rebalance_month <= 9:
                # 9月调仓：当年Q2
                ref_period = f"{dt.year}0630"
                ref_year = dt.year
            else:
                # 12月调仓：当年Q3
                ref_period = f"{dt.year}0930"
                ref_year = dt.year
        else:
            # 回退到年报模式
            ref_period = f"{dt.year - 1}1231"
            ref_year = dt.year - 1

        if verbose:
            mode = "TTM" if use_ttm else "年报"
            print(f"  [FCF] Step3: 数据口径={mode}, 参考报告期={ref_period} "
                  f"（全样本空间 {len(passed_turnover)} 只）")

        all_candidates = []  # 全样本空间候选（含金融地产）
        report_years: Dict[str, int] = {}  # 缓存每只股票的可用年报年份
        for code in passed_turnover:
            info = info_map.get(code, {})
            name = info.get("name", code)
            industry = str(info.get("industry", ""))

            # 获取财务数据（TTM或年报）
            if use_ttm:
                fin = self._fin_cache.get_ttm_financials(code, ref_period)
                # TTM数据缺失时回退到年报
                if fin["oper_cf"] is None or fin["capex"] is None:
                    rep_year = self._get_available_report_year(date_str, code)
                    report_years[code] = rep_year
                    fin = self._fin_cache.get_annual_financials(code, rep_year)
                else:
                    report_years[code] = ref_year
            else:
                # 逐只判定：该股票在 date_str 时最新可用的年报年份
                rep_year = self._get_available_report_year(date_str, code)
                report_years[code] = rep_year
                fin = self._fin_cache.get_annual_financials(code, rep_year)

            # 计算 FCF
            fcf = self._calc_fcf(fin["oper_cf"], fin["capex"])

            # 计算盈利质量
            pq = self._calc_profit_quality(fin["oper_cf"], fin["oper_profit"], fin["total_assets"])

            all_candidates.append({
                "ts_code": code,
                "name": name,
                "industry": industry,
                "is_financial": _is_financial_or_real_estate(industry),
                "fcf": fcf,
                "oper_cf": fin["oper_cf"],
                "capex": fin["capex"],
                "total_liab": fin["total_liab"],
                "money_cap": fin["money_cap"],
                "total_assets": fin["total_assets"],
                "oper_profit": fin["oper_profit"],
                "profit_quality": pq,
            })

        # ── Step 4: 盈利质量前 80%（在全样本空间上计算）──
        # 全样本空间 = 通过可投资性筛选的全部股票（含金融地产）
        # [实验结论 2026-06-06] PQ基数不含金融地产反而降低回测表现
        # (CAGR 15.3%→14.2%, 夏普 0.81→0.76)，Recall对后期无影响
        # 因此保持 pq_base=all 不变，详见 cache/experiment/zz800_fcf_pq_fixed/
        pq_all = [c["profit_quality"] for c in all_candidates if c["profit_quality"] is not None]
        if pq_all:
            pq_cutoff = np.percentile(pq_all, 20)  # 剔除后 20%
        else:
            pq_cutoff = float("-inf")

        if verbose:
            n_financial = sum(1 for c in all_candidates if c["is_financial"])
            n_non_fin = len(all_candidates) - n_financial
            n_below = sum(1 for p in pq_all if p < pq_cutoff)
            # 也计算仅非金融的cutoff用于对比
            pq_non_fin = [c["profit_quality"] for c in all_candidates
                          if c["profit_quality"] is not None and not c["is_financial"]]
            pq_cutoff_nf = np.percentile(pq_non_fin, 20) if pq_non_fin else float("-inf")
            print(f"  [FCF] Step4: 盈利质量 cutoff={pq_cutoff:.4f} "
                  f"（全样本空间 {len(pq_all)} 只，含金融地产 {n_financial} 只）")
            print(f"         仅非金融cutoff={pq_cutoff_nf:.4f} ({len(pq_non_fin)}只) → "
                  f"全样本更{'宽松' if pq_cutoff < pq_cutoff_nf else '严格'}")
            # 统计年报年份分布
            from collections import Counter
            year_dist = Counter(report_years.values())
            year_str = ", ".join(f"{y}年:{n}只" for y, n in sorted(year_dist.items()))
            print(f"         年报年份分布: {year_str}")

        # ── Step 5: 行业过滤 + 应用所有筛选条件 ──
        candidates = [c for c in all_candidates if not c["is_financial"]]
        if verbose:
            print(f"  [FCF] Step5: 剔除金融/地产 {n_financial} 只，"
                  f"剩余 {len(candidates)} 只非金融候选")

        if not candidates:
            return {}

        # ── Step 6: 四条条件同时过滤 ──
        # 条件：(1)非金融地产(已做) (2)FCF>0且EV>0 (3)OCF连续为正 (4)盈利质量≥cutoff
        # 条件(4b)(可选): OCF/营业利润 > 1.0
        # 先做可用缓存快速判断的：FCF>0, 盈利质量≥cutoff, OCF>0
        passed_fast = []
        for c in candidates:
            # 条件(4): 盈利质量（缺失/NaN 数据直接淘汰，避免新上市股票绕过滤）
            if c["profit_quality"] is None or (isinstance(c["profit_quality"], float) and math.isnan(c["profit_quality"])) or c["profit_quality"] < pq_cutoff:
                continue
            # 条件(4b): OCF/营业利润 > 1.0（二维质量过滤，可选）
            if use_ocf_profit_filter:
                oper_cf = c.get("oper_cf")
                oper_profit = c.get("oper_profit")
                if oper_cf is None or oper_profit is None or oper_profit <= 1e6:
                    # 利润缺失/为负/过小(低于100万)→直接排除
                    continue
                if oper_cf / oper_profit <= 1.0:
                    continue
            # 条件(2): FCF > 0（先做，EV需要API后续拉取）
            if c["fcf"] is None or c["fcf"] <= 0:
                continue
            # 条件(3): 连续OCF为正
            stock_rep_year = report_years.get(c["ts_code"], 0)
            code = c["ts_code"]

            # 确定 OCF 检查窗口起始年：
            # - 默认：base_year - 4（最近5个会计年度）
            # - 严格模式：不截断，缺失年份视为不通过
            # - 宽松模式：截断到上市年 max(ocf_start, list_year)，缺失年份跳过
            info_row = info_map.get(code, {})
            list_date_str = str(info_row.get("list_date", ""))
            ocf_start = stock_rep_year - 4

            if not self.strict_ocf:
                # 宽松模式：截断检查窗口到上市年
                try:
                    if list_date_str and len(list_date_str) >= 4:
                        list_year = int(list_date_str[:4])
                        ocf_start = max(ocf_start, list_year)
                except (ValueError, TypeError):
                    pass

            ocf_ref_period = ref_period if use_ttm else None
            if not self._fin_cache.check_5yr_positive_ocf(code, stock_rep_year, start_year=ocf_start, ref_period=ocf_ref_period, strict=self.strict_ocf):
                continue
            passed_fast.append(c)

        if verbose:
            filter_desc = "FCF>0, PQ≥cutoff, 5yrOCF>0"
            if use_ocf_profit_filter:
                filter_desc += ", OCF/利润>1.0"
            print(f"  [FCF] Step6: {filter_desc} → {len(passed_fast)} 只")

        if not passed_fast:
            return {}

        # ── Step 7: 拉取流通市值，计算 EV > 0 ──
        # 复用 turnover_filter 已获取的 mv_map，缺失的补拉
        codes_need_mv = [c["ts_code"] for c in passed_fast if c["ts_code"] not in mv_map]
        if codes_need_mv:
            pro = _get_tushare_api()
            extra_mv = self._batch_fetch_market_cap(pro, date_str, codes_need_mv)
            mv_map.update(extra_mv)
            if verbose:
                print(f"  [FCF] Step7: 补拉市值 {len(extra_mv)//2} 只（已复用 {(len(mv_map)-len(extra_mv))//2} 只）")

        final = []
        for c in passed_fast:
            code = c["ts_code"]
            # 对齐932368编制方案：EV = 总市值 + 总负债 - 现金
            # 使用总市值(total_mv)，而非流通市值(circ_mv)
            # 流通市值对中国移动/中国电信等限售股占比高的标的
            # 远小于总市值，导致FCF Yield虚高
            total_mv = mv_map.get(code)  # 总市值
            if total_mv is None:
                total_mv = mv_map.get(f"{code}_circ")  # 回退流通市值
            if total_mv is None:
                continue

            # 条件(2): EV > 0（用总市值计算，对齐932368）
            ev = self._calc_ev(total_mv, c["total_liab"], c["money_cap"])
            if ev is None or ev <= 0:
                continue

            fcf_yield = self._calc_fcf_yield(c["fcf"], ev)
            if fcf_yield is None:
                continue

            c["total_mv"] = total_mv
            c["circ_mv"] = mv_map.get(f"{code}_circ")
            c["ev"] = ev
            c["fcf_yield"] = fcf_yield
            final.append(c)

        if verbose:
            print(f"  [FCF] Step7: EV>0 过滤后 {len(final)} 只")

        # ── Step 8: 按 FCF 率排序，选 Top N ──
        final.sort(key=lambda x: x["fcf_yield"], reverse=True)
        selected = final[:top_n]

        if verbose:
            print(f"  [FCF] Step8: Top {top_n} FCF率范围 "
                  f"{selected[-1]['fcf_yield']*100:.2f}% ~ {selected[0]['fcf_yield']*100:.2f}%")

        # ── Step 9: 计算 FCF 加权权重（迭代 capped redistribution，上限 10%）──
        # 规则原文：「自由现金流加权，且单一样本权重不超过 10%」
        # 边界检查：若选中数量太少，10%上限下数学上无法满仓
        can_full_invest = len(selected) * 0.10 >= 1.0
        if not can_full_invest and verbose:
            print(f"  [FCF] ⚠️ 选中 {len(selected)} 只 < 10 只: "
                  f"单股10%上限下最多配置 {len(selected)*10:.0f}% 仓位，"
                  f"将允许剩余现金（不强制满仓）")

        # 注意：此处已过滤 FCF>0，直接使用正值 FCF
        total_fcf = sum(r["fcf"] for r in selected)
        raw_weights: Dict[str, float] = {}
        for r in selected:
            raw_weights[r["ts_code"]] = (
                r["fcf"] / total_fcf if total_fcf > 0 else 1.0 / len(selected)
            )

        final_weights = self._apply_capped_redistribution(
            raw_weights, cap=0.10, allow_cash=not can_full_invest
        )

        basket = {}
        for r in selected:
            code = r["ts_code"]
            sector = INDUSTRY_TO_SECTOR.get(r["industry"], "其他")

            basket[code] = {
                "name": r["name"],
                "industry": r["industry"],
                "sector": sector,
                "fcf": r["fcf"],
                "ev": r["ev"],
                "fcf_yield": r["fcf_yield"],
                "profit_quality": r["profit_quality"],
                "total_mv": r["total_mv"],
                "category": "FCF精选",
                "certainty": "B+",
                "is_etf": False,
                "weight": round(final_weights.get(code, 0), 4),
            }

        # 附加数据质量告警
        if self._data_quality_warnings:
            basket["__quality_warnings__"] = list(self._data_quality_warnings)
            self._data_quality_warnings.clear()

        return basket

    def _batch_fetch_market_cap(
        self, pro, date_str: str, ts_codes: List[str]
    ) -> Dict[str, float]:
        """
        批量获取 date_str 时各股票的总市值与流通市值。

        优先从本地缓存 (data/fcf_financials/daily_basic_cache/) 读取，
        仅在缓存缺失时回退到 tushare API。

        返回 {ts_code: total_mv, f"{ts_code}_circ": circ_mv}
        """
        from datetime import datetime as dt_dt, timedelta as dt_td

        date_key = date_str.replace("-", "")
        code_set = set(ts_codes)

        base = dt_dt.strptime(date_key, "%Y%m%d")
        result = {}

        # 优先从本地缓存读取
        for delta in range(6):
            d = (base - dt_td(days=delta)).strftime("%Y%m%d")
            cached_df = self._load_daily_basic_from_cache(d)
            if cached_df is not None and not cached_df.empty:
                self._extract_mv_from_daily_basic_df(cached_df, code_set, {}, result_map=result)
                if result:
                    return result

        # 缓存未命中，回退到 API
        for delta in range(6):
            d = (base - dt_td(days=delta)).strftime("%Y%m%d")
            # 再次检查缓存
            cached_df = self._load_daily_basic_from_cache(d)
            if cached_df is not None and not cached_df.empty:
                self._extract_mv_from_daily_basic_df(cached_df, code_set, {}, result_map=result)
                if result:
                    return result
            try:
                df = pro.daily_basic(
                    trade_date=d,
                    fields="ts_code,total_mv,circ_mv",
                )
                time.sleep(0.3)
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        code = str(row["ts_code"])
                        if code in code_set:
                            if code not in result:
                                mv = row.get("total_mv")
                                if pd.notna(mv) and float(mv) > 0:
                                    result[code] = float(mv)
                            circ_key = f"{code}_circ"
                            if circ_key not in result:
                                cmv = row.get("circ_mv")
                                if pd.notna(cmv) and float(cmv) > 0:
                                    result[circ_key] = float(cmv)
                    if result:
                        break
            except Exception:
                time.sleep(1)

        return result


# ══════════════════════════════════════════════════════════════════
# 便捷入口
# ══════════════════════════════════════════════════════════════════

def get_fcf_eligible_meta(date_str: str, top_n: int = 100) -> Dict[str, Dict]:
    """
    便捷函数：获取 FCF 精选 100 标的的元数据。

    可以作为 BacktestEngine 的 universe 数据源。
    """
    uni = FcfUniverse()
    uni.preload_all()
    return uni.get_fcf_basket(date_str, top_n=top_n)


if __name__ == "__main__":
    # 首次运行：下载全部数据
    uni = FcfUniverse()
    uni.preload_all(download=True)
    
    # 测试调仓日
    for test_date in ["2020-06-30", "2022-03-31", "2024-09-30"]:
        print(f"\n{'='*60}")
        print(f"  📅 {test_date}")
        print(f"{'='*60}")
        basket = uni.get_fcf_basket(test_date, top_n=20, verbose=True)
        print(f"\n  选出 {len(basket)} 只标的:")
        for i, (code, m) in enumerate(sorted(basket.items(), 
                                              key=lambda x: -x[1]["fcf_yield"])[:10]):
            print(f"  {i+1:3d}. {m['name']:<10s} ({code}) "
                  f"FCF率={m['fcf_yield']*100:.2f}%  "
                  f"FCF={m['fcf']/1e8:.1f}亿  "
                  f"权重={m['weight']*100:.1f}%")
