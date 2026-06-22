"""
quick_fundamentals.py — 快速查看515180当前基本面和历史分位
"""

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import tushare as ts

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))


def init_tushare():
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


def main():
    pro = init_tushare()
    ts_code = "515180.SH"
    idx_code = "000922.SH"  # 中证红利指数

    print("=" * 65)
    print(f"  515180（易方达中证红利ETF）+ 中证红利指数 当前基本面")
    print(f"  数据日期: {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 65)

    # ── 1. 指数估值（PE/PB/股息率） ──
    print("\n── 1. 中证红利指数（000922）估值 ──\n")

    try:
        # 指数日线估值
        idx_daily = pro.index_dailybasic(
            ts_code=idx_code,
            start_date="20150101",
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        if not idx_daily.empty:
            idx_daily["trade_date"] = pd.to_datetime(idx_daily["trade_date"])
            idx_daily = idx_daily.sort_values("trade_date")

            latest = idx_daily.iloc[-1]
            print(f"  最新交易日: {latest['trade_date'].strftime('%Y-%m-%d')}")
            print(f"  ─────────────────────────────────────")
            
            for col, label in [
                ("pe", "PE（市盈率）"),
                ("pe_ttm", "PE_TTM"),
                ("pb", "PB（市净率）"),
            ]:
                if col in idx_daily.columns and pd.notna(latest.get(col)):
                    val = latest[col]
                    series = idx_daily[col].dropna()
                    if len(series) > 0:
                        pct = sum(series < val) / len(series) * 100
                        p10 = np.percentile(series, 10)
                        p50 = np.percentile(series, 50)
                        p90 = np.percentile(series, 90)
                        print(f"  {label}: {val:.2f}  (历史 {pct:.0f}% 分位)")
                        print(f"    区间: P10={p10:.2f}  P50={p50:.2f}  P90={p90:.2f}")
                        print(f"    最小={series.min():.2f}  最大={series.max():.2f}")
        else:
            print("  ⚠️ index_dailybasic 无数据")
    except Exception as e:
        print(f"  ⚠️ index_dailybasic 失败: {e}")

    # ── 2. 指数日线估值（备选接口） ──
    print("\n── 2. 中证红利估值详细（index_valuation）──\n")
    try:
        idx_val = pro.index_daily(
            ts_code=idx_code,
            start_date="20150101",
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        if not idx_val.empty:
            idx_val["trade_date"] = pd.to_datetime(idx_val["trade_date"])
            idx_val = idx_val.sort_values("trade_date")
            latest = idx_val.iloc[-1]

            for col, label in [
                ("pe", "PE"),
                ("pb", "PB"),
                ("vol", "成交量(手)"),
            ]:
                if col in idx_val.columns and pd.notna(latest.get(col)):
                    val = latest[col]
                    series = idx_val[col].dropna()
                    if len(series) > 0:
                        pct = sum(series < val) / len(series) * 100
                        print(f"  {label}: {val:.2f}  (分位 {pct:.0f}%)")
        else:
            print("  ⚠️ index_daily 无数据")
    except Exception as e:
        print(f"  ⚠️ index_daily 失败: {e}")

    # ── 3. 指数权重股 FCF/EV ──
    print("\n── 3. 中证红利指数权重股 FCF/EV ──\n")
    try:
        # 获取指数成分权重
        weights = pro.index_weight(
            index_code="000922",
            trade_date=datetime.now().strftime("%Y%m%d"),
        )
        if weights.empty:
            # 尝试最近一个交易日
            weights = pro.index_weight(
                index_code="000922",
                start_date="20260101",
                end_date=datetime.now().strftime("%Y%m%d"),
            )
            if not weights.empty:
                weights = weights.sort_values("trade_date").iloc[-20:]  # 最近一次

        if not weights.empty:
            print(f"  成分股数量: {len(weights)}")
            
            # 取前20大权重
            if "weight" in weights.columns:
                weights = weights.sort_values("weight", ascending=False)
            top20 = weights.head(20)
            
            # 获取这些股票的财务数据
            codes = top20["con_code"].tolist()
            print(f"  前20大权重股: {', '.join(codes[:10])}...")

            # 获取最新财报的 FCF 数据
            from weekly_harness.fcf_universe import FCFUniverse
            fcf = FCFUniverse(pro=pro)
            
            # 尝试直接拉取财务数据
            fina_list = []
            for code in codes[:20]:
                try:
                    # 获取最新季报
                    income = pro.income(
                        ts_code=code,
                        period=datetime.now().strftime("%Y%m%d"),
                        fields="ts_code,end_date,total_revenue,n_income",
                    )
                    if not income.empty:
                        income = income.sort_values("end_date").iloc[-1]
                    
                    # 现金流量表
                    cashflow = pro.cashflow(
                        ts_code=code,
                        period=datetime.now().strftime("%Y%m%d"),
                        fields="ts_code,end_date,n_cashflow_act,free_cashflow,c_pay_acq_const_fiolta",
                    )
                    if not cashflow.empty:
                        cashflow = cashflow.sort_values("end_date").iloc[-1]
                        
                    fina_list.append({
                        "code": code,
                        "income": income if not income.empty else None,
                        "cashflow": cashflow if not cashflow.empty else None,
                    })
                except Exception as ex:
                    continue

            if fina_list:
                print(f"\n  获得 {len(fina_list)} 只股票的财务数据")
                
                # 简化的FCF计算
                for item in fina_list[:10]:
                    code = item["code"]
                    cf = item["cashflow"]
                    inc = item["income"]
                    if cf is not None:
                        ocf = float(cf.get("n_cashflow_act", 0)) / 1e8 if pd.notna(cf.get("n_cashflow_act")) else 0
                        capex = float(cf.get("c_pay_acq_const_fiolta", 0)) / 1e8 if pd.notna(cf.get("c_pay_acq_const_fiolta")) else 0
                        fcf_val = ocf - capex
                        print(f"    {code}: OCF={ocf:.1f}亿  Capex={capex:.1f}亿  FCF={fcf_val:.1f}亿")
        else:
            print("  ⚠️ 无指数成分权重数据")
    except Exception as e:
        print(f"  ⚠️ 权重/FCF数据获取失败: {e}")

    # ── 4. 指数股息率 ──
    print("\n── 4. 股息率相关 ──\n")
    try:
        div = pro.index_dailybasic(
            ts_code=idx_code,
            trade_date=datetime.now().strftime("%Y%m%d"),
        )
        if not div.empty:
            for col in div.columns:
                if "div" in col.lower() or "yield" in col.lower():
                    print(f"  {col}: {div.iloc[0][col]}")
        else:
            print("  ⚠️ 无当日数据，查最近...")
            div = pro.index_dailybasic(
                ts_code=idx_code,
                start_date="20260601",
                end_date=datetime.now().strftime("%Y%m%d"),
            )
            if not div.empty:
                div = div.sort_values("trade_date").iloc[-1]
                for col in div.index:
                    if "div" in col.lower() or "yield" in col.lower():
                        print(f"  {col}: {div[col]}")
    except Exception as e:
        print(f"  ⚠️ 股息率获取失败: {e}")

    # ── 5. 用现有数据估算 ──
    print("\n── 5. 515180 ETF 层面 ──\n")
    try:
        etf_info = pro.fund_basic(ts_code=ts_code, fields="ts_code,name,found_date,management,a_custodian")
        if not etf_info.empty:
            row = etf_info.iloc[0]
            print(f"  名称: {row['name']}")
            print(f"  成立: {row['found_date']}")
            print(f"  管理: {row['management']} / {row.get('a_custodian', '')}")
    except Exception as e:
        print(f"  ⚠️ ETF信息获取失败: {e}")

    # ── 6. 指数行情概览 ──
    print("\n── 6. 指数近期行情 ──\n")
    try:
        idx_quote = pro.index_daily(
            ts_code=idx_code,
            start_date="20260501",
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        if not idx_quote.empty:
            idx_quote = idx_quote.sort_values("trade_date")
            recent = idx_quote.tail(5)
            for _, row in recent.iterrows():
                chg = row.get("pct_chg", 0)
                print(f"  {row['trade_date']}: close={row['close']:.2f}  chg={chg:+.2f}%")
    except Exception as e:
        print(f"  ⚠️ 行情获取失败: {e}")

    print("\n" + "=" * 65)
    print("  完成")
    print("=" * 65)


if __name__ == "__main__":
    main()
