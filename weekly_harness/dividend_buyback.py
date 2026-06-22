"""
中证800红利指数（931644）— 回购增强版
=======================================

在 DividendUniverse 基础上叠加回购因子：
  股东回报率 = (年分红总额 + 年完成回购金额 × 0.3) / 年末总市值 × 100%
  三年平均股东回报率 Top100 选股

过滤条件不变：连续三年分红 + 股利支付率过滤

使用方法：
  uni = DividendBuybackUniverse(index_code="000906.SH")
  uni.preload_all()
  basket = uni.get_dividend_basket("2020-06-15", top_n=100)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Optional, List

from weekly_harness.dividend_universe import DividendUniverse, _PROJECT_ROOT

_DATA_DIR = Path(_PROJECT_ROOT) / "data"
_BB_DIR = _DATA_DIR / "buyback_history"


class DividendBuybackUniverse(DividendUniverse):
    """
    回购增强版 800红利 — 股东回报率 = 分红 + 回购 × 0.3
    """

    BUYBACK_WEIGHT = 0.3  # 回购权重（模拟注销式占比）

    def __init__(self, index_code: str = "000906.SH"):
        super().__init__(index_code=index_code)
        # 年回购金额: {ts_code: {year: buyback_amount_yuan}}
        self._buyback_by_year: Dict[str, Dict[int, float]] = {}
        # 市值缓存: {date_str: {ts_code: total_mv_yuan}}
        self._mv_cache: Dict[str, Dict[str, float]] = {}

    # ── 覆盖：加载回购数据 ──
    def _load_dividend_cache(self):
        """加载分红 + 回购数据"""
        super()._load_dividend_cache()
        self._load_buyback_cache()

    def _load_buyback_cache(self):
        """从 data/buyback_history/ 加载回购数据 → 年度回购金额索引"""
        if not _BB_DIR.exists():
            print(f"  ⚠️ 回购数据目录不存在: {_BB_DIR}，仅用分红")
            return

        bb_files = sorted(_BB_DIR.glob("buyback_*.csv"))
        all_df = []
        for f in bb_files:
            try:
                df = pd.read_csv(f, dtype={"ts_code": str, "ann_date": str, "proc": str})
                all_df.append(df)
            except Exception:
                pass

        if not all_df:
            print(f"  ⚠️ 回购数据为空")
            return

        big = pd.concat(all_df, ignore_index=True)
        big["amount"] = pd.to_numeric(big["amount"], errors="coerce").fillna(0)
        big["ann_year"] = big["ann_date"].astype(str).str[:4].astype(int)

        # 仅统计 proc=完成 的回购
        done = big[big["proc"] == "完成"]
        year_bb = done.groupby(["ts_code", "ann_year"])["amount"].sum().reset_index()

        for _, r in year_bb.iterrows():
            code = str(r["ts_code"])
            yr = int(r["ann_year"])
            amt = float(r["amount"])
            if code not in self._buyback_by_year:
                self._buyback_by_year[code] = {}
            self._buyback_by_year[code][yr] = self._buyback_by_year[code].get(yr, 0) + amt

        n_stocks = len(self._buyback_by_year)
        n_records = sum(len(v) for v in self._buyback_by_year.values())
        amt_total = sum(
            sum(v.values()) for v in self._buyback_by_year.values()
        ) / 1e8
        print(f"  📂 回购缓存: {n_stocks}只, {n_records}条年度记录, 总额{amt_total:.0f}亿")

    # ── 覆盖：市值缓存 ──
    def _prices_for_date(self, date_str: str) -> Dict[str, float]:
        """获取收盘价 + 总市值（扩展父类，额外缓存 total_mv）"""
        # 先调父类拿价格
        prices = super()._prices_for_date(date_str)

        # 加载/下载市值
        cache_key = f"MV|{date_str}"
        if cache_key in self._mv_cache:
            return prices

        from weekly_harness.dividend_universe import _PRICE_DIR, _get_tushare_api
        import time as _time

        trade_d = self._nearest_trading_day(date_str)
        cache_file = _PRICE_DIR / f"{trade_d}_mv.csv"

        if cache_file.exists():
            df = pd.read_csv(cache_file, dtype={"ts_code": str})
            self._mv_cache[cache_key] = dict(zip(df["ts_code"], df["total_mv"]))
            return prices

        # 下载（首次，后续纯读盘）
        try:
            pro = _get_tushare_api()
            df = pro.daily_basic(
                trade_date=trade_d,
                fields="ts_code,trade_date,total_mv"
            )
            if df is not None and not df.empty:
                df["total_mv"] = pd.to_numeric(df["total_mv"], errors="coerce")
                df = df[df["total_mv"] > 0][["ts_code", "total_mv"]]
                # total_mv 单位：万元 → 元
                df["total_mv"] = df["total_mv"] * 10000
                df.to_csv(cache_file, index=False)
                self._mv_cache[cache_key] = dict(zip(df["ts_code"], df["total_mv"]))
            _time.sleep(0.10)
        except Exception:
            pass

        return prices

    def _get_total_mv(self, ts_code: str, date_str: str) -> Optional[float]:
        """获取指定日期的总市值（元）"""
        # 确保市值已加载
        self._prices_for_date(date_str)
        cache_key = f"MV|{date_str}"
        return self._mv_cache.get(cache_key, {}).get(ts_code)

    # ── 覆盖：股东回报率计算 ──
    def _get_annual_buyback(self, ts_code: str, year: int) -> float:
        """获取某年完成回购金额（元）"""
        return self._buyback_by_year.get(ts_code, {}).get(year, 0.0)

    def _calc_avg_dividend_yield(
        self, ts_code: str, date_str: str, years: int = 3
    ) -> Optional[float]:
        """
        覆盖：三年平均股东回报率。

        股东回报率_i = (年分红总额_i + 年完成回购金额_i × 0.3) / 年末总市值_i × 100%
        三年均值 = AVG(股东回报率_1, 股东回报率_2, 股东回报率_3)
        """
        rebalance_year = int(date_str[:4])
        latest_year = rebalance_year - 1
        check_years = list(range(latest_year - years + 1, latest_year + 1))

        annual_returns = []
        for yr in check_years:
            dps = self._get_annual_dividends(ts_code, yr)
            if dps is None or dps <= 0:
                continue

            bb = self._get_annual_buyback(ts_code, yr)

            # 总市值（优先年末，回退年中）
            mv = self._get_total_mv(ts_code, f"{yr}-12-31")
            if mv is None or mv <= 0:
                mv = self._get_total_mv(ts_code, f"{yr}-06-30")

            if mv is None or mv <= 0:
                # 退而用价格反推（price + 估算总股本）
                price_yr = self._get_stock_price(ts_code, f"{yr}-12-31")
                if price_yr is None or price_yr <= 0:
                    price_yr = self._get_stock_price(ts_code, f"{yr}-06-30")
                if price_yr is None or price_yr <= 0:
                    continue
                # 用 dps 反推总股本: shares = (分红总额无法获取) 
                # 退路：直接用原始股息率公式 + 回购加成
                raw_div_yield = dps / price_yr * 100
                # 回购部分无法计算（缺总股本/总市值），仅用分红
                annual_returns.append(raw_div_yield)
                continue

            # 标准路径：用总市值
            # 股东回报率 = (dps * shares + bb * 0.3) / mv
            # dps * shares = 分红总额（但我们没有总股本）
            # 改用：原始股息率(已有) + 回购加成 = dps/price*100 + bb*0.3/mv*100
            raw_div_yield = self._calc_raw_div_yield_for_year(ts_code, yr)

            bb_contribution = (bb * self.BUYBACK_WEIGHT) / mv * 100
            shareholder_return = raw_div_yield + bb_contribution
            annual_returns.append(shareholder_return)

        if len(annual_returns) == 0:
            return None

        return sum(annual_returns) / len(annual_returns)

    def _calc_raw_div_yield_for_year(self, ts_code: str, yr: int) -> float:
        """计算单年原始股息率（不含回购）"""
        dps = self._get_annual_dividends(ts_code, yr)
        if dps is None or dps <= 0:
            return 0.0

        price_yr = self._get_stock_price(ts_code, f"{yr}-12-31")
        if price_yr is None or price_yr <= 0:
            price_yr = self._get_stock_price(ts_code, f"{yr}-06-30")

        if price_yr is None or price_yr <= 0:
            return 0.0

        return dps / price_yr * 100
