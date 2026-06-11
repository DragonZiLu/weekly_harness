#!/usr/bin/env python3
"""run_800div_full.py — 中证800红利指数（931644）复现回测：选股→NAV→报告"""
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
    "2015-06-15",  # 2nd Fri=06-12
    "2015-12-14",  # 2nd Fri=12-11
    "2016-06-13",  # 2nd Fri=06-10
    "2016-12-12",  # 2nd Fri=12-09
    "2017-06-12",  # 2nd Fri=06-09
    "2017-12-11",  # 2nd Fri=12-08
    "2018-06-11",  # 2nd Fri=06-08
    "2018-12-17",  # 2nd Fri=12-14
    "2019-06-17",  # 2nd Fri=06-14
    "2019-12-16",  # 2nd Fri=12-13
    "2020-06-15",  # 2nd Fri=06-12
    "2020-12-14",  # 2nd Fri=12-11
    "2021-06-14",  # 2nd Fri=06-11
    "2021-12-13",  # 2nd Fri=12-10
    "2022-06-13",  # 2nd Fri=06-10
    "2022-12-12",  # 2nd Fri=12-09
    "2023-06-12",  # 2nd Fri=06-09
    "2023-12-11",  # 2nd Fri=12-08
    "2024-06-17",  # 2nd Fri=06-14
    "2024-12-16",  # 2nd Fri=12-13
    "2025-06-16",  # 2nd Fri=06-13
    "2025-12-15",  # 2nd Fri=12-12
    "2026-06-15",  # 2nd Fri=06-12
]

TOP_N = 100
CAP = 0.10
MAX_TURNOVER = 0.20
OUT_DIR = PROJECT_ROOT / "output" / "800div"
OUT_DIR.mkdir(parents=True, exist_ok=True)
X_OUT_DIR = PROJECT_ROOT / "output" / "800div_x"
X_OUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════ 第一步：选股 ═══════════════
all_baskets = {}

if RUN_SELECTION:
    print("=" * 70)
    print("第一步：800红利 X版（全合规成分股息率加权，Smart Beta基准）")
    print("=" * 70)

    uni = DividendUniverse(index_code="000906.SH")
    uni.preload_all(rebalance_dates=REBALANCE_DATES)

    # ── X版：全合规成分股息率加权（不做Top100截断）──
    x_baskets = {}
    t_x = time.time()
    for i, date_str in enumerate(REBALANCE_DATES):
        cons = uni._idx_cache.get_constituents(date_str)
        eligible = []
        for ts_code in cons:
            if ts_code not in uni._dps_by_year: continue
            if not uni._check_consecutive_dividends(ts_code, date_str): continue
            if not uni._check_payout_ratio(ts_code, date_str): continue
            dy = uni._calc_avg_dividend_yield(ts_code, date_str)
            if dy is None or dy <= 0: continue
            info = uni._get_stock_info(ts_code)
            eligible.append({
                'ts_code': ts_code,
                'name': info.get('name', ts_code) if info else ts_code,
                'industry': info.get('industry', '') if info else '',
                'div_yield_3y': round(dy, 4),
            })
        eligible.sort(key=lambda x: x['div_yield_3y'], reverse=True)
        uni._apply_dividend_weighting(eligible, cap=0.10)
        x_baskets[date_str] = eligible
        # 只打前3期+每5期
        if i < 3 or i % 5 == 0:
            avg_y = sum(s['div_yield_3y'] for s in eligible) / len(eligible) if eligible else 0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: {len(eligible)}只 "
                  f"avg_y={avg_y:.2f}% ({time.time()-t_x:.0f}s)")

    with open(X_OUT_DIR / "all_baskets_2015_2026.json", "w") as f:
        json.dump(x_baskets, f, ensure_ascii=False, indent=2)
    x_valid = sum(1 for d in x_baskets if len(x_baskets[d]) >= 100)
    print(f"  ✅ X版: {x_valid}/{len(x_baskets)}期有效 → {X_OUT_DIR}/")
    all_baskets['X'] = x_baskets

    print(f"\n{'='*70}")
    print("第一步b：800红利 Top100版（931644复现）")
    print("=" * 70)

    baskets = {}
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
            )
            stocks = sorted(raw.values(), key=lambda x: x["weight"], reverse=True)
            baskets[date_str] = stocks
            prev_codes = {s["ts_code"] for s in stocks}
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: "
                  f"{len(stocks)}只, 首仓={stocks[0]['name'] if stocks else 'N/A'} "
                  f"({stocks[0]['weight']*100:.1f}% if stocks else 'N/A') "
                  f"({elapsed:.0f}s)")
        except Exception as ex:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
            baskets[date_str] = []

    with open(OUT_DIR / "all_baskets_2015_2026.json", "w") as f:
        json.dump(baskets, f, ensure_ascii=False, indent=2)

    valid = sum(1 for d in baskets if len(baskets[d]) >= 10)
    print(f"  ✅ 800红利: {valid}/{len(baskets)}期有效 → {OUT_DIR}/")

else:
    print("  加载已有 baskets...")
    with open(OUT_DIR / "all_baskets_2015_2026.json") as f:
        baskets = json.load(f)
    print(f"  ✅ 加载 800红利 Top100: {len(baskets)} 期")
    x_path = X_OUT_DIR / "all_baskets_2015_2026.json"
    if x_path.exists():
        with open(x_path) as f:
            x_baskets = json.load(f)
        print(f"  ✅ 加载 800红利 X版: {len(x_baskets)} 期")
    else:
        x_baskets = {}

    all_baskets["800红利"] = baskets
    all_baskets["X"] = x_baskets

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
# 932368 全收益（价格 × 股息调整比例）
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

# ── 800红利 Top100 NAV ──
print("  计算 800红利 Top100 NAV...")
nav_div = calc_nav(baskets)
nav_div.to_csv(OUT_DIR / "backtest_nav_tr.csv", index=False)
final_nav = nav_div['nav'].iloc[-1] if len(nav_div) > 0 else 0
print(f"  800红利 Top100: {len(nav_div)}期, 期末NAV={final_nav:.4f}x")

# ── 800红利 X版 NAV（全合规成分股息率加权） ──
x_baskets_nav = all_baskets.get('X', {})
if x_baskets_nav:
    print("  计算 800红利 X版 NAV...")
    nav_x = calc_nav(x_baskets_nav, min_stocks=100, min_weight=0.5)
    nav_x.to_csv(X_OUT_DIR / "backtest_nav_tr.csv", index=False)
    x_final_nav = nav_x['nav'].iloc[-1] if len(nav_x) > 0 else 0
    print(f"  800红利 X版: {len(nav_x)}期, 期末NAV={x_final_nav:.4f}x")
else:
    nav_x = pd.DataFrame()

# ── 加载E版NAV对比 ──
e_nav_path = PROJECT_ROOT / "output/zz800_fcf_lenient_buffer_e40/backtest_nav_tr.csv"
e_nav_data = None
e_annual = None
if e_nav_path.exists():
    e_nav_data_raw = pd.read_csv(e_nav_path)
    # E版是季度调仓（45期=11.25年），直接用其独立的NAV链计算年化
    e_final_nav_raw = e_nav_data_raw['nav'].iloc[-1]
    e_years = len(e_nav_data_raw) / 4  # 每年4期
    e_annual = (e_final_nav_raw ** (1/e_years) - 1) * 100
    # 提取E版在各调仓日的NAV（用最近rb_date的NAV近似）
    e_nav_by_date = dict(zip(e_nav_data_raw['rb_date'], e_nav_data_raw['nav']))

    print(f"  E版基准: {len(e_nav_data_raw)}期(季度), 期末NAV={e_final_nav_raw:.4f}x, "
          f"年化={e_annual:.2f}%")

# ═══════════════ 第三步：生成报告 ═══════════════
print("\n" + "=" * 70)
print("第三步：生成对比报告")
print("=" * 70)

m = nav_div[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','div_ret','div_nav']

# 计算基准区间收益
m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)

# 基准净值链
i_n, h_n = 1.0, 1.0
i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['idx_nav'] = i_navs
m['hs_nav']  = h_navs

# 超额
m['div_exc'] = m['div_ret'] - m['idx_ret']
m['div_exc_hs'] = m['div_ret'] - m['hs_ret']

# ── 添加 X 版到对比 ──
if len(nav_x) > 0:
    nx = nav_x[['rb_date','next_rb','period_ret','nav']].copy()
    nx.columns = ['rb_date','next_rb','x_ret','x_nav']
    m = m.merge(nx, on=['rb_date','next_rb'], how='left')

def stats(rc, nc, data=m):
    rets = data[rc].dropna(); navs = data[nc].dropna()
    if len(rets) < 2:
        return dict(ann=0, vol=0, mdd=0, sharpe=0, calmar=0, win=0, nav=0)
    n = len(rets)
    # 半年度调仓：n期 = n/2 年
    # 年化 = (期末NAV)^(1/年数) - 1 = (期末NAV)^(2/n) - 1
    years = n * 0.5
    ann  = (navs.iloc[-1]**(1/years)-1)*100 if years > 0 else 0
    vol  = rets.std()*np.sqrt(2)         # 年化波动率（半年→年）
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

div_s = stats('div_ret','div_nav')
idx_s = stats('idx_ret','idx_nav')
hs_s  = stats('hs_ret','hs_nav')
x_s   = stats('x_ret','x_nav') if 'x_ret' in m.columns else dict(ann=0,vol=0,mdd=0,sharpe=0,calmar=0,win=0,nav=0)
div_to = turnover(baskets)
x_to   = turnover(x_baskets_nav) if x_baskets_nav else 0

# ── 逐年收益 ──
def yearly_rets(rc, data=m):
    yr_ret = {}
    for yr in sorted(data['rb_date'].str[:4].unique()):
        rows = data[data['rb_date'].str[:4]==yr]
        if len(rows) > 0:
            yr_ret[yr] = (1+rows[rc]/100).prod()-1
    return yr_ret

ar_div = yearly_rets('div_ret')
ar_idx = yearly_rets('idx_ret')
ar_hs  = yearly_rets('hs_ret')
ar_x   = yearly_rets('x_ret') if 'x_ret' in m.columns else {}

# ── 生成Markdown报告 ──
now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)

lines = []
lines.append("# 中证800红利指数（931644）复现回测报告")
lines.append("")
lines.append(f"> 生成时间：{now}")
lines.append(f"> 回测区间：{m['rb_date'].iloc[0]} → {m['next_rb'].iloc[-1]}（共{N}期，半年度调仓）")
lines.append("> 选股逻辑：三年连续分红 + 股利支付率过滤 + 三年平均股息率Top100")
lines.append("> 加权方式：股息率加权 + 单股10%封顶")
lines.append("> 换手限制：每期换手率 ≤ 20%")
lines.append("")

lines.append("---")
lines.append("")
lines.append("## 一、策略说明")
lines.append("")
lines.append("| 项目 | 内容 |")
lines.append("|------|------|")
lines.append("| 对标指数 | 中证800红利（931644） |")
lines.append("| 样本空间 | 中证800（000906.SH） |")
lines.append("| 选股数 | Top 100 / X版全合规成分 |")
lines.append("| 选股指标 | 过去三年平均现金股息率 |")
lines.append("| 过滤条件 | 连续三年分红 + 股利支付率∈(0,1) |")
lines.append("| 加权方式 | 股息率加权，10%封顶 |")
lines.append("| 调仓频率 | 半年度（6月/12月） |")
lines.append("| 换手限制 | ≤20%（Top100版） / 无（X版） |")
lines.append("| 基日 | 2013-12-31，基点1000 |")
lines.append("")

lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")

headers = ["指标", "800红利", "800红利X", "932368", "沪深300"]
lines.append("| " + " | ".join(headers) + " |")
sep = "|" + "|".join(["------"] * len(headers)) + "|"
lines.append(sep)

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
    vals.append(fmt_s.format(div_s[key]))
    vals.append(fmt_s.format(x_s[key]))
    vals.append(fmt_s.format(idx_s[key]))
    vals.append(fmt_s.format(hs_s[key]))
    lines.append("| " + " | ".join(vals) + " |")

lines.append("")
lines.append(f"> **换手率**：800红利Top100 {div_to:.1f}%（限制≤20%）| 800红利X {x_to:.1f}%（无限制）")
if e_annual is not None:
    lines.append(f"> **E版(FCF策略)参考**：年化 **{e_annual:.2f}%**（季度调仓，2015-03→2026-06共45期，同期可比口径）")
lines.append("")

# 超额
lines.append("---")
lines.append("")
lines.append("## 三、超额收益分析")
lines.append("")
lines.append(f"- vs 932368 年化超额：{div_s['ann']-idx_s['ann']:+.2f}pp")
lines.append(f"- vs 沪深300 年化超额：{div_s['ann']-hs_s['ann']:+.2f}pp")
lines.append(f"- X版 vs Top100版 年化差异：{x_s['ann']-div_s['ann']:+.2f}pp（X版全合规成分加权，Top100版精选）")
if e_annual is not None:
    lines.append(f"- vs E版(FCF策略) 年化差异：{div_s['ann']-e_annual:+.2f}pp（注：E版为季度调仓全周期结果）")
lines.append(f"- vs 932368 超额胜率：{(m['div_exc']>0).mean()*100:.1f}%")
lines.append(f"- vs 沪深300 超额胜率：{(m['div_exc_hs']>0).mean()*100:.1f}%")
lines.append("")

# 逐年对比
years = sorted(set(list(ar_div.keys()) + list(ar_idx.keys()) + list(ar_hs.keys()) + list(ar_x.keys())))
lines.append("---")
lines.append("")
lines.append("## 四、逐年收益对比")
lines.append("")

yr_headers = ["年份", "800红利", "800红利X", "932368", "沪深300", "最佳"]
lines.append("| " + " | ".join(yr_headers) + " |")
yr_sep = "|" + "|".join(["------"] * len(yr_headers)) + "|"
lines.append(yr_sep)

for yr in sorted(years):
    d = ar_div.get(yr, 0) * 100
    x = ar_x.get(yr, 0) * 100
    i = ar_idx.get(yr, 0) * 100
    h = ar_hs.get(yr, 0) * 100

    all_vals = [("800红利", d), ("800红利X", x), ("932368", i), ("沪深300", h)]
    best = max(all_vals, key=lambda v: v[1])

    def fmt(v):
        sgn = "+" if v >= 0 else ""
        return f"{'**' if v == best[1] else ''}{sgn}{v:.2f}%{'**' if v == best[1] else ''}"

    row = f"| {yr} | {fmt(d)} | {fmt(x)} | {fmt(i)} | {fmt(h)} | {best[0]} |"
    lines.append(row)

lines.append("")

# 逐期明细
lines.append("---")
lines.append("")
lines.append("## 五、逐期收益明细")
lines.append("")
lines.append("| 调仓日 | 800红利 | 932368 | 沪深300 | 超额(800-932368) |")
lines.append("|--------|:------:|:------:|:-------:|:----------------:|")
for _, r in m.iterrows():
    dd = r['div_ret']; ii = r['idx_ret']; hh = r['hs_ret']
    exc = dd - ii
    sgn = lambda v: ("+" if v>=0 else "") + f"{v:.2f}%"
    lines.append(f"| {r['rb_date']} | {sgn(dd)} | {sgn(ii)} | {sgn(hh)} | {sgn(exc)} |")
lines.append("")

# 结论
lines.append("---")
lines.append("")
lines.append("## 六、综合结论")
lines.append("")

lines.append(f"1. **年化收益**：800红利Top100复现年化 **{div_s['ann']:.2f}%**，X版(全合规成分) **{x_s['ann']:.2f}%**。"
             f" Top100 vs X版差异 {div_s['ann']-x_s['ann']:+.2f}pp")
if e_annual is not None:
    if div_s['ann'] > e_annual:
        lines.append(f"   **跑赢** E版FCF策略（{e_annual:.2f}%），高股息策略在此期间表现更优")
    else:
        lines.append(f"   低于E版FCF策略（{e_annual:.2f}%），FCF质量因子在此回测区间更强")
lines.append(f"2. **vs 932368**：800红利年化{'超' if div_s['ann']>idx_s['ann'] else '低'}于中证800现金流指数"
             f" {abs(div_s['ann']-idx_s['ann']):.2f}pp")
lines.append(f"3. **vs 沪深300**：800红利年化超过沪深300全收益 {div_s['ann']-hs_s['ann']:+.2f}pp")
lines.append(f"4. **最大回撤**：{abs(div_s['mdd']):.2f}%，{'优于' if abs(div_s['mdd'])<abs(hs_s['mdd']) else '差于'}"
             f" 沪深300的{abs(hs_s['mdd']):.2f}%")
lines.append(f"5. **换手率**：{div_to:.1f}%（限制20%）| X版 {x_to:.1f}%（无限制）")
lines.append(f"6. **期末净值**：Top100 {div_s['nav']:.3f}x | X版 {x_s['nav']:.3f}x")

lines.append("")
lines.append("---")
lines.append(f"*报告自动生成，计算日期：{now}*")
lines.append(f"*数据来源：Tushare Pro，复现代码：run_800div_full.py + dividend_universe.py*")

report = "\n".join(lines)
report_path = PROJECT_ROOT / "docs" / "2026-06-11_800红利指数复现.md"
with open(report_path, "w") as f:
    f.write(report)

# ═══════════════ 输出摘要 ═══════════════
print("\n" + "=" * 80)
print("800红利指数复现回测完成！")
print("=" * 80)
print(f"\n{'指标':<18} {'800红利':>10} {'800红利X':>10} {'932368':>10} {'沪深300':>10}")
print("-" * 68)
for label, key in [("年化收益", "ann"), ("最大回撤", "mdd"), ("夏普比率", "sharpe"),
                   ("换手率", "to"), ("期末NAV", "nav")]:
    if key == 'to':
        vals = [f"{div_to:.1f}%", f"{x_to:.1f}%", "—", "—"]
    elif key in ['ann', 'mdd']:
        vals = [f"{div_s[key]:.2f}%", f"{x_s[key]:.2f}%", f"{idx_s[key]:.2f}%", f"{hs_s[key]:.2f}%"]
    elif key == 'sharpe':
        vals = [f"{div_s[key]:.3f}", f"{x_s[key]:.3f}", f"{idx_s[key]:.3f}", f"{hs_s[key]:.3f}"]
    else:
        vals = [f"{div_s[key]:.3f}x", f"{x_s[key]:.3f}x", f"{idx_s[key]:.3f}x", f"{hs_s[key]:.3f}x"]
    print(f"{label:<18} " + " ".join(f"{v:>10}" for v in vals))

print(f"\n对比：E版(FCF策略) 年化={e_annual:.2f}% (季度调仓, 2015-03→2026-06)")

# 逐年摘要
print(f"\n逐年收益：")
print(f"{'年份':<6} {'Top100':>8} {'X版':>8} {'932368':>8} {'沪深300':>8}")
for yr in sorted(years):
    d = ar_div.get(yr, 0) * 100
    x = ar_x.get(yr, 0) * 100
    i = ar_idx.get(yr, 0) * 100
    h = ar_hs.get(yr, 0) * 100
    row = f"{yr:<6} " + ("+" if d>=0 else "") + f"{d:>7.2f}% " + ("+" if x>=0 else "") + f"{x:>7.2f}% " + ("+" if i>=0 else "") + f"{i:>7.2f}% " + ("+" if h>=0 else "") + f"{h:>7.2f}%"
    print(row)

print(f"\n✅ 报告: docs/2026-06-11_800红利指数复现.md")
print(f"✅ 选股输出: Top100 → {OUT_DIR}/ | X版 → {X_OUT_DIR}/")
print(f"✅ NAV输出: {OUT_DIR}/, {X_OUT_DIR}/")
