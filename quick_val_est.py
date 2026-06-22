"""
quick_val_est.py — 快速估值估算：用 800红利成分股 + tushare daily_basic
"""
import sys
from datetime import datetime, timedelta
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
    
    print("=" * 65)
    print("  中证红利 / 800红利 当前估值快照")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)

    # ── A. 直接用 tushare 试图拉 000922 的 daily_basic ──
    print("\n── A. 中证红利指数 000922 的 daily_basic ──")
    for attempt_date in ["20260612", "20260611", "20260610", "20260609"]:
        try:
            basic = pro.index_dailybasic(
                ts_code="000922.SH",
                trade_date=attempt_date,
            )
            if not basic.empty:
                print(f"  成功! trade_date={attempt_date}")
                cols = basic.columns.tolist()
                print(f"  可用字段: {cols}")
                for _, r in basic.iterrows():
                    for c in cols:
                        print(f"    {c}: {r[c]}")
                break
        except Exception as e:
            continue
    else:
        print("  ⚠️ 所有日期均无数据，尝试成分股聚合...")

    # ── B. 成分股聚合 ──
    print("\n── B. 800红利 Top100成分股估值聚合 ──")
    
    # 获取 800红利 最近一次调仓的成分股 (2025-12-15)
    # 使用 dividend_universe 来获取
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        from weekly_harness.dividend_universe import DividendUniverse
        
        div = DividendUniverse(index_code="000906.SH")
        div.preload_all()
        
        # 获取 2025-12-15 的持仓
        basket = div.get_dividend_basket(
            date_str="2025-12-15",
            top_n=100,
            max_turnover=0.20,
        )
        
        codes = list(basket.keys())
        stocks_info = list(basket.values())
        print(f"  800红利持仓: {len(codes)} 只")
        print(f"  前10: {', '.join(codes[:10])}")
        
        # 已有选股时的三年平均股息率
        hist_div_yields = [s["div_yield_3y"] for s in stocks_info]
        print(f"  选股时三年平均股息率: {np.mean(hist_div_yields):.2f}% (中位数 {np.median(hist_div_yields):.2f}%)")
        
        # 权重
        weights = {s["ts_code"]: s.get("weight", 1/len(codes)) for s in stocks_info}
        
        # 批量获取 daily_basic — 逐只查询
        all_basic = []
        failed_codes = []
        for code in codes[:30]:  # 先试前30只
            for try_date in ["20260612", "20260611", "20260610", "20260609", "20260605"]:
                try:
                    basic = pro.daily_basic(
                        ts_code=code,
                        trade_date=try_date,
                        fields="ts_code,trade_date,total_mv,pe_ttm,pb,dv_ttm",
                    )
                    if basic is not None and not basic.empty:
                        all_basic.append(basic)
                        break
                except:
                    continue
            else:
                failed_codes.append(code)
        
        print(f"  获取到 {len(all_basic)} 只股票行情，失败 {len(failed_codes)} 只")
        if failed_codes:
            print(f"  失败代码: {failed_codes[:5]}...")
        
        if all_basic:
            df = pd.concat(all_basic, ignore_index=True)
            df = df.drop_duplicates(subset="ts_code")
            print(f"  获取到 {len(df)} 只行情数据")
            
            # 清理
            for c in ["total_mv", "pe_ttm", "pb", "dv_ttm"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df[df["pe_ttm"].between(0, 500)]
            df = df[df["total_mv"] > 0]
            
            # 加权计算
            w_list = []
            for _, r in df.iterrows():
                w_list.append(weights.get(r["ts_code"], 0))
            w_arr = np.array(w_list)
            w_arr = w_arr / w_arr.sum()  # 归一化
            
            weighted_pe = np.average(df["pe_ttm"].values, weights=w_arr)
            weighted_pb = np.average(df["pb"].values, weights=w_arr)
            weighted_dv = np.average(df["dv_ttm"].values, weights=w_arr) if df["dv_ttm"].notna().any() else None
            
            # 中位数
            med_pe = df["pe_ttm"].median()
            med_pb = df["pb"].median()
            med_dv = df["dv_ttm"].median() if df["dv_ttm"].notna().any() else None
            
            # 总市值
            total_mv = df["total_mv"].sum()
            
            print(f"\n  800红利Top100 成分股估值:")
            print(f"    成分股数: {len(df)} (有行情数据)")
            print(f"    总市值: {total_mv/1e12:.2f} 万亿")
            print(f"    加权PE: {weighted_pe:.2f}x   中位数PE: {med_pe:.2f}x")
            print(f"    加权PB: {weighted_pb:.2f}x   中位数PB: {med_pb:.2f}x")
            if weighted_dv:
                print(f"    加权股息率: {weighted_dv:.2f}%   中位数股息率: {med_dv:.2f}%")
            
            # PE分位（用df本身的分布近似）
            print(f"\n    PE分布:")
            for pct_val in [10, 25, 50, 75, 90]:
                print(f"      P{pct_val}: {np.percentile(df['pe_ttm'], pct_val):.1f}x")
            
            if df["dv_ttm"].notna().any():
                dv_vals = df["dv_ttm"].dropna()
                print(f"\n    股息率分布:")
                for pct_val in [10, 25, 50, 75, 90]:
                    print(f"      P{pct_val}: {np.percentile(dv_vals, pct_val):.2f}%")
            
        else:
            print("  ⚠️ 无法获取成分股行情数据")
    except Exception as e:
        print(f"  ⚠️ 成分股聚合失败: {e}")
        import traceback
        traceback.print_exc()

    # ── C. FCF/EV 估算 ──
    print("\n── C. FCF/EV 估算（前10权重股）──")
    try:
        if 'codes' in dir() and len(codes) > 0:
            top10_codes = codes[:10]
            code_str = ",".join(top10_codes)
            
            cf = pro.cashflow(
                ts_code=code_str,
                end_date="20260331",
                fields="ts_code,end_date,n_cashflow_act,c_pay_acq_const_fiolta",
            )
            if cf is not None and not cf.empty:
                cf = cf.sort_values("end_date", ascending=False)
                # 每个股票取最新一期
                cf["rank"] = cf.groupby("ts_code").cumcount()
                cf = cf[cf["rank"] == 0]
                
                # 获取市值
                mv = pro.daily_basic(
                    ts_code=code_str,
                    trade_date="20260612",
                    fields="ts_code,total_mv",
                )
                
                if not mv.empty:
                    merged = cf.merge(mv, on="ts_code", how="inner")
                    for _, r in merged.iterrows():
                        ocf = float(r["n_cashflow_act"]) / 1e4 if pd.notna(r["n_cashflow_act"]) else 0
                        capex = float(r["c_pay_acq_const_fiolta"]) / 1e4 if pd.notna(r["c_pay_acq_const_fiolta"]) else 0
                        fcf = ocf - capex  # 万 → 亿
                        mv_val = float(r["total_mv"]) / 1e4
                        if mv_val > 0:
                            fcf_ev = fcf / mv_val * 100
                            print(f"    {r['ts_code']}: FCF={fcf:.1f}亿  FCF/MV={fcf_ev:+.1f}%")
    except Exception as e:
        print(f"  ⚠️ FCF计算失败: {e}")
    
    print("\n" + "=" * 65)
    print("  完成")
    print("=" * 65)

if __name__ == "__main__":
    main()
