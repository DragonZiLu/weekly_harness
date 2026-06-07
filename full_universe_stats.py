#!/usr/bin/env python3
"""full_universe_stats.py — 沪深300 & ZZ800 全成分FCF统计（不做Top50筛选）
对每个调仓日统计：
  - 满足筛选条件的公司数量
  - FCF加和（亿元）
  - (OCF−营业利润) 加和（亿元）
  - 计算全成分FCF加权NAV（10%封顶）与B版对比
"""
import sys, json, time, os
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from fcf_universe import FcfUniverse
from compute_nav_cached import get_adj_close_cached

REBALANCE_DATES = [
    "2015-03-16","2015-06-15","2015-09-14","2015-12-14",
    "2016-03-14","2016-06-13","2016-09-12","2016-12-12",
    "2017-03-13","2017-06-12","2017-09-11","2017-12-11",
    "2018-03-12","2018-06-11","2018-09-17","2018-12-17",
    "2019-03-11","2019-06-17","2019-09-16","2019-12-16",
    "2020-03-16","2020-06-15","2020-09-14","2020-12-14",
    "2021-03-15","2021-06-14","2021-09-13","2021-12-13",
    "2022-03-14","2022-06-13","2022-09-12","2022-12-12",
    "2023-03-13","2023-06-12","2023-09-11","2023-12-11",
    "2024-03-11","2024-06-17","2024-09-16","2024-12-16",
    "2025-03-17","2025-06-16","2025-09-15","2025-12-15",
    "2026-03-16","2026-06-15",
]

CAP = 0.10   # 单股10%封顶

def fcf_weights(stocks, cap=CAP, max_iter=100):
    """FCF绝对值加权 + 10%封顶迭代重分配"""
    if not stocks: return stocks
    fcf_vals = [max(s.get('fcf', 0), 0) for s in stocks]
    total = sum(fcf_vals)
    if total <= 0:
        w = 1.0 / len(stocks)
        for s in stocks: s['weight'] = round(w, 6)
        return stocks
    weights = [v / total for v in fcf_vals]
    for _ in range(max_iter):
        overflow = sum(w - cap for w in weights if w > cap)
        if overflow < 1e-9: break
        capped = [min(w, cap) for w in weights]
        below = sum(c for c in capped if c < cap)
        if below <= 0: break
        weights = [min(c + overflow*(c/below), cap) if c < cap else cap for c in capped]
    total_w = sum(weights)
    for s, w in zip(stocks, weights):
        s['weight'] = round(w / total_w, 6)
    return stocks

def get_full_basket(uni, date_str, top_n=None):
    """获取全成分篮子（不做Top50筛选），top_n=None表示取所有通过筛选的"""
    # 先取一个大候选池（比如800），然后不做Top50截断
    basket_raw = uni.get_fcf_basket(date_str, top_n=800, verbose=False, use_ttm=True)
    ranked = [dict(v, ts_code=k) for k, v in basket_raw.items()
              if k != "__quality_warnings__" and isinstance(v, dict)]
    ranked.sort(key=lambda x: x.get('fcf_yield', 0), reverse=True)
    
    # B版筛选逻辑：OCF 5yr + PQ + 行业排除，但top_n不限制为50
    # get_fcf_basket已经做了筛选，所以ranked里都是合格的
    # 不做top50截断，全部保留
    all_stocks = [dict(s) for s in ranked]
    fcf_weights(all_stocks)
    return all_stocks

def calc_stats(stocks):
    """统计数量、FCF加和、(OCF-营业利润)加和"""
    if not stocks:
        return dict(count=0, fcf_sum=0, ocf_op_sum=0)
    count = len(stocks)
    fcf_sum = sum(s.get('fcf', 0) for s in stocks) / 1e8  # 亿元
    # OCF - 营业利润 = FCF + CAPEX - 营业利润
    # 但我们有 profit_quality = (OCF - oper_profit) / total_assets
    # 直接用 profit_quality * total_assets * 1e4 来算 (OCF - oper_profit)
    ocf_op_sum = 0
    for s in stocks:
        pq = s.get('profit_quality', 0)
        total_assets = s.get('total_assets', 0)
        if pq and total_assets:
            ocf_op_sum += pq * total_assets * 1e4 / 1e8  # 亿元
    return dict(count=count, fcf_sum=fcf_sum, ocf_op_sum=ocf_op_sum)

# ======== 加载B版篮子 ========
with open("output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json") as f:
    b_baskets = json.load(f)
nav_df = pd.read_csv("output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv")

# ======== 沪深300 & ZZ800 全成分 ========
print("初始化 Universe...")
uni_zz800 = FcfUniverse(index_code="000906.SH", strict_ocf=False)
uni_zz800.preload_all(download=False)
uni_hs300 = FcfUniverse(index_code="000300.SH", strict_ocf=False)
uni_hs300.preload_all(download=False)

# 加载沪深300日线
df_hs = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str)
df_hs = df_hs.sort_values('trade_date')

# 加载932368日线
df_idx = pd.read_csv("data/index_daily/932368.CSI.csv")
df_idx['trade_date'] = df_idx['trade_date'].astype(str)
df_idx = df_idx.sort_values('trade_date')

def idx_ret(df, s, e):
    s_k, e_k = s.replace('-',''), e.replace('-','')
    p0 = float(df[df['trade_date'] <= s_k]['close'].iloc[-1])
    p1 = float(df[df['trade_date'] <= e_k]['close'].iloc[-1])
    return (p1/p0 - 1)*100

# ======== 生成全成分篮子 ========
t0 = time.time()
zz800_full = {}  # 全成分篮子
hs300_full = {}
zz800_stats = {}
hs300_stats = {}

print(f"\n生成全成分篮子（{len(REBALANCE_DATES)}期）...")
for i, date_str in enumerate(REBALANCE_DATES):
    try:
        zz_stocks = get_full_basket(uni_zz800, date_str)
        hs_stocks = get_full_basket(uni_hs300, date_str)
        
        zz800_full[date_str] = zz_stocks
        hs300_full[date_str] = hs_stocks
        
        zz800_stats[date_str] = calc_stats(zz_stocks)
        hs300_stats[date_str] = calc_stats(hs_stocks)
        
        elapsed = time.time() - t0
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ZZ800={zz800_stats[date_str]['count']}只 FCF={zz800_stats[date_str]['fcf_sum']:.0f}亿  "
              f"HS300={hs300_stats[date_str]['count']}只 FCF={hs300_stats[date_str]['fcf_sum']:.0f}亿  ({elapsed:.0f}s)")
    except Exception as ex:
        print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
        zz800_full[date_str] = []
        hs300_full[date_str] = []
        zz800_stats[date_str] = dict(count=0, fcf_sum=0, ocf_op_sum=0)
        hs300_stats[date_str] = dict(count=0, fcf_sum=0, ocf_op_sum=0)

# ======== 保存篮子 ========
OUT_ZZ = PROJECT_ROOT / "output" / "zz800_fcf_full_universe"
OUT_HS = PROJECT_ROOT / "output" / "hs300_fcf_full_universe"
OUT_ZZ.mkdir(parents=True, exist_ok=True)
OUT_HS.mkdir(parents=True, exist_ok=True)

with open(OUT_ZZ / "all_baskets_2015_2026.json", "w") as f:
    json.dump(zz800_full, f, ensure_ascii=False)
with open(OUT_HS / "all_baskets_2015_2026.json", "w") as f:
    json.dump(hs300_full, f, ensure_ascii=False)

print(f"\n篮子已保存: {OUT_ZZ}/all_baskets_2015_2026.json, {OUT_HS}/all_baskets_2015_2026.json")

# ======== 计算NAV ========
def calc_nav(baskets):
    nav = 1.0; rows = []
    for _, row in nav_df.iterrows():
        rb, nrb = row['rb_date'], row['next_rb']
        stocks = baskets.get(rb, [])
        if len(stocks) < 5: continue
        w_ret, w_tot = 0.0, 0.0
        for s in stocks:
            r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
            if r:
                w_ret += s['weight'] * (r[1]/r[0]-1)
                w_tot += s['weight']
        if w_tot < 0.3: continue  # 全成分覆盖率要求更低
        pr = w_ret / w_tot
        nav *= (1 + pr)
        rows.append({'rb_date': rb, 'next_rb': nrb, 'period_ret': pr*100, 'nav': nav})
    return pd.DataFrame(rows)

print("\n计算NAV...")
b_nav_df = calc_nav(b_baskets)        # B版 (Top50)
zz_full_nav = calc_nav(zz800_full)    # ZZ800全成分
hs_full_nav = calc_nav(hs300_full)    # HS300全成分

b_nav_df.to_csv(OUT_ZZ / "b50_nav_tr.csv", index=False)
zz_full_nav.to_csv(OUT_ZZ / "full_nav_tr.csv", index=False)
hs_full_nav.to_csv(OUT_HS / "full_nav_tr.csv", index=False)

# ======== 合并对比表 ========
# 基准：B版NAV的调仓日
m = b_nav_df[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b50_ret','b50_nav']

# ZZ800全成分
zz = zz_full_nav[['rb_date','period_ret','nav']].copy()
zz.columns = ['rb_date','zz_full_ret','zz_full_nav']
m = m.merge(zz, on='rb_date', how='left')

# HS300全成分
hs = hs_full_nav[['rb_date','period_ret','nav']].copy()
hs.columns = ['rb_date','hs_full_ret','hs_full_nav']
m = m.merge(hs, on='rb_date', how='left')

# 932368基准
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_idx_ret'] = m.apply(lambda r: idx_ret(df_hs,  r['rb_date'], r['next_rb']), axis=1)

# 指数NAV
i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_idx_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_idx_nav'] = h_navs

# 统计数据
m['zz_count'] = m['rb_date'].map(lambda d: zz800_stats.get(d, {}).get('count', 0))
m['zz_fcf']   = m['rb_date'].map(lambda d: zz800_stats.get(d, {}).get('fcf_sum', 0))
m['zz_ocf_op'] = m['rb_date'].map(lambda d: zz800_stats.get(d, {}).get('ocf_op_sum', 0))
m['hs_count'] = m['rb_date'].map(lambda d: hs300_stats.get(d, {}).get('count', 0))
m['hs_fcf']   = m['rb_date'].map(lambda d: hs300_stats.get(d, {}).get('fcf_sum', 0))
m['hs_ocf_op'] = m['rb_date'].map(lambda d: hs300_stats.get(d, {}).get('ocf_op_sum', 0))

# B版Top50统计
b_count = {}; b_fcf = {}; b_ocf_op = {}
for d, stocks in b_baskets.items():
    b_count[d] = len(stocks)
    b_fcf[d] = sum(s.get('fcf',0) for s in stocks) / 1e8
    pq_sum = 0
    for s in stocks:
        pq = s.get('profit_quality',0)
        ta = s.get('total_assets',0)
        if pq and ta: pq_sum += pq * ta * 1e4 / 1e8
    b_ocf_op[d] = pq_sum
m['b50_count'] = m['rb_date'].map(lambda d: b_count.get(d,0))
m['b50_fcf']   = m['rb_date'].map(lambda d: b_fcf.get(d,0))
m['b50_ocf_op'] = m['rb_date'].map(lambda d: b_ocf_op.get(d,0))

# ======== 统计函数 ========
def stats(rc, nc, data=m):
    rets = data[rc]; navs = data[nc]
    n = len(rets.dropna())
    ann  = (navs.dropna().iloc[-1]**(4/n)-1)*100
    vol  = rets.dropna().std()*2
    peak = navs.dropna().cummax()
    mdd  = ((peak-navs.dropna())/peak).max()*100
    sharpe = (ann-2.0)/vol if vol>0 else 0
    calmar = ann/mdd if mdd>0 else 0
    win  = (rets.dropna()>0).mean()*100
    return dict(ann=ann, vol=vol, mdd=-mdd, sharpe=sharpe, calmar=calmar, win=win, nav=navs.dropna().iloc[-1])

bs = stats('b50_ret','b50_nav')
zs = stats('zz_full_ret','zz_full_nav')
hs_s = stats('hs_full_ret','hs_full_nav')
is_ = stats('idx_ret','i_nav')
hs_idx = stats('hs_idx_ret','h_idx_nav')

# ======== 生成报告 ========
now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)

lines = []
lines.append("# ZZ800 & HS300 全成分FCF统计报告（不做Top50筛选）")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + str(m['rb_date'].iloc[0]) + " → " + str(m['next_rb'].iloc[-1]) + "（共 " + str(N) + " 期）")
lines.append("> 全收益模式（含分红再投资，复权价计算）")
lines.append("> 筛选条件：5年期OCF + 盈利质量PQ前80% + 行业排除（金融地产），但**不做Top50截断**")
lines.append("> 加权方式：FCF绝对值加权 + 单股10%封顶迭代重分配")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 一、核心指标对比")
lines.append("")
lines.append("| 指标 | B版Top50 | ZZ800全成分 | HS300全成分 | 932368 | 沪深300指数 |")
lines.append("|------|----------|-------------|-------------|--------|-------------|")
lines.append("| **年化收益** | " + str(round(bs['ann'],2)) + "% | " + str(round(zs['ann'],2)) + "% | " + str(round(hs_s['ann'],2)) + "% | " + str(round(is_['ann'],2)) + "% | " + str(round(hs_idx['ann'],2)) + "% |")
lines.append("| **最大回撤** | " + str(round(bs['mdd'],2)) + "% | " + str(round(zs['mdd'],2)) + "% | " + str(round(hs_s['mdd'],2)) + "% | " + str(round(is_['mdd'],2)) + "% | " + str(round(hs_idx['mdd'],2)) + "% |")
lines.append("| 夏普比率 | " + str(round(bs['sharpe'],3)) + " | " + str(round(zs['sharpe'],3)) + " | " + str(round(hs_s['sharpe'],3)) + " | " + str(round(is_['sharpe'],3)) + " | " + str(round(hs_idx['sharpe'],3)) + " |")
lines.append("| Calmar比率 | " + str(round(bs['calmar'],3)) + " | " + str(round(zs['calmar'],3)) + " | " + str(round(hs_s['calmar'],3)) + " | " + str(round(is_['calmar'],3)) + " | " + str(round(hs_idx['calmar'],3)) + " |")
lines.append("| 期末净值 | " + str(round(bs['nav'],3)) + "x | " + str(round(zs['nav'],3)) + "x | " + str(round(hs_s['nav'],3)) + "x | " + str(round(is_['nav'],3)) + "x | " + str(round(hs_idx['nav'],3)) + "x |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、每期调仓统计明细")
lines.append("")
lines.append("### 2.1 ZZ800（中证800）全成分统计")
lines.append("")
lines.append("| 调仓日 | 公司数 | FCF加和(亿) | (OCF-营业利润)加和(亿) | 全成分NAV | B版Top50 NAV | 全成分收益 | Top50收益 | 差异 |")
lines.append("|--------|--------|-------------|------------------------|-----------|---------------|-----------|-----------|------|")
for _, r in m.iterrows():
    zz_diff = r.get('zz_full_ret',0) - r.get('b50_ret',0)
    zz_diff_str = ("+" if zz_diff>=0 else "") + str(round(zz_diff,2)) + "%"
    zz_fcf_str = str(round(r.get('zz_fcf',0),0))
    zz_ocf_op_str = str(round(r.get('zz_ocf_op',0),0))
    zz_nav_str = str(round(r.get('zz_full_nav',0),3)) if pd.notna(r.get('zz_full_nav')) else "—"
    b50_nav_str = str(round(r.get('b50_nav',0),3))
    zz_ret_str = ("+" if r.get('zz_full_ret',0)>=0 else "") + str(round(r.get('zz_full_ret',2),2)) + "%" if pd.notna(r.get('zz_full_ret')) else "—"
    b50_ret_str = ("+" if r.get('b50_ret',0)>=0 else "") + str(round(r.get('b50_ret',2),2)) + "%"
    lines.append("| " + str(r['rb_date']) + " | " + str(int(r.get('zz_count',0))) + " | " + zz_fcf_str + " | " + zz_ocf_op_str + " | " + zz_nav_str + " | " + b50_nav_str + " | " + zz_ret_str + " | " + b50_ret_str + " | " + zz_diff_str + " |")

lines.append("")
lines.append("### 2.2 HS300（沪深300）全成分统计")
lines.append("")
lines.append("| 调仓日 | 公司数 | FCF加和(亿) | (OCF-营业利润)加和(亿) | 全成分NAV | 沪深300指数NAV | 全成分收益 | 指数收益 | 超额 |")
lines.append("|--------|--------|-------------|------------------------|-----------|---------------|-----------|---------|------|")
for _, r in m.iterrows():
    hs_exc = r.get('hs_full_ret',0) - r.get('hs_idx_ret',0)
    hs_exc_str = ("+" if hs_exc>=0 else "") + str(round(hs_exc,2)) + "%"
    hs_fcf_str = str(round(r.get('hs_fcf',0),0))
    hs_ocf_op_str = str(round(r.get('hs_ocf_op',0),0))
    hs_nav_str = str(round(r.get('hs_full_nav',0),3)) if pd.notna(r.get('hs_full_nav')) else "—"
    h_idx_nav_str = str(round(r.get('h_idx_nav',0),3))
    hs_ret_str = ("+" if r.get('hs_full_ret',0)>=0 else "") + str(round(r.get('hs_full_ret',2),2)) + "%" if pd.notna(r.get('hs_full_ret')) else "—"
    hs_idx_ret_str = ("+" if r.get('hs_idx_ret',0)>=0 else "") + str(round(r.get('hs_idx_ret',2),2)) + "%"
    lines.append("| " + str(r['rb_date']) + " | " + str(int(r.get('hs_count',0))) + " | " + hs_fcf_str + " | " + hs_ocf_op_str + " | " + hs_nav_str + " | " + h_idx_nav_str + " | " + hs_ret_str + " | " + hs_idx_ret_str + " | " + hs_exc_str + " |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、逐年汇总")
lines.append("")
lines.append("| 年份 | ZZ800公司数 | ZZ800 FCF(亿) | ZZ800 (OCF-OP)(亿) | HS300公司数 | HS300 FCF(亿) | HS300 (OCF-OP)(亿) | B版Top50 NAV | ZZ800全NAV | HS300全NAV | 932368 NAV | 沪深300 NAV |")
lines.append("|------|-------------|----------------|--------------------|-------------|----------------|--------------------|-------------|------------|------------|-----------|------------|")
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    zz_cnt_avg = rows['zz_count'].mean()
    zz_fcf_avg = rows['zz_fcf'].mean()
    zz_ocf_avg = rows['zz_ocf_op'].mean()
    hs_cnt_avg = rows['hs_count'].mean()
    hs_fcf_avg = rows['hs_fcf'].mean()
    hs_ocf_avg = rows['hs_ocf_op'].mean()
    # 取年末NAV
    b_nav_yr  = rows['b50_nav'].iloc[-1]
    zz_nav_yr = rows['zz_full_nav'].iloc[-1] if pd.notna(rows['zz_full_nav'].iloc[-1]) else "—"
    hs_nav_yr = rows['hs_full_nav'].iloc[-1] if pd.notna(rows['hs_full_nav'].iloc[-1]) else "—"
    i_nav_yr  = rows['i_nav'].iloc[-1]
    h_nav_yr  = rows['h_idx_nav'].iloc[-1]
    lines.append("| " + yr + " | " + str(round(zz_cnt_avg,0)) + " | " + str(round(zz_fcf_avg,0)) + " | " + str(round(zz_ocf_avg,0)) + " | " +
                 str(round(hs_cnt_avg,0)) + " | " + str(round(hs_fcf_avg,0)) + " | " + str(round(hs_ocf_avg,0)) + " | " +
                 str(round(b_nav_yr,2)) + " | " + (str(round(zz_nav_yr,2)) if isinstance(zz_nav_yr,float) else zz_nav_yr) + " | " +
                 (str(round(hs_nav_yr,2)) if isinstance(hs_nav_yr,float) else hs_nav_yr) + " | " + str(round(i_nav_yr,2)) + " | " + str(round(h_nav_yr,2)) + " |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、公司数量 & FCF规模趋势分析")
lines.append("")
lines.append("### ZZ800合格公司数趋势")
lines.append("")
zz_counts = [int(m.iloc[i]['zz_count']) for i in range(len(m))]
zz_fcfs   = [round(m.iloc[i]['zz_fcf'],0) for i in range(len(m))]
hs_counts = [int(m.iloc[i]['hs_count']) for i in range(len(m))]
hs_fcfs   = [round(m.iloc[i]['hs_fcf'],0) for i in range(len(m))]

lines.append("- ZZ800平均合格公司数: " + str(round(np.mean(zz_counts),0)) + "（范围 " + str(min(zz_counts)) + "~" + str(max(zz_counts)) + "）")
lines.append("- ZZ800平均FCF加和: " + str(round(np.mean(zz_fcfs),0)) + " 亿元")
lines.append("- HS300平均合格公司数: " + str(round(np.mean(hs_counts),0)) + "（范围 " + str(min(hs_counts)) + "~" + str(max(hs_counts)) + "）")
lines.append("- HS300平均FCF加和: " + str(round(np.mean(hs_fcfs),0)) + " 亿元")
lines.append("- ZZ800 vs B版Top50: Top50只占全成分的 " + str(round(50/np.mean(zz_counts)*100,1)) + "%")
lines.append("- B版Top50 FCF集中度: Top50占全成分FCF的 " + str(round(np.mean([m.iloc[i]['b50_fcf'] for i in range(len(m))]) / np.mean(zz_fcfs) * 100, 1)) + "%")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 五、综合结论")
lines.append("")
lines.append("1. **ZZ800全成分 vs B版Top50**：")
zz_full_ann_str = str(round(zs['ann'],2)) + "%"
b50_ann_str = str(round(bs['ann'],2)) + "%"
if zs['ann'] > bs['ann']:
    lines.append("   - 全成分年化 " + zz_full_ann_str + " > Top50 " + b50_ann_str + "，全成分策略更优")
else:
    lines.append("   - 全成分年化 " + zz_full_ann_str + " < Top50 " + b50_ann_str + "，Top50筛选提升了收益质量")

lines.append("2. **HS300全成分 vs 沪深300指数**：")
hs_full_ann_str = str(round(hs_s['ann'],2)) + "%"
hs_idx_ann_str = str(round(hs_idx['ann'],2)) + "%"
if hs_s['ann'] > hs_idx['ann']:
    lines.append("   - 全成分年化 " + hs_full_ann_str + " > 沪深300 " + hs_idx_ann_str + "，FCF加权显著跑赢指数")
else:
    lines.append("   - 全成分年化 " + hs_full_ann_str + " < 沪深300 " + hs_idx_ann_str)

lines.append("3. **Top50集中度效应**：B版Top50仅选取约" + str(round(50/np.mean(zz_counts)*100,0)) + "%的合格公司，但贡献了全成分FCF的" + str(round(np.mean([m.iloc[i]['b50_fcf'] for i in range(len(m))]) / np.mean(zz_fcfs) * 100, 0)) + "%")
lines.append("4. **(OCF-营业利润)含义**：正值代表经营现金流超过营业利润（盈利质量好），负值代表利润含水分")
zz_ocf_mean = np.mean([m.iloc[i]['zz_ocf_op'] for i in range(len(m))])
hs_ocf_mean = np.mean([m.iloc[i]['hs_ocf_op'] for i in range(len(m))])
lines.append("   - ZZ800平均(OCF-OP)加和: " + str(round(zz_ocf_mean,0)) + " 亿元（" + ("盈利质量整体良好" if zz_ocf_mean>0 else "部分利润含水分") + ")")
lines.append("   - HS300平均(OCF-OP)加和: " + str(round(hs_ocf_mean,0)) + " 亿元")

lines.append("")
lines.append("---")
lines.append("*报告自动生成，计算日期：" + now + "*")

report = "\n".join(lines)
with open("docs/full_universe_fcf_stats.md", "w") as f:
    f.write(report)

# ======== 输出摘要 ========
print("\n" + "="*70)
print("全成分FCF统计 & NAV对比摘要")
print("="*70)
print("")
print("核心指标对比:")
print("-"*70)
print("策略             年化收益    最大回撤    夏普    期末NAV")
print("-"*70)
print("B版Top50(ZZ800)  " + str(round(bs['ann'],2)).rjust(7) + "%  " + str(round(bs['mdd'],2)).rjust(8) + "%  " + str(round(bs['sharpe'],3)).rjust(6) + "  " + str(round(bs['nav'],3)).rjust(7) + "x")
print("ZZ800全成分      " + str(round(zs['ann'],2)).rjust(7) + "%  " + str(round(zs['mdd'],2)).rjust(8) + "%  " + str(round(zs['sharpe'],3)).rjust(6) + "  " + str(round(zs['nav'],3)).rjust(7) + "x")
print("HS300全成分      " + str(round(hs_s['ann'],2)).rjust(7) + "%  " + str(round(hs_s['mdd'],2)).rjust(8) + "%  " + str(round(hs_s['sharpe'],3)).rjust(6) + "  " + str(round(hs_s['nav'],3)).rjust(7) + "x")
print("932368           " + str(round(is_['ann'],2)).rjust(7) + "%  " + str(round(is_['mdd'],2)).rjust(8) + "%  " + str(round(is_['sharpe'],3)).rjust(6) + "  " + str(round(is_['nav'],3)).rjust(7) + "x")
print("沪深300指数      " + str(round(hs_idx['ann'],2)).rjust(7) + "%  " + str(round(hs_idx['mdd'],2)).rjust(8) + "%  " + str(round(hs_idx['sharpe'],3)).rjust(6) + "  " + str(round(hs_idx['nav'],3)).rjust(7) + "x")
print("")
print("每期统计（最近5期）:")
print("-"*70)
print("调仓日     ZZ800数  FCF加和(亿)  (OCF-OP)(亿)  HS300数  FCF加和(亿)")
print("-"*70)
for i in range(min(5, len(m))):
    r = m.iloc[-(5-i)]
    print(str(r['rb_date']) + "  " + str(int(r['zz_count'])).rjust(5) + "  " +
          str(round(r['zz_fcf'],0)).rjust(10) + "  " + str(round(r['zz_ocf_op'],0)).rjust(10) + "  " +
          str(int(r['hs_count'])).rjust(5) + "  " + str(round(r['hs_fcf'],0)).rjust(10))
print("")
print("✅ 报告已保存: docs/full_universe_fcf_stats.md")
print("✅ 篮子已保存: output/zz800_fcf_full_universe/, output/hs300_fcf_full_universe/")