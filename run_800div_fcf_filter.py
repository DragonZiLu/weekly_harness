#!/usr/bin/env python3
"""run_800div_fcf_filter.py — 800红利+FCF过滤实验：在原始931644选股基础上增加"最近一年FCF(TTM)>0"过滤"""

import sys, json, time, argparse
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime

# ═══════════════ 参数 ═══════════════
parser = argparse.ArgumentParser()
parser.add_argument('--nav-only', action='store_true', help='跳过选股，用已有basket算NAV+报告')
args = parser.parse_args()

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from dividend_universe import DividendUniverse
from compute_nav_cached import get_adj_close_cached

RUN_SELECTION = not args.nav_only
RUN_NAV = True

# ═══════════════ 调仓日（半年度：6月/12月第二个星期五的下一交易日） ═══════════════
REBALANCE_DATES = [
    "2015-06-15",
    "2015-12-14",
    "2016-06-13",
    "2016-12-12",
    "2017-06-12",
    "2017-12-11",
    "2018-06-11",
    "2018-12-17",
    "2019-06-17",
    "2019-12-16",
    "2020-06-15",
    "2020-12-14",
    "2021-06-14",
    "2021-12-13",
    "2022-06-13",
    "2022-12-12",
    "2023-06-12",
    "2023-12-11",
    "2024-06-17",
    "2024-12-16",
    "2025-06-16",
    "2025-12-15",
    "2026-06-15",
]

TOP_N = 100
CAP = 0.10
MAX_TURNOVER = 0.20
OUT_DIR = PROJECT_ROOT / "output" / "800div_fcf"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════ 第一步：选股 ═══════════════
baskets = {}
fcf_stats = []  # 记录每期的FCF过滤统计

if RUN_SELECTION:
    print("=" * 70)
    print("第一步：800红利+FCF过滤 — 选股")
    print("=" * 70)
    print("  规则: 原931644选股 + 最近一年FCF(TTM)>0过滤")
    print("        FCF_TTM = TTM_OCF - TTM_Capex")
    print("        6月调仓→当年Q1 TTM, 12月调仓→当年Q3 TTM")
    print("        数据不足时回退年报，仍不足则保守通过")
    print("=" * 70)

    uni = DividendUniverse(index_code="000906.SH")
    uni.preload_all(rebalance_dates=REBALANCE_DATES)

    prev_codes = set()
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        try:
            raw = uni.get_dividend_basket(
                date_str=date_str,
                top_n=TOP_N,
                prev_basket_codes=prev_codes if i > 0 else None,
                max_turnover=MAX_TURNOVER,
                verbose=True,
                enable_fcf_filter=True,  # ★ 启用FCF过滤
            )
            stocks = sorted(raw.values(), key=lambda x: x["weight"], reverse=True)
            baskets[date_str] = stocks
            prev_codes = {s["ts_code"] for s in stocks}
            elapsed = time.time() - t0

            # 首仓信息
            top5_names = ", ".join(s["name"] for s in stocks[:5])
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: "
                  f"{len(stocks)}只, Top5: {top5_names} "
                  f"({elapsed:.0f}s)")
        except Exception as ex:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
            baskets[date_str] = []

    with open(OUT_DIR / "all_baskets_2015_2026.json", "w") as f:
        json.dump(baskets, f, ensure_ascii=False, indent=2)

    valid = sum(1 for d in baskets if len(baskets.get(d, [])) >= 10)
    print(f"\n  ✅ 800红利+FCF: {valid}/{len(baskets)}期有效 → {OUT_DIR}/")

else:
    print("  加载已有 baskets...")
    with open(OUT_DIR / "all_baskets_2015_2026.json") as f:
        baskets = json.load(f)
    print(f"  ✅ 加载 800红利+FCF: {len(baskets)} 期")

# ═══════════════ 第二步：计算NAV ═══════════════
if not RUN_NAV:
    sys.exit(0)

print("\n" + "=" * 70)
print("第二步：计算 NAV")
print("=" * 70)

nav_df = pd.DataFrame([
    {'rb_date': REBALANCE_DATES[i], 'next_rb': REBALANCE_DATES[i+1]}
    for i in range(len(REBALANCE_DATES)-1)
])

# ── 基准指数加载 ──
# 932368 全收益
df_idx_price = pd.read_csv("data/index_daily/932368.CSI.csv")
df_idx_price['trade_date'] = df_idx_price['trade_date'].astype(str)
df_idx_price = df_idx_price[['trade_date','close']].rename(columns={'close':'p'}).sort_values('trade_date')

df_hs_p = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs_p['trade_date'] = df_hs_p['trade_date'].astype(str)
df_hs_p = df_hs_p[['trade_date','close']].rename(columns={'close':'hs_p'})
df_hs_tr = pd.read_csv("data/index_daily/H00300.CSI.csv")
df_hs_tr['trade_date'] = df_hs_tr['trade_date'].astype(str)
df_hs_tr = df_hs_tr[['trade_date','close']].rename(columns={'close':'hs_tr'})

df_div = df_hs_p.merge(df_hs_tr, on='trade_date', how='inner')
df_div['div_adj'] = df_div['hs_tr'] / df_div['hs_p']

df_idx = df_idx_price.merge(df_div[['trade_date','div_adj']], on='trade_date', how='inner')
df_idx['close'] = df_idx['p'] * df_idx['div_adj']

# 沪深300全收益
df_hs = pd.read_csv("data/index_daily/H00300.CSI.csv")
df_hs['trade_date'] = df_hs['trade_date'].astype(str)
df_hs = df_hs[['trade_date','close']].sort_values('trade_date')

def idx_ret(df, s, e):
    sk, ek = s.replace('-',''), e.replace('-','')
    try:
        p0 = float(df[df['trade_date']<=sk]['close'].iloc[-1])
        p1 = float(df[df['trade_date']<=ek]['close'].iloc[-1])
        return (p1/p0-1)*100 if p0 > 0 else 0.0
    except IndexError:
        return 0.0

def calc_nav(baskets, min_stocks=5, min_weight=0.3):
    nav = 1.0; rows = []
    for _, row in nav_df.iterrows():
        rb, nrb = row['rb_date'], row['next_rb']
        stocks = baskets.get(rb, [])
        if len(stocks) < min_stocks: continue
        w_ret, w_tot = 0.0, 0.0
        for s in stocks:
            r = get_adj_close_cached(s['ts_code'], rb, nrb, auto_fetch=False)
            if r:
                w_ret += s['weight'] * (r[1]/r[0]-1)
                w_tot += s['weight']
        if w_tot < min_weight: continue
        pr = w_ret/w_tot
        nav *= (1+pr)
        rows.append({'rb_date':rb, 'next_rb':nrb, 'period_ret':pr*100, 'nav':nav})
    return pd.DataFrame(rows)

# ── 800红利+FCF NAV ──
print("  计算 800红利+FCF NAV...")
nav_fcf = calc_nav(baskets)
nav_fcf.to_csv(OUT_DIR / "backtest_nav_tr.csv", index=False)
final_nav = nav_fcf['nav'].iloc[-1] if len(nav_fcf) > 0 else 0
print(f"  800红利+FCF: {len(nav_fcf)}期, 期末NAV={final_nav:.4f}x")

# ── 加载原始800红利对比 ──
orig_path = PROJECT_ROOT / "output/800div/all_baskets_2015_2026.json"
orig_nav_path = PROJECT_ROOT / "output/800div/backtest_nav_tr.csv"
orig_nav = None
if orig_nav_path.exists():
    orig_nav = pd.read_csv(orig_nav_path)
    print(f"  ✅ 加载 原始800红利 NAV: {len(orig_nav)}期, "
          f"期末NAV={orig_nav['nav'].iloc[-1]:.4f}x")

# ═══════════════ 第三步：生成报告 ═══════════════
print("\n" + "=" * 70)
print("第三步：生成对比报告")
print("=" * 70)

m = nav_fcf[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','fcf_ret','fcf_nav']

# 基准
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)

# 加入原始800红利对比
if orig_nav is not None:
    on = orig_nav[['rb_date','next_rb','period_ret','nav']].copy()
    on.columns = ['rb_date','next_rb','orig_ret','orig_nav']
    m = m.merge(on, on=['rb_date','next_rb'], how='left')

# 基准净值链
i_n, h_n = 1.0, 1.0
i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['idx_nav'] = i_navs
m['hs_nav']  = h_navs

def stats(rc, nc, data=m):
    rets = data[rc].dropna(); navs = data[nc].dropna()
    if len(rets) < 2:
        return dict(ann=0, vol=0, mdd=0, sharpe=0, calmar=0, win=0, nav=0)
    n = len(rets)
    years = n * 0.5
    ann  = (navs.iloc[-1]**(1/years)-1)*100 if years > 0 else 0
    vol  = rets.std()*np.sqrt(2)
    peak = navs.cummax()
    mdd  = ((peak-navs)/peak).max()*100
    sharpe = (ann-2.0)/vol if vol>0 else 0
    calmar = ann/mdd if mdd>0 else 0
    win  = (rets>0).mean()*100
    return dict(ann=ann, vol=vol, mdd=-mdd, sharpe=sharpe, calmar=calmar, win=win, nav=navs.iloc[-1])

def turnover(baskets):
    dates = sorted([d for d in baskets if len(baskets.get(d, [])) >= 10])
    tos = []
    for i in range(1, len(dates)):
        prev = {s['ts_code'] for s in baskets.get(dates[i-1], [])}
        curr = {s['ts_code'] for s in baskets.get(dates[i], [])}
        if curr: tos.append(len(curr-prev)/len(curr))
    return np.mean(tos)*100 if tos else 0

fcf_s = stats('fcf_ret','fcf_nav')
idx_s = stats('idx_ret','idx_nav')
hs_s  = stats('hs_ret','hs_nav')
orig_s = stats('orig_ret','orig_nav') if 'orig_ret' in m.columns else dict(ann=0,vol=0,mdd=0,sharpe=0,calmar=0,win=0,nav=0)
fcf_to = turnover(baskets)

# 逐年收益
def yearly_rets(rc, data=m):
    yr_ret = {}
    for yr in sorted(data['rb_date'].str[:4].unique()):
        rows = data[data['rb_date'].str[:4]==yr]
        if len(rows) > 0:
            yr_ret[yr] = (1+rows[rc]/100).prod()-1
    return yr_ret

ar_fcf  = yearly_rets('fcf_ret')
ar_idx  = yearly_rets('idx_ret')
ar_hs   = yearly_rets('hs_ret')
ar_orig = yearly_rets('orig_ret') if 'orig_ret' in m.columns else {}

# ── 生成Markdown报告 ──
now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)

lines = []
lines.append("# 800红利+FCF过滤 实验报告")
lines.append("")
lines.append(f"> 生成时间：{now}")
lines.append(f"> 回测区间：{m['rb_date'].iloc[0]} → {m['next_rb'].iloc[-1]}（共{N}期，半年度调仓）")
lines.append('> 实验说明：在原始931644选股逻辑基础上，增加"最近一年FCF(TTM)>0"过滤')
lines.append("> 对比基准：原始800红利（无FCF过滤）")
lines.append("")

lines.append("---")
lines.append("")
lines.append("## 一、策略说明")
lines.append("")
lines.append("| 项目 | 800红利+FCF | 原始800红利 |")
lines.append("|------|------------|------------|")
lines.append("| 样本空间 | 中证800（000906.SH） | 中证800（000906.SH） |")
lines.append("| 选股指标 | 三年平均股息率 + FCF(TTM)>0 | 三年平均股息率 |")
lines.append("| 过滤条件 | 连续三年分红 + FCF(TTM)>0 + 股利支付率∈(0,1) | 连续三年分红 + 股利支付率∈(0,1) |")
lines.append("| FCF计算 | TTM口径：6月→Q1 TTM，12月→Q3 TTM | — |")
lines.append("| FCF兜底 | 季度数据不足时回退到最近年报 | — |")
lines.append("| 加权方式 | 股息率加权，10%封顶 | 股息率加权，10%封顶 |")
lines.append("| 调仓频率 | 半年度（6月/12月） | 半年度（6月/12月） |")
lines.append("| 换手限制 | ≤20% | ≤20% |")
lines.append("")

lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")

headers = ["指标", "800红利+FCF", "原800红利", "vs原版", "932368", "沪深300"]
lines.append("| " + " | ".join(headers) + " |")
lines.append("|" + "|".join(["------"]*len(headers)) + "|")

for metric, key, fmt_s in [
    ('**年化收益**', 'ann', "{:.2f}%"),
    ('**最大回撤**', 'mdd', "{:.2f}%"),
    ('年化波动率', 'vol', "{:.2f}%"),
    ('夏普比率', 'sharpe', "{:.3f}"),
    ('Calmar比率', 'calmar', "{:.3f}"),
    ('单期胜率', 'win', "{:.1f}%"),
    ('期末净值', 'nav', "{:.3f}x"),
]:
    vals = [metric]
    vals.append(fmt_s.format(fcf_s[key]))
    vals.append(fmt_s.format(orig_s[key]))
    diff = fcf_s[key] - orig_s[key]
    sign = "+" if diff >= 0 else ""
    vals.append(f"{sign}{fmt_s.format(diff).replace('%','pp')}")
    vals.append(fmt_s.format(idx_s[key]))
    vals.append(fmt_s.format(hs_s[key]))
    lines.append("| " + " | ".join(vals) + " |")

lines.append("")
lines.append(f"> **换手率**：800红利+FCF **{fcf_to:.1f}%** | 原始800红利 ~12.5%")
lines.append("")

# 超额
lines.append("---")
lines.append("")
lines.append("## 三、超额收益分析")
lines.append("")
lines.append(f"- 800红利+FCF vs 原800红利 年化差异：**{fcf_s['ann']-orig_s['ann']:+.2f}pp**")
lines.append(f"- 800红利+FCF vs 932368 年化超额：{fcf_s['ann']-idx_s['ann']:+.2f}pp")
lines.append(f"- 800红利+FCF vs 沪深300 年化超额：{fcf_s['ann']-hs_s['ann']:+.2f}pp")
lines.append(f"- vs 932368 超额胜率：{(m['fcf_ret']>m['idx_ret']).mean()*100:.1f}%")
lines.append(f"- vs 沪深300 超额胜率：{(m['fcf_ret']>m['hs_ret']).mean()*100:.1f}%")
if 'orig_ret' in m.columns:
    lines.append(f"- vs 原800红利 超额胜率：{(m['fcf_ret']>m['orig_ret']).mean()*100:.1f}%")
lines.append("")

# 逐年对比
years = sorted(set(list(ar_fcf.keys()) + list(ar_idx.keys()) + list(ar_hs.keys()) + list(ar_orig.keys())))
lines.append("---")
lines.append("")
lines.append("## 四、逐年收益对比")
lines.append("")

yr_headers = ["年份", "800红利+FCF", "原800红利", "932368", "沪深300", "最佳"]
lines.append("| " + " | ".join(yr_headers) + " |")
lines.append("|" + "|".join(["------"]*len(yr_headers)) + "|")

for yr in sorted(years):
    f = ar_fcf.get(yr, 0) * 100
    o = ar_orig.get(yr, 0) * 100
    i = ar_idx.get(yr, 0) * 100
    h = ar_hs.get(yr, 0) * 100

    all_vals = [("800红利+FCF", f), ("原800红利", o), ("932368", i), ("沪深300", h)]
    best = max(all_vals, key=lambda v: v[1])

    def fmt(v):
        sgn = "+" if v >= 0 else ""
        return f"{'**' if v == best[1] else ''}{sgn}{v:.2f}%{'**' if v == best[1] else ''}"

    row = f"| {yr} | {fmt(f)} | {fmt(o)} | {fmt(i)} | {fmt(h)} | {best[0]} |"
    lines.append(row)

lines.append("")

# 逐期明细
lines.append("---")
lines.append("")
lines.append("## 五、逐期收益明细")
lines.append("")
if 'orig_ret' in m.columns:
    lines.append("| 调仓日 | 800红利+FCF | 原800红利 | 932368 | 沪深300 | vs原版超额 |")
    lines.append("|--------|:------:|:------:|:------:|:-------:|:----------:|")
    for _, r in m.iterrows():
        f = r['fcf_ret']; o = r['orig_ret']; i = r['idx_ret']; h = r['hs_ret']
        exc = f - o
        sgn = lambda v: ("+" if v>=0 else "") + f"{v:.2f}%"
        lines.append(f"| {r['rb_date']} | {sgn(f)} | {sgn(o)} | {sgn(i)} | {sgn(h)} | {sgn(exc)} |")
else:
    lines.append("| 调仓日 | 800红利+FCF | 932368 | 沪深300 | 超额(vs 932368) |")
    lines.append("|--------|:------:|:------:|:-------:|:----------------:|")
    for _, r in m.iterrows():
        f = r['fcf_ret']; i = r['idx_ret']; h = r['hs_ret']
        exc = f - i
        sgn = lambda v: ("+" if v>=0 else "") + f"{v:.2f}%"
        lines.append(f"| {r['rb_date']} | {sgn(f)} | {sgn(i)} | {sgn(h)} | {sgn(exc)} |")
lines.append("")

# 结论
lines.append("---")
lines.append("")
lines.append("## 六、综合结论")
lines.append("")
lines.append(f"1. **年化收益**：800红利+FCF年化 **{fcf_s['ann']:.2f}%**，"
             f"原800红利 **{orig_s['ann']:.2f}%**，差异 **{fcf_s['ann']-orig_s['ann']:+.2f}pp**")
diff = fcf_s['ann'] - orig_s['ann']
if diff > 0.5:
    lines.append(f"   ✅ FCF过滤有效提升收益（+{diff:.2f}pp），剔除FCF为负的\"伪红利股\"确实改善了组合质量")
elif diff > -0.5:
    lines.append(f"   ➡️ FCF过滤影响有限（{diff:+.2f}pp），红利策略中FCF因子的边际贡献不大")
else:
    lines.append(f"   ❌ FCF过滤反而降低收益（{diff:.2f}pp），可能因过滤掉了某些高分红的周期股")
lines.append(f"2. **最大回撤**：FCF版 {abs(fcf_s['mdd']):.2f}% vs 原版 {abs(orig_s['mdd']):.2f}%，"
             f"{'改善' if abs(fcf_s['mdd'])<abs(orig_s['mdd']) else '恶化'} "
             f"{abs(abs(fcf_s['mdd'])-abs(orig_s['mdd'])):.2f}pp")
lines.append(f"3. **vs 932368**：FCF版年化{'超' if fcf_s['ann']>idx_s['ann'] else '低'}于中证800现金流指数 "
             f"{abs(fcf_s['ann']-idx_s['ann']):.2f}pp")
lines.append(f"4. **vs 沪深300**：FCF版年化超过沪深300全收益 {fcf_s['ann']-hs_s['ann']:+.2f}pp")
lines.append(f"5. **换手率**：{fcf_to:.1f}%")
lines.append(f"6. **期末净值**：FCF版 {fcf_s['nav']:.3f}x | 原版 {orig_s['nav']:.3f}x")
lines.append("")

lines.append("---")
lines.append(f"*报告自动生成，计算日期：{now}*")
lines.append(f"*数据来源：Tushare Pro，复现代码：run_800div_fcf_filter.py + dividend_universe.py*")

report = "\n".join(lines)
report_path = PROJECT_ROOT / "docs" / "2026-06-16_800红利FCF过滤实验.md"
with open(report_path, "w") as f:
    f.write(report)

# ═══════════════ 输出摘要 ═══════════════
print("\n" + "=" * 80)
print("800红利+FCF过滤回测完成！")
print("=" * 80)
print(f"\n{'指标':<18} {'800红利+FCF':>12} {'原800红利':>12} {'932368':>12} {'沪深300':>12}")
print("-" * 72)
for label, key in [("年化收益", "ann"), ("最大回撤", "mdd"), ("夏普比率", "sharpe"),
                   ("期末NAV", "nav")]:
    if key in ['ann', 'mdd']:
        vals = [f"{fcf_s[key]:.2f}%", f"{orig_s[key]:.2f}%", f"{idx_s[key]:.2f}%", f"{hs_s[key]:.2f}%"]
    elif key == 'sharpe':
        vals = [f"{fcf_s[key]:.3f}", f"{orig_s[key]:.3f}", f"{idx_s[key]:.3f}", f"{hs_s[key]:.3f}"]
    else:
        vals = [f"{fcf_s[key]:.3f}x", f"{orig_s[key]:.3f}x", f"{idx_s[key]:.3f}x", f"{hs_s[key]:.3f}x"]
    print(f"{label:<18} " + " ".join(f"{v:>12}" for v in vals))
print(f"\n换手率: 800红利+FCF {fcf_to:.1f}%")
print(f"\nvs 原800红利 年化差异: {fcf_s['ann']-orig_s['ann']:+.2f}pp")

# 逐年摘要
print(f"\n逐年收益：")
print(f"{'年份':<6} {'FCF版':>8} {'原版':>8} {'932368':>8} {'沪深300':>8}")
for yr in sorted(years):
    f = ar_fcf.get(yr, 0) * 100
    o = ar_orig.get(yr, 0) * 100
    i = ar_idx.get(yr, 0) * 100
    h = ar_hs.get(yr, 0) * 100
    row = (f"{yr:<6} " + ("+" if f>=0 else "") + f"{f:>7.2f}% "
           + ("+" if o>=0 else "") + f"{o:>7.2f}% "
           + ("+" if i>=0 else "") + f"{i:>7.2f}% "
           + ("+" if h>=0 else "") + f"{h:>7.2f}%")
    print(row)

print(f"\n✅ 报告: {report_path}")
print(f"✅ 选股输出: {OUT_DIR}/")
