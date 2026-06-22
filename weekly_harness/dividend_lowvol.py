"""
中证红利低波动100指数（930955）选股引擎
==========================================

基于中证指数公司编制方案实现：
  样本空间：中证800（000906.SH，用户指定）
  选股逻辑：
    1. 过去三年连续现金分红，每年股息率>0
    2. 股息率 = (过去三个会计年度分红总额/3) / 调整日总市值
    3. 第一轮：股息率降序 → Top 300
    4. 第二轮：波动率升序 → Top 100
  加权方式：股息率/波动率加权，单股≤10%，中证二级行业≤20%（申万代理）
  调仓频率：季度（3/6/9/12月第二个星期五的下一交易日）

🔴 核心约束：两阶段模式
  Phase 1 (preload): 批量下载所有数据到磁盘（可调API）
  Phase 2 (select_basket): 纯读本地缓存，零API调用

复用：
  - DividendUniverse 的数据层（分红加载、DPS预计算、价格获取）
  - IndexWeightCache 的成分股权重
  - get_adj_close_cached 的复权价格缓存（NAV计算）
"""

from __future__ import annotations

import os
import sys
import time
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_FCF_DIR = _DATA_DIR / "fcf_financials"
_DIV_DIR = _DATA_DIR / "dividend_history"
_IDX_DIR = _DATA_DIR / "index_weights"
_PRICE_DIR = _DATA_DIR / "price_snapshots"
_VOL_CACHE_DIR = _DATA_DIR / "volatility_cache"
_STOCK_BASIC_FILE = _DATA_DIR / "stock_basic.csv"


def _get_tushare_api():
    """延迟初始化 Tushare API"""
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_PROJECT_ROOT))
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
    import tushare as ts
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


# ============================================================================
# IndexWeightCache（精简版，从 dividend_universe.py 复用逻辑）
# ============================================================================

class IndexWeightCache:
    """指数成分股权重缓存"""

    def __init__(self, index_code: str = "000906.SH"):
        self.index_code = index_code
        self._weights: Optional[pd.DataFrame] = None

    def load(self):
        cached = _IDX_DIR / f"index_weight_{self.index_code}.csv"
        if cached.exists():
            self._weights = pd.read_csv(cached, dtype={"con_code": str, "trade_date": str})
            print(f"  📂 成分股权重缓存: {self.index_code} {len(self._weights)}条")
            return
        self.download()

    def download(self):
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
        """获取date_str时的最新成分股列表"""
        if self._weights is None or self._weights.empty:
            return []
        d = date_str.replace("-", "")
        dates = sorted(self._weights["trade_date"].unique())
        rebalance_month = d[4:6]
        rebalance_year = d[:4]

        if rebalance_month in ('06', '12'):
            month_prefix = f"{rebalance_year}{rebalance_month}"
            same_month = [x for x in dates if x[:6] == month_prefix]
            snap = same_month[-1] if same_month else dates[-1]
        else:
            prior = [x for x in dates if x <= d]
            snap = prior[-1] if prior else dates[0]
        return self._weights[self._weights["trade_date"] == snap]["con_code"].tolist()


# ============================================================================
# 交易日历（类级缓存，所有实例共享）
# ============================================================================

_trading_days_cache: Optional[set] = None


def _load_trading_calendar() -> set:
    global _trading_days_cache
    if _trading_days_cache is not None:
        return _trading_days_cache

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _DATA_DIR / "trade_cal.csv"

    if cache_file.exists():
        cal = pd.read_csv(cache_file, dtype={"cal_date": str})
        _trading_days_cache = set(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
        return _trading_days_cache

    try:
        pro = _get_tushare_api()
        cal = pro.trade_cal(exchange="SSE", start_date="20100101", end_date="20261231")
        cal.to_csv(cache_file, index=False)
        _trading_days_cache = set(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    except Exception:
        _trading_days_cache = set()
    return _trading_days_cache


def _nearest_trading_day(date_str: str) -> str:
    """找到 date_str 或之前最近的交易日"""
    d = date_str.replace("-", "")
    tdays = _load_trading_calendar()
    from datetime import date as dt_date
    base = dt_date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    for delta in range(7):
        td = (base - timedelta(days=delta)).strftime("%Y%m%d")
        if td in tdays:
            return td
    return d


# ============================================================================
# DividendLowvolEngine — 核心选股引擎
# ============================================================================

class DividendLowvolEngine:
    """
    中证红利低波动100指数（930955）选股引擎

    两阶段模式：
      Phase 1 — preload(): 批量下载所有数据（可调API）
      Phase 2 — select_basket(): 纯读盘选股（零API）

    用法:
        engine = DividendLowvolEngine()
        engine.preload(download=True, rebalance_dates=[...])  # Phase 1
        basket = engine.select_basket("2020-03-16")            # Phase 2
    """

    VOL_LOOKBACK_DAYS = 400         # 波动率回溯自然日
    DEFAULT_VOL = 30.0              # 默认波动率（数据缺失时兜底）
    MIN_TRADING_DAYS = 60           # 波动率最小交易日数

    def __init__(self, index_code: str = "000906.SH"):
        self._idx_cache = IndexWeightCache(index_code)
        self._stock_basic: Optional[pd.DataFrame] = None
        self._stock_info_map: Dict[str, Dict] = {}

        # 分红数据（复用 dividend_universe 的数据结构）
        self._dps_by_year: Dict[str, Dict[int, float]] = {}  # {ts_code: {year: dps}}

        # 价格缓存: {"BATCH|YYYY-MM-DD": {ts_code: close}}
        self._price_cache: Dict[str, Dict[str, float]] = {}

        # 波动率磁盘缓存: {date_str_yyyymmdd: {ts_code: ann_vol}}
        self._vol_cache: Dict[str, Dict[str, Optional[float]]] = {}

        self._preloaded = False
        self._all_rebalance_dates: List[str] = []

    # ————————————————————————————————————————————————
    # Phase 1: 预下载所有数据
    # ————————————————————————————————————————————————

    def preload(self, download: bool = False, rebalance_dates: List[str] = None):
        """
        Phase 1 — 批量预下载所有数据到本地磁盘。

        Parameters
        ----------
        download : 是否触发 Tushare 下载（首次需 True）
        rebalance_dates : 所有调仓日列表（用于批量下载价格和波动率）
        """
        if self._preloaded:
            return

        self._all_rebalance_dates = rebalance_dates or []
        t0 = time.time()
        print("=" * 60)
        print("DividendLowvolEngine: Phase 1 — 预下载数据")
        print("=" * 60)

        # 1) 成分股权重
        print("\n[1/5] 加载 CSI 800 成分股权重...")
        if download:
            self._idx_cache.download()
        else:
            self._idx_cache.load()

        # 2) 股票基本信息
        print("\n[2/5] 加载 stock_basic...")
        self._load_stock_basic()

        # 3) 分红数据
        print("\n[3/5] 加载分红数据...")
        self._load_dividend_cache()

        # 4) 价格快照
        print("\n[4/5] 预加载价格快照...")
        if self._all_rebalance_dates:
            self._preload_prices(download=download)

        # 5) 波动率批量预计算 → 磁盘缓存
        print("\n[5/5] 预计算波动率 → 磁盘缓存...")
        if self._all_rebalance_dates:
            self._preload_volatility(download=download)
        else:
            print("  ⚠️ 无调仓日列表，跳过波动率预计算")

        self._preloaded = True
        elapsed = time.time() - t0
        print(f"\n✅ Phase 1 完成 ({elapsed/60:.1f}min)")

        # —— Phase 1 数据质量自检 ——
        self._run_data_quality_checks()

    def _load_stock_basic(self):
        """加载股票基本信息 → O(1)索引"""
        if _STOCK_BASIC_FILE.exists():
            self._stock_basic = pd.read_csv(_STOCK_BASIC_FILE, dtype={
                "ts_code": str, "name": str, "industry": str,
                "list_date": str, "list_status": str
            })
        else:
            self._stock_basic = pd.DataFrame()

        self._stock_info_map = {}
        for _, r in self._stock_basic.iterrows():
            self._stock_info_map[str(r["ts_code"])] = {
                "name": str(r.get("name", "")),
                "industry": str(r.get("industry", "")),
                "list_date": str(r.get("list_date", "")),
                "list_status": str(r.get("list_status", "")),
            }
        print(f"  📂 stock_basic: {len(self._stock_info_map)}只")

    def _load_dividend_cache(self):
        """从本地CSV加载分红数据 + 预计算年度DPS"""
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

                df["end_year"] = df["end_date"].astype(str).str[:4].astype(int)
                df["proc_rank"] = df["div_proc"].map(lambda x: proc_priority.get(str(x), 3))
                dedup = df.sort_values("proc_rank").drop_duplicates(subset=["end_date"], keep="first")
                year_dps = dedup.groupby("end_year")["cash_div_tax"].sum().to_dict()
                self._dps_by_year[ts_code] = {yr: dps for yr, dps in year_dps.items() if dps > 0}
                loaded += 1
            except Exception:
                pass
        print(f"  📂 分红缓存: {loaded}只有效数据")

    def _preload_prices(self, download: bool = False):
        """批量预加载价格到磁盘（复用 dividend_universe 模式）"""
        needed_dates = set()
        for d in self._all_rebalance_dates:
            needed_dates.add(d)
            latest_year = int(d[:4]) - 1
            for yr in range(latest_year - 2, latest_year + 1):
                needed_dates.add(f"{yr}-12-31")
                needed_dates.add(f"{yr}-06-30")

        _PRICE_DIR.mkdir(parents=True, exist_ok=True)

        # 统计缓存命中
        to_download_dates = []
        for d in sorted(needed_dates):
            td = _nearest_trading_day(d)
            if not (_PRICE_DIR / f"{td}.csv").exists():
                to_download_dates.append(d)

        if not to_download_dates:
            print(f"  📂 价格: 全部命中本地缓存 ({len(needed_dates)}个日期)")
        elif download:
            print(f"  📥 下载价格: {len(to_download_dates)}个新日期...", end=" ", flush=True)
            pro = _get_tushare_api()
            for d in to_download_dates:
                self._fetch_and_cache_prices(d, pro)
                time.sleep(0.10)
            print("完成")
        else:
            print(f"  ⚠️ {len(to_download_dates)}个日期缺失（跳过下载，选股时可能缺价）")

        # 全部装入内存
        for d in sorted(needed_dates):
            self._load_prices_from_cache(d)

    def _fetch_and_cache_prices(self, date_str: str, pro):
        """拉取单日价格并存入磁盘"""
        trade_d = _nearest_trading_day(date_str)
        cache_file = _PRICE_DIR / f"{trade_d}.csv"
        if cache_file.exists():
            return

        try:
            df = pro.daily_basic(trade_date=trade_d, fields="ts_code,trade_date,close")
            if df is not None and not df.empty:
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df[df["close"] > 0][["ts_code", "close"]]
                df.to_csv(cache_file, index=False)
                print(f".", end="", flush=True)
        except Exception:
            pass

    def _load_prices_from_cache(self, date_str: str) -> Dict[str, float]:
        """从磁盘缓存加载某日价格"""
        cache_key = f"BATCH|{date_str}"
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        trade_d = _nearest_trading_day(date_str)
        cache_file = _PRICE_DIR / f"{trade_d}.csv"
        if cache_file.exists():
            df = pd.read_csv(cache_file, dtype={"ts_code": str})
            result = dict(zip(df["ts_code"], df["close"]))
            self._price_cache[cache_key] = result
            return result
        return {}

    # ——— 全市场 pct_chg 矩阵（从 adj_close_cache 构建，零 API）———
    _PCT_CHG_PARQUET = _DATA_DIR / "daily_pct_chg.parquet"
    _PCT_CHG_MATRIX: Optional[pd.DataFrame] = None  # index=trade_date, columns=ts_code

    @classmethod
    def _build_pct_chg_matrix(cls) -> pd.DataFrame:
        """从 adj_close_cache 一次性构建全市场 pct_chg 矩阵 → parquet"""
        if cls._PCT_CHG_MATRIX is not None:
            return cls._PCT_CHG_MATRIX

        if cls._PCT_CHG_PARQUET.exists():
            print(f"    📂 加载 pct_chg 矩阵: {cls._PCT_CHG_PARQUET}")
            cls._PCT_CHG_MATRIX = pd.read_parquet(cls._PCT_CHG_PARQUET)
            return cls._PCT_CHG_MATRIX

        adj_dir = _DATA_DIR / "adj_close_cache"
        if not adj_dir.exists():
            print(f"    ⚠️ adj_close_cache 目录不存在，无法构建 pct_chg 矩阵")
            cls._PCT_CHG_MATRIX = pd.DataFrame()
            return cls._PCT_CHG_MATRIX

        print(f"    🔨 从 adj_close_cache 构建全市场 pct_chg 矩阵...")
        t0 = time.time()
        cache_files = sorted(adj_dir.glob("*.csv"))
        all_series = []

        for i, f in enumerate(cache_files):
            try:
                df = pd.read_csv(f, usecols=["trade_date", "adj_close"],
                                 dtype={"trade_date": str, "adj_close": float})
                df = df.dropna(subset=["adj_close"])
                df = df[df["adj_close"] > 0].sort_values("trade_date")
                if len(df) < 60:
                    continue
                df["pct_chg"] = df["adj_close"].pct_change()
                s = df.set_index("trade_date")["pct_chg"].dropna()
                s.name = f.stem
                all_series.append(s)
            except Exception:
                pass

            if (i + 1) % 500 == 0:
                print(f"      {i+1}/{len(cache_files)}...", flush=True)

        print(f"      构建 DataFrame ({len(all_series)}只)...", end=" ", flush=True)
        matrix = pd.concat(all_series, axis=1).sort_index()
        matrix.index.name = "trade_date"

        # 存为 parquet（压缩率高，读盘快）
        matrix.to_parquet(cls._PCT_CHG_PARQUET, compression="zstd")
        elapsed = time.time() - t0
        print(f"完成 ({elapsed:.1f}s, {matrix.shape[1]}只 × {matrix.shape[0]}天)")
        cls._PCT_CHG_MATRIX = matrix
        return matrix

    def _preload_volatility(self, download: bool = False):
        """
        ★ 批量预计算波动率 → 磁盘缓存

        优化方案：从 adj_close_cache 构建全市场 pct_chg 矩阵（parquet）
        → 对每个调仓日，从矩阵中过滤成分股 + 日期窗口 → 本地计算波动率
        → 存入 data/volatility_cache/vol_{YYYYMMDD}.csv
        → 零 API 调用！
        """
        _VOL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # 统计已有缓存
        missing_dates = []
        for d in self._all_rebalance_dates:
            end_str = d.replace("-", "")
            if not (_VOL_CACHE_DIR / f"vol_{end_str}.csv").exists():
                missing_dates.append(d)

        if not missing_dates:
            print(f"  📂 波动率缓存: 全部{len(self._all_rebalance_dates)}期已缓存")
            for d in self._all_rebalance_dates:
                self._load_vol_from_disk(d)
            return

        if not download:
            print(f"  ⚠️ {len(missing_dates)}期波动率缓存缺失（跳过下载）")
            return

        # 构建/加载全市场 pct_chg 矩阵（一次，零 API）
        matrix = self._build_pct_chg_matrix()
        if matrix.empty:
            print(f"  ❌ pct_chg 矩阵为空，无法计算波动率")
            return

        print(f"  📊 从 pct_chg 矩阵计算 {len(missing_dates)} 期波动率...")
        all_codes_in_matrix = set(matrix.columns)
        t0 = time.time()

        for i, d in enumerate(missing_dates):
            end_str = d.replace("-", "")
            cache_file = _VOL_CACHE_DIR / f"vol_{end_str}.csv"

            constituents = self._idx_cache.get_constituents(d)
            if not constituents:
                pd.DataFrame(columns=["ts_code", "ann_vol"]).to_csv(cache_file, index=False)
                continue

            # 过滤：只保留在矩阵中有数据的成分股
            target_codes = [c for c in constituents if c in all_codes_in_matrix]
            if not target_codes:
                pd.DataFrame(columns=["ts_code", "ann_vol"]).to_csv(cache_file, index=False)
                continue

            # 日期窗口
            end_dt = datetime.strptime(d[:10], "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=self.VOL_LOOKBACK_DAYS)
            start_key = start_dt.strftime("%Y%m%d")
            end_key = end_dt.strftime("%Y%m%d")

            # 切片矩阵
            mask_dates = (matrix.index >= start_key) & (matrix.index <= end_key)
            window = matrix.loc[mask_dates, target_codes]

            # 批量计算波动率
            rows = []
            for code in target_codes:
                pct = window[code].dropna()
                if len(pct) < self.MIN_TRADING_DAYS:
                    rows.append({"ts_code": code, "ann_vol": np.nan})
                else:
                    ann_vol = float(pct.std() * math.sqrt(244) * 100)
                    rows.append({"ts_code": code, "ann_vol": round(ann_vol, 4)})

            # 补齐未在矩阵中的成分股
            for code in constituents:
                if code not in all_codes_in_matrix:
                    rows.append({"ts_code": code, "ann_vol": np.nan})

            df_out = pd.DataFrame(rows)
            df_out.to_csv(cache_file, index=False)

            self._vol_cache[end_str] = {
                r["ts_code"]: r["ann_vol"] if pd.notna(r["ann_vol"]) else None
                for _, r in df_out.iterrows()
            }

            if (i + 1) % 10 == 0 or i == len(missing_dates) - 1:
                elapsed = time.time() - t0
                eta = elapsed / (i + 1) * (len(missing_dates) - i - 1)
                print(f"    [{i+1}/{len(missing_dates)}] "
                      f"有效{df_out['ann_vol'].notna().sum()}/{len(df_out)}只 "
                      f"| {elapsed:.0f}s | eta {eta:.0f}s")

        elapsed = time.time() - t0
        print(f"  ✅ 波动率预计算完成 ({elapsed:.1f}s)")

        # 装入已有缓存
        for d in self._all_rebalance_dates:
            if d not in missing_dates:
                self._load_vol_from_disk(d)

    def _load_vol_from_disk(self, date_str: str) -> Dict[str, Optional[float]]:
        """从磁盘缓存读取波动率（零API）"""
        end_str = date_str.replace("-", "")
        if end_str in self._vol_cache:
            return self._vol_cache[end_str]

        cache_file = _VOL_CACHE_DIR / f"vol_{end_str}.csv"
        if not cache_file.exists():
            self._vol_cache[end_str] = {}
            return {}

        df = pd.read_csv(cache_file, dtype={"ts_code": str})
        result = {}
        for _, row in df.iterrows():
            v = row.get("ann_vol")
            result[str(row["ts_code"])] = float(v) if pd.notna(v) else None
        self._vol_cache[end_str] = result
        return result

    # ————————————————————————————————————————————————
    # Phase 2: 纯读盘选股（零API）
    # ————————————————————————————————————————————————

    def select_basket(
        self, date_str: str, verbose: bool = True
    ) -> Dict[str, Dict]:
        """
        Phase 2 — 纯本地读盘选股，零API调用。

        Returns
        -------
        Dict[ts_code, Dict]: {ts_code: {name, industry, div_yield_3y, ann_vol, weight, ...}}
        """
        if verbose:
            print(f"\n{'─'*50}")
            print(f"  [{date_str}] 红利低波选股")
            print(f"{'─'*50}")

        # Step 1: 获取 CSI 800 成分股
        constituents = self._idx_cache.get_constituents(date_str)
        if verbose:
            print(f"  CSI800成分股: {len(constituents)}只")

        if not constituents:
            return {}

        # Step 2: 连续三年分红检查 + 股息率计算
        eligible = []
        n_no_data = 0
        n_no_div = 0
        n_no_price = 0

        for ts_code in constituents:
            # 检查分红数据
            if ts_code not in self._dps_by_year:
                n_no_data += 1
                continue

            # 连续三年分红检查
            if not self._check_consecutive_dividends(ts_code, date_str):
                n_no_div += 1
                continue

            # 计算三年平均股息率
            div_yield = self._calc_avg_dividend_yield(ts_code, date_str)
            if div_yield is None or div_yield <= 0:
                n_no_price += 1
                continue

            info = self._stock_info_map.get(ts_code, {})
            eligible.append({
                "ts_code": ts_code,
                "name": info.get("name", ts_code),
                "industry": info.get("industry", ""),
                "div_yield_3y": round(div_yield, 4),
            })

        if verbose:
            print(f"  无分红数据: {n_no_data} | 无连续分红: {n_no_div} | 缺价/股息率: {n_no_price}")
            print(f"  合格样本: {len(eligible)}只")

        # —— D6: 股息率合理性检查 ——
        if eligible and verbose:
            yields = [s["div_yield_3y"] for s in eligible]
            print(f"  📊 股息率: min={min(yields):.2f}% median={np.median(yields):.2f}% max={max(yields):.2f}%")
            if min(yields) <= 0:
                print(f"  ⚠️ D6警告: 存在股息率≤0的标的（{sum(1 for y in yields if y <= 0)}只）")
            if np.mean(yields) < 2 or np.mean(yields) > 8:
                print(f"  ⚠️ D6注意: 平均股息率{np.mean(yields):.2f}%偏离合理区间(2%-8%)")

        if not eligible:
            return {}

        # Step 3: 第一轮 — 股息率降序 → Top 300
        eligible.sort(key=lambda x: x["div_yield_3y"], reverse=True)
        top_n_div = min(300, len(eligible))
        div_top = eligible[:top_n_div]

        if verbose:
            print(f"  股息率 Top{top_n_div}: "
                  f"最高 {div_top[0]['div_yield_3y']:.2f}% "
                  f"最低 {div_top[-1]['div_yield_3y']:.2f}%")

        # Step 4: 第二轮 — 加载波动率 → 升序 → Top 100
        vol_data = self._load_vol_from_disk(date_str)

        # 为 top_n_div 中的标的附加波动率
        n_vol_missing = 0
        for s in div_top:
            vol = vol_data.get(s["ts_code"])
            if vol is None or (isinstance(vol, float) and np.isnan(vol)):
                s["ann_vol"] = None
                n_vol_missing += 1
            else:
                s["ann_vol"] = vol

        if verbose:
            print(f"  波动率数据: 缺失{n_vol_missing}只/{len(div_top)}只 "
                  f"({n_vol_missing/len(div_top)*100:.1f}% 用默认值{self.DEFAULT_VOL}%)")

        # —— D7: 波动率合理性检查 ——
        vols = [s.get("ann_vol") for s in div_top if s.get("ann_vol") is not None]
        if verbose and vols:
            print(f"  📊 波动率: min={min(vols):.1f}% median={np.median(vols):.1f}% max={max(vols):.1f}%")
            if max(vols) > 80:
                print(f"  ⚠️ D7警告: 存在波动率>80%的异常标的")

        # 按波动率升序（缺失波动率排最后）
        def _vol_sort_key(s):
            v = s.get("ann_vol")
            if v is None:
                return (1, self.DEFAULT_VOL)  # 缺失值排最后
            return (0, v)

        div_top.sort(key=_vol_sort_key)
        top_n_final = min(100, len(div_top))
        selected = div_top[:top_n_final]

        if verbose:
            sel_vols = [s.get("ann_vol") for s in selected if s.get("ann_vol") is not None]
            print(f"  波动率 Top{top_n_final}: "
                  f"最低 {min(sel_vols):.1f}% " + (f"最高 {max(sel_vols):.1f}%" if sel_vols else "N/A"))

        # —— D8: 篮子规模检查 ——
        if len(selected) < 50 and verbose:
            print(f"  ⚠️ D8警告: 可选标的不足 ({len(selected)}只 < 50只)")

        # Step 5: 股息率/波动率加权 + 行业20%上限
        selected = self._apply_div_vol_weighting_and_cap(selected)

        # —— CP2: 篮子自检 ——
        self._run_basket_checks(selected, verbose)

        # Step 6: 转为返回格式
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

        if verbose:
            total_w = sum(s["weight"] for s in selected)
            avg_yield = np.mean([s["div_yield_3y"] for s in selected])
            avg_vol = np.mean([s.get("ann_vol") or self.DEFAULT_VOL for s in selected])
            print(f"  ✅ 最终持仓: {len(selected)}只, 权重合计={total_w:.4f}, "
                  f"平均股息率={avg_yield:.2f}%, 平均波动率={avg_vol:.1f}%")

        return result

    # ————————————————————————————————————————————————
    # 选股辅助方法
    # ————————————————————————————————————————————————

    def _get_annual_dividends(self, ts_code: str, year: int) -> Optional[float]:
        """获取某年每股税前分红总额（O(1)查询）"""
        year_map = self._dps_by_year.get(ts_code)
        if year_map is None:
            return None
        dps = year_map.get(year, 0.0)
        return dps if dps > 0 else 0.0

    def _check_consecutive_dividends(
        self, ts_code: str, date_str: str, years: int = 3
    ) -> bool:
        """检查是否过去years年连续现金分红"""
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        check_years = list(range(latest_year - years + 1, latest_year + 1))
        for yr in check_years:
            dps = self._get_annual_dividends(ts_code, yr)
            if dps is None or dps <= 0:
                return False
        return True

    def _get_stock_price(self, ts_code: str, date_str: str) -> Optional[float]:
        """获取指定日期的收盘价"""
        prices = self._load_prices_from_cache(date_str)
        return prices.get(ts_code)

    def _calc_avg_dividend_yield(
        self, ts_code: str, date_str: str, years: int = 3
    ) -> Optional[float]:
        """
        计算过去years年平均股息率。

        官方公式（930955）：股息率 = (过去三个会计年度分红总额/3) / 调整日总市值
        实际实现：每年独立计算 DPS_i / 年末股价_i，然后平均
        （与 dividend_universe.py 的 _calc_avg_dividend_yield 逻辑一致）
        """
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        check_years = list(range(latest_year - years + 1, latest_year + 1))

        annual_yields = []
        for yr in check_years:
            dps = self._get_annual_dividends(ts_code, yr)
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

    def _apply_div_vol_weighting_and_cap(
        self,
        stocks: List[Dict],
        single_cap: float = 0.10,
        industry_cap: float = 0.20,
        max_iter: int = 200,
    ) -> List[Dict]:
        """
        股息率/波动率加权 + 单股10%封顶 + 行业20%上限。

        两步迭代：
          Step A: 股息率/波动率加权，单股10%封顶迭代重分配
          Step B: 行业20%上限，超标行业缩放，溢出分配给其他行业
        """
        if not stocks:
            return stocks

        n = len(stocks)

        # — Step A: 股息率/波动率加权 + 单股封顶 —
        scores = []
        for s in stocks:
            div_y = s.get("div_yield_3y", 0) or 0
            vol = s.get("ann_vol")
            if vol is None or vol <= 0:
                vol = self.DEFAULT_VOL
            if vol < 0.5:  # 极端低波保护分母
                vol = 0.5
            scores.append(div_y / vol)

        total_score = sum(scores)
        if total_score <= 0:
            w = 1.0 / n
            for s in stocks:
                s["weight"] = round(w, 6)
            return stocks

        weights = [s / total_score for s in scores]

        # 单股封顶迭代
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
                min(c + overflow * (c / below_sum), single_cap) if c < single_cap else single_cap
                for c in capped
            ]

        # 归一化
        tw = sum(weights)
        weights = [w / tw for w in weights]
        for s, w in zip(stocks, weights):
            s["weight"] = round(w, 6)

        # — Step B: 行业20%上限封顶 —
        for iteration in range(max_iter):
            # 统计行业权重
            industry_weights: Dict[str, float] = {}
            for s in stocks:
                ind = s.get("industry", "") or "其他"
                industry_weights[ind] = industry_weights.get(ind, 0) + s["weight"]

            # 找超标行业
            overflow_industries = {
                ind: w for ind, w in industry_weights.items() if w > industry_cap
            }
            if not overflow_industries:
                break

            # 总超标量
            total_overflow = sum(w - industry_cap for w in overflow_industries.values())

            # 未超标行业的总权重
            under_weight = sum(
                w for ind, w in industry_weights.items()
                if ind not in overflow_industries
            )
            if under_weight <= 0:
                break

            # 超标行业缩减到上限
            for s in stocks:
                ind = s.get("industry", "") or "其他"
                if ind in overflow_industries:
                    ratio = industry_cap / industry_weights[ind]
                    s["weight"] *= ratio

            # 溢出分配给未超标行业（按当前权重比例）
            for s in stocks:
                ind = s.get("industry", "") or "其他"
                if ind not in overflow_industries:
                    s["weight"] += total_overflow * (s["weight"] / under_weight)

            # 归一化
            tw = sum(s["weight"] for s in stocks)
            if tw > 0:
                for s in stocks:
                    s["weight"] /= tw

        # 最终归一化
        tw = sum(s["weight"] for s in stocks)
        if tw > 0:
            for s in stocks:
                s["weight"] = round(s["weight"] / tw, 6)

        return stocks

    # ————————————————————————————————————————————————
    # 数据质量自检
    # ————————————————————————————————————————————————

    def _run_data_quality_checks(self):
        """Phase 1 完成后的数据质量自检（D1-D5）"""
        print("\n" + "=" * 60)
        print("📋 数据质量自检")
        print("=" * 60)

        # D1: 成分股完整性
        weights = self._idx_cache._weights
        if weights is not None and not weights.empty:
            n_records = len(weights)
            print(f"  ✅ D1 成分股权重: {n_records}条记录")
            if n_records < 10000:
                print(f"  ⚠️ D1警告: 成分股权重记录较少 ({n_records}条)，历史覆盖可能不足")
        else:
            print(f"  ❌ D1失败: 成分股权重文件为空")

        # D2: 分红覆盖率
        if self._all_rebalance_dates and self._all_rebalance_dates:
            # 抽查第一期
            first_date = self._all_rebalance_dates[0]
            constituents = self._idx_cache.get_constituents(first_date)
            if constituents:
                has_div = sum(1 for c in constituents if c in self._dps_by_year)
                ratio = has_div / len(constituents) * 100
                status = "✅" if ratio >= 70 else "⚠️"
                print(f"  {status} D2 分红覆盖率({first_date}): {has_div}/{len(constituents)} = {ratio:.1f}%")
                if ratio < 70:
                    print(f"     ⚠️ 低于70%阈值，大量标的无法通过连续3年分红检查")
        else:
            n_div = len(self._dps_by_year)
            print(f"  📊 D2 分红数据: {n_div}只有分红记录")

        # D3: 波动率覆盖率
        vol_cached = 0
        vol_total = 0
        for d in self._all_rebalance_dates:
            vol_data = self._load_vol_from_disk(d)
            if vol_data:
                valid = sum(1 for v in vol_data.values() if v is not None)
                vol_cached += valid
                vol_total += len(vol_data)
        if vol_total > 0:
            ratio = vol_cached / vol_total * 100
            status = "✅" if ratio >= 80 else "⚠️"
            print(f"  {status} D3 波动率覆盖率: {vol_cached}/{vol_total} = {ratio:.1f}%")
            if ratio < 80:
                print(f"     ⚠️ 低于80%阈值，加权时将大量使用默认值{self.DEFAULT_VOL}%")
        else:
            print(f"  ⚠️ D3: 波动率缓存为空（尚未下载？）")

        # D4: 价格覆盖率（抽查第一期）
        if self._all_rebalance_dates:
            first_date = self._all_rebalance_dates[0]
            constituents = self._idx_cache.get_constituents(first_date)
            if constituents:
                prices = self._load_prices_from_cache(first_date)
                has_price = sum(1 for c in constituents if c in prices)
                ratio = has_price / len(constituents) * 100
                status = "✅" if ratio >= 95 else "⚠️"
                print(f"  {status} D4 价格覆盖率({first_date}): {has_price}/{len(constituents)} = {ratio:.1f}%")
            else:
                print(f"  ⚠️ D4: 第一期无成分股")

        # D5: 行业覆盖率
        if self._all_rebalance_dates:
            first_date = self._all_rebalance_dates[0]
            constituents = self._idx_cache.get_constituents(first_date)
            if constituents:
                has_ind = sum(1 for c in constituents
                             if self._stock_info_map.get(c, {}).get("industry", ""))
                ratio = has_ind / len(constituents) * 100
                status = "✅" if ratio >= 90 else "⚠️"
                print(f"  {status} D5 行业覆盖率({first_date}): {has_ind}/{len(constituents)} = {ratio:.1f}%")
                if ratio < 90:
                    print(f"     ⚠️ 低于90%阈值，行业上限封顶可能失效")
        else:
            n_stocks = len(self._stock_info_map)
            has_ind = sum(1 for info in self._stock_info_map.values() if info.get("industry", ""))
            print(f"  📊 D5 行业信息: {has_ind}/{n_stocks}只 ({has_ind/n_stocks*100:.1f}%)")

        print("=" * 60)

    def _run_basket_checks(self, selected: List[Dict], verbose: bool = True):
        """选股完成后的篮子自检（CP2：权重/换手合理性）"""
        if not selected or not verbose:
            return

        n = len(selected)
        weights = [s.get("weight", 0) for s in selected]
        max_w = max(weights)
        total_w = sum(weights)

        # 单股权重上限
        if max_w > 0.105:
            print(f"  ⚠️ CP2警告: 单股权重{max_w:.4f} > 10%上限")

        # 权重合计
        if abs(total_w - 1.0) > 0.01:
            print(f"  ⚠️ CP2警告: 权重合计{total_w:.4f} ≠ 1.0")

        # 行业权重上限
        ind_weights: Dict[str, float] = {}
        for s in selected:
            ind = s.get("industry", "") or "其他"
            ind_weights[ind] = ind_weights.get(ind, 0) + s["weight"]
        max_ind_w = max(ind_weights.values())
        max_ind_name = max(ind_weights, key=ind_weights.get)
        if max_ind_w > 0.205:
            print(f"  ⚠️ CP2警告: 行业'{max_ind_name}'权重{max_ind_w:.4f} > 20%上限")

        # 篮子数量
        if n != 100:
            print(f"  ⚠️ CP2注意: 篮子数量{n} ≠ 100")

        if max_w <= 0.105 and abs(total_w - 1.0) <= 0.01 and max_ind_w <= 0.205 and n == 100:
            print(f"  ✅ CP2 篮子自检通过: {n}只, 最大单股权重{max_w:.4f}, "
                  f"最大行业权重{max_ind_w:.4f}({max_ind_name})")


# ============================================================================
# 测试入口
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  中证红利低波动100指数（930955）选股引擎 — 测试")
    print("=" * 60)

    # 仅测试基本功能（不触发下载）
    engine = DividendLowvolEngine(index_code="000906.SH")
    engine.preload(download=False)

    test_dates = ["2018-06-15", "2020-12-14", "2025-06-16"]
    for d in test_dates:
        try:
            basket = engine.select_basket(d)
            print(f"\n  结果: {len(basket)}只")
            if basket:
                top3 = list(basket.items())[:3]
                for code, info in top3:
                    print(f"    {code} {info['name']}: 股息率={info['div_yield_3y']:.2f}% "
                          f"波动率={info.get('ann_vol', 'N/A')} 权重={info['weight']:.4f}")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            import traceback
            traceback.print_exc()
