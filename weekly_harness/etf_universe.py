"""
ETF 持仓宇宙管理器
====================
从主流红利 ETF 的历史持仓（fund_portfolio）中动态构建策略股票池。

核心思路：
- 主流红利 ETF 本身就是由专业指数公司筛选的高股息标的集合，
  ETF 的重仓股代表了该指数中最高权重的红利标的。
- 通过聚合多只 ETF 的前 N 大持仓，可以构建一个更具代表性的
  "机构公认红利核心资产"策略池。

数据说明（无未来信息保证）：
  tushare fund_portfolio 记录了每期报告的 ann_date（实际公告日），
  如 end_date=20151231 的年报，ann_date 在 20160121~20160329。
  本模块严格用 ann_date 过滤，确保回测中只用到"当时已公开"的持仓，
  不存在将未来的持仓信息用于历史回测的问题。

披露时间规律（基于 510880 实测）：
  - 季报（0331/0930，前十大持仓）→ 约 20~25 天后公告
  - 半年报（0630，前五十大持仓）→ 约 18~25 天后公告
  - 年报（1231，前五十大持仓）→ 约 20~90 天后公告（最晚次年3月底）

使用的 ETF（用户指定）：
  - 561580.SH  央企红利ETF华泰柏瑞   中证央企红利指数  2023年起
  - 512890.SH  红利低波ETF           红利低波动指数    2019年起
  - 515180.SH  易方达中证红利ETF     中证红利指数      2019年起
  - 510880.SH  华夏红利ETF           中证红利指数      2007年起（历史最深）

使用方式：
  from weekly_harness.etf_universe import EtfTopUniverse

  universe = EtfTopUniverse()       # 使用默认4只ETF
  universe.preload_all()            # 预加载所有历史持仓

  # 查询指定季度调仓日的策略池（自动用 ann_date 过滤，无未来信息）
  meta = universe.get_eligible_meta("2020-06-30")
  # 返回: {ts_code: {name, category, certainty, sector, is_etf, etf_count, ...}}
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
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


# ─── 行业 → (sector, category) 映射（与 index_universe.py 保持一致）─────────
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


class EtfHoldingCache:
    """
    缓存多只红利 ETF 的历史持仓数据（fund_portfolio），严格用 ann_date 过滤。

    关键：ann_date 是基金实际公告日，用它过滤可确保无未来信息泄露。
    例如 end_date=20151231 的年报，最早在 20160121 才公告，
    因此在 2016-01-21 之前的调仓日，只能用 20150930 季报的持仓。

    数据结构：
        _data[etf_code] = pd.DataFrame (含 ann_date, end_date, symbol, stk_mkv_ratio)
    """

    # 用户指定的 4 只 ETF（默认列表）
    DEFAULT_ETFS: List[Tuple[str, str]] = [
        ("510880.SH", "华夏红利ETF"),          # 中证红利指数，2007年起（历史最深）
        ("515180.SH", "易方达中证红利ETF"),     # 中证红利指数，2019年起
        ("512890.SH", "红利低波ETF"),           # 红利低波动指数，2019年起
        ("561580.SH", "央企红利ETF华泰柏瑞"),   # 中证央企红利指数，2023年起
    ]

    def __init__(self, etf_codes: Optional[List[str]] = None):
        """
        Args:
            etf_codes: 指定使用的 ETF 代码列表，None 则使用 DEFAULT_ETFS
        """
        if etf_codes is None:
            self._etfs = list(self.DEFAULT_ETFS)
        else:
            etf_map = {c: n for c, n in self.DEFAULT_ETFS}
            self._etfs = [(c, etf_map.get(c, c)) for c in etf_codes]

        # {etf_code: DataFrame}  — 保留 ann_date 字段用于严格过滤
        self._data: Dict[str, pd.DataFrame] = {}
        self._loaded: set = set()

    def _api(self):
        if not hasattr(self, "_pro") or self._pro is None:
            self._pro = _get_tushare_api()
        return self._pro

    def ensure_loaded(self, etf_code: str) -> None:
        """确保某只 ETF 的持仓历史已加载"""
        if etf_code in self._loaded:
            return
        pro = self._api()
        try:
            df = pro.fund_portfolio(ts_code=etf_code)
            time.sleep(0.5)
            if df is not None and not df.empty:
                df = df[df["stk_mkv_ratio"] > 0].copy()
                df["end_date"] = df["end_date"].astype(str)
                df["ann_date"] = df["ann_date"].astype(str)
                self._data[etf_code] = df
                print(f"    ✅ 加载 {etf_code}: {len(df['end_date'].unique())}期, "
                      f"end_date={df['end_date'].min()}~{df['end_date'].max()}, "
                      f"ann_date={df['ann_date'].min()}~{df['ann_date'].max()}")
            else:
                self._data[etf_code] = pd.DataFrame()
                print(f"    ⚠️ {etf_code}: 无数据")
        except Exception as e:
            print(f"    ❌ [EtfHoldingCache] 加载失败({etf_code}): {e}")
            self._data[etf_code] = pd.DataFrame()
        self._loaded.add(etf_code)

    def preload_all(self) -> None:
        """预加载所有配置 ETF 的持仓历史"""
        print("  📥 加载红利ETF持仓历史（用 ann_date 严格过滤无未来信息）...")
        for etf_code, etf_name in self._etfs:
            if etf_code not in self._loaded:
                print(f"  📋 {etf_name}({etf_code})", end=" → ")
                self.ensure_loaded(etf_code)

    def get_top_holdings_by_ann_date(
        self,
        etf_code: str,
        query_date: str,
        top_n: int = 30,
    ) -> pd.DataFrame:
        """
        返回 etf_code 在 query_date 当天已公告的最新一期前 top_n 大持仓。

        严格无未来信息过滤：
          - 找所有 ann_date <= query_date 的记录
          - 取其中最新的 end_date（即最新已公告报告期）
          - 返回该期前 top_n 大持仓

        这样做的效果：
          - 例如调仓日 2016-02-01，此时 2015年报（end_date=20151231）
            于2016-01-21已公告，所以可用，取年报完整持仓
          - 例如调仓日 2016-01-10，2015年报尚未公告（最早20160121才公告），
            所以只能用 2015-09-30 季报（20151027公告）

        Returns:
            DataFrame with columns: symbol, stk_mkv_ratio, end_date, ann_date
        """
        self.ensure_loaded(etf_code)
        df = self._data.get(etf_code, pd.DataFrame())
        if df.empty:
            return pd.DataFrame()

        # 用 ann_date 过滤：只用当天已公告的记录
        q_str = query_date.replace("-", "")
        available = df[df["ann_date"] <= q_str].copy()
        if available.empty:
            return pd.DataFrame()

        # 取最新的 end_date（报告期）
        latest_end = available["end_date"].max()
        df_snap = available[available["end_date"] == latest_end].copy()
        df_snap = df_snap.sort_values("stk_mkv_ratio", ascending=False).head(top_n)
        df_snap = df_snap.reset_index(drop=True)
        return df_snap

    def get_available_etfs_at(self, query_date: str) -> List[str]:
        """返回在 query_date 时有已公告持仓数据的 ETF 代码列表"""
        result = []
        for etf_code, _ in self._etfs:
            self.ensure_loaded(etf_code)
            df = self._data.get(etf_code, pd.DataFrame())
            if df.empty:
                continue
            q_str = query_date.replace("-", "")
            available = df[df["ann_date"] <= q_str]
            if not available.empty:
                result.append(etf_code)
        return result

    def describe_coverage(self) -> str:
        """描述各 ETF 的数据覆盖情况"""
        lines = ["ETF数据覆盖情况:"]
        for etf_code, etf_name in self._etfs:
            self.ensure_loaded(etf_code)
            df = self._data.get(etf_code, pd.DataFrame())
            if df.empty:
                lines.append(f"  {etf_name}({etf_code}): 无数据")
                continue
            periods = sorted(df["end_date"].unique())
            ann_min = df["ann_date"].min()
            ann_max = df["ann_date"].max()
            lines.append(
                f"  {etf_name}({etf_code}): {len(periods)}期报告, "
                f"end_date={periods[0]}~{periods[-1]}, "
                f"ann_date={ann_min}~{ann_max}"
            )
        return "\n".join(lines)


class EtfTopUniverse:
    """
    红利 ETF 持仓 Top 宇宙（动态策略池）。

    核心策略：
      1. 在每个调仓日，查询各红利 ETF 截至当天已公告的最新持仓
         （严格用 ann_date 过滤，无未来信息）
      2. 取各 ETF 前 top_n 大持仓的并集/交集（min_etf_count 控制）
      3. 返回与 BacktestEngine 兼容的元数据格式

    历史覆盖：
      - 2007~2019：只有 510880（华夏红利ETF），策略池 = 其前 top_n 大持仓
      - 2019~2023：510880 + 515180 + 512890，策略池更丰富
      - 2023至今：4只 ETF 全部可用，策略池最完整

    使用方式：
        universe = EtfTopUniverse(top_n=30, min_etf_count=1)
        universe.preload_all()
        meta = universe.get_eligible_meta("2020-06-30")
    """

    def __init__(
        self,
        etf_codes: Optional[List[str]] = None,
        top_n: int = 30,
        min_etf_count: int = 1,
    ):
        """
        Args:
            etf_codes: 使用的 ETF 代码列表，None 则使用默认4只
                       (510880, 515180, 512890, 561580)
            top_n: 每只 ETF 取前 top_n 大持仓（基于 stk_mkv_ratio 权重）
            min_etf_count: 标的至少出现在多少只 ETF 中才纳入策略池
                           1 = 取并集（任何一只ETF持有即纳入）
                           2 = 至少2只ETF同时持有（提高共识度）
        """
        self.top_n = top_n
        self.min_etf_count = min_etf_count
        self._cache = EtfHoldingCache(etf_codes)
        self._stock_info_cache: Optional[pd.DataFrame] = None
        self._pro = None

    def _api(self):
        if self._pro is None:
            self._pro = _get_tushare_api()
        return self._pro

    def preload_all(self) -> None:
        """预加载所有 ETF 的持仓历史数据"""
        print("\n  📥 预加载红利ETF持仓历史数据（严格按 ann_date 过滤）...")
        self._cache.preload_all()
        print(f"\n  {self._cache.describe_coverage()}")
        # 预加载行业信息
        self._get_stock_info()
        print("  ✅ 行业信息加载完成")

    def _get_stock_info(self) -> pd.DataFrame:
        """获取全量股票基础信息（ts_code → name, industry）"""
        if self._stock_info_cache is None:
            pro = self._api()
            self._stock_info_cache = pro.stock_basic(fields="ts_code,name,industry")
            time.sleep(0.3)
        return self._stock_info_cache

    def get_pool_at(
        self, date_str: str
    ) -> Dict[str, Dict]:
        """
        获取 date_str 时的策略股票池（已公告的持仓，无未来信息）。

        基于各 ETF 在 date_str 时已公告的最新一期持仓，
        取前 top_n 大持仓的并集（或按 min_etf_count 取交集区域）。

        Returns:
            {ts_code: {etf_count, total_weight, appeared_in, used_period}}
        """
        from collections import defaultdict
        code_count: Dict[str, int] = defaultdict(int)
        code_weight: Dict[str, float] = defaultdict(float)
        code_etfs: Dict[str, List[str]] = defaultdict(list)
        code_period: Dict[str, str] = {}

        for etf_code, etf_name in self._cache._etfs:
            self._cache.ensure_loaded(etf_code)
            df_top = self._cache.get_top_holdings_by_ann_date(
                etf_code, date_str, self.top_n
            )
            if df_top.empty:
                continue
            used_period = df_top["end_date"].iloc[0]
            for _, row in df_top.iterrows():
                sym = row["symbol"]
                code_count[sym] += 1
                code_weight[sym] += float(row.get("stk_mkv_ratio", 0))
                code_etfs[sym].append(etf_name)
                code_period[sym] = used_period  # 记录来源报告期

        # 按 min_etf_count 过滤
        pool = {}
        for sym, cnt in code_count.items():
            if cnt >= self.min_etf_count:
                pool[sym] = {
                    "etf_count":    cnt,
                    "total_weight": code_weight[sym],
                    "appeared_in":  code_etfs[sym],
                    "used_period":  code_period.get(sym, ""),
                }
        return pool

    def get_eligible_meta(
        self, date_str: str
    ) -> Dict[str, Dict]:
        """
        返回 date_str 时策略股票池的完整元数据，格式与 BacktestEngine 兼容。

        数据保证：严格用 ann_date 过滤，确保回测无未来信息泄露。

        Returns:
            {
              "600900.SH": {
                "name": "长江电力",
                "category": "弱周期红利",
                "certainty": "B+",
                "sector": "水电",
                "is_etf": False,
                "etf_count": 2,
                "total_weight": 3.5,
                "used_period": "20231231",  # 来源报告期（调试用）
              },
              ...
            }
        """
        pool = self.get_pool_at(date_str)
        if not pool:
            return {}

        stock_info = self._get_stock_info()
        info_map = {}
        for _, row in stock_info.iterrows():
            info_map[row["ts_code"]] = (row.get("name", ""), row.get("industry", ""))

        meta = {}
        for sym, pool_info in pool.items():
            name, industry = info_map.get(sym, (sym[:6], ""))
            sector, category = INDUSTRY_MAP.get(str(industry), ("其他", "弱周期红利"))

            meta[sym] = {
                "name":         name,
                "category":     category,
                "certainty":    "B+",   # 默认值，可由波动率动态覆盖
                "sector":       sector,
                "is_etf":       False,
                "etf_count":    pool_info["etf_count"],
                "total_weight": pool_info["total_weight"],
                "used_period":  pool_info["used_period"],
            }

        return meta

    def describe_pool_at(self, date_str: str) -> str:
        """返回可读的策略池描述（包含来源报告期，便于调试验证）"""
        meta = self.get_eligible_meta(date_str)
        avail = self._cache.get_available_etfs_at(date_str)
        if not meta:
            return f"[{date_str}] 无可用持仓数据（ETF均未公告）"

        lines = [
            f"[{date_str}] ETF持仓策略池: {len(meta)}只",
            f"  可用ETF: {avail}",
        ]
        sorted_items = sorted(
            meta.items(),
            key=lambda x: (-x[1].get("etf_count", 0), -x[1].get("total_weight", 0))
        )
        for ts_code, info in sorted_items[:20]:
            lines.append(
                f"  {ts_code} {info['name']:6s} [{info['category']}] "
                f"ETF×{info['etf_count']} 权重={info['total_weight']:.2f}% "
                f"来源报告期={info['used_period']}"
            )
        if len(meta) > 20:
            lines.append(f"  ... 共 {len(meta)} 只")
        return "\n".join(lines)


# ─── 命令行测试 ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="红利ETF持仓策略池 — 测试工具")
    parser.add_argument("--date", default="2024-06-30", help="查询日期")
    parser.add_argument("--top-n", type=int, default=30, help="每只ETF取前N大持仓")
    parser.add_argument("--min-etf", type=int, default=1, help="至少出现在N只ETF中")
    parser.add_argument(
        "--show-history", action="store_true",
        help="显示多个时间点的持仓变化"
    )
    parser.add_argument(
        "--etf-codes", default=None,
        help="指定ETF代码，逗号分隔 (默认: 510880,515180,512890,561580)"
    )
    args = parser.parse_args()

    etf_codes = args.etf_codes.split(",") if args.etf_codes else None

    universe = EtfTopUniverse(
        etf_codes=etf_codes,
        top_n=args.top_n,
        min_etf_count=args.min_etf,
    )
    universe.preload_all()

    if args.show_history:
        # 展示多个季度调仓日的动态变化
        dates = [
            "2015-03-31", "2015-09-30",
            "2016-03-31", "2016-09-30",
            "2017-03-31", "2017-09-30",
            "2018-03-31", "2018-09-30",
            "2019-03-31", "2019-09-30",
            "2020-03-31", "2020-09-30",
            "2021-03-31", "2021-09-30",
            "2022-03-31", "2022-09-30",
            "2023-03-31", "2023-09-30",
            "2024-03-31", "2024-09-30",
            "2025-03-31",
        ]
        for d in dates:
            meta = universe.get_eligible_meta(d)
            avail = universe._cache.get_available_etfs_at(d)
            if not meta:
                print(f"\n{d}: 无数据 (可用ETF={avail})")
                continue
            # 检查来源报告期
            periods_used = set(v["used_period"] for v in meta.values())
            print(f"\n{d}: {len(meta)}只 | 可用ETF={len(avail)}只 | 报告期={periods_used}")
            top5 = sorted(meta.items(), key=lambda x: (-x[1]["etf_count"], -x[1]["total_weight"]))[:5]
            for code, info in top5:
                print(f"  {code} {info['name']:6s} [{info['sector']}] "
                      f"ETF×{info['etf_count']} {info['total_weight']:.1f}% "
                      f"(期={info['used_period']})")
    else:
        print("\n" + universe.describe_pool_at(args.date))
