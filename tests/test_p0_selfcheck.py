"""P0-1: 反向验证入选100只标的数据完整性"""
import sys
from pathlib import Path
from collections import Counter

_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from weekly_harness.fcf_universe import FcfUniverse
import tushare as ts
from config.settings import tushare_cfg

uni = FcfUniverse()
uni.preload_all(download=False)

# ── 获取数据 ──
basket = uni.get_fcf_basket("2026-03-20", top_n=100, verbose=False)
codes = [k for k in basket if k != "__quality_warnings__"]

pro = ts.pro_api(tushare_cfg.token)
avail = pro.index_weight(index_code="932365.CSI", start_date="20260301", end_date="20260401")
avail["trade_date"] = avail["trade_date"].astype(str)
closest = sorted(avail["trade_date"].unique())[0]
actual = avail[avail["trade_date"] == closest]
aw = dict(zip(actual["con_code"], actual["weight"]))

# ── 1. 字段完整性 ──
bad_field = {"no_fcf": [], "no_ev": [], "no_yield": [], "no_pq": [], "no_mv": [],
             "neg_fcf": [], "neg_ev": [], "neg_yield": []}
for c in codes:
    m = basket[c]
    if m["fcf"] is None:          bad_field["no_fcf"].append(c)
    elif m["fcf"] <= 0:           bad_field["neg_fcf"].append((c, m["fcf"] / 1e8))
    if m["ev"] is None:           bad_field["no_ev"].append(c)
    elif m["ev"] <= 0:            bad_field["neg_ev"].append((c, m["ev"] / 1e8))
    if m["fcf_yield"] is None:    bad_field["no_yield"].append(c)
    elif m["fcf_yield"] <= 0:     bad_field["neg_yield"].append((c, m["fcf_yield"] * 100))
    if m["profit_quality"] is None: bad_field["no_pq"].append(c)
    if m["total_mv"] is None:     bad_field["no_mv"].append(c)

total_issues = sum(len(v) for v in bad_field.values())
print(f"\n{'='*60}")
print(f"  P0-1: 入选标的自检")
print(f"{'='*60}")

print(f"\n  📦 字段完整性: ", end="")
if total_issues == 0:
    print("✅ 全部通过")
else:
    print(f"❌ {total_issues} 个问题")
    for k, v in bad_field.items():
        if v:
            print(f"     {k}: {v[:5]}{'...' if len(v) > 5 else ''}")

# ── 2. 异常值检测 ──
reports = []
for c in codes:
    m = basket[c]
    fy = m["fcf_yield"]
    fcf = m["fcf"] / 1e8
    ev = m["ev"] / 1e8
    mv = m["total_mv"] / 1e8
    pq = m["profit_quality"]
    reports.append((c, m["name"], fy * 100, fcf, ev, mv, pq))

reports.sort(key=lambda x: -x[2])

print(f"\n  📊 FCF率分布:")
fy_vals = [r[2] for r in reports]
print(f"     Min={min(fy_vals):.1f}%  Median={sorted(fy_vals)[len(fy_vals)//2]:.1f}%  Max={max(fy_vals):.1f}%")

# FCF率 > 30% 的标的（异常高）
high_fy = [r for r in reports if r[2] > 30]
if high_fy:
    print(f"\n  ⚠️ FCF率 > 30%: {len(high_fy)} 只")
    for c, n, fy, fcf, ev, mv, pq in high_fy[:10]:
        print(f"     {c} {n}: FCF率={fy:.1f}% FCF={fcf:.0f}亿 EV={ev:.0f}亿 MV={mv:.0f}亿")
else:
    print(f"\n  ✅ FCF率无异常高位（均 ≤ 30%）")

# ── 3. 市值分布 ──
mvs = sorted([r[5] for r in reports])
print(f"\n  📊 市值分布 (亿):")
print(f"     Min={mvs[0]:.1f}  P10={mvs[len(mvs)//10]:.1f}  P25={mvs[len(mvs)//4]:.1f}  "
      f"Median={mvs[len(mvs)//2]:.1f}  P75={mvs[len(mvs)*3//4]:.1f}  Max={mvs[-1]:.1f}")

# 微盘股（< 30 亿）
micro = [r for r in reports if r[5] < 30]
print(f"\n  ⚠️ 市值 < 30 亿: {len(micro)} 只")
for c, n, fy, fcf, ev, mv, pq in micro[:5]:
    print(f"     {c} {n}: {mv:.1f}亿 FCF率={fy:.1f}% FCF={fcf:.1f}亿")

# ── 4. 与 932365 重叠部分对比 ──
common = set(codes) & set(aw.keys())
print(f"\n  🔗 与 932365 重叠: {len(common)} 只")
if common:
    ow = [basket[c]["weight"] * 100 for c in common]
    aw_list = [float(aw[c]) for c in common]
    from scipy.stats import spearmanr, pearsonr
    sp, _ = spearmanr(ow, aw_list)
    pr, _ = pearsonr(ow, aw_list)
    print(f"     Spearman={sp:.4f}  Pearson={pr:.4f}")

    # 权重大差异
    big_diff = [(c, basket[c]["name"], ow[i], aw_list[i])
                for i, c in enumerate(common) if abs(ow[i] - aw_list[i]) > 2]
    if big_diff:
        print(f"     权重大差异 (>2%): {len(big_diff)} 只")
        big_diff.sort(key=lambda x: -abs(x[2] - x[3]))
        for c, n, o, a in big_diff[:5]:
            print(f"       {c} {n}: 我们 {o:.1f}% vs 指数 {a:.1f}%")

# ── 5. 行业分布 ──
ind = Counter(basket[c]["industry"] for c in codes)
print(f"\n  🏭 行业分布 (Top10):")
for name, cnt in ind.most_common(10):
    print(f"     {name}: {cnt}只")

print(f"\n  ✅ P0-1 自检完成")
