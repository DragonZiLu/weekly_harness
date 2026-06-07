"""P0: 自检 + 缺失诊断 合并执行（避免重复 API 调用）"""
import sys
from pathlib import Path
from collections import Counter

_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from weekly_harness.fcf_universe import FcfUniverse, IndexWeightCache, _is_financial_or_real_estate
import tushare as ts
from config.settings import tushare_cfg
import numpy as np

DATE = "2026-03-20"

# ── 一次性加载 ──
print("加载数据...")
uni = FcfUniverse(); uni.preload_all(download=False)
iwc = IndexWeightCache(); iwc.load()
pro = ts.pro_api(tushare_cfg.token)

# ── 获取 basket（唯一昂贵操作）──
basket = uni.get_fcf_basket(DATE, top_n=100, verbose=True)
our_codes = {k for k in basket if k != "__quality_warnings__"}

# ── 获取 932365 ──
avail = pro.index_weight(index_code="932365.CSI", start_date="20260301", end_date="20260401")
avail["trade_date"] = avail["trade_date"].astype(str)
closest = sorted(avail["trade_date"].unique())[0]
actual = avail[avail["trade_date"] == closest]
aw = dict(zip(actual["con_code"], actual["weight"]))
actual_codes = set(aw.keys())

info_map = uni._stock_basic.set_index("ts_code").to_dict("index") if uni._stock_basic is not None and not uni._stock_basic.empty else {}
csi_codes = set(iwc.get_constituents(DATE))

# ═══════════════════════════════════════════════════════════
#  P0-1: 入选标的自检
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  P0-1: 入选标的自检 ({len(our_codes)} 只)")
print(f"{'='*60}")

bad = {"no_fcf": [], "no_ev": [], "no_yield": [], "no_pq": [], "no_mv": [],
       "neg_fcf": [], "neg_ev": [], "neg_yield": []}
for c in our_codes:
    m = basket[c]
    if m["fcf"] is None:          bad["no_fcf"].append(c)
    elif m["fcf"] <= 0:           bad["neg_fcf"].append((c,m["fcf"]/1e8))
    if m["ev"] is None:           bad["no_ev"].append(c)
    elif m["ev"] <= 0:            bad["neg_ev"].append((c,m["ev"]/1e8))
    if m["fcf_yield"] is None:    bad["no_yield"].append(c)
    elif m["fcf_yield"] <= 0:     bad["neg_yield"].append((c,m["fcf_yield"]*100))
    if m["profit_quality"] is None: bad["no_pq"].append(c)
    if m["total_mv"] is None:     bad["no_mv"].append(c)

total_issues = sum(len(v) for v in bad.values())
print(f"  字段完整性: {'✅ 全部通过' if total_issues==0 else f'❌ {total_issues}项异常'}")
if total_issues > 0:
    for k,v in bad.items():
        if v: print(f"    {k}: {v[:3]}")

# 数值分布
reports = [(c, basket[c]["name"], basket[c]["fcf_yield"]*100,
            basket[c]["fcf"]/1e8, basket[c]["ev"]/1e8,
            basket[c]["total_mv"]/10000,  # 万元→亿元
            basket[c]["profit_quality"])
           for c in our_codes]
fy_vals = sorted([r[2] for r in reports])
mv_vals = sorted([r[5] for r in reports])

print(f"  FCF率: min={fy_vals[0]:.1f}% median={fy_vals[len(fy_vals)//2]:.1f}% max={fy_vals[-1]:.1f}%")
print(f"  市值:   min={mv_vals[0]:.1f}亿 P10={mv_vals[len(mv_vals)//10]:.1f}亿 median={mv_vals[len(mv_vals)//2]:.1f}亿 max={mv_vals[-1]:.1f}亿")

# 微盘股
micro = [r for r in reports if r[5] < 30]
print(f"  微盘股(<30亿): {len(micro)} 只" + (f" → {[(r[0],r[1],f'{r[5]:.0f}亿') for r in micro[:5]]}" if micro else ""))

# 与 932365 重叠
common = our_codes & actual_codes
print(f"  与932365重叠: {len(common)} 只")
if len(common) >= 3:
    from scipy.stats import spearmanr, pearsonr
    ow = [basket[c]["weight"]*100 for c in common]
    aw_v = [float(aw[c]) for c in common]
    print(f"  Spearman={spearmanr(ow,aw_v)[0]:.4f}  Pearson={pearsonr(ow,aw_v)[0]:.4f}")

# 行业分布
ind = Counter(basket[c]["industry"] for c in our_codes)
print(f"  行业Top5: {ind.most_common(5)}")

# ═══════════════════════════════════════════════════════════
#  P0-2: 缺失标的诊断
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  P0-2: 缺失标的诊断 (932365={closest})")
print(f"{'='*60}")

missing = actual_codes - our_codes
print(f"  缺失 {len(missing)} 只, 丢失总权重={sum(float(aw[c]) for c in missing):.1f}%")

# 计算 PQ cutoff（只用 csi_codes 中与 passed_turnover 重合的）
from datetime import datetime, timedelta as dt_td
# 简化：仅计算 CSI 全指中所有有数据的
pq_vals = []
for code in csi_codes:
    ry = uni._get_available_report_year(DATE, code)
    f = uni._fin_cache.get_annual_financials(code, ry)
    o = f["oper_cf"]; p = f["oper_profit"]; t = f["total_assets"]
    if o is not None and p is not None and t is not None and t > 0:
        pq_vals.append((o-p)/t)
pq_cutoff = np.percentile(pq_vals, 20) if pq_vals else float("-inf")
print(f"  PQ cutoff(全样本): {pq_cutoff:.4f} (n={len(pq_vals)})")

buckets = {"not_in_csi": [], "financial": [], "no_data": [],
           "neg_fcf": [], "bad_pq": [], "bad_5yr_ocf": [], "ranked_out": []}

for code in sorted(missing, key=lambda x: -float(aw[x])):
    w = float(aw[code])
    if code not in csi_codes:
        buckets["not_in_csi"].append((code, w)); continue
    ind_s = str(info_map.get(code, {}).get("industry", ""))
    if _is_financial_or_real_estate(ind_s):
        buckets["financial"].append((code, w)); continue

    ry = uni._get_available_report_year(DATE, code)
    fin = uni._fin_cache.get_annual_financials(code, ry)
    ocf = fin["oper_cf"]; capex = fin["capex"]; op = fin["oper_profit"]
    ta = fin["total_assets"]

    if ocf is None:
        buckets["no_data"].append((code, w, f"无OCF")); continue
    fcf = ocf - capex if capex is not None else None
    if fcf is None:
        buckets["no_data"].append((code, w, "无capex")); continue
    if fcf <= 0:
        buckets["neg_fcf"].append((code, w, f"FCF={fcf/1e8:.1f}亿")); continue

    pq = (ocf-op)/ta if (op is not None and ta is not None and ta>0) else None
    if pq is not None and pq < pq_cutoff:
        buckets["bad_pq"].append((code, w, f"PQ={pq:.4f}")); continue

    # 5yr OCF
    ld = str(info_map.get(code, {}).get("list_date", ""))
    ocf_s = ry - 4
    try:
        if ld and len(ld)>=4: ocf_s = max(ocf_s, int(ld[:4]))
    except: pass
    dok = ry >= 2019 or ocf_s > (ry-4)
    if not dok:
        buckets["bad_5yr_ocf"].append((code, w, f"数据不足 ry={ry}")); continue
    if not uni._fin_cache.check_5yr_positive_ocf(code, ry, start_year=ocf_s):
        bad_l = [f"{y}({uni._fin_cache.get_annual_financials(code,y)['oper_cf']})" 
                 for y in range(ocf_s, ry+1)
                 if (v:=uni._fin_cache.get_annual_financials(code,y)["oper_cf"]) is None or v<=0]
        buckets["bad_5yr_ocf"].append((code, w, f"失败:{bad_l}")); continue

    buckets["ranked_out"].append((code, w, f"FCF={fcf/1e8:.0f}亿 PQ={pq:.4f}" if pq else f"FCF={fcf/1e8:.0f}亿"))

# 输出
total_m = len(missing)
for bname, label, detail_flag in [
    ("not_in_csi",   "❌ 不在CSI全指", False),
    ("financial",    "🏦 金融/地产",   False),
    ("no_data",      "📭 无财务数据",  True),
    ("neg_fcf",      "📉 FCF≤0",      True),
    ("bad_pq",       "📊 PQ不足",     True),
    ("bad_5yr_ocf",  "⏱️ 5年OCF失败", True),
    ("ranked_out",   "📋 排名不足",   True),
]:
    items = buckets[bname]
    if not items: continue
    wsum = sum(x[1] for x in items)
    print(f"\n  {label}: {len(items)}只 ({len(items)/total_m*100:.1f}%) 丢失权重={wsum:.1f}%")
    if detail_flag and items:
        for code, w, reason in sorted(items, key=lambda x:-x[1])[:8]:
            name = info_map.get(code,{}).get("name","")
            print(f"    {code} {name} w={w:.1f}%: {reason}")

# 汇总
print(f"\n{'─'*50}")
print(f"  根因汇总:")
for bname, label in [
    ("not_in_csi","不在CSI全指"),("financial","金融/地产"),("no_data","无财务数据"),
    ("neg_fcf","FCF≤0"),("bad_pq","PQ不足"),("bad_5yr_ocf","5年OCF失败"),("ranked_out","排名不足"),
]:
    items = buckets[bname]
    if items:
        print(f"  {label:<12}: {len(items):>3}只 {len(items)/total_m*100:>5.1f}%  {sum(x[1] for x in items):>6.1f}%权重")

print(f"\n✅ P0 完成")
