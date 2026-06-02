"""
动态指数成分股宇宙管理器
支持按回测时间点动态查询指数成分股（沪深300、中证500等），
并实时计算该时点的股息率以过滤高股息标的。

tushare index_weight 数据特性：
- 每月底有权重快照，但存在年份空洞（2015/2019/2023 部分缺失）
- 当某季度无快照时，用前向填充（取最近快照）替代
"""

from __future__ import annotations
import time
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_tushare_api():
    sys.path.insert(0, str(_PROJECT_ROOT))
    import tushare as ts
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


# ──────────────────────────────────────────────────────────────────────────────
# ROE 历史缓存（按需拉取，全局单例）
# ──────────────────────────────────────────────────────────────────────────────
class RoeCache:
    """
    缓存所有股票历史年报 ROE（净资产收益率）。

    用法::

        cache = RoeCache()
        cache.ensure_loaded(ts_codes, pro)      # 批量预加载
        ok = cache.passes_roe_filter(
            "000625.SZ", date_str="2017-12-29",
            min_roe=8.0, min_years=3
        )   # 查询 date_str 前连续 min_years 年 ROE >= min_roe
    """

    def __init__(self):
        # { ts_code: { year(int): roe(float) } }
        self._data: Dict[str, Dict[int, float]] = {}
        self._loaded_codes: set = set()

    def ensure_loaded(self, ts_codes: List[str], pro) -> None:
        """
        批量拉取 ts_codes 中未缓存的股票年报ROE（2010年至今），
        每批最多50只以避免API限制。
        """
        need = [c for c in ts_codes if c not in self._loaded_codes]
        if not need:
            return

        # 逐只查询（批量查询时start_date被API忽略，只返回近8年数据，历史数据不全）
        for code in need:
            if code in self._loaded_codes:
                continue
            try:
                df = pro.fina_indicator(
                    ts_code=code,
                    fields="ts_code,ann_date,end_date,roe",
                    start_date="20100101",
                )
                time.sleep(0.3)
                if df is None or df.empty:
                    self._loaded_codes.add(code)
                    continue

                # 只保留年报（end_date 以 1231 结尾），去重取最新公告
                df = df[df["end_date"].astype(str).str.endswith("1231")].copy()
                df["year"] = df["end_date"].astype(str).str[:4].astype(int)
                df = df.dropna(subset=["roe"]).sort_values("ann_date")
                df = df.drop_duplicates(subset=["year"], keep="last")

                self._data[code] = dict(zip(df["year"], df["roe"]))
                self._loaded_codes.add(code)
            except Exception as e:
                print(f"    [RoeCache] 拉取ROE失败({code}): {e}")
                time.sleep(1)
                self._loaded_codes.add(code)  # 标记已尝试，避免死循环

    def passes_roe_filter(
        self,
        ts_code: str,
        date_str: str,
        min_roe: float = 8.0,
        min_years: int = 3,
    ) -> bool:
        """
        检查 ts_code 在 date_str 之前是否连续 min_years 年 ROE >= min_roe。

        逻辑：
        - 取 date_str 年份的前一年作为最近可用年报年份
          （当年年报通常在次年3~4月才公告，date_str 时不一定能看到）
        - 向前找 min_years 个年份，全部 ROE >= min_roe 则通过
        """
        if ts_code not in self._data:
            # 无数据时默认通过（保守策略：宁可多纳入）
            return True

        base_year = datetime.strptime(date_str[:10], "%Y-%m-%d").year
        # 保守：用上一年年报（因为当年年报可能还没公告）
        # 若 date_str 已过4月，则当年年报已公告，可用当年
        date_obj = datetime.strptime(date_str[:10], "%Y-%m-%d")
        if date_obj.month >= 5:
            latest_year = base_year - 1   # 用上一年（已公告）
        else:
            latest_year = base_year - 2   # 用前年（更保守）

        roe_by_year = self._data[ts_code]
        years_to_check = list(range(latest_year - min_years + 1, latest_year + 1))

        passed_count = 0
        for yr in years_to_check:
            roe = roe_by_year.get(yr)
            if roe is None:
                # 缺失某年数据：视为该年不达标（严格模式）
                return False
            if roe < min_roe:
                return False
            passed_count += 1

        # 至少有1年数据才算通过（完全没有数据时保守通过）
        return passed_count > 0

    def get_roe_summary(self, ts_code: str, date_str: str, years: int = 5) -> str:
        """返回可读的ROE历史摘要，用于日志"""
        if ts_code not in self._data:
            return "无ROE数据"
        date_obj = datetime.strptime(date_str[:10], "%Y-%m-%d")
        latest_year = date_obj.year - 1
        roe_by_year = self._data[ts_code]
        parts = []
        for yr in range(latest_year - years + 1, latest_year + 1):
            r = roe_by_year.get(yr)
            parts.append(f"{yr}:{r:.1f}%" if r is not None else f"{yr}:N/A")
        return "  ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# 波动率缓存（用历史日收益率年化标准差推算 certainty 等级）
# ──────────────────────────────────────────────────────────────────────────────
class VolatilityCache:
    """
    缓存各股票历史年化波动率，用于动态推算 certainty 等级。

    映射规则（基于 CSI300 成分股实证分布）::

        年化波动率 < 20%  → AA (15分)   极低波：四大行、长江电力
        20% ~ 25%         → A  (13分)   低波：水电、运营商
        25% ~ 30%         → A- (11分)   中低波：招商银行、神华
        30% ~ 38%         → B+ ( 8分)   中波：大多数CSI300
        >= 38%            → B  ( 5分)   高波：汽车、地产、化工

    用法::

        cache = VolatilityCache()
        cache.ensure_loaded(ts_codes, pro, date_str)    # 按调仓日预加载
        cert, score = cache.get_certainty("601398.SH", "2017-06-30")  # ("AA", 15)
    """

    # 波动率 → certainty 分档
    _VOL_THRESHOLDS = [
        (20.0, "AA", 15),
        (25.0, "A",  13),
        (30.0, "A-", 11),
        (38.0, "B+",  8),
        (float("inf"), "B", 5),
    ]
    # 回溯窗口（个自然日）—— 约 1 年交易日
    _LOOKBACK_DAYS = 400

    def __init__(self):
        # { ts_code: { end_date_str: ann_vol(float) } }
        # end_date_str 格式 'YYYYMMDD'（调仓日）
        self._data: Dict[str, Dict[str, float]] = {}
        # 已加载 key = (ts_code, end_date)
        self._loaded: set = set()

    @staticmethod
    def _vol_to_certainty(ann_vol: float) -> tuple:
        for threshold, cert, score in VolatilityCache._VOL_THRESHOLDS:
            if ann_vol < threshold:
                return cert, score
        return "B", 5

    def ensure_loaded(
        self, ts_codes: List[str], pro, date_str: str
    ) -> None:
        """
        批量预加载 ts_codes 在 date_str 前约1年的日收益率，
        计算年化波动率并缓存。

        每只股票逐一拉取（日线数据量大，批量不可靠）。
        """
        import math
        from datetime import datetime as dt, timedelta

        end_d = dt.strptime(date_str[:10], "%Y-%m-%d")
        start_d = end_d - timedelta(days=self._LOOKBACK_DAYS)
        start_str = start_d.strftime("%Y%m%d")
        end_str = end_d.strftime("%Y%m%d")

        need = [c for c in ts_codes if (c, end_str) not in self._loaded]
        if not need:
            return

        import numpy as np
        for code in need:
            if (code, end_str) in self._loaded:
                continue
            try:
                df = pro.daily(
                    ts_code=code,
                    start_date=start_str,
                    end_date=end_str,
                    fields="trade_date,pct_chg",
                )
                time.sleep(0.15)
                if df is None or df.empty or len(df) < 60:
                    # 数据不足：设 None，后续使用默认 B+
                    self._data.setdefault(code, {})[end_str] = None
                    self._loaded.add((code, end_str))
                    continue

                pct = df["pct_chg"].dropna() / 100.0
                ann_vol = float(pct.std() * math.sqrt(244) * 100)  # 年化 %
                self._data.setdefault(code, {})[end_str] = ann_vol
                self._loaded.add((code, end_str))
            except Exception as e:
                print(f"    [VolCache] 加载失败({code}@{end_str}): {e}")
                time.sleep(1)
                self._data.setdefault(code, {})[end_str] = None
                self._loaded.add((code, end_str))

    def get_certainty(
        self, ts_code: str, date_str: str, default: str = "B+"
    ) -> tuple:
        """
        返回 (certainty_str, score_int)。

        若缺数据则返回 (default, 8)。
        """
        end_str = date_str.replace("-", "")[:8]
        ann_vol = self._data.get(ts_code, {}).get(end_str)
        if ann_vol is None:
            # default fallback
            default_score = {"AA": 15, "A": 13, "A-": 11, "B+": 8, "B": 5}.get(default, 8)
            return default, default_score
        return self._vol_to_certainty(ann_vol)

    def get_vol_summary(self, ts_code: str, date_str: str) -> str:
        """返回可读摘要，用于日志"""
        end_str = date_str.replace("-", "")[:8]
        ann_vol = self._data.get(ts_code, {}).get(end_str)
        if ann_vol is None:
            return "vol=N/A"
        cert, score = self._vol_to_certainty(ann_vol)
        return f"vol={ann_vol:.1f}%→{cert}({score}分)"


# ──────────────────────────────────────────────────────────────────────────────
# industry → (sector, category) 完整映射
# ──────────────────────────────────────────────────────────────────────────────
INDUSTRY_MAP: Dict[str, Tuple[str, str]] = {
    # 弱周期红利
    "银行":      ("银行",   "弱周期红利"),
    "多元金融":  ("银行",   "弱周期红利"),
    "证券":      ("银行",   "弱周期红利"),
    "保险":      ("保险",   "弱周期红利"),
    "火力发电":  ("火电",   "弱周期红利"),
    "新型电力":  ("火电",   "弱周期红利"),
    "供气供热":  ("火电",   "弱周期红利"),
    "水力发电":  ("水电",   "弱周期红利"),
    "水务":      ("水电",   "弱周期红利"),
    "路桥":      ("水电",   "弱周期红利"),
    "环境保护":  ("水电",   "弱周期红利"),
    "铁路":      ("水电",   "弱周期红利"),
    "电信运营":  ("运营商", "弱周期红利"),
    "通信设备":  ("运营商", "弱周期红利"),
    "建筑工程":  ("银行",   "弱周期红利"),
    "专用机械":  ("银行",   "弱周期红利"),
    "出版业":    ("银行",   "弱周期红利"),
    "互联网":    ("银行",   "弱周期红利"),
    "园区开发":  ("银行",   "弱周期红利"),
    "工程机械":  ("银行",   "弱周期红利"),
    "运输设备":  ("银行",   "弱周期红利"),
    # 消费成长红利
    "白酒":      ("白酒",   "消费成长红利"),
    "啤酒":      ("白酒",   "消费成长红利"),
    "软饮料":    ("白酒",   "消费成长红利"),
    "食品":      ("白酒",   "消费成长红利"),
    "乳制品":    ("白酒",   "消费成长红利"),
    "广告包装":  ("家电",   "消费成长红利"),
    "中成药":    ("中药",   "消费成长红利"),
    "化学制药":  ("中药",   "消费成长红利"),
    "医药商业":  ("中药",   "消费成长红利"),
    "医疗保健":  ("中药",   "消费成长红利"),
    "生物制药":  ("中药",   "消费成长红利"),
    "种植业":    ("中药",   "消费成长红利"),
    "农业综合":  ("中药",   "消费成长红利"),
    "家用电器":  ("家电",   "消费成长红利"),
    "家居用品":  ("家电",   "消费成长红利"),
    "服饰":      ("家电",   "消费成长红利"),
    "摩托车":    ("家电",   "消费成长红利"),
    "汽车整车":  ("家电",   "消费成长红利"),
    "汽车配件":  ("家电",   "消费成长红利"),
    "文教休闲":  ("家电",   "消费成长红利"),
    "日用化工":  ("家电",   "消费成长红利"),
    "百货":      ("家电",   "消费成长红利"),
    "IT设备":    ("家电",   "消费成长红利"),
    # 周期资源红利
    "煤炭开采":  ("煤炭",   "周期资源红利"),
    "石油开采":  ("石油",   "周期资源红利"),
    "石油加工":  ("石油",   "周期资源红利"),
    "铝":        ("矿业",   "周期资源红利"),
    "铜":        ("矿业",   "周期资源红利"),
    "特种钢":    ("矿业",   "周期资源红利"),
    "普钢":      ("矿业",   "周期资源红利"),
    "其他建材":  ("矿业",   "周期资源红利"),
    "农药化肥":  ("矿业",   "周期资源红利"),
    "化工原料":  ("矿业",   "周期资源红利"),
    "染料涂料":  ("矿业",   "周期资源红利"),
    "电气设备":  ("矿业",   "周期资源红利"),
    "水泥":      ("矿业",   "周期资源红利"),
    "港口":      ("海运",   "周期资源红利"),
    "水运":      ("海运",   "周期资源红利"),
    "仓储物流":  ("海运",   "周期资源红利"),
}


class DynamicIndexUniverse:
    """
    动态指数成分股宇宙。

    使用方式:
        universe = DynamicIndexUniverse("000300.SH")
        # 在回测调仓日，获取当时成分股中 dv_ttm >= 3%
        # 且 ROE 连续 min_roe_years 年 >= min_roe 的标的元数据
        stock_meta = universe.get_eligible_meta(
            date_str="2020-06-30", min_div_yield=3.0,
            min_roe=8.0, min_roe_years=3
        )
    """

    def __init__(self, index_code: str = "000300.SH"):
        self.index_code = index_code
        self._weights_cache: Optional[pd.DataFrame] = None   # 全量成分股权重快照
        self._stock_info_cache: Optional[pd.DataFrame] = None  # ts_code → name/industry
        self._daily_basic_cache: Dict[str, pd.DataFrame] = {}  # date → daily_basic
        self._pro = None
        self._roe_cache = RoeCache()          # ROE 历史缓存（单例，跨调仓日共享）
        self._vol_cache = VolatilityCache()   # 波动率缓存（单例，跨调仓日共享）

    def _api(self):
        if self._pro is None:
            self._pro = _get_tushare_api()
        return self._pro

    def preload_weights(self):
        """预加载全量成分股权重快照（分年段拉取，绕过2000行限制）"""
        if self._weights_cache is not None:
            return
        pro = self._api()
        dfs = []
        year_ranges = [
            ("20150101", "20161231"),
            ("20170101", "20181231"),
            ("20190101", "20201231"),
            ("20210101", "20221231"),
            ("20230101", "20241231"),
            ("20250101", "20261231"),
        ]
        for s, e in year_ranges:
            df = pro.index_weight(index_code=self.index_code, start_date=s, end_date=e)
            time.sleep(0.3)
            if not df.empty:
                dfs.append(df)
        if dfs:
            self._weights_cache = pd.concat(dfs, ignore_index=True)
            self._weights_cache["trade_date"] = self._weights_cache["trade_date"].astype(str)
        else:
            self._weights_cache = pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"])

    def _get_snapshot_date(self, query_date: str) -> Optional[str]:
        """找 query_date 之前最近的权重快照日（前向填充）"""
        if self._weights_cache is None:
            self.preload_weights()
        dates = sorted(self._weights_cache["trade_date"].unique())
        d = query_date.replace("-", "")
        prior = [x for x in dates if x <= d]
        return prior[-1] if prior else None

    def get_members_at(self, date_str: str) -> List[str]:
        """返回 date_str 时最新的指数成分股 ts_code 列表"""
        snap = self._get_snapshot_date(date_str)
        if snap is None:
            return []
        members = self._weights_cache[self._weights_cache["trade_date"] == snap]["con_code"].tolist()
        return members

    def _get_stock_info(self) -> pd.DataFrame:
        if self._stock_info_cache is None:
            pro = self._api()
            self._stock_info_cache = pro.stock_basic(fields="ts_code,name,industry")
            time.sleep(0.3)
        return self._stock_info_cache

    def _get_daily_basic_at(self, date_str: str, ts_codes: List[str]) -> pd.DataFrame:
        """
        批量获取 date_str 时的 daily_basic。

        策略：先用 trade_date 参数一次性拉取全市场当日数据，
        再过滤出成分股 ts_codes，性能远优于逐只查询。
        若当日无数据（非交易日），向前最多找 5 个交易日。
        """
        date_key = date_str.replace("-", "")
        if date_key in self._daily_basic_cache:
            return self._daily_basic_cache[date_key]

        pro = self._api()
        code_set = set(ts_codes)

        # 尝试当日及前 5 个自然日（应对非交易日）
        from datetime import datetime, timedelta
        base = datetime.strptime(date_key, "%Y%m%d")
        result = pd.DataFrame()
        for delta in range(6):
            d = (base - timedelta(days=delta)).strftime("%Y%m%d")
            try:
                df = pro.daily_basic(
                    trade_date=d,
                    fields="ts_code,trade_date,close,pe_ttm,pb,dv_ttm,total_mv",
                )
                time.sleep(0.25)
                if not df.empty:
                    result = df[df["ts_code"].isin(code_set)].copy()
                    break
            except Exception:
                time.sleep(1)

        self._daily_basic_cache[date_key] = result
        return result

    def get_eligible_meta(
        self,
        date_str: str,
        min_div_yield: float = 3.0,
        max_pe: float = 100.0,
        min_roe: float = 0.0,
        min_roe_years: int = 0,
        vol_certainty: bool = False,
    ) -> Dict[str, Dict]:
        """
        返回指数在 date_str 时满足以下条件的标的元数据：
          1. dv_ttm >= min_div_yield  （当期股息率）
          2. pe_ttm 在合理区间 (0, max_pe)
          3. 若 min_roe > 0 且 min_roe_years > 0：
             过去 min_roe_years 个完整年报中 ROE 均 >= min_roe
          4. 若 vol_certainty=True：
             用过去1年历史波动率动态计算 certainty（替代固定 B+），
             低波→AA/A，高波→B+/B，让银行/水电自然获得更高评分

        格式与 BacktestEngine._stock_meta 兼容。
        """
        members = self.get_members_at(date_str)
        if not members:
            return {}

        stock_info = self._get_stock_info()
        basic = self._get_daily_basic_at(date_str, members)

        if basic.empty:
            return {}

        # ── Step 1: 股息率 + PE 过滤 ──
        mask = (
            (basic["dv_ttm"] >= min_div_yield)
            & (basic["pe_ttm"] > 0)
            & (basic["pe_ttm"] < max_pe)
        )
        eligible = basic[mask].copy()

        if eligible.empty:
            return {}

        # ── Step 2: ROE 连续达标过滤 ──
        if min_roe > 0 and min_roe_years > 0:
            eligible_codes = eligible["ts_code"].tolist()
            # 批量预加载ROE（利用缓存，只拉一次）
            self._roe_cache.ensure_loaded(eligible_codes, self._api())

            passed_codes = [
                c for c in eligible_codes
                if self._roe_cache.passes_roe_filter(
                    c, date_str,
                    min_roe=min_roe,
                    min_years=min_roe_years,
                )
            ]
            filtered_out = len(eligible_codes) - len(passed_codes)
            if filtered_out > 0:
                print(f"    [ROE过滤] {date_str}: {len(eligible_codes)}只通过股息率"
                      f"→ROE过滤后剩 {len(passed_codes)} 只（剔除{filtered_out}只）")
            eligible = eligible[eligible["ts_code"].isin(passed_codes)].copy()

        if eligible.empty:
            return {}

        # ── Step 3: 若启用波动率 certainty，预加载波动率数据 ──
        eligible_codes_final = eligible["ts_code"].tolist()
        if vol_certainty:
            self._vol_cache.ensure_loaded(eligible_codes_final, self._api(), date_str)

        # ── Step 4: 合并行业信息，生成 meta ──
        eligible = eligible.merge(
            stock_info[["ts_code", "name", "industry"]], on="ts_code", how="left"
        )

        meta = {}
        for _, row in eligible.iterrows():
            industry = str(row.get("industry", ""))
            sector, category = INDUSTRY_MAP.get(industry, ("其他", "弱周期红利"))

            # 动态 certainty：若开启低波映射则用波动率推算，否则固定 B+
            if vol_certainty:
                cert, _ = self._vol_cache.get_certainty(row["ts_code"], date_str)
            else:
                cert = "B+"

            meta[row["ts_code"]] = {
                "name":      row.get("name", row["ts_code"]),
                "category":  category,
                "certainty": cert,
                "sector":    sector,
                "is_etf":    False,
            }
        return meta


if __name__ == "__main__":
    u = DynamicIndexUniverse("000300.SH")
    u.preload_weights()
    m = u.get_members_at("2022-03-25")
    print(f"2022-03-25 沪深300成分: {len(m)} 只，快照日: {u._get_snapshot_date('2022-03-25')}")
    m2 = u.get_members_at("2018-06-29")
    print(f"2018-06-29 成分: {len(m2)} 只，快照日: {u._get_snapshot_date('2018-06-29')}")
