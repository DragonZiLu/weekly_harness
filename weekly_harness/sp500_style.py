"""
S&P 500 风格选股引擎 — 基于 ZZ800 成分池，对标标普500 指数编制规则。

核心规则（A股适配）：
  1. 市值门槛：ZZ800 成分股本身即为大中盘，直接作为候选池
  2. 盈利要求：最近年度归母净利润 (n_income_attr_p) > 0
  3. 流动性：流通市值 > 0 且流通市值/总市值 ≥ 15%
  4. 行业平衡：按申万一级行业在合格池中的自由流通市值权重，比例分配 300 个名额
  5. 市值加权：自由流通市值加权（free_share × close）+ 单股上限 10% 封顶

v2 (2026-06-10): 加权方式从 circ_mv 升级为 free_share × close（真正自由流通市值）
v3 (2026-06-10): 性能优化（O(1)索引替代O(n)过滤，~30x加速）+ 回退纯申万分类
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data" / "fcf_financials"
_DAILY_BASIC_DIR = _DATA_DIR / "daily_basic_cache"

# ============================================================================
# 行业分类（申万一级 → GICS 11大板块）
# ============================================================================

# 申万一级 → GICS 板块映射（对标标普500行业结构）
_SW_TO_GICS: Dict[str, str] = {
    # ====== 能源 Energy ======
    "石油开采": "能源", "石油加工": "能源", "石油贸易": "能源",
    "煤炭开采": "能源", "焦炭加工": "能源",

    # ====== 材料 Materials ======
    "化工原料": "材料", "农药化肥": "材料", "化纤": "材料",
    "塑料": "材料", "橡胶": "材料", "染料涂料": "材料",
    "日用化工": "材料", "矿物制品": "材料",
    "水泥": "材料", "玻璃": "材料", "陶瓷": "材料", "其他建材": "材料",
    "普钢": "材料", "特种钢": "材料", "钢加工": "材料",
    "铅锌": "材料", "铜": "材料", "铝": "材料", "黄金": "材料", "小金属": "材料",
    "造纸": "材料",

    # ====== 工业 Industrials ======
    "电气设备": "工业", "工程机械": "工业", "专用机械": "工业",
    "轻工机械": "工业", "化工机械": "工业", "纺织机械": "工业",
    "机床制造": "工业", "机械基件": "工业", "电器仪表": "工业",
    "航空": "工业", "船舶": "工业", "运输设备": "工业",
    "铁路": "工业", "公共交通": "工业",
    "建筑工程": "工业", "装修装饰": "工业",
    "环境保护": "工业", "水务": "工业",
    "仓储物流": "工业", "商贸代理": "工业",
    "批发业": "工业", "其他商业": "工业",
    "综合类": "工业",
    "空运": "工业", "水运": "工业", "港口": "工业", "机场": "工业", "路桥": "工业",

    # ====== 可选消费 Consumer Discretionary ======
    "汽车整车": "可选消费", "汽车配件": "可选消费", "汽车服务": "可选消费",
    "摩托车": "可选消费",
    "家用电器": "可选消费", "家居用品": "可选消费",
    "服饰": "可选消费", "纺织": "可选消费",
    "旅游景点": "可选消费", "旅游服务": "可选消费", "酒店餐饮": "可选消费",
    "文教休闲": "可选消费", "广告包装": "可选消费", "影视音像": "可选消费",
    "百货": "可选消费", "超市连锁": "可选消费", "电器连锁": "可选消费",
    "商品城": "可选消费",

    # ====== 必需消费 Consumer Staples ======
    "白酒": "必需消费", "啤酒": "必需消费", "红黄酒": "必需消费",
    "软饮料": "必需消费", "乳制品": "必需消费", "食品": "必需消费",
    "饲料": "必需消费",
    "农业综合": "必需消费", "种植业": "必需消费", "渔业": "必需消费",
    "农用机械": "必需消费",

    # ====== 医疗保健 Health Care ======
    "化学制药": "医疗保健", "生物制药": "医疗保健", "中成药": "医疗保健",
    "医疗保健": "医疗保健", "医药商业": "医疗保健",

    # ====== 金融 Financials ======
    "银行": "金融", "证券": "金融", "保险": "金融", "多元金融": "金融",
    "全国地产": "金融", "区域地产": "金融", "房产服务": "金融",

    # ====== 信息技术 Info Tech ======
    "半导体": "信息技术", "元器件": "信息技术", "通信设备": "信息技术",
    "软件服务": "信息技术", "互联网": "信息技术",
    "IT设备": "信息技术", "电脑设备": "信息技术",

    # ====== 通信服务 Communication Services ======
    "电信运营": "通信服务", "出版业": "通信服务",

    # ====== 公用事业 Utilities ======
    "水力发电": "公用事业", "火力发电": "公用事业", "新型电力": "公用事业",
    "供气供热": "公用事业",

    # ====== 房地产 Real Estate ======
    "园区开发": "房地产",
}


def normalize_industry(raw: str) -> str:
    """将申万行业名映射到 GICS 11大板块"""
    raw = str(raw).strip()
    if not raw:
        return "其他"
    return _SW_TO_GICS.get(raw, "其他")


# ============================================================================
# Sp500StyleEngine — 核心选股引擎
# ============================================================================

class Sp500StyleEngine:
    """S&P 500 风格选股引擎

    用法:
        engine = Sp500StyleEngine()
        engine.preload()                        # 加载数据
        basket = engine.select_basket("2020-03-16", target_n=300)  # 选股
        # basket: {"000001.SZ": {"name":..., "industry":..., "weight":..., ...}, ...}
    """

    TARGET_N = 300          # 目标成分股数量
    CAP = 0.10              # 单股权重上限
    MIN_CIRC_RATIO = 0.15   # 流通市值/总市值 最低比例

    def __init__(self):
        from fcf_universe import IndexWeightCache

        self._idx_cache = IndexWeightCache("000906.SH")  # ZZ800
        self._hs300_cache = IndexWeightCache("000300.SH")  # CSI 300 官方权重（用于加权校准）
        self._stock_basic: Optional[pd.DataFrame] = None  # ts_code, name, industry
        self._income: Dict[int, pd.DataFrame] = {}  # year -> annual income DataFrame
        self._quarterly_income: Optional[pd.DataFrame] = None  # 季度 n_income_attr_p（S&P 500 规则）
        self._quarterly_cashflow: Optional[pd.DataFrame] = None  # 季度 OCF + Capex（FCF 规则）
        self._loaded = False

        # ★ 预建索引 (O(1) lookup)
        self._qi_by_code: Dict[str, pd.DataFrame] = {}       # ts_code → 季度利润表（已排序）
        self._qcf_by_code: Dict[str, pd.DataFrame] = {}      # ts_code → 季度现金流表（已排序）
        self._name_dict: Dict[str, str] = {}                  # ts_code → 股票名称
        self._industry_dict: Dict[str, str] = {}              # ts_code → GICS板块
        self._raw_sw_dict: Dict[str, str] = {}                 # ts_code → 原始申万行业
        self._daily_basic_by_date: Dict[str, pd.DataFrame] = {}  # "YYYYMMDD" → daily_basic DataFrame

    # ----- 预加载 -----

    def preload(self, download_stock_basic: bool = True):
        """预加载所有数据

        Args:
            download_stock_basic: 是否从 tushare 拉取 stock_basic（首次需要）
        """
        t_start = time.time()
        print("=" * 60)
        print("Sp500StyleEngine: 预加载数据...")

        # 1) 加载 ZZ800 成分股权重历史
        print("  [1/6] 加载 ZZ800 成分股权重...")
        self._idx_cache.load()
        print(f"        覆盖 {len(self._idx_cache._weights)} 条记录")

        # 2) 加载 stock_basic（行业分类）+ 预建 name/industry dict
        print("  [2/6] 加载 stock_basic...")
        self._load_stock_basic(download=download_stock_basic)

        # 3) 加载年度利润表
        print("  [3/6] 扫描年度利润表缓存...")
        self._scan_income_files()

        # 4) 加载季度利润表 + 预建索引
        print("  [4/6] 加载季度利润表 + 建索引...")
        self._load_quarterly_income()

        # 5) 加载季度现金流量表 + 预建索引
        print("  [5/6] 加载季度现金流量表 + 建索引...")
        self._load_quarterly_cashflow()

        # 6) 预加载 daily_basic 市值数据（一次读入，O(1)查找）
        print("  [6/6] 预加载 daily_basic 市值缓存...")
        self._preload_daily_basic()

        self._loaded = True
        elapsed = time.time() - t_start
        print(f"✅ 预加载完成 ({elapsed:.1f}s)\n")

    def _load_stock_basic(self, download: bool = True):
        """加载股票基本信息（行业分类）+ 建 O(1) 索引"""
        # 如果已加载，不重复操作
        if self._stock_basic is not None and not self._stock_basic.empty:
            print(f"        (已缓存 {len(self._stock_basic)} 只)")
            return

        if not download:
            self._stock_basic = pd.DataFrame()
            print(f"  ⚠️ 跳过 stock_basic 下载，行业分类不可用")
            return

        import os
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")

        try:
            import tushare as ts
            pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))
            self._stock_basic = pro.stock_basic(
                fields="ts_code,name,industry,list_date,list_status"
            )
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ stock_basic 加载失败: {e}")
            self._stock_basic = pd.DataFrame()

        if self._stock_basic is not None and not self._stock_basic.empty:
            # ★ 预建 O(1) 索引 dict
            for _, row in self._stock_basic.iterrows():
                code = str(row["ts_code"])
                self._name_dict[code] = str(row.get("name", code))
                raw_sw = str(row.get("industry", "其他"))
                if raw_sw and raw_sw != "nan":
                    self._raw_sw_dict[code] = raw_sw
                    self._industry_dict[code] = normalize_industry(raw_sw)
                else:
                    self._raw_sw_dict[code] = "其他"
                    self._industry_dict[code] = "其他"
            print(f"        {len(self._stock_basic)} 只股票基本信息 (索引已建)")
        else:
            print(f"  ⚠️ stock_basic 为空，行业平衡将回退为不分行业")

    def _scan_income_files(self):
        """扫描已存在的年度利润表文件"""
        for year in range(2010, 2026):
            path = _DATA_DIR / f"income_{year}_annual.csv"
            if path.exists():
                try:
                    df = pd.read_csv(path, dtype={"ts_code": str})
                    if "n_income_attr_p" in df.columns:
                        self._income[year] = df
                except Exception:
                    pass
        available = sorted(self._income.keys())
        print(f"        {len(available)} 年份年度利润表可用: {available[0]}-{available[-1]}" if available else "        ⚠️ 无年度利润表文件！")

    def _load_quarterly_income(self):
        """加载季度利润表缓存 + 建 O(1) 索引"""
        path = _DATA_DIR / "quarterly_income.csv"
        if not path.exists():
            print(f"        ⚠️ quarterly_income.csv 不存在，季度盈利检查不可用")
            self._quarterly_income = pd.DataFrame()
            return
        df = pd.read_csv(path, dtype={"ts_code": str, "end_date": str})
        self._quarterly_income = df
        n_stocks = df["ts_code"].nunique()
        # ★ 预建索引：ts_code → 已排序 DataFrame
        for code, grp in df.groupby("ts_code"):
            self._qi_by_code[code] = grp.sort_values("end_date")
        print(f"        {n_stocks} 只股票, {len(df)} 条季度记录 (索引已建)")

    # ----- 数据获取 -----

    def _get_latest_quarterly_profit(self, code: str, ref_date: str) -> Optional[Tuple[float, float]]:
        """获取 ref_date 前最新季度的单季归母净利润 (O(1) 索引查找)

        S&P 500 规则：最近一个季度净利润 > 0（非累计，单季值）

        Returns:
            (single_quarter_profit, cumulative_4q_profit) or None
        """
        stock_data = self._qi_by_code.get(code)
        if stock_data is None or stock_data.empty:
            return None

        ref_d = ref_date.replace("-", "")
        # 找 ref_date 之前最新的季度
        before = stock_data[stock_data["end_date"] <= ref_d]
        if before.empty:
            return None

        latest = before.iloc[-1]
        prev = before.iloc[-2] if len(before) >= 2 else None

        latest_cum = float(latest["n_income_attr_p"])
        if pd.isna(latest_cum):
            return None

        latest_year = int(latest["end_date"][:4])

        if prev is not None and int(prev["end_date"][:4]) == latest_year:
            prev_cum = float(prev["n_income_attr_p"])
            if pd.isna(prev_cum):
                return None
            single_q = latest_cum - prev_cum
        else:
            single_q = latest_cum

        # ★ 连续四个季度滚动求和
        latest_end = latest["end_date"]
        mmdd = latest_end[4:]
        prev_q4 = stock_data[stock_data["end_date"] == f"{latest_year-1}1231"]
        prev_year_q = stock_data[stock_data["end_date"] == f"{latest_year-1}{mmdd}"]

        if not prev_q4.empty and not prev_year_q.empty:
            pq4 = float(prev_q4.iloc[0]["n_income_attr_p"])
            pq = float(prev_year_q.iloc[0]["n_income_attr_p"])
            if pd.notna(pq4) and pd.notna(pq):
                rolling_4q = pq4 + latest_cum - pq
            else:
                rolling_4q = pq4 if pd.notna(pq4) else latest_cum
        elif mmdd == "1231":
            rolling_4q = latest_cum
        else:
            q4_all = stock_data[stock_data["end_date"].str.endswith("1231")]
            rolling_4q = float(q4_all.iloc[-1]["n_income_attr_p"]) if not q4_all.empty else latest_cum

        return (single_q, rolling_4q)

    def _load_quarterly_cashflow(self):
        """加载季度现金流量表缓存 + 建 O(1) 索引"""
        path = _DATA_DIR / "quarterly_cashflow.csv"
        if not path.exists():
            print(f"        ⚠️ quarterly_cashflow.csv 不存在，FCF检查不可用")
            self._quarterly_cashflow = pd.DataFrame()
            return
        df = pd.read_csv(path, dtype={"ts_code": str, "end_date": str})
        self._quarterly_cashflow = df
        n_stocks = df["ts_code"].nunique()
        # ★ 预建索引：ts_code → 已排序 DataFrame
        for code, grp in df.groupby("ts_code"):
            self._qcf_by_code[code] = grp.sort_values("end_date")
        print(f"        {n_stocks} 只股票, {len(df)} 条季度记录 (索引已建)")

    def _preload_daily_basic(self):
        """预加载所有 daily_basic CSV 到内存 dict，避免每期重复读盘"""
        files = sorted(_DAILY_BASIC_DIR.glob("daily_basic_*.csv"))
        if not files:
            print(f"        ⚠️ daily_basic_cache 目录无文件（{_DAILY_BASIC_DIR}）")
            return
        for fp in files:
            # 从文件名提取日期: daily_basic_20260316.csv → "20260316"
            date_key = fp.stem.replace("daily_basic_", "")
            if len(date_key) != 8 or not date_key.isdigit():
                continue
            try:
                df = pd.read_csv(fp, dtype={"ts_code": str})
                self._daily_basic_by_date[date_key] = df
            except Exception:
                pass
        print(f"        {len(self._daily_basic_by_date)} 个交易日市值数据已缓存")

    def _get_quarterly_fcf(self, code: str, ref_date: str) -> Optional[Tuple[float, float]]:
        """获取 ref_date 前最新季度的单季 FCF (O(1) 索引查找)

        FCF = n_cashflow_act - c_pay_acq_const_fiolta（与 fcf_universe.py 对齐）

        Returns:
            (single_quarter_fcf, ttm_fcf) or None
        """
        stock = self._qcf_by_code.get(code)
        if stock is None or stock.empty:
            return None

        ref_d = ref_date.replace("-", "")
        before = stock[stock["end_date"] <= ref_d]
        if before.empty:
            return None

        latest = before.iloc[-1]
        latest_year = int(latest["end_date"][:4])

        # 计算累计 FCF
        ocf_latest = float(latest["n_cashflow_act"])
        capex_latest = float(latest["c_pay_acq_const_fiolta"])
        if pd.isna(ocf_latest) or pd.isna(capex_latest):
            return None
        fcf_latest_cum = ocf_latest - capex_latest

        # 单季 FCF（同一财年内才相减）
        if len(before) > 1:
            prev = before.iloc[-2]
            if int(prev["end_date"][:4]) == latest_year:
                ocf_prev = float(prev["n_cashflow_act"])
                capex_prev = float(prev["c_pay_acq_const_fiolta"])
                if pd.notna(ocf_prev) and pd.notna(capex_prev):
                    single_fcf = fcf_latest_cum - (ocf_prev - capex_prev)
                else:
                    single_fcf = fcf_latest_cum
            else:
                single_fcf = fcf_latest_cum
        else:
            single_fcf = fcf_latest_cum

        # ★ 连续四个季度滚动求和（S&P 500 规则）
        # rolling_4Q = prev_year_Q4_cum + latest_cum - same_quarter_prev_year_cum
        latest_end = latest["end_date"]
        mmdd = latest_end[4:]
        # 上年 Q4 FCF 累计
        prev_q4_fcf = stock[stock["end_date"] == f"{latest_year-1}1231"]
        prev_year_q_fcf = stock[stock["end_date"] == f"{latest_year-1}{mmdd}"]

        if not prev_q4_fcf.empty and not prev_year_q_fcf.empty and mmdd != "1231":
            ocf_pq4 = float(prev_q4_fcf.iloc[0]["n_cashflow_act"])
            capex_pq4 = float(prev_q4_fcf.iloc[0]["c_pay_acq_const_fiolta"])
            ocf_pq = float(prev_year_q_fcf.iloc[0]["n_cashflow_act"])
            capex_pq = float(prev_year_q_fcf.iloc[0]["c_pay_acq_const_fiolta"])
            if all(pd.notna(x) for x in [ocf_pq4, capex_pq4, ocf_pq, capex_pq]):
                pq4_f = ocf_pq4 - capex_pq4
                pq_f = ocf_pq - capex_pq
                rolling_4q_fcf = pq4_f + fcf_latest_cum - pq_f
            else:
                rolling_4q_fcf = fcf_latest_cum
        elif mmdd == "1231":
            rolling_4q_fcf = fcf_latest_cum
        else:
            q4 = stock[stock["end_date"].str.endswith("1231")]
            if not q4.empty:
                ocf_q4 = float(q4.iloc[-1]["n_cashflow_act"])
                capex_q4 = float(q4.iloc[-1]["c_pay_acq_const_fiolta"])
                rolling_4q_fcf = ocf_q4 - capex_q4 if pd.notna(ocf_q4) and pd.notna(capex_q4) else fcf_latest_cum
            else:
                rolling_4q_fcf = fcf_latest_cum

        return (single_fcf, rolling_4q_fcf)

    def _get_raw_sw_industry(self, code: str) -> str:
        """获取原始申万一级行业名 (O(1) 索引)"""
        return self._raw_sw_dict.get(code, "其他")

    def _get_industry(self, code: str) -> str:
        """获取股票的申万一级行业分类 (O(1) 索引)"""
        return self._raw_sw_dict.get(code, "其他")

    def _get_stock_name(self, code: str) -> str:
        """获取股票名称 (O(1) 索引)"""
        return self._name_dict.get(code, code)

    def _get_annual_profit(self, code: str, year: int) -> Optional[float]:
        """获取某只股票某年度的归母净利润 (n_income_attr_p)

        Returns:
            float or None: 归母净利润（元），None 表示数据缺失
        """
        df = self._income.get(year)
        if df is None:
            return None
        rows = df[df["ts_code"] == code]
        if rows.empty:
            return None
        val = rows.iloc[0]["n_income_attr_p"]
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _get_profitability_check(
        self, code: str, ref_year: int, ref_quarter: int, date_str: str = "",
        use_fcf: bool = False, use_both: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """盈利检查

        三种模式：
          - 默认：最近单季净利润 > 0 且 TTM 净利润 > 0
          - FCF：最近单季 FCF > 0 且 TTM FCF > 0
          - BOTH：净利润双正 AND FCF 双正（最严格）
        """
        # -- BOTH 模式：净利润双正 AND FCF 双正（均用滚动4季度）--
        if use_both and date_str:
            # 净利润检查（★ 滚动4季度：最新连续四个季度累计归母净利润 > 0）
            q_ni = self._get_latest_quarterly_profit(code, date_str)
            if q_ni is None:
                return False, "无单季净利润数据"
            if q_ni[0] <= 0:
                return False, f"单季净利润={q_ni[0]/1e8:.2f}亿≤0"
            # ★ rolling_4q 替代年报TTM
            rolling_4q_ni = q_ni[1]
            if pd.isna(rolling_4q_ni) or rolling_4q_ni <= 0:
                return False, f"连续4季度净利润={rolling_4q_ni/1e8:.2f}亿≤0"

            # FCF 检查（★ 滚动4季度：最新连续四个季度累计 FCF > 0）
            q_fcf = self._get_quarterly_fcf(code, date_str)
            if q_fcf is None:
                return False, "无FCF数据"
            if q_fcf[1] <= 0:
                return False, f"连续4季度FCF={q_fcf[1]/1e8:.2f}亿≤0"
            if q_fcf[0] <= 0:
                return False, f"单季FCF={q_fcf[0]/1e8:.2f}亿≤0"

            return True, None

        # -- FCF 模式 --
        if use_fcf and date_str:
            q_result = self._get_quarterly_fcf(code, date_str)
            if q_result is None:
                use_fcf = False  # 回退
            else:
                _, ttm = q_result
                if ttm <= 0:
                    return False, f"TTM FCF={ttm/1e8:.2f}亿 ≤ 0"

        # -- 默认/回退：净利润模式 --
        if not use_fcf:
            annual_year = ref_year - 1 if ref_quarter <= 3 else ref_year
            ttm = self._get_annual_profit(code, annual_year)
            if ttm is None:
                ttm = self._get_annual_profit(code, annual_year - 1)
            if ttm is None and date_str:
                # ★ 年度数据缺失时，回退到季度滚动4季度（如银行/保险/石油等大市值股）
                q_result = self._get_latest_quarterly_profit(code, date_str)
                if q_result is not None:
                    ttm = q_result[1]  # rolling_4q（连续四个季度累计归母净利润）
            if ttm is None:
                return False, f"无{annual_year}年报数据，且无季度数据"
            if ttm <= 0:
                return False, f"TTM净利润={ttm/1e8:.2f}亿 ≤ 0"

        # 条件1：最近一个季度 > 0（单季）
        if use_fcf and date_str:
            q_result = self._get_quarterly_fcf(code, date_str)
            if q_result is None:
                return True, None
            single_q, _ = q_result
            if single_q <= 0:
                return False, f"单季FCF={single_q/1e8:.2f}亿 ≤ 0"
        elif date_str:
            q_result = self._get_latest_quarterly_profit(code, date_str)
            if q_result is None:
                return True, None
            single_q, _ = q_result
            if single_q <= 0:
                return False, f"单季净利润={single_q/1e8:.2f}亿 ≤ 0"

        return True, None

    def _get_hs300_official_weights(self, date_str: str) -> Dict[str, float]:
        """获取 CSI 300 在 date_str 时的官方权重（前向填充）

        Returns:
            {ts_code: weight_in_percent}, 如 {"600519.SH": 3.49}
            权重已包含分级靠档调整 + 10% 单股上限
        """
        if self._hs300_cache._weights is None or self._hs300_cache._weights.empty:
            return {}

        d = date_str.replace("-", "")
        dates = sorted(self._hs300_cache._weights["trade_date"].unique())
        if not dates:
            return {}

        # 前向填充：取 <= date 的最近快照
        prior = [x for x in dates if x <= d]
        snap = prior[-1] if prior else dates[0]

        snap_df = self._hs300_cache._weights[
            self._hs300_cache._weights["trade_date"] == snap
        ]
        weights = {}
        for _, row in snap_df.iterrows():
            code = str(row["con_code"])
            w = float(row["weight"])
            if w > 0:
                weights[code] = w

        return weights

    def _load_market_cap(
        self, date_str: str, codes: List[str]
    ) -> Dict[str, Dict[str, float]]:
        """从预加载的 daily_basic dict 获取市值数据 (O(1) 内存查找)

        Args:
            date_str: 日期 "YYYY-MM-DD"
            codes: 需要加载市值的股票代码列表

        Returns:
            {code: {
                "total_mv": float(万元),
                "circ_mv": float(万元),
                "free_float_mv": float(万元),  # ★ free_share × close（真正自由流通市值）
            }, ...}
        """
        code_set = set(codes)
        result: Dict[str, Dict[str, float]] = {}

        base = datetime.strptime(date_str[:10], "%Y-%m-%d")

        # 回溯最多 7 个交易日
        for delta in range(7):
            d = (base - timedelta(days=delta)).strftime("%Y%m%d")
            df = self._daily_basic_by_date.get(d)
            if df is None:
                continue

            needed = code_set - set(result.keys())
            if not needed:
                break

            for _, row in df.iterrows():
                code = str(row["ts_code"])
                if code not in needed:
                    continue
                total_mv = row.get("total_mv")
                circ_mv = row.get("circ_mv")
                if pd.isna(total_mv) or pd.isna(circ_mv):
                    continue
                total_mv = float(total_mv)
                circ_mv = float(circ_mv)
                if total_mv <= 0 or circ_mv <= 0:
                    continue

                entry: Dict[str, float] = {"total_mv": total_mv, "circ_mv": circ_mv, "free_float_mv": 0.0}

                # ★ 计算自由流通市值 = free_share(万股) × close(元)
                free_share = row.get("free_share")
                close = row.get("close")
                if pd.notna(free_share) and pd.notna(close):
                    free_share = float(free_share)
                    close = float(close)
                    if free_share > 0 and close > 0:
                        entry["free_float_mv"] = free_share * close  # 万元

                # 兜底：如果 free_share 不可用，用 circ_mv 近似
                if entry["free_float_mv"] <= 0:
                    entry["free_float_mv"] = circ_mv

                result[code] = entry

        return result

    # ----- 核心选股流程 -----

    def select_basket(
        self,
        date_str: str,
        target_n: int = 300,
        verbose: bool = True,
        use_fcf: bool = False,
        use_both: bool = False,
    ) -> Dict[str, Dict]:
        """S&P 500 风格选股

        Args:
            date_str: 调仓日期 "YYYY-MM-DD"
            target_n: 目标成分股数量 (默认300)
            verbose: 打印筛选过程

        Returns:
            {
                "000001.SZ": {
                    "name": "平安银行",
                    "industry": "银行",
                    "total_mv": 12345678.0,     # 总市值（万元）
                    "circ_mv": 8765432.0,        # 流通市值（万元）
                    "circ_ratio": 0.71,           # 流通占比
                    "profit": 12345678901.0,      # 年度归母净利润（元）
                    "weight": 0.0234,             # 流通市值加权权重
                },
                ...
                "__stats__": {                    # 统计信息
                    "total_candidates": 800,
                    "passed_profit": 720,
                    "passed_liquidity": 680,
                    "selected": 300,
                    "industry_distribution": {"银行": 25, ...},
                }
            }
        """
        if not self._loaded:
            raise RuntimeError("请先调用 preload() 加载数据")

        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        rebalance_month = dt.month

        # 确定参考报告期
        if 1 <= rebalance_month <= 3:
            ref_year, ref_quarter = dt.year - 1, 3
        elif 4 <= rebalance_month <= 6:
            ref_year, ref_quarter = dt.year, 1
        elif 7 <= rebalance_month <= 9:
            ref_year, ref_quarter = dt.year, 2
        else:
            ref_year, ref_quarter = dt.year, 3

        if verbose:
            print(f"\n{'='*60}")
            print(f"选股: {date_str} | 参考报告期: {ref_year}Q{ref_quarter} | 目标: {target_n}只")

        # ============================================================
        # Step 1: 获取 ZZ800 成分股
        # ============================================================
        all_constituents = self._idx_cache.get_constituents(date_str)
        if verbose:
            print(f"  Step 1: ZZ800 成分股 = {len(all_constituents)} 只")

        # ============================================================
        # Step 2: 盈利过滤（年度归母净利润 > 0）
        # ============================================================
        passed_profit: List[str] = []
        profit_vals: Dict[str, float] = {}
        failed_profit: List[Tuple[str, str]] = []

        for code in all_constituents:
            ok, reason = self._get_profitability_check(code, ref_year, ref_quarter, date_str, use_fcf, use_both)
            if ok:
                passed_profit.append(code)
                # 获取利润值用于后续
                # 优先年度数据，缺失时回退到季度滚动4季度（TTM）
                annual_year = ref_year - 1 if ref_quarter <= 3 else ref_year
                p = self._get_annual_profit(code, annual_year)
                if p is None:
                    p = self._get_annual_profit(code, annual_year - 1)
                if p is None and date_str:
                    q_result = self._get_latest_quarterly_profit(code, date_str)
                    if q_result is not None:
                        p = q_result[1]  # rolling_4q（连续四个季度累计归母净利润）
                if p is None:
                    p = 0.0
                profit_vals[code] = p
            elif reason:
                failed_profit.append((code, reason))

        if verbose:
            data_missing = sum(1 for _, r in failed_profit if "无" in r)
            neg_profit = len(failed_profit) - data_missing
            print(f"  Step 2: 盈利过滤 → 通过 {len(passed_profit)}/{len(all_constituents)}"
                  f" | 亏损 {neg_profit} | 缺数据 {data_missing}")

        if len(passed_profit) < target_n:
            if verbose:
                print(f"  ⚠️ 盈利通过仅 {len(passed_profit)} 只，不足目标 {target_n} 只，放宽条件")
            # 数据缺失的也纳入（净利润可能 OK 只是没缓存）
            passed_profit = list(set(passed_profit) | {
                c for c, r in failed_profit if "无" in r
            })

        # ============================================================
        # Step 3: 加载市值 + 流动性过滤
        # ============================================================
        mv_data = self._load_market_cap(date_str, passed_profit)

        passed_liquidity: List[str] = []
        circ_ratios: Dict[str, float] = {}
        for code in passed_profit:
            mv = mv_data.get(code)
            if mv is None:
                continue  # 无市值数据，跳过
            circ_ratio = mv["circ_mv"] / mv["total_mv"] if mv["total_mv"] > 0 else 0
            if circ_ratio < self.MIN_CIRC_RATIO:
                continue
            passed_liquidity.append(code)
            circ_ratios[code] = circ_ratio

        if verbose:
            print(f"  Step 3: 流动性过滤 → 通过 {len(passed_liquidity)}/{len(passed_profit)}"
                  f" (circ/total ≥ {self.MIN_CIRC_RATIO*100:.0f}%)")

        if len(passed_liquidity) < target_n:
            if verbose:
                print(f"  ⚠️ 流动性通过仅 {len(passed_liquidity)} 只，放宽 circ 比例到 5%")
            passed_liquidity = []
            for code in passed_profit:
                mv = mv_data.get(code)
                if mv is None:
                    continue
                circ_ratio = mv["circ_mv"] / mv["total_mv"] if mv["total_mv"] > 0 else 0
                if circ_ratio < 0.05:
                    continue
                passed_liquidity.append(code)
                circ_ratios[code] = circ_ratio
            if verbose:
                print(f"          → 通过 {len(passed_liquidity)}/{len(passed_profit)} (放宽后)")

        # ============================================================
        # Step 4: 行业平衡分配（纯申万一级行业，108个细分行业）
        #
        # 对标 S&P 500 GICS 行业平衡思路，但保留申万细粒度分类，
        # 避免粗粒度 GICS 板块内好/差子行业互相稀释 alpha。
        # ============================================================
        eligible = passed_liquidity

        # 4a: 按申万行业分组 + 计算自由流通市值权重
        industry_groups: Dict[str, List[str]] = {}
        industry_mv: Dict[str, float] = {}
        for code in eligible:
            ind = self._get_industry(code)
            industry_groups.setdefault(ind, []).append(code)
        for ind, codes in industry_groups.items():
            industry_mv[ind] = sum(mv_data.get(c, {}).get("circ_mv", 0) for c in codes)
        total_industry_mv = sum(industry_mv.values())

        # 4b: D'Hondt 方法按自由流通市值比例分配 slot
        # 保证每个行业至少 1 个 slot（当 target_n ≥ 行业数时），
        # 大行业按市值比例获得更多 slot，避免超额分配和字母序截断
        industry_slots: Dict[str, int] = {ind: 0 for ind in industry_groups}
        if total_industry_mv > 0 and target_n > 0:
            # D'Hondt/Jefferson 方法：逐 slot 分配给 mv/(slots+1) 最大的行业
            for _ in range(target_n):
                best_ind = max(industry_groups.keys(),
                              key=lambda ind: industry_mv[ind] / (industry_slots[ind] + 1))
                industry_slots[best_ind] += 1
        else:
            ni = len(industry_groups)
            for i, ind in enumerate(sorted(industry_groups.keys())):
                industry_slots[ind] = target_n // ni + (1 if i < target_n % ni else 0)

        # 4c: 行业内按流通市值排序选股（按行业市值降序排列，大行业优先）
        selected: List[str] = []
        for ind, _ in sorted(industry_slots.items(), key=lambda x: -industry_mv.get(x[0], 0)):
            slots = industry_slots[ind]
            codes_in = industry_groups.get(ind, [])
            codes_sorted = sorted(
                codes_in,
                key=lambda c: mv_data.get(c, {}).get("circ_mv", 0),
                reverse=True
            )
            picked = codes_sorted[:min(slots, len(codes_sorted))]
            selected.extend(picked)

        # 补满（某些行业股票不够）
        if len(selected) < target_n:
            remaining_codes = [c for c in eligible if c not in set(selected)]
            remaining_codes.sort(
                key=lambda c: mv_data.get(c, {}).get("circ_mv", 0),
                reverse=True
            )
            need = target_n - len(selected)
            selected.extend(remaining_codes[:need])

        if verbose:
            selected_industries = {}
            for c in selected:
                ind = self._get_industry(c)
                selected_industries[ind] = selected_industries.get(ind, 0) + 1
            print(f"  Step 4: 行业平衡分配 → 选中 {len(selected)} 只")
            top_inds = sorted(selected_industries.items(), key=lambda x: -x[1])[:6]
            ind_summary = ", ".join(f"{ind}({n})" for ind, n in top_inds)
            print(f"         行业分布: {ind_summary}")

        # ============================================================
        # Step 5: 自由流通市值加权（free_share × close）+ 10% 上限封顶
        #
        # ★ v2 改进：使用真正的自由流通市值，而非流通市值(circ_mv)
        #   - circ_mv 包含大股东持有但很少交易的股份（银行股严重高估）
        #   - free_float_mv = free_share(万股) × close(元)
        #   - free_share 已剔除控股股东、战略投资者、员工持股等非自由流通部分
        # ============================================================
        if len(selected) == 0:
            if verbose:
                print(f"  Step 5: ⚠️ 无标的入选，返回空篮子")
            return {"__stats__": {
                "total_candidates": len(all_constituents),
                "passed_profit": len(passed_profit),
                "passed_liquidity": len(passed_liquidity),
                "selected": 0,
                "industry_distribution": {},
            }}

        weights = []
        for code in selected:
            ff_mv = mv_data.get(code, {}).get("free_float_mv", 0)
            weights.append(max(ff_mv, 0))

        total_w = sum(weights)
        if total_w <= 0:
            raw_w = [1.0 / len(selected)] * len(selected)
        else:
            raw_w = [w / total_w for w in weights]

        # Capped redistribution (max 10%)
        for _ in range(100):
            overflow = sum(max(w - self.CAP, 0) for w in raw_w)
            if overflow < 1e-9:
                break
            capped = [min(w, self.CAP) for w in raw_w]
            below_sum = sum(c for c in capped if c < self.CAP)
            if below_sum <= 0:
                break
            raw_w = [
                min(c + overflow * (c / below_sum), self.CAP) if c < self.CAP else self.CAP
                for c in capped
            ]

        # 归一化
        total_w = sum(raw_w)
        final_weights = [round(w / total_w, 6) for w in raw_w]

        # ============================================================
        # 构建输出
        # ============================================================
        basket: Dict[str, Dict] = {}
        for code, w in zip(selected, final_weights):
            mv = mv_data.get(code, {})
            basket[code] = {
                "name": self._get_stock_name(code),
                "industry": self._get_industry(code),
                "total_mv": mv.get("total_mv", 0),
                "circ_mv": mv.get("circ_mv", 0),
                "free_float_mv": mv.get("free_float_mv", 0),  # ★ 自由流通市值
                "circ_ratio": round(circ_ratios.get(code, 0), 4),
                "profit": profit_vals.get(code),
                "weight": w,
            }

        # 统计
        stats = {
            "total_candidates": len(all_constituents),
            "passed_profit": len(passed_profit),
            "passed_liquidity": len(passed_liquidity),
            "selected": len(basket),
            "industry_distribution": {},
        }
        for c in selected:
            ind = self._get_industry(c)
            stats["industry_distribution"][ind] = stats["industry_distribution"].get(ind, 0) + 1

        basket["__stats__"] = stats

        if verbose:
            top_weights = sorted(basket.items(), key=lambda x: -x[1].get("weight", 0) if isinstance(x[1], dict) else 0)[:5]
            print(f"  Step 5: 自由流通市值加权 → 完成")
            for c, d in top_weights:
                if c == "__stats__":
                    continue
                print(f"         {c} {d.get('name', ''):8s} weight={d['weight']:.4f}")

        return basket


# ============================================================================
# 辅助：确定调仓日的参考报告期
# ============================================================================

def get_ref_period(date_str: str) -> Tuple[int, int]:
    """根据调仓日确定最近的可用财务报告期

    Returns:
        (ref_year, ref_quarter): 如 (2020, 1) 表示 2020Q1
    """
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    month = dt.month
    if 1 <= month <= 3:
        return dt.year - 1, 3   # 上年Q3
    elif 4 <= month <= 6:
        return dt.year, 1        # 当年Q1
    elif 7 <= month <= 9:
        return dt.year, 2        # 当年Q2
    else:
        return dt.year, 3        # 当年Q3
