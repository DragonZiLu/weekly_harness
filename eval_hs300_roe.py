"""
CSI 300 ROE 筛选 + 周报评分体系评估
========================================
筛选条件：沪深300成分股中，最近3年每年 ROE >= 8%
评分体系：复用 dividend_evaluator 的完整打分（股息率分位/息差/等效分红/确定性）
"""

import sys
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

import dividend_evaluator
from weekly_harness.index_universe import RoeCache, INDUSTRY_MAP

# ─── 1. 获取 CSI 300 成分股 ────────────────────────────────────
print("=" * 60)
print("  CSI 300 + ROE>=8% 连续3年 → 周报评分体系评估")
print("=" * 60)

print("\n[1/4] 获取 CSI 300 成分股...")
df = pro.index_weight(index_code='000300.SH', trade_date='20260529')
codes = list(set(df['con_code'].tolist()))
print(f"  成分股: {len(codes)} 只")

# 获取名称+行业
name_map = {}
for i in range(0, len(codes), 200):
    batch = codes[i:i+200]
    sb = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry')
    for _, r in sb.iterrows():
        name_map[r['ts_code']] = (r['name'], r.get('industry', '') or '')

print(f"  获取行业: {len(name_map)} 只")

# ─── 2. 获取 ROE 数据并过滤 ─────────────────────────────────────
print("\n[2/4] 拉取 ROE 数据 + 过滤 ROE>=8% 连续3年...")
roe_cache = RoeCache()
roe_cache.ensure_loaded(codes, pro)

passed = []
failed = []
for code in codes:
    ok = roe_cache.passes_roe_filter(
        code, date_str=datetime.now().strftime('%Y-%m-%d'),
        min_roe=8.0, min_years=3,
    )
    if ok:
        passed.append(code)
    else:
        failed.append(code)

print(f"  通过 ROE>=8% 连续3年: {len(passed)} 只")
print(f"  未通过: {len(failed)} 只")

if not passed:
    print("\n❌ 无股票通过 ROE 筛选")
    sys.exit(0)

# ─── 3. 构建 COMPANIES 格式池 ───────────────────────────────────
print("\n[3/4] 构建评估池（行业→sector/category 映射）...")
pool = defaultdict(dict)
cat_counts = defaultdict(int)

for code in passed:
    name, industry = name_map.get(code, (code[:6], ''))
    sector, category = INDUSTRY_MAP.get(industry, ("其他", "弱周期红利"))

    pool[sector][name] = {
        'ts_code': code,
        'category': category,
        'certainty': 'B',
        'moat': '',
        'comment': f'[CSI300] ROE优质 | {industry}',
    }
    cat_counts[category] += 1

print(f"  分类分布:")
for cat, cnt in sorted(cat_counts.items()):
    print(f"    {cat}: {cnt} 只")

pool = dict(pool)

# ─── 4. 运行周报评分体系 ─────────────────────────────────────────
print("\n[4/4] 运行评分体系...")
original = dividend_evaluator.COMPANIES
dividend_evaluator.COMPANIES = pool

try:
    evaluator = dividend_evaluator.DividendCycleEvaluator()
    evaluator.BOND_YIELD = 1.70  # 当前10年期国债收益率
    results = evaluator.evaluate_all()
finally:
    dividend_evaluator.COMPANIES = original

# ─── 5. 汇总展示 ─────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  📊 CSI 300 ROE优质标的 — 周报评分排名")
print("=" * 70)

# 按总分排序
results.sort(key=lambda x: x['total_score'], reverse=True)

print(f"\n{'排名':<4} {'名称':<8} {'代码':<12} {'总分':>4} {'S1':>4} {'S2':>4} {'S3':>4} {'S4':>4} {'评级':<14} {'分类':<12} {'股息率':>6}")
print("-" * 100)

for rank, r in enumerate(results, 1):
    verdict = r.get('verdict', '')
    cat = r.get('category', '')
    div_y = r.get('div_yield', 0)
    s1 = r.get('s1_score', 0)
    s2 = r.get('s2_score', 0)
    s3 = r.get('s3_score', 0)
    s4 = r.get('s4_score', 0)

    # emoji for verdict
    emoji = {'🔥 大胆攒股': '🔥', '✅ 积极布局': '✅',
             '👀 观察等待': '👀', '⏸️ 暂不参与': '⏸️'}.get(verdict, '')

    print(f"{rank:<4} {r['name']:<8} {r['ts_code']:<12} {r['total_score']:>4.0f} "
          f"{s1:>4.0f} {s2:>4.0f} {s3:>4.0f} {s4:>4.0f} "
          f"{emoji} {verdict:<12} {cat:<12} {div_y:>5.1f}%")

# 统计汇总
print("\n" + "-" * 100)
print(f"\n📊 统计:")
strong = [r for r in results if '大胆攒股' in r.get('verdict', '')]
buy = [r for r in results if '积极布局' in r.get('verdict', '')]
watch = [r for r in results if '观察等待' in r.get('verdict', '')]
skip = [r for r in results if '暂不参与' in r.get('verdict', '')]
print(f"  🔥 大胆攒股: {len(strong)} 只")
print(f"  ✅ 积极布局: {len(buy)} 只")
print(f"  👀 观察等待: {len(watch)} 只")
print(f"  ⏸️ 暂不参与: {len(skip)} 只")

if strong:
    print(f"\n  🔥 大胆攒股标的:")
    for r in strong:
        print(f"    {r['name']} ({r['ts_code']}) — 总分{r['total_score']:.0f} 股息率{r.get('div_yield',0):.1f}%")
