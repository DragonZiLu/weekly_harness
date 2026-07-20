#!/usr/bin/env python3
"""HKEX 收入 vs 北向互联互通成交额 相关性验证。

需求来源：dayOne 香港交易所（0388.HK）深度调研中定性结论
「互联互通成交变宽 → HKEX 护城河变宽 → 收入增长」需定量验证。

数据来源（已在 dayOne 调研报告中查实并回读）：
- 北向 Stock Connect ADT（外资买 A 股，平均每日成交额，RMB 亿元/日）：
    2021=1201, 2022=1004, 2023=1083, 2024=1501, 2025=2124
    来源：HKEX《Stock Connect 2025 Review》（官方）
- HKEX 总收入（HK$ 十亿）：
    2021=20.75, 2022=18.27, 2023=20.39, 2024=22.27, 2025=29.16
    来源：HKEX 年报 / Investing.com 财务摘要（dayOne 报告已回读）

方法：Pearson 相关系数 + 简单线性回归（纯标准库，零依赖）。
注意：n=5 小样本、收入为总量（含上市费/数据费/LME 等），北向 ADT 仅代理
互联互通活跃度；本验证证明「方向一致性」，非精确因果归因。
"""
from statistics import mean


def pearson(x, y):
    n = len(x)
    mx, my = mean(x), mean(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = sum((a - mx) ** 2 for a in x) ** 0.5
    sy = sum((b - my) ** 2 for b in y) ** 0.5
    return cov / (sx * sy)


def linreg(x, y):
    mx, my = mean(x), mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    slope = sxy / sxx
    intercept = my - slope * mx
    yhat = [intercept + slope * a for a in x]
    ss_res = sum((b - h) ** 2 for b, h in zip(y, yhat))
    ss_tot = sum((b - my) ** 2 for b in y)
    r2 = 1 - ss_res / ss_tot
    return slope, intercept, r2


years = [2021, 2022, 2023, 2024, 2025]
north_adt = [1201, 1004, 1083, 1501, 2124]          # 北向 ADT, RMB 亿元/日
hkex_rev = [20.75, 18.27, 20.39, 22.27, 29.16]       # HKEX 总收入, HK$ bn

r = pearson(north_adt, hkex_rev)
slope, intercept, r2 = linreg(north_adt, hkex_rev)

print("=" * 64)
print("HKEX 收入 vs 北向互联互通成交额 相关性验证")
print("=" * 64)
print(f"样本年份          : {years}")
print(f"北向 ADT(亿元/日) : {north_adt}")
print(f"HKEX 收入(HK$bn)  : {hkex_rev}")
print("-" * 64)
print(f"Pearson 相关系数 r = {r:.4f}")
print(f"线性回归: 收入 = {slope:.5f} × ADT + {intercept:.3f}")
print(f"R² = {r2:.4f}")
print("-" * 64)

# 年增长率对照（验证同向性）
print("年度同比对照：")
print(f"{'年份':>6} {'北向ADT同比':>12} {'HKEX收入同比':>13}")
prev_n, prev_r = None, None
for y, n, rev in zip(years, north_adt, hkex_rev):
    ng = f"{(n/prev_n-1)*100:+.1f}%" if prev_n else "—"
    rg = f"{(rev/prev_r-1)*100:+.1f}%" if prev_r else "—"
    print(f"{y:>6} {ng:>12} {rg:>13}")
    prev_n, prev_r = n, rev
print("-" * 64)
print("结论：r=%.2f 表明两者强正相关，2022 同降、2023-2025 同升，" % r)
print("互联互通成交活跃度与 HKEX 收入高度同向——dayOne 定性结论获定量支持。")
