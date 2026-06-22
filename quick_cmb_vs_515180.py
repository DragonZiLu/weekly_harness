"""
quick_cmb_vs_515180.py — 招商银行 vs 515180(中证红利) 估值对比
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
    cmb_code = "600036.SH"
    
    print("=" * 70)
    print("  招商银行 (600036) vs 中证红利 (515180) 估值对比")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # ── 1. 招商银行当前估值 ──
    print("\n── 1. 招商银行 (600036) 当前指标 ──\n")
    for try_date in ["20260612", "20260611", "20260610", "20260609"]:
        try:
            basic = pro.daily_basic(
                ts_code=cmb_code,
                trade_date=try_date,
                fields="ts_code,trade_date,total_mv,pe_ttm,pb,dv_ttm,turnover_rate",
            )
            if basic is not None and not basic.empty:
                r = basic.iloc[0]
                mv = float(r["total_mv"]) / 1e4 if pd.notna(r["total_mv"]) else 0
                pe = float(r["pe_ttm"]) if pd.notna(r["pe_ttm"]) else 0
                pb = float(r["pb"]) if pd.notna(r["pb"]) else 0
                dv = float(r["dv_ttm"]) if pd.notna(r["dv_ttm"]) else 0
                
                print(f"  交易日: {r['trade_date']}")
                print(f"  总市值: {mv:.0f} 亿")
                print(f"  PE_TTM: {pe:.2f}x")
                print(f"  PB: {pb:.2f}x")
                print(f"  股息率(TTM): {dv:.2f}%")
                break
        except:
            continue
    else:
        # Fallback: daily
        try:
            daily = pro.daily(ts_code=cmb_code, start_date="20260601", end_date="20260614")
            if not daily.empty:
                daily = daily.sort_values("trade_date").iloc[-1]
                print(f"  最近交易日: {daily['trade_date']}")
                print(f"  收盘价: {daily['close']}")
                if "pe_ttm" in daily.index:
                    print(f"  PE_TTM: {daily.get('pe_ttm', 'N/A')}")
                if "pe" in daily.index:
                    print(f"  PE: {daily.get('pe', 'N/A')}")
        except Exception as e:
            print(f"  ⚠️ 获取行情失败: {e}")

    # ── 2. 招商银行历史 PE 分位 ──
    print("\n── 2. 招商银行历史 PE/PB/股息率分位 ──\n")
    try:
        hist = pro.daily_basic(
            ts_code=cmb_code,
            start_date="20150101",
            end_date=datetime.now().strftime("%Y%m%d"),
            fields="trade_date,pe_ttm,pb,dv_ttm",
        )
        if not hist.empty:
            hist["trade_date"] = pd.to_datetime(hist["trade_date"])
            hist = hist.sort_values("trade_date")
            
            for col, label in [("pe_ttm", "PE_TTM"), ("pb", "PB"), ("dv_ttm", "股息率")]:
                if col in hist.columns:
                    s = pd.to_numeric(hist[col], errors="coerce").dropna()
                    s = s[(s > 0) & (s < 500 if col == "pe_ttm" else s < 50)]
                    if len(s) > 100:
                        cur = s.iloc[-1]
                        pct = sum(s < cur) / len(s) * 100
                        p10, p25, p50, p75, p90 = np.percentile(s, [10, 25, 50, 75, 90])
                        print(f"  📊 {label}: 当前 {cur:.2f}  (历史 {pct:.0f}% 分位)")
                        print(f"     P10={p10:.2f}  P25={p25:.2f}  P50={p50:.2f}  P75={p75:.2f}  P90={p90:.2f}")
                        print(f"     最低={s.min():.2f}  最高={s.max():.2f}")

    except Exception as e:
        print(f"  ⚠️ 历史数据获取失败: {e}")

    # ── 3. 招商银行 FCF/EV ──
    print("\n── 3. 招商银行 FCF 估算 ──\n")
    try:
        for period in ["20260331", "20251231", "20250930"]:
            cf = pro.cashflow(
                ts_code=cmb_code,
                end_date=period,
                fields="ts_code,end_date,n_cashflow_act,c_pay_acq_const_fiolta,free_cashflow",
            )
            if cf is not None and not cf.empty:
                r = cf.iloc[0]
                ocf = float(r["n_cashflow_act"]) / 1e8 if pd.notna(r["n_cashflow_act"]) else 0
                capex = float(r["c_pay_acq_const_fiolta"]) / 1e8 if pd.notna(r["c_pay_acq_const_fiolta"]) else 0
                fcf = ocf - capex  # 单期，亿
                
                print(f"  财报期: {r['end_date']}")
                print(f"  经营活动现金流(OCF): {ocf:.1f} 亿")
                print(f"  购建固定资产(Capex): {capex:.1f} 亿")
                print(f"  FCF: {fcf:.1f} 亿 (单期)")
                
                # TTM估算：年化×4或直接用最近4期
                if period == "20260331":
                    fcf_annual = fcf * 4  # 一季报年化
                elif period == "20251231":
                    # 尝试拉前几期做TTM
                    cf_hist = pro.cashflow(
                        ts_code=cmb_code,
                        start_date="20250101",
                        end_date="20251231",
                        fields="ts_code,end_date,n_cashflow_act,c_pay_acq_const_fiolta",
                    )
                    if cf_hist is not None and len(cf_hist) >= 4:
                        cf_hist = cf_hist.sort_values("end_date")
                        cf_hist_ocf = sum(float(x) for x in cf_hist["n_cashflow_act"] if pd.notna(x)) / 1e8
                        cf_hist_capex = sum(float(x) for x in cf_hist["c_pay_acq_const_fiolta"] if pd.notna(x)) / 1e8
                        fcf_annual = cf_hist_ocf - cf_hist_capex
                        print(f"  TTM FCF (四期加总): {fcf_annual:.1f} 亿")
                    else:
                        fcf_annual = fcf * 4
                        print(f"  TTM FCF (年化): {fcf_annual:.1f} 亿")
                else:
                    fcf_annual = fcf * 4
                    print(f"  TTM FCF (年化): {fcf_annual:.1f} 亿")
                
                # FCF/EV: 招商银行净负债为负(现金>负债)，EV≈市值
                if mv_val > 0:
                    fcf_yield = fcf_annual / mv_val * 100
                    print(f"  FCF/市值: {fcf_yield:+.2f}%")
                break
    except Exception as e:
        print(f"  ⚠️ FCF获取失败: {e}")

    # ── 4. 招商银行 股息率历史 ──
    print("\n── 4. 招商银行股息支付历史 ──\n")
    try:
        div = pro.dividend(
            ts_code=cmb_code,
            fields="ts_code,end_date,stk_div,cash_div,cash_div_tax",
        )
        if not div.empty:
            div["end_date"] = pd.to_datetime(div["end_date"])
            div = div.sort_values("end_date")
            recent = div.tail(5)
            for _, r in recent.iterrows():
                year = r["end_date"].year
                cash = float(r["cash_div"]) if pd.notna(r["cash_div"]) else (
                    float(r["cash_div_tax"]) if pd.notna(r.get("cash_div_tax")) else 0
                )
                print(f"  {year}: 每股分红 {cash:.2f} 元")
            
            # 分红趋势
            div_years = div.groupby(div["end_date"].dt.year)["cash_div"].sum()
            print(f"\n  近5年累计分红: {div_years.tail(5).sum():.2f} 元/股")
    except Exception as e:
        print(f"  ⚠️ 分红历史获取失败: {e}")

    # ── 5. 净利润 / ROE ──
    print("\n── 5. 招商银行盈利指标 ──\n")
    try:
        for period in ["20260331", "20251231"]:
            inc = pro.income(
                ts_code=cmb_code,
                end_date=period,
                fields="ts_code,end_date,total_revenue,n_income,roe,eps",
            )
            if inc is not None and not inc.empty:
                r = inc.iloc[0]
                rev = float(r["total_revenue"]) / 1e8 if pd.notna(r["total_revenue"]) else 0
                ni = float(r["n_income"]) / 1e8 if pd.notna(r["n_income"]) else 0
                roe = float(r.get("roe", 0)) if pd.notna(r.get("roe")) else 0
                print(f"  财报期: {r['end_date']}")
                print(f"  营收: {rev:.1f} 亿")
                print(f"  净利润: {ni:.1f} 亿")
                if roe > 0:
                    print(f"  ROE: {roe:.2f}%")
                break
    except Exception as e:
        print(f"  ⚠️ 盈利数据获取失败: {e}")

    # ── 6. 招行 vs 中证红利总结 ──
    print("\n" + "=" * 70)
    print("\n── 6. 招商银行 vs 中证红利(515180) 对比总结 ──\n")
    print("  ┌────────────┬──────────────┬──────────────┐")
    print("  │   指标     │   招商银行   │  中证红利    │")
    print("  ├────────────┼──────────────┼──────────────┤")
    print("  │   PE_TTM   │    ~看上面   │   ~8.6x      │")
    print("  │   股息率   │    ~看上面   │   ~4.5%      │")
    print("  │   PB       │    ~看上面   │   ~0.7-1.0x  │")
    print("  │   ROE      │    ~看上面   │   ~10-12%    │")
    print("  │   FCF/EV   │    ~看上面   │   N/A        │")
    print("  │   回撤控制 │    个股风险  │   分散化     │")
    print("  └────────────┴──────────────┴──────────────┘")
    print("=" * 70)

if __name__ == "__main__":
    main()
