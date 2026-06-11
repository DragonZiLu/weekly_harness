"""
中证800红利指数（931644）选股引擎
==================================

基于中证指数公司编制方案实现：
  样本空间：中证800指数样本（000906.SH）
  过滤条件：
    1. 过去三年连续现金分红
    2. 过去三年股利支付率均值和过去一年股利支付率均大于0且小于1
  选样方法：按过去三年平均现金股息率由高到低排名，选取前100只
  加权方式：股息率加权，单样本权重不超过10%
  调仓频率：每半年（6月和12月第二个星期五的下一交易日）
  换手限制：每次调整样本比例不超过20%

参考FCF策略架构（fcf_universe.py），复用IndexWeightCache和辅助方法。

使用方式：
  from weekly_harness.dividend_universe import DividendUniverse

  uni = DividendUniverse(index_code="000906.SH")
  uni.preload_all()

  basket = uni.get_dividend_basket("2020-06-15", top_n=100)
  # 返回: {ts_code: {name, div_yield_3y, weight, ...}}
"""

from __future__ import annotations

import sys, os, time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_DIV_DIR = _DATA_DIR / "dividend_history"
_FCF_DIR = _DATA_DIR / "fcf_financials"
_IDX_DIR = _DATA_DIR / "index_weights"
_PRICE_DIR = _DATA_DIR / "price_snapshots"  # 历史价格本地缓存（一次下载，永不复用 API）


def _get_tushare_api():
    sys.path.insert(0, str(_PROJECT_ROOT))
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
    import tushare as ts
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


# ════════════════════════════════════════════════════════════════
# IndexWeightCache (from fcf_universe, adapted)
# ════════════════════════════════════════════════════════════════

class IndexWeightCache:
    """指数成分股权重缓存（精简版，复用fcf_universe逻辑）"""

    def __init__(self, index_code: str = "000906.SH"):
        self.index_code = index_code
        self._weights: Optional[pd.DataFrame] = None

    def load(self):
        """从缓存加载成分股权重"""
        cached = _IDX_DIR / f"index_weight_{self.index_code}.csv"
        if cached.exists():
            self._weights = pd.read_csv(cached, dtype={"con_code": str, "trade_date": str})
            print(f"  📂 成分股权重缓存: {self.index_code} {len(self._weights)}条")
            return
        self.download()

    def download(self):
        """分年段下载成分股权重"""
        _IDX_DIR.mkdir(parents=True, exist_ok=True)
        cached = _IDX_DIR / f"index_weight_{self.index_code}.csv"
        if cached.exists():
            self._weights = pd.read_csv(cached, dtype={"con_code": str, "trade_date": str})
            return

        pro = _get_tushare_api()
        dfs = []
        for s, e in [("20140101","20161231"),("20170101","20191231"),
                     ("20200101","20221231"),("20230101","20261231")]:
            try:
                df = pro.index_weight(index_code=self.index_code, start_date=s, end_date=e)
                time.sleep(0.5)
                if df is not None and not df.empty:
                    dfs.append(df)
                    print(f"  ✅ 成分股 {s[:4]}-{e[:4]}: {len(df)}条")
            except Exception as ex:
                print(f"  ❌ 成分股 {s[:4]}-{e[:4]}: {ex}")
                time.sleep(1)
        if dfs:
            self._weights = pd.concat(dfs, ignore_index=True)
            self._weights["trade_date"] = self._weights["trade_date"].astype(str)
            self._weights.to_csv(cached, index=False)
        else:
            self._weights = pd.DataFrame()

    def get_constituents(self, date_str: str) -> List[str]:
        """获取date_str时最新成分股列表，与fcf_universe逻辑一致"""
        if self._weights is None or self._weights.empty:
            return []
        d = date_str.replace("-", "")
        dates = sorted(self._weights["trade_date"].unique())

        rebalance_month = d[4:6]
        rebalance_year = d[:4]

        if rebalance_month in ('06', '12'):
            month_prefix = f"{rebalance_year}{rebalance_month}"
            same_month = [x for x in dates if x[:6] == month_prefix]
            if same_month:
                snap = same_month[-1]
            else:
                later = [x for x in dates if x > d]
                snap = later[0] if later else dates[-1]
        else:
            prior = [x for x in dates if x <= d]
            snap = prior[-1] if prior else dates[0]

        return self._weights[self._weights["trade_date"] == snap]["con_code"].tolist()


# ════════════════════════════════════════════════════════════════
# Dividend Universe — 800红利指数选股引擎
# ════════════════════════════════════════════════════════════════

class DividendUniverse:
    """
    中证800红利指数（931644）选股引擎

    每期调仓日执行：
      1. 获取 CSI 800 成分股
      2. 检查连续三年现金分红
      3. 计算三年股利支付率过滤
      4. 计算三年平均股息率
      5. 按股息率降序取 Top N
      6. 股息率加权 + 10%封顶
      7. 换手率限制（≤20%）
    """

    def __init__(self, index_code: str = "000906.SH"):
        self.index_code = index_code
        self._idx_cache = IndexWeightCache(index_code)
        self._stock_basic: Optional[pd.DataFrame] = None
        self._dividend_cache: Dict[str, pd.DataFrame] = {}  # {ts_code: df} — raw
        self._dps_by_year: Dict[str, Dict[int, float]] = {}  # 预计算: {ts_code: {year: total_dps}}
        self._income_df: Optional[pd.DataFrame] = None
        self._income_loaded = False
        self._income_index: Dict[str, Dict[int, Dict[str, float]]] = {}  # 预索引: {ts_code: {year: {n_income_attr_p, basic_eps}}}
        self._stock_info_map: Dict[str, Dict] = {}  # 预构建: {ts_code: {name, industry, ...}}
        self._price_cache: Dict[str, Dict[str, float]] = {}  # {"BATCH|YYYY-MM-DD": {ts_code: close}}
        self._preloaded = False  # 仿 FCF 的 _preloaded 模式，防止重复加载

    # ─── Data Loading ───────────────────────────────────────

    def preload_all(self, download: bool = False, rebalance_dates: Optional[List[str]] = None):
        """
        预加载所有数据。仿 FCF _preloaded 模式：首次加载后跳过。

        Parameters
        ----------
        download : 是否触发Tushare下载（默认False，用本地缓存）
        rebalance_dates : 调仓日列表，用于提前批量拉取所有需要的价格
        """
        if self._preloaded:
            return

        # 1. Index weights
        if download:
            self._idx_cache.download()
        else:
            self._idx_cache.load()

        # 2. Stock basic info — 缓存到本地磁盘，后续直接读盘（仿 FCF 模式）
        self._load_stock_basic()

        # 3. Dividend data: load from cached CSV files
        self._load_dividend_cache()

        # 4. Income data: load from cached CSV files
        self._load_income_cache()

        print(f"  📂 分红数据: {len(self._dps_by_year)}只有数据")
        print(f"  📂 净利润数据: {'已加载' if self._income_loaded else '未加载'}")

        # 5. 预加载历史价格（所有调仓日 + 需要的年末/年中价格）
        if rebalance_dates:
            self._preload_prices(rebalance_dates)

        self._preloaded = True

    def _load_stock_basic(self):
        """
        加载股票基本信息。优先级：本地 CSV → Tushare API（首次下载后存盘）。

        仿 FCF 模式：静态数据一次下载，后续纯读磁盘。
        """
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _DATA_DIR / "stock_basic.csv"

        # ── 优先读本地缓存 ──
        if cache_file.exists():
            self._stock_basic = pd.read_csv(cache_file, dtype={
                "ts_code": str, "name": str, "industry": str,
                "list_date": str, "list_status": str
            })
            self._build_stock_info_map()
            print(f"  📂 stock_basic: {len(self._stock_basic)}只 (本地缓存)")
            return

        # ── 首次下载 → 存盘 ──
        try:
            pro = _get_tushare_api()
            self._stock_basic = pro.stock_basic(
                fields="ts_code,name,industry,list_date,list_status"
            )
            self._stock_basic.to_csv(cache_file, index=False)
            self._build_stock_info_map()
            print(f"  📂 stock_basic: {len(self._stock_basic)}只 (首次下载+缓存)")
        except Exception as e:
            print(f"  ⚠️ stock_basic加载失败: {e}")
            self._stock_basic = pd.DataFrame()
            self._stock_info_map = {}

    def _build_stock_info_map(self):
        """从 _stock_basic 预构建 O(1) 查询 dict"""
        self._stock_info_map = {}
        for _, r in self._stock_basic.iterrows():
            self._stock_info_map[str(r["ts_code"])] = {
                "name": str(r.get("name", "")),
                "industry": str(r.get("industry", "")),
                "list_date": str(r.get("list_date", "")),
                "list_status": str(r.get("list_status", "")),
            }

    def _preload_prices(self, rebalance_dates: List[str]):
        """批量预加载价格（首次下载存盘，之后读本地文件，零 API）"""
        needed_dates = set()
        for d in rebalance_dates:
            needed_dates.add(d)
            latest_year = int(d[:4]) - 1
            for yr in range(latest_year - 2, latest_year + 1):
                needed_dates.add(f"{yr}-12-31")
                needed_dates.add(f"{yr}-06-30")

        _PRICE_DIR.mkdir(parents=True, exist_ok=True)
        # 统计需要下载的（本地文件不存在）
        to_download = []
        already_cached = 0
        for d in sorted(needed_dates):
            td = self._nearest_trading_day(d)
            if (_PRICE_DIR / f"{td}.csv").exists():
                already_cached += 1
            else:
                to_download.append(d)

        if not to_download:
            print(f"  📂 价格: {already_cached}个日期全部命中本地缓存 (零 API)")
        else:
            print(f"  📥 下载价格: {len(to_download)}个新日期 ({already_cached}已有)...",
                  end=" ", flush=True)
            t0 = time.time()
            for d in to_download:
                self._prices_for_date(d)
                time.sleep(0.10)  # 仅下载时限速，缓存命中零等待
            print(f"完成 ({time.time()-t0:.1f}s)")

        # 全部装入内存
        for d in sorted(needed_dates):
            self._prices_for_date(d)

    def _load_dividend_cache(self):
        """从本地CSV加载全部分红缓存 + 预计算年度DPS（大幅加速后续查询）"""
        if not _DIV_DIR.exists():
            print(f"  ⚠️ 分红数据目录不存在: {_DIV_DIR}")
            return

        proc_priority = {"实施": 0, "股东大会通过": 1, "预案": 2}
        div_files = list(_DIV_DIR.glob("*.csv"))
        loaded = 0
        for f in div_files:
            try:
                df = pd.read_csv(f, dtype={
                    "ts_code": str, "end_date": str, "ann_date": str,
                    "div_proc": str, "ex_date": str, "pay_date": str
                })
                if df.empty or "cash_div_tax" not in df.columns:
                    continue
                df["cash_div_tax"] = pd.to_numeric(df["cash_div_tax"], errors="coerce").fillna(0)
                ts_code = f.stem

                # ── 预计算年度 DPS ──
                df["end_year"] = df["end_date"].astype(str).str[:4].astype(int)
                # 去重：同 end_date 优先保留实施/股东大会通过/预案
                df["proc_rank"] = df["div_proc"].map(
                    lambda x: proc_priority.get(str(x), 3)
                )
                dedup = (
                    df.sort_values("proc_rank")
                    .drop_duplicates(subset=["end_date"], keep="first")
                )
                year_dps = (
                    dedup.groupby("end_year")["cash_div_tax"].sum()
                    .to_dict()
                )
                # 只保留 >0 的年份
                self._dps_by_year[ts_code] = {
                    yr: dps for yr, dps in year_dps.items() if dps > 0
                }
                loaded += 1
            except Exception:
                pass

        print(f"  📂 分红缓存: {loaded}只有效数据 (预计算年度DPS)")

    def _load_income_cache(self):
        """从本地CSV加载净利润数据"""
        inc_dfs = []
        has_profit_attr = False  # 是否有 n_income_attr_p 字段
        has_eps = False          # 是否有 basic_eps 字段

        for year in range(2012, 2027):
            p = _FCF_DIR / f"income_{year}.csv"
            if p.exists():
                try:
                    df = pd.read_csv(p, dtype={
                        "ts_code": str, "ann_date": str,
                        "f_ann_date": str, "end_date": str
                    })
                    # 检查关键字段
                    if "n_income_attr_p" in df.columns:
                        df["n_income_attr_p"] = pd.to_numeric(df["n_income_attr_p"], errors="coerce")
                        has_profit_attr = True
                    elif "n_income" in df.columns:
                        df["n_income_attr_p"] = pd.to_numeric(df["n_income"], errors="coerce")
                        has_profit_attr = True

                    if "basic_eps" in df.columns:
                        df["basic_eps"] = pd.to_numeric(df["basic_eps"], errors="coerce")
                        has_eps = True

                    inc_dfs.append(df)
                except Exception:
                    pass

        if inc_dfs:
            self._income_df = pd.concat(inc_dfs, ignore_index=True)
            if not has_profit_attr:
                self._income_df["n_income_attr_p"] = np.nan
            if not has_eps:
                self._income_df["basic_eps"] = np.nan
            self._income_loaded = has_profit_attr or has_eps  # 至少有一个字段才算加载成功
            if not self._income_loaded:
                print(f"  ⚠️ Income数据缺少n_income_attr_p和basic_eps，"
                      f"股利支付率检查将跳过（保守通过）")
            else:
                # ★ 预构建 income O(1) 索引（避免每期逐只 pandas filter，这是性能瓶颈核心）
                self._build_income_index()
        else:
            self._income_df = pd.DataFrame()
            self._income_loaded = False

    def _build_income_index(self):
        """将 income DataFrame 预索引为 {ts_code: {year: {field: value}}}，查询 O(1)"""
        df = self._income_df.copy()
        df['end_year'] = df['end_date'].astype(str).str[:4].astype(int)
        # 按 ann_date 降序，groupby first 取每个 (ts_code, year) 的最新报告
        df = df.sort_values('ann_date', ascending=False)
        index: Dict[str, Dict[int, Dict[str, float]]] = {}
        for (ts_code, yr), grp in df.groupby(['ts_code', 'end_year']):
            r = grp.iloc[0]
            entry = {}
            for k in ['n_income_attr_p', 'basic_eps']:
                v = r.get(k)
                if pd.notna(v):
                    entry[k] = float(v)
            if entry:
                index.setdefault(str(ts_code), {})[int(yr)] = entry
        self._income_index = index
        print(f"  📂 Income索引: {len(index)}只, {sum(len(v) for v in index.values())}条年度记录")

    # ─── Stock Price Fetching ───────────────────────────────

    _trading_days: Optional[set] = None  # 类级交易日缓存，所有实例共享

    @classmethod
    def _load_trading_calendar(cls):
        """一次性加载交易日历（优先读本地缓存，首次从 Tushare 下载）"""
        if cls._trading_days is not None:
            return

        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _DATA_DIR / "trade_cal.csv"

        if cache_file.exists():
            cal = pd.read_csv(cache_file, dtype={"cal_date": str})
            cls._trading_days = set(
                cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist()
            )
            return

        try:
            pro = _get_tushare_api()
            cal = pro.trade_cal(exchange="SSE", start_date="20100101", end_date="20261231")
            cal.to_csv(cache_file, index=False)
            cls._trading_days = set(
                cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist()
            )
        except Exception:
            cls._trading_days = set()

    def _nearest_trading_day(self, date_str: str) -> str:
        """找到 date_str 或之前最近的交易日"""
        d = date_str.replace("-", "")
        self._load_trading_calendar()
        from datetime import date as dt_date, timedelta
        base = dt_date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        for delta in range(7):
            d = (base - timedelta(days=delta)).strftime("%Y%m%d")
            if d in self._trading_days:
                return d
        return date_str.replace("-", "")

    def _prices_for_date(self, date_str: str) -> Dict[str, float]:
        """
        获取某日收盘价：优先读本地缓存文件，未命中时从 Tushare 下载一次。

        之后所有日期都从本地 CSV 文件读取，零 API 调用。
        """
        cache_key = f"BATCH|{date_str}"
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        _PRICE_DIR.mkdir(parents=True, exist_ok=True)
        trade_d = self._nearest_trading_day(date_str)
        cache_file = _PRICE_DIR / f"{trade_d}.csv"

        # ── 优先读本地文件 ──
        if cache_file.exists():
            df = pd.read_csv(cache_file, dtype={"ts_code": str})
            result = dict(zip(df["ts_code"], df["close"]))
            self._price_cache[cache_key] = result
            return result

        # ── 首次下载 → 存盘 ──
        pro = _get_tushare_api()
        result = {}
        try:
            df = pro.daily_basic(trade_date=trade_d, fields="ts_code,trade_date,close")
            if df is not None and not df.empty:
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df[df["close"] > 0][["ts_code", "close"]]
                # 存本地 CSV
                df.to_csv(cache_file, index=False)
                result = dict(zip(df["ts_code"].astype(str), df["close"]))
        except Exception:
            pass

        self._price_cache[cache_key] = result
        return result

    def _get_stock_price(self, ts_code: str, date_str: str) -> Optional[float]:
        """获取指定日期的收盘价（从批量缓存中查找）"""
        prices = self._prices_for_date(date_str)
        return prices.get(ts_code)

    def _get_stock_info(self, ts_code: str) -> Optional[Dict]:
        """获取股票基本信息（预构建 dict → O(1)）"""
        return self._stock_info_map.get(ts_code)

    # ─── Core Selection Logic ───────────────────────────────

    def _get_annual_dividends(
        self, ts_code: str, year: int
    ) -> Optional[float]:
        """获取某年每股税前分红总额（预计算 dict → O(1)）"""
        year_map = self._dps_by_year.get(ts_code)
        if year_map is None:
            return None
        dps = year_map.get(year, 0.0)
        return dps if dps > 0 else 0.0

    def _check_consecutive_dividends(
        self, ts_code: str, date_str: str, years: int = 3
    ) -> bool:
        """
        检查是否过去years年连续现金分红。

        对于调仓日date_str，取最近years个完整会计年度（date_str年份的前一年开始），
        检查每年是否都有现金分红（cash_div_tax > 0）。

        例：date_str=2020-06-15 → 检查2017, 2018, 2019
        """
        rebalance_year = int(date_str[:4])
        # 最近完整会计年度 = rebalance_year - 1（年报通常在次年4月前公告）
        latest_year = rebalance_year - 1
        check_years = list(range(latest_year - years + 1, latest_year + 1))

        for yr in check_years:
            dps = self._get_annual_dividends(ts_code, yr)
            if dps is None or dps <= 0:
                return False
        return True

    def _get_annual_net_profit(
        self, ts_code: str, year: int
    ) -> Optional[float]:
        """
        获取某只股票某会计年度的归母净利润（万元）。

        O(1) dict 查询（预索引），不复用 pandas filter。
        """
        if not self._income_loaded:
            return None
        stock_data = self._income_index.get(ts_code)
        if stock_data is None:
            return None
        year_data = stock_data.get(year)
        if year_data is None:
            return None
        return year_data.get('n_income_attr_p')

    def _calc_dividend_payout_ratio(
        self, ts_code: str, year: int
    ) -> Optional[float]:
        """
        计算某会计年度的股利支付率。

        支付率 = 年度现金分红总额 / 归母净利润

        注意：dividend表中的cash_div_tax是"每股"税前分红，
        需要乘以总股本得到总额，或者用income表中的净利润直接做分母。
        实际公式：支付率 = (DPS × 总股本) / 归母净利润

        简化处理：以每股分红与每股收益的比值近似。
        或直接用：年度总分红(estimated) / 归母净利润。

        由于dividend表只给出每股数据，且我们需要的是比率而非绝对金额，
        我们使用以下方法：
        - 支付率 ≈ 每股分红 / 每股收益（basic_eps）
        - 若basic_eps不可用，则用 DPS × 总股本 / 归母净利润
        """

    def _check_payout_ratio(
        self, ts_code: str, date_str: str
    ) -> bool:
        """
        检查股利支付率条件：
        1）过去三年股利支付率均值 > 0 且 < 1
        2）过去一年股利支付率 > 0 且 < 1

        Returns True if passed, False otherwise.
        """
        if not self._income_loaded or self._income_df is None or self._income_df.empty:
            # 无收入数据时：保守通过（避免因数据缺失过滤掉过多标的）
            return True

        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1

        ratios = []
        for yr in range(latest_year - 2, latest_year + 1):
            profit = self._get_annual_net_profit(ts_code, yr)
            if profit is None or profit <= 0:
                # 净利润不可用或为负 → 该年不参与均值计算
                # 但如果三年都不行，则无法判断
                continue

            # 获取该年每股分红
            # 需要计算总分红金额 = DPS × 总股本
            # 但总股本信息不在dividend或income表中...
            # 简化：用 basic_eps 来计算支付率 = DPS / EPS
            # basic_eps在income表中
            eps = self._get_basic_eps(ts_code, yr)
            dps = self._get_annual_dividends(ts_code, yr)

            if eps is not None and eps > 0 and dps is not None and dps > 0:
                ratio = dps / eps
                if 0 < ratio < 1:
                    ratios.append(ratio)

        if len(ratios) < 1:
            # 数据不足，保守通过
            return True

        # 检查三年均值
        avg_ratio = np.mean(ratios)
        if not (0 < avg_ratio < 1):
            return False

        # 检查最近一年
        if len(ratios) >= 1:
            latest_ratio = ratios[-1]
            if not (0 < latest_ratio < 1):
                return False

        return True

    def _get_basic_eps(
        self, ts_code: str, year: int
    ) -> Optional[float]:
        """获取某年度的基本每股收益。O(1) dict 查询（预索引）"""
        if not self._income_loaded:
            return None
        stock_data = self._income_index.get(ts_code)
        if stock_data is None:
            return None
        year_data = stock_data.get(year)
        if year_data is None:
            return None
        return year_data.get('basic_eps')

    def _calc_avg_dividend_yield(
        self, ts_code: str, date_str: str, years: int = 3
    ) -> Optional[float]:
        """
        计算过去years年 TTM 平均现金股息率。

        官方公式（中证指数）：最近三个完整会计年度，每年独立计算股息率，再平均。

            股息率_i = DPS_i / 年末股价_i （i = 最近第i个完整会计年度）
            TTM3年平均股息率 = (股息率_1 + 股息率_2 + 股息率_3) / 3

        ⚠️ 之前的错误公式：SUM(DPS) / years / 当前股价
        在股价上涨的年份会系统性地低估真实股息率（2015年牛市中压低到0.5-2%）。
        """
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        check_years = list(range(latest_year - years + 1, latest_year + 1))

        annual_yields = []
        for yr in check_years:
            dps = self._get_annual_dividends(ts_code, yr)
            if dps is None or dps <= 0:
                continue

            # 用该年年末股价作为分母（优先12-31，其次用该年最后可用交易日）
            price_yr = self._get_stock_price(ts_code, f"{yr}-12-31")
            if price_yr is None or price_yr <= 0:
                # 退而求其次：用调仓日前一年对应日期的价格
                price_yr = self._get_stock_price(ts_code, f"{yr}-06-30")

            if price_yr is None or price_yr <= 0:
                continue

            annual_yields.append(dps / price_yr * 100)

        if len(annual_yields) == 0:
            return None

        return sum(annual_yields) / len(annual_yields)

    # ─── Main Basket Generation ─────────────────────────────

    def get_dividend_basket(
        self,
        date_str: str,
        top_n: int = 100,
        prev_basket_codes: Optional[Set[str]] = None,
        max_turnover: float = 0.20,
        verbose: bool = True,
    ) -> Dict[str, Dict]:
        """
        执行800红利指数选样逻辑。

        Parameters
        ----------
        date_str : 调仓日 YYYY-MM-DD
        top_n : 选取Top N只样本（默认100）
        prev_basket_codes : 上期持仓代码集合（用于换手率限制）
        max_turnover : 最大换手率（默认20%）
        verbose : 是否打印进度

        Returns
        -------
        Dict[ts_code, Dict] : 持仓篮子，每只包含 name/div_yield_3y/weight/...
        """
        # Step 1: 获取当期成分股
        constituents = self._idx_cache.get_constituents(date_str)
        if verbose:
            print(f"  [{date_str}] CSI800成分: {len(constituents)}只")

        if not constituents:
            return {}

        # Step 2: 筛选 — 连续三年分红 + 股利支付率检查
        eligible = []
        n_no_div = 0
        n_no_payout = 0
        n_no_price = 0
        n_no_data = 0

        for ts_code in constituents:
            # 检查数据可用性（使用预计算 dps_by_year）
            if ts_code not in self._dps_by_year:
                n_no_data += 1
                continue

            # 连续三年分红检查
            if not self._check_consecutive_dividends(ts_code, date_str):
                n_no_div += 1
                continue

            # 股利支付率检查
            if not self._check_payout_ratio(ts_code, date_str):
                n_no_payout += 1
                continue

            # 计算三年平均股息率
            div_yield = self._calc_avg_dividend_yield(ts_code, date_str)
            if div_yield is None or div_yield <= 0:
                n_no_price += 1
                continue

            info = self._get_stock_info(ts_code)
            eligible.append({
                "ts_code": ts_code,
                "name": info.get("name", ts_code) if info else ts_code,
                "industry": info.get("industry", "") if info else "",
                "div_yield_3y": round(div_yield, 4),
            })

        if verbose:
            print(f"    连续分红过滤: 剔除{n_no_div}只")
            print(f"    股利支付率过滤: 剔除{n_no_payout}只")
            print(f"    价格/股息率不可用: 剔除{n_no_price}只")
            print(f"    无分红数据: 剔除{n_no_data}只")
            print(f"    合格样本: {len(eligible)}只")

        if not eligible:
            return {}

        # Step 3: 按三年平均股息率降序排名
        eligible.sort(key=lambda x: x["div_yield_3y"], reverse=True)

        # Step 4: Top N 选股（含换手率限制）
        selected = eligible[:top_n]

        if prev_basket_codes and len(prev_basket_codes) > 0:
            max_keep = int(top_n * (1 - max_turnover))  # 至少保留80只
            max_new  = top_n - max_keep                  # 最多新进20只

            # 在新Top N中找到上期持仓
            prev_in_new = [s for s in selected if s["ts_code"] in prev_basket_codes]
            new_in_new  = [s for s in selected if s["ts_code"] not in prev_basket_codes]

            # 上期持仓在TopN内的全保留
            keep = list(prev_in_new[:max_keep])
            keep_codes = {s["ts_code"] for s in keep}

            # ★ 上期持仓跌出TopN的，按股息率召回补足保留数
            #    (这些不算"新进"，是上期持仓的延续)
            if len(keep) < max_keep:
                fallen = [s for s in eligible
                          if s["ts_code"] in prev_basket_codes
                          and s["ts_code"] not in keep_codes]
                recall = fallen[:max_keep - len(keep)]
                keep.extend(recall)
                keep_codes.update(s["ts_code"] for s in recall)

            # 新进严格限制 ≤ max_new
            fill = [s for s in new_in_new if s["ts_code"] not in keep_codes][:max_new]
            fill_codes = {s["ts_code"] for s in fill}

            # 兜底补足到top_n
            shortfall = top_n - len(keep) - len(fill)
            if shortfall > 0:
                beyond = [s for s in eligible
                          if s["ts_code"] not in keep_codes
                          and s["ts_code"] not in fill_codes]
                fill.extend(beyond[:shortfall])

            selected = keep + fill
            if verbose:
                actual_new = len(fill)
                actual_turnover = actual_new / len(selected) * 100 if selected else 0
                recalled = len(keep) - len(prev_in_new[:max_keep])
                extra = f', 召回跌出TopN的{recalled}只' if recalled > 0 else ''
                print(f"    换手限制: 保留{len(keep)}只 + 新进{actual_new}只 = {len(selected)}只 "
                      f"(换手{actual_turnover:.1f}%{extra})")

        # Step 5: 股息率加权 + 10%封顶
        selected = self._apply_dividend_weighting(selected, cap=0.10)

        # Step 6: 转为返回格式
        result = {}
        for s in selected:
            result[s["ts_code"]] = {
                "ts_code": s["ts_code"],
                "name": s["name"],
                "industry": s.get("industry", ""),
                "div_yield_3y": s["div_yield_3y"],
                "weight": s["weight"],
            }

        if verbose:
            total_w = sum(s["weight"] for s in selected)
            avg_yield = np.mean([s["div_yield_3y"] for s in selected])
            print(f"    最终持仓: {len(selected)}只, 权重合计={total_w:.4f}, "
                  f"平均股息率={avg_yield:.2f}%")

        return result

    def _apply_dividend_weighting(
        self,
        stocks: List[Dict],
        cap: float = 0.10,
        max_iter: int = 100,
    ) -> List[Dict]:
        """
        股息率加权 + 单股10%封顶迭代重分配。

        与FCF策略的fcf_weights逻辑一致，但用股息率替代FCF。
        """
        if not stocks:
            return stocks

        yields = [max(s.get("div_yield_3y", 0), 0) for s in stocks]
        total = sum(yields)

        if total <= 0:
            w = 1.0 / len(stocks)
            for s in stocks:
                s["weight"] = round(w, 6)
            return stocks

        weights = [v / total for v in yields]

        for _ in range(max_iter):
            overflow = sum(w - cap for w in weights if w > cap)
            if overflow < 1e-9:
                break
            capped = [min(w, cap) for w in weights]
            below_cap = [c for c in capped if c < cap]
            below_sum = sum(below_cap)
            if below_sum <= 0:
                break
            weights = [
                min(c + overflow * (c / below_sum), cap) if c < cap else cap
                for c in capped
            ]

        tw = sum(weights)
        for s, w in zip(stocks, weights):
            s["weight"] = round(w / tw, 6)

        return stocks


# ════════════════════════════════════════════════════════════════
# 测试入口
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  中证800红利指数（931644）选股引擎 — 测试")
    print("=" * 60)

    uni = DividendUniverse(index_code="000906.SH")
    uni.preload_all()

    # 测试几个调仓日
    test_dates = ["2018-06-11", "2020-12-14", "2025-06-16"]
    for d in test_dates:
        print(f"\n{'─'*50}")
        basket = uni.get_dividend_basket(d, top_n=100, verbose=True)
        if basket:
            stocks = sorted(basket.values(), key=lambda x: x["weight"], reverse=True)[:5]
            for s in stocks:
                print(f"  {s['ts_code']} {s['name']}: "
                      f"div_yield={s['div_yield_3y']:.2f}%, weight={s['weight']*100:.2f}%")
