#!/usr/bin/env python3
"""SP500 Top100 历史选股收益贡献排名"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "weekly_harness"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

BASKET_DIR = PROJECT_ROOT / "output" / "sp500_style_100" / "baskets"
from compute_nav_cached import get_adj_close_cached

REBALANCE_DATES = [
    "2015-03-16", "2015-06-15", "2015-09-14", "2015-12-14",
    "2016-03-14", "2016-06-13", "2016-09-12", "2016-12-12",
    "2017-03-13", "2017-06-12", "2017-09-11", "2017-12-11",
    "2018-03-12", "2018-06-11", "2018-09-17", "2018-12-17",
    "2019-03-11", "2019-06-17", "2019-09-16", "2019-12-16",
    "2020-03-16", "2020-06-15", "2020-09-14", "2020-12-14",
    "2021-03-15", "2021-06-14", "2021-09-13", "2021-12-13",
    "2022-03-14", "2022-06-13", "2022-09-12", "2022-12-12",
    "2023-03-13", "2023-06-12", "2023-09-11", "2023-12-11",
    "2024-03-11", "2024-06-17", "2024-09-16", "2024-12-16",
    "2025-03-17", "2025-06-16", "2025-09-15", "2025-12-15",
    "2026-03-16", "2026-06-15",
]

# 累计贡献: {ts_code → total_contribution}
contrib: dict[str, float] = {}
# 出现次数
appearances: dict[str, int] = {}
# 股票名称
names: dict[str, str] = {}
# 行业
industries: dict[str, str] = {}

for i in range(len(REBALANCE_DATES) - 1):
    rb = REBALANCE_DATES[i]
    nrb = REBALANCE_DATES[i + 1]

    basket_file = BASKET_DIR / f"basket_{rb}.csv"
    if not basket_file.exists():
        continue

    df = pd.read_csv(basket_file, dtype={"ts_code": str})
    if df.empty:
        continue

    for _, row in df.iterrows():
        code = str(row["ts_code"])
        weight = float(row["weight"])
        name = str(row.get("name", code))
        ind = str(row.get("industry", ""))

        # 获取期间收益
        result = get_adj_close_cached(code, rb, nrb, auto_fetch=False)
        if result is None:
            continue

        period_ret = result[1] / result[0] - 1
        c = weight * period_ret  # 加权贡献

        contrib[code] = contrib.get(code, 0) + c
        appearances[code] = appearances.get(code, 0) + 1
        names[code] = name
        industries[code] = ind

# 排序
sorted_contrib = sorted(contrib.items(), key=lambda x: -x[1])

print("=" * 90)
print("SP500 Top100 历史选股收益贡献排名")
print("（各期 weight × period_ret 累计，2015-03 → 2026-03）")
print("=" * 90)
print(f"\n{'排名':<6}{'代码':<12}{'名称':<10}{'行业':<12}{'累计贡献':>10}{'出现期数':>8}{'贡献/期':>10}")
print("-" * 90)

total_pos = 0.0
total_neg = 0.0

for rank, (code, c) in enumerate(sorted_contrib, 1):
    if rank <= 25:
        sign = "+" if c > 0 else ""
        print(f"{rank:<6}{code:<12}{names.get(code,'?'):<10}{industries.get(code,'?'):<12}"
              f"{sign}{c*100:>9.2f}%{appearances[code]:>8}{c/appearances[code]*100:>9.2f}%")
    if c > 0:
        total_pos += c
    else:
        total_neg += c

# Bottom 10
n_total = len(sorted_contrib)
print(f"\n{'...':>6}")
print(f"\n  ⬇️ 负贡献 Bottom 10:")
for rank, (code, c) in enumerate(sorted_contrib, 1):
    if rank > n_total - 10:
        print(f"{rank:<6}{code:<12}{names.get(code,'?'):<10}{industries.get(code,'?'):<12}"
              f"{c*100:>10.2f}%{appearances[code]:>8}{c/appearances[code]*100:>9.2f}%")

total_contrib = total_pos + total_neg
print("-" * 90)
print(f"总股票数: {n_total} | 正向总贡献: {total_pos*100:.1f}% | 负向总贡献: {total_neg*100:.1f}%")
print(f"总累计贡献: {total_contrib*100:.1f}% | Top10 占比: {sum(c for _,c in sorted_contrib[:10])/total_pos*100:.1f}%")

# 行业维度
ind_contrib: dict[str, float] = {}
for code, c in contrib.items():
    ind = industries.get(code, "其他")
    ind_contrib[ind] = ind_contrib.get(ind, 0) + c

print(f"\n{'='*90}")
print("行业维度贡献排名")
print(f"{'='*90}")
print(f"{'行业':<16}{'累计贡献':>12}{'股票数':>8}")
print("-" * 90)
for ind, c in sorted(ind_contrib.items(), key=lambda x: -x[1]):
    n = len([k for k, v in industries.items() if v == ind])
    print(f"{ind:<16}{c*100:>11.2f}%{n:>8}")
