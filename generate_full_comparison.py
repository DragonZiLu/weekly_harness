#!/usr/bin/env python3
"""
generate_full_comparison.py — 全面对比报告
D版/B版 vs 932368(800现金流TR) vs H00922(中证红利TR) vs H00300(沪深300TR) vs ZZ800
时间对齐: 2014-2026.4 (用户参考数据12.4年)
"""
import sys, json, os, time
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))
ROOT = Path(__file__).parent

# ──────────────────────────────────────────
# 加载指数数据
# ──────────────────────────────────────────
def load_idx(code):
    df = pd.read_csv(ROOT / 'data/index_daily' / f'{code}.csv')
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df

def annual_return(idx_df, year):
    y_start = f"{year}0102"
    y_end = f"{year}1231"
    start_rows = idx_df[idx_df['trade_date'] >= y_start]
    end_rows = idx_df[(idx_df['trade_date'] >= y_start) & (idx_df['trade_date'] <= y_end)]
    if start_rows.empty or end_rows.empty:
        return None
    s = start_rows.iloc[0]['close']
    e = end_rows.iloc[-1]['close']
    return (e / s - 1)

def get_idx_close(idx_df, date_str):
    """获取某日最近收盘价"""
    key = date_str.replace("-", "")
    rows = idx_df[idx_df['trade_date'] >= key]
    if rows.empty:
        rows = idx_df[idx_df['trade_date'] <= key]
        if rows.empty: return None
        return rows.iloc[-1]['close']
    return rows.iloc[0]['close']

def max_dd_from_nav(nav_series):
    peak = nav_series[0]
    dd = 0
    for n in nav_series:
        if n > peak: peak = n
        dd = max(dd, (peak - n) / peak)
    return dd

def annualized_return(total_ret, n_years):
    if total_ret <= -1 or n_years <= 0: return 0
    return (1 + total_ret) ** (1/n_years) - 1

def sharpe(annual_ret, annual_vol, rf=0.02):
    return (annual_ret - rf) / annual_vol if annual_vol > 0 else 0

# ──────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────
def main():
    print("=" * 70)
    print("全面对比报告: B版/D版 vs 指数基准 (2014-2026)")
    print("=" * 70)
    
    # 加载指数
    idx_932368 = load_idx('932368.CSI')   # 800现金流(含股息TR)
    idx_h00922 = load_idx('H00922.CSI')   # 中证红利全收益
    idx_h00300 = load_idx('H00300.CSI')   # 沪深300全收益
    idx_000906 = load_idx('000906.SH')     # ZZ800价格
    idx_000300 = load_idx('000300.SH')     # HS300价格
    
    print(f"  932368: {len(idx_932368)}条 ({idx_932368['trade_date'].min()}~{idx_932368['trade_date'].max()})")
    print(f"  H00922: {len(idx_h00922)}条 ({idx_h00922['trade_date'].min()}~{idx_h00922['trade_date'].max()})")
    print(f"  H00300: {len(idx_h00300)}条 ({idx_h00300['trade_date'].min()}~{idx_h00300['trade_date'].max()})")
    
    # ── 1. 构建全周期指数NAV (起始=1.0, 2014-01-02) ──
    start_date = "20140102"
    end_date = "20260605"
    
    def build_idx_nav(idx_df, start, end):
        """构建指数累计NAV序列(含分红再投资=全收益)"""
        rows = idx_df[(idx_df['trade_date'] >= start) & (idx_df['trade_date'] <= end)]
        if rows.empty: return pd.DataFrame()
        base_close = rows.iloc[0]['close']
        rows = rows.copy()
        rows['nav'] = rows['close'] / base_close
        return rows[['trade_date', 'close', 'nav', 'pct_chg']].reset_index(drop=True)
    
    nav_932368 = build_idx_nav(idx_932368, start_date, end_date)
    nav_h00922 = build_idx_nav(idx_h00922, start_date, end_date)
    nav_h00300 = build_idx_nav(idx_h00300, start_date, end_date)
    nav_000906 = build_idx_nav(idx_000906, start_date, end_date)
    
    # ── 2. 计算我们的策略NAV(从2016开始, 之前=1.0) ──
    b_nav = pd.read_csv(ROOT / 'output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv')
    d_nav = pd.read_csv(ROOT / 'output/zz800_fcf_lenient_buffer/backtest_nav_tr.csv')
    
    # 策略NAV起始=1.0(2014), 2014-2016按ZZ800全收益增长(无策略), 2016开始策略
    # 简化: 2014-2016Q2用932368(800现金流TR)的同期增长
    ret_2014_2016 = 1.0
    for y in [2014, 2015]:
        r = annual_return(idx_932368, y)
        if r: ret_2014_2016 *= (1 + r)
    r_2016h1 = annual_return(idx_932368, 2016)  # 2016全年
    if r_2016h1:
        # 策略从2016-06-13开始, 约上半年
        ret_2014_2016 *= (1 + r_2016h1 * 0.5)  # 近似
    
    # ── 3. 核心对比表 ──
    n_years = 12.4  # 2014初~2026.4
    
    # 指标计算
    final_932368 = nav_932368.iloc[-1]['nav']
    final_h00922 = nav_h00922.iloc[-1]['nav']
    final_h00300 = nav_h00300.iloc[-1]['nav']
    final_000906 = nav_000906.iloc[-1]['nav']
    
    cagr_932368 = annualized_return(final_932368 - 1, n_years) * 100
    cagr_h00922 = annualized_return(final_h00922 - 1, n_years) * 100
    cagr_h00300 = annualized_return(final_h00300 - 1, n_years) * 100
    
    # 策略: 从2016-06开始, 约10年
    d_final = d_nav.iloc[-1]['nav']
    b_final = b_nav.iloc[-1]['nav']
    d_cagr = annualized_return(d_final - 1, 10.0) * 100
    b_cagr = annualized_return(b_final - 1, 10.0) * 100
    
    # 最大回撤
    dd_932368 = max_dd_from_nav(nav_932368['nav'].values) * 100
    dd_h00922 = max_dd_from_nav(nav_h00922['nav'].values) * 100
    dd_h00300 = max_dd_from_nav(nav_h00300['nav'].values) * 100
    dd_000906 = max_dd_from_nav(nav_000906['nav'].values) * 100
    
    b_rets = b_nav['ret'].values * 100
    d_rets = d_nav['ret'].values * 100
    dd_b = max_dd_from_nav(b_nav['nav'].values) * 100
    dd_d = max_dd_from_nav(d_nav['nav'].values) * 100
    vol_b = np.std(b_rets) * np.sqrt(4)
    vol_d = np.std(d_rets) * np.sqrt(4)
    
    # 年化波动率(pct_chg单位=百分比如1.43%, /100转小数)
    vol_932368 = nav_932368['pct_chg'].dropna().std() * np.sqrt(252) / 100  # → 0.2267
    vol_h00922 = nav_h00922['pct_chg'].dropna().std() * np.sqrt(252) / 100
    vol_h00300 = nav_h00300['pct_chg'].dropna().std() * np.sqrt(252) / 100
    
    # 夏普(参数均为小数)
    sp_932368 = sharpe(cagr_932368/100, vol_932368)
    sp_h00922 = sharpe(cagr_h00922/100, vol_h00922)
    sp_h00300 = sharpe(cagr_h00300/100, vol_h00300)
    sp_b = sharpe(b_cagr/100, vol_b/100)
    sp_d = sharpe(d_cagr/100, vol_d/100)
    
    # ── 4. 逐年净值表(100万起始) ──
    year_end_dates = {
        2014: "20141231", 2015: "20151231", 2016: "20161230",
        2017: "20171229", 2018: "20181228", 2019: "20191231",
        2020: "20201231", 2021: "20211231", 2022: "20221230",
        2023: "20231229", 2024: "20241231", 2025: "20251231",
        2026: "20260605",
    }
    
    def get_idx_nav_at(idx_nav_df, date_key):
        rows = idx_nav_df[idx_nav_df['trade_date'] <= date_key]
        if rows.empty: return None
        return rows.iloc[-1]['nav']
    
    yearly_data = []
    for y in sorted(year_end_dates.keys()):
        d = year_end_dates[y]
        
        n9 = get_idx_nav_at(nav_932368, d)
        nh = get_idx_nav_at(nav_h00922, d)
        n3 = get_idx_nav_at(nav_h00300, d)
        nz = get_idx_nav_at(nav_000906, d)
        
        # 策略累计NAV(归一化到2014初=1.0)
        # 2014-2016H1: 用932368的增长率, 之后用策略NAV
        nav_932368_jun2016 = get_idx_nav_at(nav_932368, "20160630")
        nav_932368_2014start = get_idx_nav_at(nav_932368, "20140102")
        bridge_factor = nav_932368_jun2016 / nav_932368_2014start if nav_932368_jun2016 and nav_932368_2014start else 1.0
        
        # 策略NAV放大(bridge_factor * strategy_nav)
        d_nav_val = None
        b_nav_val = None
        if y < 2016:
            d_nav_val = n9  # 无策略期, 用800现金流TR
            b_nav_val = n9
        elif y == 2016:
            # 2016年: 上半年用932368, 下半年用策略
            d_nav_val = bridge_factor * (d_nav.iloc[0]['nav'] if len(d_nav) > 0 else 1.0)
            b_nav_val = bridge_factor * (b_nav.iloc[0]['nav'] if len(b_nav) > 0 else 1.0)
        
        yearly_data.append({
            'year': y,
            'nav_932368': n9, 'nav_h00922': nh, 'nav_h00300': n3,
            'nav_d': d_nav_val, 'nav_b': b_nav_val,
            'ret_932368': None, 'ret_h00922': None, 'ret_h00300': None,
        })
    
    # 计算年度收益率
    prev_navs = {'932368': 1.0, 'h00922': 1.0, 'h00300': 1.0}
    for yd in yearly_data:
        for key in ['932368', 'h00922', 'h00300']:
            nav_key = f'nav_{key}'
            if yd[nav_key] is not None:
                if prev_navs[key] > 0:
                    yd[f'ret_{key}'] = (yd[nav_key] / prev_navs[key] - 1) * 100
                prev_navs[key] = yd[nav_key]
    
    # 策略年度收益: 从NAV文件中按年汇总
    def strategy_annual(nav_df):
        ann = {}
        for i in range(len(nav_df)):
            rb = str(nav_df.iloc[i]['rb_date'])[:4]
            nxt_yr = str(nav_df.iloc[i]['next_rb'])[:4]
            ret = nav_df.iloc[i]['ret']
            # 分配到对应自然年
            year = rb
            if year not in ann: ann[year] = []
            ann[year].append(ret)
        
        result = {}
        for yr, rets in ann.items():
            result[yr] = (1 + np.array(rets)).prod() - 1  # 累计收益
        return result
    
    b_annual = strategy_annual(b_nav)
    d_annual = strategy_annual(d_nav)
    
    # ── 5. 分阶段年化 ──
    def period_cagr(idx_nav_df, start_d, end_d):
        s = get_idx_nav_at(idx_nav_df, start_d)
        e = get_idx_nav_at(idx_nav_df, end_d)
        if s is None or e is None or s == 0: return None
        # 计算年数
        sd = datetime.strptime(start_d, "%Y%m%d")
        ed = datetime.strptime(end_d, "%Y%m%d")
        ny = (ed - sd).days / 365.25
        if ny <= 0: return None
        return annualized_return(e / s - 1, ny) * 100
    
    stages = [
        ("2014-2015 牛市", "20140102", "20151231"),
        ("2016-2019 价值回归", "20160104", "20191231"),
        ("2020-2022 结构分化", "20200102", "20221230"),
        ("2023-2026 当前", "20230103", "20260605"),
        ("2014-2026 全周期", "20140102", "20260605"),
    ]
    
    # ── 6. 生成报告 ──
    report = f"""# ZZ800 FCF 精选策略 vs 指数基准 全面对比报告

> **回测起点**: 2014年初 (100万) → 2026年4月 ({n_years}年)
> **策略**: D版(B版+缓冲区±20%) | B版(宽松OCF+total_mv+TTM)
> **基准**: 932368(800现金流TR) | H00922(中证红利TR) | H00300(沪深300TR)

---

## 一、核心对比 (2014年初100万 → 2026.4)

| 策略 | 最终市值 | 总收益 | 年化CAGR | 最大回撤 | 夏普 |
|------|---------|--------|---------|---------|------|
| ★ D版(缓冲区) | {d_final*100:.0f}万 | +{(d_final-1)*100:.0f}% | {d_cagr:.1f}% | -{dd_d:.1f}% | {sp_d:.2f} |
| B版(宽松OCF) | {b_final*100:.0f}万 | +{(b_final-1)*100:.0f}% | {b_cagr:.1f}% | -{dd_b:.1f}% | {sp_b:.2f} |
| 932368 800现金流TR | {final_932368*100:.0f}万 | +{(final_932368-1)*100:.0f}% | {cagr_932368:.1f}% | -{dd_932368:.1f}% | {sp_932368:.2f} |
| H00922 中证红利TR | {final_h00922*100:.0f}万 | +{(final_h00922-1)*100:.0f}% | {cagr_h00922:.1f}% | -{dd_h00922:.1f}% | {sp_h00922:.2f} |
| H00300 沪深300TR | {final_h00300*100:.0f}万 | +{(final_h00300-1)*100:.0f}% | {cagr_h00300:.1f}% | -{dd_h00300:.1f}% | {sp_h00300:.2f} |

> 注: D版/B版从2016-06开始有策略, 2014-2016H1按932368同期增长

## 二、逐年净值 (起始100万)

| 年份 | D版 | 当年% | B版 | 当年% | 800现金流TR | 当年% | 中证红利TR | 当年% | 沪深300TR | 当年% |
|------|-----|-------|-----|-------|------------|-------|-----------|-------|----------|-------|
"""
    
    # 计算逐年NAV和收益率
    # 需要为策略计算累计NAV(2014起始)
    d_cum_nav = 1.0  # 2014年初=1.0
    b_cum_nav = 1.0
    
    for yd in yearly_data:
        y = yd['year']
        r9 = yd.get('ret_932368')
        rh = yd.get('ret_h00922')
        r3 = yd.get('ret_h00300')
        
        # 策略年度收益
        d_r = d_annual.get(str(y))
        b_r = b_annual.get(str(y))
        
        # 计算策略累计NAV
        if y < 2016:
            # 无策略期, 用932368增长
            d_cum_nav = yd['nav_932368'] if yd['nav_932368'] else d_cum_nav
            b_cum_nav = d_cum_nav
        else:
            if d_r is not None:
                d_cum_nav *= (1 + d_r)
            if b_r is not None:
                b_cum_nav *= (1 + b_r)
        
        yd['d_cum_nav'] = d_cum_nav
        yd['b_cum_nav'] = b_cum_nav
        yd['d_ret'] = d_r * 100 if d_r is not None else (r9 if y < 2016 else None)
        yd['b_ret'] = b_r * 100 if b_r is not None else (r9 if y < 2016 else None)
        
        d_str = f"{yd['d_ret']:+.1f}%" if yd['d_ret'] is not None else "-"
        b_str = f"{yd['b_ret']:+.1f}%" if yd['b_ret'] is not None else "-"
        r9_str = f"{r9:+.1f}%" if r9 is not None else "-"
        rh_str = f"{rh:+.1f}%" if rh is not None else "-"
        r3_str = f"{r3:+.1f}%" if r3 is not None else "-"
        
        # 市值
        d_val = f"{d_cum_nav*100:.0f}万"
        b_val = f"{b_cum_nav*100:.0f}万"
        
        # 找到当年最高收益
        all_rets = {'D版': yd['d_ret'], 'B版': yd['b_ret'], '800现金流': r9, '红利': rh, '300': r3}
        best = max(all_rets, key=lambda k: all_rets[k] if all_rets[k] is not None else -999)
        d_mark = " 🏆" if best == 'D版' and yd['d_ret'] is not None else ""
        
        report += f"| {y} | {d_val}{d_mark} | {d_str} | {b_val} | {b_str} | {yd['nav_932368']*100:.0f}万 | {r9_str} | {yd['nav_h00922']*100:.0f}万 | {rh_str} | {yd['nav_h00300']*100:.0f}万 | {r3_str} |\n"
    
    # ── 分阶段年化 ──
    report += "\n## 三、分阶段年化\n\n"
    report += "| 阶段 | D版 | B版 | 800现金流 | 中证红利 | 沪深300 |\n"
    report += "|------|-----|-----|----------|---------|--------|\n"
    
    # 策略分阶段年化(从d_annual/b_annual计算)
    def strategy_period_cagr(ann_rets, start_year, end_year):
        """从年度累计收益计算分阶段CAGR"""
        cum = 1.0
        n = 0
        for yr in range(start_year, end_year + 1):
            r = ann_rets.get(str(yr))
            if r is not None:
                cum *= (1 + r)
                n += 1
        if n == 0: return None
        ny = n  # 约1年/年
        return annualized_return(cum - 1, ny) * 100
    
    for label, sd, ed in stages:
        c9 = period_cagr(nav_932368, sd, ed)
        ch = period_cagr(nav_h00922, sd, ed)
        c3 = period_cagr(nav_h00300, sd, ed)
        
        # 策略: 从start_year到end_year
        sy = int(sd[:4])
        ey = int(ed[:4])
        
        # 策略仅从2016开始, 2014-2015用932368
        if sy < 2016:
            # 策略这部分用932368
            d_c = c9  # 同932368
            b_c = c9
            if ey >= 2016:
                # 混合: 2014-2015用932368, 2016+用策略
                d_c = None  # 混合计算复杂, 标为-
                b_c = None
        else:
            d_c = strategy_period_cagr(d_annual, sy, ey)
            b_c = strategy_period_cagr(b_annual, sy, ey)
        
        d_str = f"{d_c:.1f}%" if d_c is not None else "-"
        b_str = f"{b_c:.1f}%" if b_c is not None else "-"
        
        # 标🏆
        vals = {'D版': d_c, 'B版': b_c, '800现金流': c9, '红利': ch, '300': c3}
        best = max(vals, key=lambda k: vals[k] if vals[k] is not None else -999)
        
        report += f"| {label} | {d_str} | {b_str} | {c9:.1f}% | {ch:.1f}% | {c3:.1f}% |\n"
    
    # ── 风险指标对比 ──
    report += f"""
## 四、风险指标对比

| 指标 | D版 | B版 | 800现金流 | 中证红利 | 沪深300 |
|------|-----|-----|----------|---------|--------|
| 最大回撤 | -{dd_d:.1f}% | -{dd_b:.1f}% | -{dd_932368:.1f}% | -{dd_h00922:.1f}% | -{dd_h00300:.1f}% |
| 年化波动 | {vol_d:.1f}% | {vol_b:.1f}% | {vol_932368*100:.1f}% | {vol_h00922*100:.1f}% | {vol_h00300*100:.1f}% |
| 夏普比率 | {sp_d:.2f} | {sp_b:.2f} | {sp_932368:.2f} | {sp_h00922:.2f} | {sp_h00300:.2f} |

## 五、D版超额收益

| 对比基准 | 年化超额 | 累计超额 |
|---------|---------|---------|
| vs 800现金流TR | {(d_cagr-cagr_932368):+.1f}pp | {(d_final-final_932368)*100:+.0f}万 |
| vs 中证红利TR | {(d_cagr-cagr_h00922):+.1f}pp | {(d_final-final_h00922)*100:+.0f}万 |
| vs 沪深300TR | {(d_cagr-cagr_h00300):+.1f}pp | {(d_final-final_h00300)*100:+.0f}万 |
"""
    
    # 保存
    out_path = ROOT / 'docs/zz800_fcf_full_comparison.md'
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {out_path}")

if __name__ == "__main__":
    main()