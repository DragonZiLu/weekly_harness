#!/usr/bin/env python3
"""HS300 B/D/E/F/X 五版全流程：选股→回测→报告"""
import sys, json, time, argparse
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime
from compute_nav_cached import get_adj_close_cached

# ═══════════════ 参数解析 ═══════════════
parser = argparse.ArgumentParser()
parser.add_argument('--x-only',   action='store_true', help='只跑X版选股，不算NAV和报告')
parser.add_argument('--nav-only', action='store_true', help='跳过选股，用已有basket算NAV+报告')
parser.add_argument('--skip-x',   action='store_true', help='跳过X版选股，从已有rankings导出BDEF')
args = parser.parse_args()

RUN_X_BASKET  = not args.nav_only and not args.skip_x
RUN_BDEF      = not args.nav_only and not args.x_only
RUN_NAV       = not args.x_only
RUN_REPORT    = not args.x_only

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from fcf_universe import FcfUniverse

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

TOP_N = 50
CAP = 0.10

# ★ HS300 版本配置（目录改为 hs300_fcf_*）
DERIVED_VERSIONS = [
    ("B", 0.00, 50, 50, "output/hs300_fcf_fixed_lenient"),
    ("D", 0.20, 40, 60, "output/hs300_fcf_lenient_buffer"),
    ("E", 0.40, 30, 70, "output/hs300_fcf_lenient_buffer_e40"),
    ("F", 0.50, 25, 75, "output/hs300_fcf_lenient_buffer_f50"),
]
X_OUT_DIR = "output/hs300_fcf_full_universe"
VERSIONS = [("B", "output/hs300_fcf_fixed_lenient"),
            ("D", "output/hs300_fcf_lenient_buffer"),
            ("E", "output/hs300_fcf_lenient_buffer_e40"),
            ("F", "output/hs300_fcf_lenient_buffer_f50"),
            ("X", X_OUT_DIR)]

def fcf_weights(stocks, cap=CAP, max_iter=100):
    if not stocks: return stocks
    fcf_vals = [max(s.get('fcf',0),0) for s in stocks]
    total = sum(fcf_vals)
    if total <= 0:
        w = 1.0/len(stocks)
        for s in stocks: s['weight'] = round(w,6)
        return stocks
    weights = [v/total for v in fcf_vals]
    for _ in range(max_iter):
        overflow = sum(w-cap for w in weights if w>cap)
        if overflow < 1e-9: break
        capped = [min(w,cap) for w in weights]
        below = sum(c for c in capped if c<cap)
        if below <= 0: break
        weights = [min(c+overflow*(c/below),cap) if c<cap else cap for c in capped]
    tw = sum(weights)
    for s,w in zip(stocks,weights): s['weight'] = round(w/tw,6)
    return stocks

def apply_buffer(ranked, prev_codes, low, high, top_n):
    must = ranked[:low]
    buffer = ranked[low:high]
    buffer_old = [s for s in buffer if s['ts_code'] in prev_codes]
    buffer_new = [s for s in buffer if s['ts_code'] not in prev_codes]
    selected = must + buffer_old
    remaining = top_n - len(selected)
    if remaining > 0: selected.extend(buffer_new[:remaining])
    return selected[:top_n]

# ═══════════════ 第一步：X版选股（★ HS300唯一调用 get_fcf_basket） ═══════════════
all_baskets = {}
x_out = PROJECT_ROOT / X_OUT_DIR
x_out.mkdir(parents=True, exist_ok=True)

if RUN_X_BASKET:
    print("=" * 70)
    print("第一步：HS300 X版选股（全成分FCF，保存完整排名池供B/D/E/F导出）")
    print("=" * 70)

    uni = FcfUniverse(index_code="000300.SH", strict_ocf=False)  # ★ HS300
    uni.preload_all(download=False)

    x_baskets, x_rankings = {}, {}
    t0 = time.time()

    for i, date_str in enumerate(REBALANCE_DATES):
        try:
            raw = uni.get_fcf_basket(date_str, top_n=300, verbose=False, use_ttm=True)  # HS300最多300只
            ranked = [dict(v, ts_code=k) for k,v in raw.items()
                      if k != "__quality_warnings__" and isinstance(v, dict)]
            ranked.sort(key=lambda x: x.get('fcf_yield',0), reverse=True)
            stocks = [dict(s) for s in ranked]
            fcf_weights(stocks)
            x_baskets[date_str] = stocks
            x_rankings[date_str] = ranked
            elapsed = time.time()-t0
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: {len(stocks)}只 ({elapsed:.0f}s)")
        except Exception as ex:
            print(f"  [{i+1}/{len(REBALANCE_DATES)}] {date_str}: ERROR — {ex}")
            x_baskets[date_str] = []
            x_rankings[date_str] = []

    with open(x_out / "all_baskets_2015_2026.json", "w") as f:
        json.dump(x_baskets, f, ensure_ascii=False, indent=2)
    with open(x_out / "rankings_2015_2026.json", "w") as f:
        json.dump(x_rankings, f, ensure_ascii=False, indent=2)
    x_valid = sum(1 for d in x_baskets if len(x_baskets[d]) >= 10)
    print(f"  ✅ HS300 X版: {x_valid}/{len(x_baskets)}期有效 → {X_OUT_DIR}/")
    if args.x_only:
        print("  --x-only 模式，退出")
        sys.exit(0)
else:
    print("  加载已有HS300 X版 baskets + rankings...")
    with open(x_out / "all_baskets_2015_2026.json") as f:
        x_baskets = json.load(f)
    rp = x_out / "rankings_2015_2026.json"
    x_rankings = json.load(open(rp)) if rp.exists() else {}
    print(f"  ✅ 加载 {len(x_baskets)} 期X版basket, {len(x_rankings)} 期rankings")

all_baskets['X'] = x_baskets

# ═══════════════ 第一步b：从X版排名池导出B/D/E/F ═══════════════
if RUN_BDEF:
    print("\n" + "=" * 70)
    print("第一步b：从X版排名池导出B/D/E/F（零FCF计算）")
    print("=" * 70)

    for ver_name, buf_ratio, low, high, out_dir in DERIVED_VERSIONS:
        out_path = PROJECT_ROOT / out_dir
        out_path.mkdir(parents=True, exist_ok=True)
        print(f"\n--- {ver_name}版 (buffer=±{int(buf_ratio*100)}%, low={low}, high={high}) ---")

        baskets, prev_codes = {}, set()
        for i, date_str in enumerate(REBALANCE_DATES):
            ranked = x_rankings.get(date_str, [])
            if not ranked:
                baskets[date_str] = []; continue
            if i == 0 or not prev_codes:
                stocks = [dict(s) for s in ranked[:TOP_N]]
            else:
                stocks = [dict(s) for s in apply_buffer(ranked, prev_codes, low, high, TOP_N)]
            fcf_weights(stocks)
            baskets[date_str] = stocks
            prev_codes = {s['ts_code'] for s in stocks}

        with open(out_path / "all_baskets_2015_2026.json", "w") as f:
            json.dump(baskets, f, ensure_ascii=False, indent=2)
        valid = sum(1 for d in baskets if len(baskets[d]) >= 10)
        print(f"  ✅ {ver_name}版: {valid}/{len(baskets)}期有效 → {out_dir}/")
        all_baskets[ver_name] = baskets
else:
    print("  加载已有B/D/E/F baskets...")
    for ver_name, buf_ratio, low, high, out_dir in DERIVED_VERSIONS:
        bp = PROJECT_ROOT / out_dir / "all_baskets_2015_2026.json"
        if bp.exists():
            with open(bp) as f:
                all_baskets[ver_name] = json.load(f)
            print(f"    {ver_name}版: {len(all_baskets[ver_name])}期 ✅")
        else:
            print(f"    {ver_name}版: 无basket ⚠️")

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
# ★ 全收益基准: 932366价格指数 + 股息调整比例估算全收益
df_idx_price = pd.read_csv("data/932366_daily.csv")
df_idx_price['trade_date'] = df_idx_price['trade_date'].astype(str)
df_idx_price = df_idx_price[['trade_date','close']].rename(columns={'close':'p'}).sort_values('trade_date')

# 沪深300价格指数 → 计算股息调整比例
df_hs_p = pd.read_csv("data/index_daily/000300.SH.csv")
df_hs_p['trade_date'] = df_hs_p['trade_date'].astype(str)
df_hs_p = df_hs_p[['trade_date','close']].rename(columns={'close':'hs_p'})
df_hs_tr = pd.read_csv("data/index_daily/H00300.CSI.csv")
df_hs_tr['trade_date'] = df_hs_tr['trade_date'].astype(str)
df_hs_tr = df_hs_tr[['trade_date','close']].rename(columns={'close':'hs_tr'})

# 合并: 股息调整比例 = H00300/000300
df_div = df_hs_p.merge(df_hs_tr, on='trade_date', how='inner')
df_div['div_adj'] = df_div['hs_tr'] / df_div['hs_p']

# 932366全收益估算 = 价格 × div_adj
df_idx = df_idx_price.merge(df_div[['trade_date','div_adj']], on='trade_date', how='inner')
df_idx['close'] = df_idx['p'] * df_idx['div_adj']  # ★ 全收益估算

# HS300全收益基准（直接从H00300加载，避免rename链错误）
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

nav_results = {}
for ver_name, out_dir in VERSIONS:
    if ver_name not in all_baskets:
        print(f"  ⚠️ 跳过 {ver_name}版: 无basket数据")
        continue
    print(f"  计算 {ver_name}版 NAV...")
    nav_df_ver = calc_nav(all_baskets[ver_name])
    out_path = PROJECT_ROOT / out_dir
    out_path.mkdir(parents=True, exist_ok=True)
    nav_df_ver.to_csv(out_path / "backtest_nav_tr.csv", index=False)
    nav_results[ver_name] = nav_df_ver
    final_nav = nav_df_ver['nav'].iloc[-1] if len(nav_df_ver) > 0 else 0
    print(f"    {ver_name}版: {len(nav_df_ver)}期, 期末NAV={final_nav:.3f}x")

# ═══════════════ 第三步：生成报告 ═══════════════
print("\n" + "=" * 70)
print("第三步：生成对比报告")
print("=" * 70)

b_nav = nav_results.get('B')
if b_nav is None:
    print("❌ B版NAV缺失，无法生成报告")
    sys.exit(1)

m = b_nav[['rb_date','next_rb','period_ret','nav']].copy()
m.columns = ['rb_date','next_rb','b_ret','b_nav']

for ver_name in ['D','E','F','X']:
    df = nav_results.get(ver_name)
    if df is None: continue
    m = m.merge(df[['rb_date','period_ret','nav']].rename(
        columns={'period_ret':ver_name.lower()+'_ret','nav':ver_name.lower()+'_nav'}), on='rb_date')

m['idx_ret'] = m.apply(lambda r: idx_ret(df_idx, r['rb_date'], r['next_rb']), axis=1)
m['hs_ret']  = m.apply(lambda r: idx_ret(df_hs, r['rb_date'], r['next_rb']), axis=1)
i_n, h_n = 1.0, 1.0; i_navs, h_navs = [], []
for _, r in m.iterrows():
    i_n *= (1+r['idx_ret']/100); i_navs.append(i_n)
    h_n *= (1+r['hs_ret']/100); h_navs.append(h_n)
m['i_nav'] = i_navs; m['h_idx_nav'] = h_navs
for v in ['b','d','e','f','x']:
    if v+'_ret' in m.columns:
        m[v+'_exc'] = m[v+'_ret'] - m['idx_ret']

def stats(rc, nc, data=m):
    rets = data[rc].dropna(); navs = data[nc].dropna()
    n = len(rets)
    ann  = (navs.iloc[-1]**(4/n)-1)*100
    vol  = rets.std()*2
    peak = navs.cummax()
    mdd  = ((peak-navs)/peak).max()*100
    sharpe = (ann-2.0)/vol if vol>0 else 0
    calmar = ann/mdd if mdd>0 else 0
    win  = (rets>0).mean()*100
    return dict(ann=ann,vol=vol,mdd=-mdd,sharpe=sharpe,calmar=calmar,win=win,nav=navs.iloc[-1])

def turnover(baskets):
    dates = [r['rb_date'] for _,r in nav_df.iterrows() if baskets.get(r['rb_date'])]
    tos = []
    for i in range(1, len(dates)):
        prev = {s['ts_code'] for s in baskets.get(dates[i-1],[])}
        curr = {s['ts_code'] for s in baskets.get(dates[i],[])}
        if curr: tos.append(len(curr-prev)/len(curr))
    return np.mean(tos)*100 if tos else 0

ver_stats = {}; ver_to = {}
for ver_name, out_dir in VERSIONS:
    rc = ver_name.lower()+'_ret'; nc = ver_name.lower()+'_nav'
    if rc not in m.columns: continue
    ver_stats[ver_name] = stats(rc, nc)
    ver_to[ver_name] = turnover(all_baskets[ver_name])

idx_s = stats('idx_ret','i_nav')
hs_s = stats('hs_ret','h_idx_nav')

# 年度
ar = {}
for yr in sorted(m['rb_date'].str[:4].unique()):
    rows = m[m['rb_date'].str[:4]==yr]
    ar[yr] = {}
    for v in ['B','D','E','F','X']:
        col = v.lower()+'_ret'
        if col in rows.columns:
            ar[yr][v] = (1+rows[col]/100).prod()-1
    ar[yr]['932366'] = (1+rows['idx_ret']/100).prod()-1
    ar[yr]['沪深300'] = (1+rows['hs_ret']/100).prod()-1

now = datetime.now().strftime("%Y-%m-%d %H:%M")
N = len(m)
bmap = {'B':'B版','D':'D版','E':'E版','F':'F版','X':'X版','932366':'932366','沪深300':'沪深300'}
buf_map = {'B':'±0%','D':'±20%','E':'±40%','F':'±50%','X':'全成分'}

best_ver = max(ver_stats, key=lambda k: ver_stats[k]['ann']) if ver_stats else 'B'

# ──── 生成报告 ────
lines = []
lines.append("# HS300 FCF 策略全版本回测报告（B/D/E/F/X 五版对比）")
lines.append("")
lines.append("> 生成时间：" + now)
lines.append("> 回测区间：" + str(m['rb_date'].iloc[0]) + " → " + str(m['next_rb'].iloc[-1]) + "（共 " + str(N) + " 期）")
lines.append("> 全收益模式（含分红再投资，复权价计算）")
lines.append("")

for vn in ['B','D','E','F','X']:
    if vn not in ver_stats:
        print(f"  ⚠️ {vn}版无统计数据，跳过报告相关行")

lines.append("---")
lines.append("")

lines.append("## 一、策略版本说明")
lines.append("")
lines.append("| 版本 | 缓冲区 | 选股方式 | 换手率 | 核心差异 |")
lines.append("|------|--------|----------|--------|----------|")
for vn in ['B','D','E','F','X']:
    if vn in ver_to and vn in ver_stats:
        desc = {
            'B': '纯FCF率排名Top50', 'D': '前40必选，41-60粘性',
            'E': '前30必选，31-70粘性', 'F': '前25必选，26-75粘性',
            'X': '不做Top50截断，所有合格公司FCF加权'
        }.get(vn, '')
        lines.append(f"| **{vn}版** | {buf_map[vn]} | Top50" + ("（缓冲区）" if vn in 'DEF' else "") + f" | {round(ver_to[vn],1)}% | {desc} |")
lines.append("| 932366 | — | — | — | 官方沪深300现金流TR基准 |")
lines.append("| 沪深300 | — | — | — | 大盘基准 |")
lines.append("")
lines.append("> **加权方式（五版一致）**：FCF绝对值加权 + 单股10%封顶迭代重分配")
lines.append("")

lines.append("---")
lines.append("")
lines.append("## 二、核心指标对比")
lines.append("")
# 表头
hdr_cols = "| 指标 |" + "|".join(f" {vn}版 |" for vn in ['B','D','E','F','X'] if vn in ver_stats) + " 932366 | 沪深300 |"
lines.append(hdr_cols)
sep_cols = "|------|" + "|".join("-----|" for _ in range(len([v for v in ['B','D','E','F','X'] if v in ver_stats]))) + "--------|---------|"
lines.append(sep_cols)

for metric, key in [('**年化收益**','ann'),('**最大回撤**','mdd'),('年化波动率','vol'),
                     ('夏普比率','sharpe'),('Calmar比率','calmar'),('单期胜率','win'),
                     ('平均换手率','to'),('期末净值','nav')]:
    row_parts = ["| " + metric]
    for vn in ['B','D','E','F','X']:
        if vn not in ver_stats: continue
        if key == 'to':
            v = str(round(ver_to[vn],1)) + "%"
        elif key == 'nav':
            v = str(round(ver_stats[vn]['nav'],3)) + "x"
        elif key == 'win':
            v = str(round(ver_stats[vn]['win'],1)) + "%"
        else:
            fmt_str = "%.2f" if key in ['ann','vol','mdd'] else "%.3f"
            v = fmt_str % ver_stats[vn][key] + ("%" if key in ['ann','vol','mdd'] else "")
        row_parts.append(v)
    for s_ref in [idx_s, hs_s]:
        if key in ['to']: row_parts.append("—")
        elif key == 'nav': row_parts.append(str(round(s_ref['nav'],3))+"x")
        elif key == 'win': row_parts.append(str(round(s_ref['win'],1))+"%")
        else:
            fmt_str = "%.2f" if key in ['ann','vol','mdd'] else "%.3f"
            row_parts.append(fmt_str % s_ref[key] + ("%" if key in ['ann','vol','mdd'] else ""))
    lines.append(" | ".join(row_parts) + " |")

# 三、逐年
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、逐年收益对比")
lines.append("")
hdr = "| 年份 |" + "".join(f" {v}版 |" for v in ['B','D','E','F','X'] if v in ver_stats) + " 932366 | 沪深300 | 🏆最佳 |"
lines.append(hdr)
lines.append("|" + "|".join(["------"] * (2 + len([v for v in ['B','D','E','F','X'] if v in ver_stats]) + 2)) + "|--------|")
for yr, rets in sorted(ar.items()):
    valid_versions = [v for v in ['B','D','E','F','X'] if v in rets]
    if not valid_versions: continue
    best = max(valid_versions, key=lambda v: rets[v])
    def fmt(v):
        s = ("+%s" if v>=0 else "%s") % str(round(v*100,2)) + "%"
        return "**"+s+"**" if rets.get(best)==v else s
    row = "| " + yr
    for v in ['B','D','E','F','X']:
        if v in rets:
            row += " | " + fmt(rets[v])
    row += " | " + fmt(rets.get('932366',0)) + " | " + fmt(rets.get('沪深300',0)) + " | " + bmap.get(best,best) + " |"
    lines.append(row)

# 四、综合结论
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、综合结论")
lines.append("")
x_in = 'X' in ver_stats
b_in = 'B' in ver_stats
if x_in and b_in:
    lines.append("1. **Top50筛选有效性**: " + ("✅ B版Top50年化高于X版全成分，FCF率排序截断有效" if ver_stats['B']['ann']>ver_stats['X']['ann'] else "⚠️ X版全成分年化高于B版Top50"))
if ver_stats:
    lines.append("2. **最优版本**: **" + best_ver + "版**（年化" + str(round(ver_stats[best_ver]['ann'],2)) + "%）")
    lines.append("3. **vs 932366**: 所有版本均" + ("跑赢" if all(ver_stats[v]['ann']>idx_s['ann'] for v in ver_stats) else "部分跑赢") + "官方基准（" + str(round(idx_s['ann'],2)) + "%）")
lines.append("")
lines.append("---")
lines.append("*报告自动生成，计算日期：" + now + "*")

report = "\n".join(lines)
report_path = "docs/hs300_bdefx_strategy_comparison.md"
with open(report_path, "w") as f:
    f.write(report)

# ═══════════════ 输出摘要 ═══════════════
print("\n" + "=" * 80)
print("HS300 五版回测完成！")
print("=" * 80)
print("")
print("版本   年化收益    最大回撤    夏普    换手率    期末NAV")
print("-" * 70)
for vn in ['B','D','E','F','X']:
    if vn not in ver_stats: continue
    s = ver_stats[vn]; to = ver_to.get(vn, 0)
    print(vn+"版   "+str(round(s['ann'],2)).rjust(7)+"%  "+str(round(s['mdd'],2)).rjust(8)+"%  "+
          str(round(s['sharpe'],3)).rjust(6)+"  "+str(round(to,1)).rjust(5)+"%  "+
          str(round(s['nav'],3)).rjust(7)+"x")
print("-" * 70)
print("932366 "+str(round(idx_s['ann'],2)).rjust(7)+"%  "+str(round(idx_s['mdd'],2)).rjust(8)+"%  "+
      str(round(idx_s['sharpe'],3)).rjust(6)+"     —  "+str(round(idx_s['nav'],3)).rjust(7)+"x")
print("沪深300 "+str(round(hs_s['ann'],2)).rjust(7)+"%  "+str(round(hs_s['mdd'],2)).rjust(8)+"%  "+
      str(round(hs_s['sharpe'],3)).rjust(6)+"     —  "+str(round(hs_s['nav'],3)).rjust(7)+"x")
print("")
print("逐年收益：")
print("年份    B       D       E       F       X     932366")
for yr, rets in sorted(ar.items()):
    row = yr+"  "
    for v in ['B','D','E','F','X']:
        if v in rets:
            r = rets[v]*100; row += ("+" if r>=0 else "")+str(round(r,2)).rjust(6)+"%  "
        else:
            row += "   —   "
    r366 = rets.get('932366', 0)*100
    row += ("+" if r366>=0 else "")+str(round(r366,2)).rjust(6)+"%"
    print(row)
print("")
print("✅ 报告: " + report_path)
