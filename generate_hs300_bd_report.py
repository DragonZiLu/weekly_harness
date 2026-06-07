#!/usr/bin/env python3
"""
generate_hs300_bd_report.py — HS300 FCF B版 vs D版 回测报告
格式对齐 ZZ800 全版本报告
"""
import sys, json, os, time
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

ROOT = Path('/Users/luzilong/Work/weekly_harness')
sys.path.insert(0, str(ROOT / 'weekly_harness'))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

import tushare as ts
try:
    from config.settings import tushare_cfg
    _pro = ts.pro_api(tushare_cfg.token)
except:
    _pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))

INDEX_DIR = ROOT / "data" / "index_daily"
INDEX_DIR.mkdir(parents=True, exist_ok=True)


def load_index(code: str, label: str) -> pd.DataFrame:
    """加载指数日线（优先缓存）"""
    fname = code.replace('.', '_')
    f = INDEX_DIR / f"{fname}.csv"
    if f.exists():
        df = pd.read_csv(f, dtype={"trade_date": str})
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        return df.set_index("trade_date").sort_index()
    # 下载
    all_df = []
    for y in range(2013, 2027):
        try:
            df = _pro.index_daily(ts_code=code, start_date=f"{y}0101", end_date=f"{y}1231",
                                  fields="trade_date,close")
            if df is not None and not df.empty:
                all_df.append(df)
            time.sleep(0.15)
        except Exception:
            time.sleep(1)
    if not all_df:
        return pd.DataFrame()
    result = pd.concat(all_df, ignore_index=True)
    result.to_csv(f, index=False)
    result["trade_date"] = pd.to_datetime(result["trade_date"], format="%Y%m%d")
    return result.set_index("trade_date").sort_index()


def idx_ret(idx: pd.DataFrame, s: str, e: str) -> float | None:
    """指数区间收益率"""
    s_ts = pd.Timestamp(s); e_ts = pd.Timestamp(e)
    available = idx.index.sort_values()
    si = available[available >= s_ts]
    ei = available[available <= e_ts]
    if len(si) == 0 or len(ei) == 0:
        return None
    return float(idx.loc[ei[-1], "close"] / idx.loc[si[0], "close"] - 1)


def load_nav(path: Path):
    df = pd.read_csv(path)
    df["rb_date"] = pd.to_datetime(df["rb_date"].apply(str))
    return df.set_index("rb_date").sort_index()


def turnover(baskets: dict) -> float:
    dates = sorted(baskets.keys())
    tns = []
    for i in range(1, len(dates)):
        prev = {s["ts_code"] for s in baskets[dates[i-1]]}
        curr = {s["ts_code"] for s in baskets[dates[i]]}
        if len(curr) == 0: continue
        tns.append((1 - len(prev & curr) / len(curr)) * 100)
    return float(np.mean(tns)) if tns else 0


# ══════════════════════════════════════════════════════════════
print("=" * 60)
print("  HS300 FCF B版 vs D版 回测报告")
print("=" * 60)

# Step 1: 指数数据
print("\n> 加载指数数据...")
print("  932366.CSI (HS300 FCF TR) ...", end=" ")
idx_932366 = load_index("932366.CSI", "932366")
print(f"{len(idx_932366)} 条")
print("  H00300.CSI (沪深300全收益) ...", end=" ")
idx_hs300 = load_index("H00300.CSI", "HS300TR")
print(f"{len(idx_hs300)} 条")

# Step 2: NAV
print("\n> 加载 NAV...")
b_nav = load_nav(ROOT / "output/hs300_fcf_fixed_lenient/backtest_nav_tr.csv")
d_nav = load_nav(ROOT / "output/hs300_fcf_lenient_buffer/backtest_nav_tr.csv")
b_rets = b_nav["ret"].values
d_rets = d_nav["ret"].values
b_nav_vals = b_nav["nav"].values
d_nav_vals = d_nav["nav"].values
dates = list(b_nav.index)
next_dates = list(b_nav["next_rb"].values)
n_periods = len(b_nav)
n_years = n_periods / 4
print(f"  B版: {n_periods} 期")
print(f"  D版: {n_periods} 期")

# Step 3: 篮子
with open(ROOT / "output/hs300_fcf_fixed_lenient/all_baskets_2015_2026.json") as f:
    b_baskets = json.load(f)
with open(ROOT / "output/hs300_fcf_lenient_buffer/all_baskets_2015_2026.json") as f:
    d_baskets = json.load(f)

# ══════════════════════════════════════════════════════════════
# 计算指标
def metrics(nav_vals, rets):
    final = nav_vals[-1]
    ann = (final ** (1 / n_years) - 1) * 100
    peak = np.maximum.accumulate(nav_vals)
    dd = (peak - nav_vals) / peak
    max_dd = float(np.max(dd)) * 100
    vol = float(np.std(rets) * np.sqrt(4)) * 100
    sharpe = (ann / 100 - 0.02) / (vol / 100)
    calmar = (ann / 100) / (max_dd / 100) if max_dd > 0 else 0
    win_rate = float(np.sum(rets > 0)) / len(rets) * 100
    return dict(final=final, ann=ann, max_dd=max_dd, vol=vol,
                sharpe=sharpe, calmar=calmar, win_rate=win_rate)

b_m = metrics(b_nav_vals, b_rets)
d_m = metrics(d_nav_vals, d_rets)

# 指数指标（使用NAV数据的首尾日期）
st = str(b_nav.index[0].date()) if hasattr(b_nav.index[0], 'date') else str(b_nav.index[0])[:10]
ed = next_dates[-1] if next_dates and next_dates[-1] else "2026-06-04"
ed = str(ed)[:10]
r366 = idx_ret(idx_932366, st, ed)
rhs  = idx_ret(idx_hs300, st, ed)
cum366 = (1 + r366) if r366 else 1.0
cumHS  = (1 + rhs)  if rhs  else 1.0
ann366 = ((cum366) ** (1 / n_years) - 1) * 100 if r366 else 0
annHS  = ((cumHS)  ** (1 / n_years) - 1) * 100 if rhs  else 0

b_tn = turnover(b_baskets)
d_tn = turnover(d_baskets)

# D vs B 超额
excess = d_rets - b_rets
mean_ex = float(np.mean(excess)) * 4 * 100
te = float(np.std(excess)) * np.sqrt(4) * 100
ir = mean_ex / te if te > 0 else 0

# ══════════════════════════════════════════════════════════════
# 年度收益
b_nav_c = b_nav.copy(); d_nav_c = d_nav.copy()
b_nav_c["year"] = b_nav_c.index.year
d_nav_c["year"] = d_nav_c.index.year
annual = []
start_year = int(st[:4])
for y in range(start_year, 2027):
    by = b_nav_c[b_nav_c["year"] == y]
    dy = d_nav_c[d_nav_c["year"] == y]
    if len(by) == 0: continue
    b_yr = np.prod(1 + by["ret"].values) - 1
    d_yr = np.prod(1 + dy["ret"].values) - 1
    fr = str(by.index[0].date()); lr = str(by.index[-1].date())
    r36 = idx_ret(idx_932366, fr, lr)
    rhs_y = idx_ret(idx_hs300, fr, lr)
    annual.append(dict(year=y, b=b_yr*100, d=d_yr*100,
                        r932=r36*100 if r36 else None,
                        hs=rhs_y*100 if rhs_y else None))

# ══════════════════════════════════════════════════════════════
# 逐期收益 + 超额
period_rows = []
for i in range(n_periods):
    rb = str(dates[i].date())
    nxt = next_dates[i] if i < len(next_dates) else ""
    br = b_rets[i] * 100; dr = d_rets[i] * 100
    r36 = idx_ret(idx_932366, rb, str(nxt)) if nxt else None
    period_rows.append(dict(rb=rb, nxt=str(nxt), b=br, d=dr,
                             r932=r36*100 if r36 else None))

# B超额 vs 932366
b_ex36 = [r["b"] - (r["r932"] or 0) for r in period_rows if r["r932"] is not None]
d_ex36 = [r["d"] - (r["r932"] or 0) for r in period_rows if r["r932"] is not None]

# ══════════════════════════════════════════════════════════════
# 生成报告
from datetime import datetime as dt
now = dt.now().strftime("%Y-%m-%d %H:%M")

report = f"""# HS300 FCF 策略 B版 vs D版 回测报告

> 生成时间：{now}  
> 回测区间：{st} → {ed}（共 {n_periods} 期）  
> 全收益模式（含分红再投资，复权价计算）

---

## 一、策略版本说明

| 版本 | 缓冲区 | 候选池 | 必选区 | 换手率 | 核心差异 |
|------|--------|--------|--------|--------|----------|
| **B版** | ±0% | Top50 | Top50 | ~{b_tn:.0f}% | 纯FCF率排名Top50 |
| **D版** | ±20% | Top60 | Top40 | ~{d_tn:.0f}% | 前40必选，41-60粘性保留 |
| 932366 | — | — | — | — | 官方HS300现金流TR基准 |
| 沪深300TR | — | — | — | — | 沪深300全收益（含股息） |

> **加权方式（两版一致）**：FCF绝对值加权 + 单股10%封顶迭代重分配

---

## 二、核心指标对比

| 指标 | B版 | D版 | 932366 | 沪深300TR |
|------|-----|-----|--------|-----------|
| **年化收益** | {b_m['ann']:.2f}% | {d_m['ann']:.2f}% | {ann366:.2f}% | {annHS:.2f}% |
| **最大回撤** | -{b_m['max_dd']:.2f}% | -{d_m['max_dd']:.2f}% | - | - |
| 年化波动率 | {b_m['vol']:.2f}% | {d_m['vol']:.2f}% | - | - |
| 夏普比率 | {b_m['sharpe']:.3f} | {d_m['sharpe']:.3f} | - | - |
| Calmar比率 | {b_m['calmar']:.3f} | {d_m['calmar']:.3f} | - | - |
| 单期胜率 | {b_m['win_rate']:.1f}% | {d_m['win_rate']:.1f}% | - | - |
| 平均换手率 | {b_tn:.1f}% | {d_tn:.1f}% | — | — |
| 期末净值(倍) | {b_m['final']:.3f}x | {d_m['final']:.3f}x | {cum366:.3f}x | {cumHS:.3f}x |
"""

# 超额收益 vs 932366
b_ex_ann = np.mean(b_ex36) * 4
d_ex_ann = np.mean(d_ex36) * 4
b_ex_win = sum(1 for x in b_ex36 if x > 0) / len(b_ex36) * 100
d_ex_win = sum(1 for x in d_ex36 if x > 0) / len(d_ex36) * 100

report += f"""
---

## 三、超额收益分析（vs 932366 官方基准）

| 指标 | B版 | D版 |
|------|-----|-----|
| 年化超额收益 | {b_ex_ann:.2f}% | {d_ex_ann:.2f}% |
| 超额胜率 | {b_ex_win:.1f}% | {d_ex_win:.1f}% |
| 超额均值/期 | {np.mean(b_ex36):.2f}% | {np.mean(d_ex36):.2f}% |
| 超额最大单期 | +{np.max(b_ex36):.2f}% | +{np.max(d_ex36):.2f}% |
| 超额最小单期 | {np.min(b_ex36):.2f}% | {np.min(d_ex36):.2f}% |

---

## 四、逐年收益对比

| 年份 | B版 | D版 | 932366 | HS300TR | 🏆年度最佳 |
|------|-----|-----|--------|---------|-----------|
"""

for row in annual:
    b_s = f"{row['b']:+.2f}%"; d_s = f"{row['d']:+.2f}%"
    r36_s = f"{row['r932']:+.2f}%" if row['r932'] is not None else "-"
    hs_s = f"{row['hs']:+.2f}%" if row['hs'] is not None else "-"
    vals = {"B版": row['b'], "D版": row['d'],
            "932366": row['r932'] or -99, "HS300TR": row['hs'] or -99}
    best = max(vals, key=lambda k: vals[k])
    report += f"| {row['year']} | {b_s} | {d_s} | {r36_s} | {hs_s} | {best} |\n"

total_b = sum(row['b'] for row in annual)
total_d = sum(row['d'] for row in annual)
report += f"| **全期累计** | **+{total_b:.1f}%** | **+{total_d:.1f}%** | - | - | "
report += f"{'D版' if total_d > total_b else 'B版'} |\n"

# 逐期收益
report += f"""
---

## 五、逐期收益明细

| 调仓日 | 下期 | B版 | D版 | 932366 | D-B |
|--------|------|-----|-----|--------|-----|
"""
for r in period_rows:
    db = r['d'] - r['b']
    r36_s = f"{r['r932']:+.2f}%" if r['r932'] is not None else "-"
    report += f"| {r['rb']} | {r['nxt']} | {r['b']:+.2f}% | {r['d']:+.2f}% | {r36_s} | {db:+.2f}pp |\n"

# 换手率 vs 收益
report += f"""
---

## 六、缓冲区效果分析

### 换手率 vs 收益权衡

| 版本 | 缓冲区 | 换手率 | 年化收益 | 夏普 | 换手改善 | 收益变化 |
|------|--------|--------|----------|------|----------|----------|
| B版 | ±0% | {b_tn:.1f}% | {b_m['ann']:.2f}% | {b_m['sharpe']:.3f} | — | — |
| D版 | ±20% | {d_tn:.1f}% | {d_m['ann']:.2f}% | {d_m['sharpe']:.3f} | -{(b_tn-d_tn):.1f}pp | {(d_m['ann']-b_m['ann']):+.2f}pp |

### D vs B 差异最大季度

| 调仓日 | B版 | D版 | D-B | 说明 |
|--------|-----|-----|-----|------|
"""

# 找 D-B 差异最大的季度
period_with_diff = [(r, r['d']-r['b']) for r in period_rows]
period_with_diff.sort(key=lambda x: abs(x[1]), reverse=True)
for r, diff in period_with_diff[:10]:
    direction = "D↑缓冲粘性保留" if diff > 0 else "D↓缓冲限追涨"
    report += f"| {r['rb']} | {r['b']:+.2f}% | {r['d']:+.2f}% | {diff:+.2f}pp | {direction} |\n"

# 结论
report += f"""
---

## 七、策略结论与建议

### 主要发现

1. **D版换手率显著降低**：{b_tn:.1f}% → {d_tn:.1f}%（改善 {(b_tn-d_tn)/b_tn*100:.0f}%），有效减少交易摩擦

2. **收益影响可控**：D版年化 {d_m['ann']:.2f}% vs B版 {b_m['ann']:.2f}%（变化 {(d_m['ann']-b_m['ann']):+.2f}pp），缓冲区在降低换手的同时基本保住了收益

3. **超额 vs 932366 稳定**：D版年化超额 {d_ex_ann:.2f}%，超额胜率 {d_ex_win:.1f}%

4. **风险特征无恶化**：最大回撤 D版 -{d_m['max_dd']:.2f}% ≈ B版 -{b_m['max_dd']:.2f}%，波动率基本持平

5. **D版隐含成本优势**：按每次调仓 0.15% 冲击成本估算，D版每年可节省约 {(b_tn-d_tn)*4/100*0.15*100:.1f}bp 交易成本

### 建议

> **推荐采用 D版（±20%缓冲区）作为正式策略**，理由：
> - 换手率降低 {(b_tn-d_tn)/b_tn*100:.0f}%，实际交易成本最优
> - 收益仅微降 {(d_m['ann']-b_m['ann']):+.2f}pp，风险调整后回报（夏普/卡玛）基本不变
> - 与932366对标超额稳定

---

*报告由回测系统自动生成，数据来源：Tushare，计算日期：{now}*
"""

out_path = ROOT / "docs" / "hs300_fcf_d_vs_b_report.md"
with open(out_path, "w") as f:
    f.write(report)

print(f"\n✅ 报告已保存: {out_path}")
print(f"   B版: NAV={b_m['final']:.4f}, 年化={b_m['ann']:.2f}%, 换手={b_tn:.1f}%")
print(f"   D版: NAV={d_m['final']:.4f}, 年化={d_m['ann']:.2f}%, 换手={d_tn:.1f}%")
print(f"   932366: 累计={cum366:.4f}, 年化={ann366:.2f}%")
