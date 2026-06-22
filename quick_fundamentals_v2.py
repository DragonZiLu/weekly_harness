"""
quick_fundamentals_v2.py — 515180 / 中证红利指数 当前基本面 + 历史分位
使用 index_dailybasic + 成分股FCF聚合
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
    idx_code = "000922.SH"

    print("=" * 70)
    print("  中证红利指数（000922）→ 515180 基本面快照")
    print(f"  查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # ── 1. PE / PB 历史分位 ──
    print("\n── 1. PE / PB 历史估值分位 ──\n")

    for field, label in [("pe_ttm", "PE_TTM"), ("pb", "PB")]:
        try:
            data = pro.index_dailybasic(
                ts_code=idx_code,
                start_date="20150101",
                end_date=datetime.now().strftime("%Y%m%d"),
                fields=f"trade_date,{field}"
            )
            if data.empty:
                print(f"  ⚠️ {label}: 无数据")
                continue

            data["trade_date"] = pd.to_datetime(data["trade_date"])
            data = data.sort_values("trade_date")
            data[field] = pd.to_numeric(data[field], errors="coerce")
            data = data.dropna(subset=[field])

            if len(data) < 10:
                print(f"  ⚠️ {label}: 数据太少 ({len(data)}条)")
                continue

            current = float(data.iloc[-1][field])
            current_date = data.iloc[-1]["trade_date"].strftime("%Y-%m-%d")

            series = data[field].values
            pct = sum(series < current) / len(series) * 100
            p10, p25, p50, p75, p90 = np.percentile(series, [10, 25, 50, 75, 90])
            vmin, vmax = series.min(), series.max()

            print(f"  📊 {label}（{current_date}）")
            print(f"     当前: {current:.2f}")
            print(f"     历史分位: {pct:.0f}%  "
                  f"（高于 {100-pct:.0f}% 时间处于更低估值）")
            print(f"     分位数: P10={p10:.2f} P25={p25:.2f} P50={p50:.2f} "
                  f"P75={p75:.2f} P90={p90:.2f}")
            print(f"     极值: 最低={vmin:.2f}  最高={vmax:.2f}")

            # 判断
            if pct >= 80:
                judge = "⚠️ 偏贵（高于历史80%时间）"
            elif pct >= 60:
                judge = "⚡ 中等偏贵"
            elif pct >= 40:
                judge = "✅ 合理区间"
            elif pct >= 20:
                judge = "✅ 中等偏低"
            else:
                judge = "💎 便宜（低于历史20%时间）"
            print(f"     判断: {judge}")

        except Exception as e:
            print(f"  ⚠️ {label}: {e}")

    # ── 2. 股息率 ──
    print("\n── 2. 股息率 ──\n")
    try:
        div_data = pro.index_dailybasic(
            ts_code=idx_code,
            start_date="20150101",
            end_date=datetime.now().strftime("%Y%m%d"),
            fields="trade_date,dv_ratio,dv_ratio_ttm"
        )
        if not div_data.empty:
            div_data["trade_date"] = pd.to_datetime(div_data["trade_date"])
            div_data = div_data.sort_values("trade_date")

            for col in ["dv_ratio", "dv_ratio_ttm"]:
                if col in div_data.columns:
                    div_data[col] = pd.to_numeric(div_data[col], errors="coerce")

            latest = div_data.iloc[-1]
            for col, label in [("dv_ratio", "股息率"), ("dv_ratio_ttm", "股息率_TTM")]:
                if col in div_data.columns:
                    s = div_data[col].dropna()
                    if len(s) > 0:
                        cur = float(latest[col]) if pd.notna(latest[col]) else float(s.iloc[-1])
                        pct_val = sum(s < cur) / len(s) * 100
                        p10v, p50v, p90v = np.percentile(s, [10, 50, 90])
                        print(f"  📊 {label}: {cur:.2f}%")
                        print(f"     历史分位: {pct_val:.0f}%")
                        print(f"     分位数: P10={p10v:.2f}% P50={p50v:.2f}% P90={p90v:.2f}%")
                        print(f"     极值: 最低={s.min():.2f}%  最高={s.max():.2f}%")
                        
                        if pct_val <= 20:
                            print(f"     判断: ⚠️ 股息率偏低（红利策略吸引力下降）")
                        elif pct_val >= 80:
                            print(f"     判断: 💎 股息率偏高（红利策略极具吸引力）")
                        else:
                            print(f"     判断: ✅ 中等水平")
        else:
            print("  ⚠️ 无股息率数据")
    except Exception as e:
        print(f"  ⚠️ 股息率获取失败: {e}")

    # ── 3. 获取成分股并计算 FCF/EV ──
    print("\n── 3. 成分股 FCF/EV 估算 ──\n")

    try:
        # 获取指数成分
        members = pro.index_member(index_code="000922")
        if members.empty:
            print("  ⚠️ 无法获取成分股列表")
        else:
            # 取最新一次调仓的成分
            if "in_date" in members.columns:
                members = members.sort_values("in_date")
            codes = members["con_code"].unique().tolist()
            print(f"  成分股总数: {len(codes)}")

            # 批量获取最新财务数据（最近2期季报）
            end_periods = ["20260331", "20251231", "20250930"]
            
            fcf_list = []
            batch_size = 30
            
            for i in range(0, min(len(codes), 100), batch_size):
                batch = codes[i:i+batch_size]
                code_str = ",".join(batch)
                
                for period in end_periods:
                    try:
                        # 现金流
                        cf = pro.cashflow(
                            ts_code=code_str,
                            end_date=period,
                            fields="ts_code,end_date,n_cashflow_act,c_pay_acq_const_fiolta,free_cashflow",
                        )
                        if cf is not None and not cf.empty:
                            break
                    except:
                        continue
                
                if cf is None or cf.empty:
                    continue
                
                # 按ts_code取每个股票最新一期
                cf["end_date"] = pd.to_datetime(cf["end_date"])
                cf = cf.sort_values("end_date").groupby("ts_code").last().reset_index()
                
                for _, row in cf.iterrows():
                    code = row["ts_code"]
                    ocf = float(row["n_cashflow_act"]) if pd.notna(row["n_cashflow_act"]) else 0
                    capex_raw = float(row["c_pay_acq_const_fiolta"]) if pd.notna(row["c_pay_acq_const_fiolta"]) else 0
                    fcf_raw = float(row["free_cashflow"]) if pd.notna(row.get("free_cashflow")) else 0
                    
                    # FCF = OCF - Capex (TTM口径：年化)
                    if ocf != 0 or capex_raw != 0:
                        fcf_ttm = ocf - capex_raw  # 单期数据，粗略估算
                        fcf_list.append({
                            "code": code,
                            "ocf": ocf,
                            "capex": capex_raw,
                            "fcf": fcf_ttm,
                        })
            
            if fcf_list:
                df_fcf = pd.DataFrame(fcf_list)
                # 获取市值
                total_mv = 0
                total_fcf_val = 0
                fcf_positive = 0
                
                for _, row in df_fcf.iterrows():
                    code = row["code"]
                    try:
                        daily = pro.daily_basic(
                            ts_code=code,
                            trade_date=datetime.now().strftime("%Y%m%d"),
                            fields="ts_code,total_mv",
                        )
                        if not daily.empty:
                            mv = float(daily.iloc[0]["total_mv"]) / 1e4  # 万元→亿
                            total_mv += mv
                            if row["fcf"] > 0:
                                fcf_positive += 1
                                total_fcf_val += row["fcf"]
                    except:
                        continue
                
                n_stocks = len(df_fcf)
                print(f"  有财务数据: {n_stocks} 只")
                print(f"  FCF>0: {fcf_positive}/{n_stocks} "
                      f"({fcf_positive/n_stocks*100:.0f}%)")
                
                if total_mv > 0:
                    # 用OCF和Capex都是单期（万元），需要换算
                    avg_fcf_ratio = total_fcf_val / (total_mv * 1e4) * 100  # 粗略
                    print(f"  覆盖市值: {total_mv:.0f} 亿")
                    
                    # 统计FCF/EV分布
                    fcfs = df_fcf["fcf"].values
                    print(f"  FCF统计（单期，亿元）:")
                    print(f"    中位数: {np.median(fcfs):.1f}")
                    print(f"    均值: {np.mean(fcfs):.1f}")
                    print(f"    P25: {np.percentile(fcfs, 25):.1f}  "
                          f"P75: {np.percentile(fcfs, 75):.1f}")
            else:
                print("  ⚠️ 无FCF数据")
                
    except Exception as e:
        print(f"  ⚠️ FCF计算失败: {e}")
        import traceback
        traceback.print_exc()

    # ── 4. 总结 ──
    print("\n" + "=" * 70)

    # 汇总输出
    print("\n── 汇总判断 ──\n")
    
    # 尝试再拉一次pe_ttm用于总结
    try:
        pe_data = pro.index_dailybasic(
            ts_code=idx_code,
            start_date="20150101",
            end_date=datetime.now().strftime("%Y%m%d"),
            fields="trade_date,pe_ttm"
        )
        if not pe_data.empty:
            pe_data["pe_ttm"] = pd.to_numeric(pe_data["pe_ttm"], errors="coerce")
            pe_data = pe_data.dropna(subset=["pe_ttm"])
            if len(pe_data) > 0:
                cur_pe = float(pe_data.iloc[-1]["pe_ttm"])
                pe_pct = sum(pe_data["pe_ttm"] < cur_pe) / len(pe_data) * 100
                print(f"  PE_TTM: {cur_pe:.2f}x  (历史 {pe_pct:.0f}% 分位)")
    except:
        pass

    try:
        pb_data = pro.index_dailybasic(
            ts_code=idx_code,
            start_date="20150101",
            end_date=datetime.now().strftime("%Y%m%d"),
            fields="trade_date,pb"
        )
        if not pb_data.empty:
            pb_data["pb"] = pd.to_numeric(pb_data["pb"], errors="coerce")
            pb_data = pb_data.dropna(subset=["pb"])
            if len(pb_data) > 0:
                cur_pb = float(pb_data.iloc[-1]["pb"])
                pb_pct = sum(pb_data["pb"] < cur_pb) / len(pb_data) * 100
                print(f"  PB:     {cur_pb:.2f}x  (历史 {pb_pct:.0f}% 分位)")
    except:
        pass

    try:
        div_data = pro.index_dailybasic(
            ts_code=idx_code,
            start_date="20150101",
            end_date=datetime.now().strftime("%Y%m%d"),
            fields="trade_date,dv_ratio"
        )
        if not div_data.empty:
            div_data["dv_ratio"] = pd.to_numeric(div_data["dv_ratio"], errors="coerce")
            div_data = div_data.dropna(subset=["dv_ratio"])
            if len(div_data) > 0:
                cur_dv = float(div_data.iloc[-1]["dv_ratio"])
                dv_pct = sum(div_data["dv_ratio"] < cur_dv) / len(div_data) * 100
                print(f"  股息率:  {cur_dv:.2f}%  (历史 {dv_pct:.0f}% 分位)")
    except:
        pass

    print("\n" + "=" * 70)
    print("  完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
