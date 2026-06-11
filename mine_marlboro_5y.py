"""
CSI 300 万宝路挖掘 — 5年股息再投评估
===========================================
与10年版本相同逻辑，窗口缩短为过去5年（2021-01-04 → 2026-01-04）

筛选标准（万宝路特征）：
  1. 股息再投贡献 > 50%（收益主要来自分红）
  2. 累计分红 > 本金 25%（5年窗口，10年的一半）
  3. 股价涨幅温和/为负但仍盈利（典型万宝路）
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import time

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

import tushare as ts
import pandas as pd
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

from stock_10y_hold import simulate as simulate_hold

BUY_DATE = "2021-01-04"   # 5年前第一个交易日
YEARS = 5
LIST_CUTOFF = "20210101"   # 2021年前上市

# 5年窗口筛选阈值（比10年版本适当放宽）
DIV_CONTRIB_THRESHOLD = 0.5   # 50%以上收益来自股息（10年用0.6）
DIV_RATIO_THRESHOLD = 25      # 累计分红>本金25%（10年用50%）
MIN_TOTAL_RET = 0             # 正收益

# ─── 1. 获取 CSI 300 成分 ──────────────────────────────────────
print("=" * 70)
print("  CSI 300 万宝路挖掘 — 5年股息再投扫描 (2021-2026)")
print("=" * 70)

print(f"\n[1/2] 获取 CSI 300 成分股...")
try:
    df = pro.index_weight(index_code='000300.SH', trade_date='20260529')
    codes = sorted(set(df['con_code'].tolist()))
except Exception as e:
    print(f"  ⚠️ 指数成分获取失败: {e}")
    print("  尝试使用 2026-05-08 日期...")
    df = pro.index_weight(index_code='000300.SH', trade_date='20260508')
    codes = sorted(set(df['con_code'].tolist()))
print(f"  成分股: {len(codes)} 只")

# 获取名称 + 上市日期
name_map = {}
list_date_map = {}
for i in range(0, len(codes), 200):
    batch = codes[i:i+200]
    sb = pro.stock_basic(ts_code=','.join(batch), fields='ts_code,name,industry,list_date')
    for _, r in sb.iterrows():
        name_map[r['ts_code']] = (r['name'], r.get('industry', '') or '')
        list_date_map[r['ts_code']] = r.get('list_date', '')

# 过滤：2021年前上市
codes_filtered = [c for c in codes if list_date_map.get(c, '9999') < LIST_CUTOFF]
print(f"  2021年前上市: {len(codes_filtered)} 只 (剔除 {len(codes) - len(codes_filtered)} 只)")

codes = codes_filtered

# ─── 2. 批量5年评估 ──────────────────────────────────────────
print(f"\n[2/2] 逐只评估 5年股息再投...")
print(f"  (预计 {len(codes)} 只，需时较长)")

results = []
errors = 0

for idx, code in enumerate(codes):
    name, industry = name_map.get(code, (code[:6], ''))

    if idx % 30 == 0:
        print(f"  进度: {idx}/{len(codes)} ...")

    try:
        rows, final_val, cagr, split_factor = simulate_hold(
            code, BUY_DATE, 100000, verbose=False, years=YEARS
        )
        if not rows:
            continue

        # 关键指标
        buy_price = rows[0].start_value / rows[0].shares if rows[0].shares > 0 else 0
        last_price = rows[-1].price
        eff_buy = buy_price / split_factor if split_factor > 0 else buy_price
        price_chg = (last_price / eff_buy - 1) * 100 if eff_buy > 0 else 0
        total_ret = (final_val / 100000 - 1) * 100
        div_contrib = total_ret - price_chg
        div_total = sum(r.div_cash for r in rows)
        div_ratio = div_total / 100000 * 100
        div_amplify = (rows[-1].shares / rows[0].shares - 1) * 100 if rows[0].shares > 0 else 0

        max_yr_loss = min(r.total_return for r in rows)

        results.append({
            'code': code, 'name': name, 'industry': industry,
            'total_ret': total_ret, 'cagr': cagr,
            'price_chg': price_chg, 'div_contrib': div_contrib,
            'div_ratio': div_ratio, 'div_amplify': div_amplify,
            'buy_price': buy_price, 'last_price': last_price,
            'div_total': div_total, 'max_yr_loss': max_yr_loss,
        })
        time.sleep(0.15)

    except Exception as e:
        errors += 1
        if errors <= 3:
            print(f"  ⚠️ {name}({code}): {e}")
        time.sleep(0.5)

print(f"\n  完成: {len(results)} 只有效数据, {errors} 只失败")

# ─── 保存完整结果 CSV ───────────────────────────────────────────
if results:
    output_dir = Path(__file__).parent / "data"
    output_dir.mkdir(exist_ok=True)
    df_all = pd.DataFrame(results)
    cols_order = ['code', 'name', 'industry', 'total_ret', 'cagr', 'price_chg',
                  'div_contrib', 'div_ratio', 'div_amplify', 'div_total',
                  'buy_price', 'last_price', 'max_yr_loss']
    df_all = df_all[[c for c in cols_order if c in df_all.columns]]
    df_all.to_csv(output_dir / "marlboro_hs300_5y_all.csv", index=False, encoding='utf-8-sig')
    print(f"  💾 全部 {len(results)} 只结果 → data/marlboro_hs300_5y_all.csv")

# ─── 3. 筛选万宝路标的（5年窗口版）─────────────────────────────
if not results:
    print("❌ 无结果")
    sys.exit(0)

results.sort(key=lambda x: x['div_contrib'] / max(x['total_ret'], 0.01), reverse=True)

# 万宝路5年标准：股息贡献>50% 且 累计分红>25%
marlboro = [
    r for r in results
    if r['div_contrib'] > 0
    and r['div_contrib'] / max(r['total_ret'], 0.01) > DIV_CONTRIB_THRESHOLD
    and r['div_ratio'] > DIV_RATIO_THRESHOLD
    and r['total_ret'] > MIN_TOTAL_RET
]

# 稳健型（分红驱动但不太极端）
stable = [
    r for r in results
    if r['div_contrib'] > 0
    and r['div_contrib'] / max(r['total_ret'], 0.01) > 0.3
    and r['div_ratio'] > 15
    and r['total_ret'] > 10
    and r not in marlboro
]

# ─── 4. 展示 ───────────────────────────────────────────────────
print("\n" + "=" * 90)
print(f"  🚬 万宝路型标的 (5年窗口，收益>50%来自股息，累计分红>本金25%)")
print("=" * 90)
print(f"  找到 {len(marlboro)} 只")
print()

print(f"  {'名称':<8} {'代码':<12} {'总收益':>7s} {'CAGR':>6s} {'股息贡献':>8s} {'股息占比':>7s} {'分红/本金':>8s} {'股价变化':>7s} {'最差年':>6s} {'行业'}")
print(f"  {'─' * 100}")

for r in marlboro:
    if r['total_ret'] > 0:
        div_pct = r['div_contrib'] / r['total_ret'] * 100
    else:
        div_pct = 0
    print(f"  {r['name']:<8} {r['code']:<12} {r['total_ret']:>+6.1f}% {r['cagr']:>+5.1f}% "
          f"{r['div_contrib']:>+7.1f}% {div_pct:>6.0f}% {r['div_ratio']:>7.0f}% "
          f"{r['price_chg']:>+6.1f}% {r['max_yr_loss']:>+5.1f}% {r['industry']}")

if stable:
    print(f"\n  {'─' * 100}")
    print(f"  📊 稳健红利型 (5年窗口，分红驱动≥30%，总收益>10%) — {len(stable)} 只")
    print(f"  {'─' * 100}")
    for r in stable[:15]:
        if r['total_ret'] > 0:
            div_pct = r['div_contrib'] / r['total_ret'] * 100
        else:
            div_pct = 0
        print(f"  {r['name']:<8} {r['code']:<12} {r['total_ret']:>+6.1f}% {r['cagr']:>+5.1f}% "
              f"{r['div_contrib']:>+7.1f}% {div_pct:>6.0f}% {r['div_ratio']:>7.0f}% "
              f"{r['price_chg']:>+6.1f}% {r['max_yr_loss']:>+5.1f}% {r['industry']}")

# ─── 5. 全排名 Top 20 ──────────────────────────────────────────
print(f"\n\n  {'─' * 100}")
print(f"  🏆 CSI 300 股息再投 5年总收益 Top 20")
print(f"  {'─' * 100}")
all_sorted = sorted(results, key=lambda x: x['total_ret'], reverse=True)
for rank, r in enumerate(all_sorted[:20], 1):
    if r['total_ret'] > 0:
        div_pct = r['div_contrib'] / r['total_ret'] * 100
    else:
        div_pct = 0
    tag = "🚬" if r in marlboro else ("⭐" if r in stable else "  ")
    print(f"  {rank:>2}. {tag} {r['name']:<8} {r['code']:<12} "
          f"{r['total_ret']:>+6.1f}% {r['cagr']:>+5.1f}% "
          f"股息{div_pct:.0f}% 分红{r['div_ratio']:.0f}% {r['industry']}")

# ─── 定制输出切 ─────────────────────────────────────────────
marlboro_df = pd.DataFrame(marlboro)
if not marlboro_df.empty:
    marlboro_df.to_csv(output_dir / "marlboro_hs300_5y_pure.csv", index=False, encoding='utf-8-sig')
    print(f"\n  💾 万宝路型 {len(marlboro)} 只 → data/marlboro_hs300_5y_pure.csv")

stable_df = pd.DataFrame(stable)
if not stable_df.empty:
    stable_df.to_csv(output_dir / "marlboro_hs300_5y_stable.csv", index=False, encoding='utf-8-sig')
    print(f"  💾 稳健型 {len(stable)} 只 → data/marlboro_hs300_5y_stable.csv")

print()
print("=" * 70)
print(f"  📊 5年 vs 10年对比")
print(f"  {'─' * 40}")
print(f"  10年万宝路: 22只纯万宝路 + 25只稳健")
print(f"  5年万宝路:  {len(marlboro)}只纯万宝路 + {len(stable)}只稳健")
print(f"  (5年窗口筛选阈值自动放宽：股息占比>50% vs >60%，分红比>25% vs >50%)")
print("=" * 70)
print()
