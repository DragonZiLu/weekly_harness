"""全A股 2020视角 Siegel 评估报告生成器"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
df = pd.read_csv(ROOT / "data/marlboro_all_a_2020.csv")

def classify_siegel(row):
    total = row['total_ret']
    div_contrib = row['div_contrib']
    div_ratio = row['div_ratio']
    price_chg = row['price_chg']
    if total <= 0: return ('💀 价值毁灭', '股价+分红全亏')
    div_pct = div_contrib / total * 100 if total > 0 else 0
    if div_pct >= 60 and div_ratio >= 50 and total > 0:
        if price_chg < 0: return ('🚬 极致万宝路', '股价下跌仍盈利')
        if price_chg < 50: return ('🚬 经典万宝路', '股价温和+高分红')
        return ('🚬 豪华万宝路', '分红股价双丰收')
    if div_pct >= 40 and div_ratio >= 40 and total > 30: return ('💰 红利复利', '分红驱动稳健增长')
    if price_chg > 100 and div_ratio >= 50 and total > 200: return ('🌟 成长红利双驱', '成长+分红双引擎')
    if price_chg > 200 and total > 200 and div_ratio < 30: return ('🚀 纯成长', '股价驱动分红薄')
    if price_chg > 100 and total > 100: return ('📈 成长驱动', '股价为主分红为辅')
    if total < 50 and price_chg < 0: return ('⚠️ 价值陷阱', '股价跌+分红少')
    if price_chg < -20 and div_ratio < 40: return ('🕳️ 股息陷阱', '高股息来自暴跌')
    if total < 80: return ('😐 平庸', '勉强跑赢通胀')
    return ('📊 一般', '不上不下')

def siegel_score(row):
    score = 0
    if row['total_ret'] > 0:
        score += min(row['cagr'] * 3, 30)
        div_pct = row['div_contrib'] / row['total_ret'] * 100
        score += min(div_pct * 0.4, 25)
        score += min(row['div_ratio'] * 0.15, 20)
        if row['max_yr_loss'] > -10: score += 15
        elif row['max_yr_loss'] > -20: score += 10
        elif row['max_yr_loss'] > -30: score += 5
        score += min(row['total_ret'] * 0.01, 10)
    return round(score)

# 分类
df['category'], df['cat_desc'] = zip(*df.apply(classify_siegel, axis=1))
df['siegel_score'] = df.apply(siegel_score, axis=1)
df_sorted = df.sort_values('siegel_score', ascending=False).reset_index(drop=True)

# 统计
marlboro = df_sorted[df_sorted['category'].str.contains('万宝路')]
trap = df_sorted[df_sorted['category'] == '⚠️ 价值陷阱']
destroy = df_sorted[df_sorted['category'] == '💀 价值毁灭']
div_recovery = df_sorted[df_sorted['category'] == '💰 红利复利']
growth_div = df_sorted[df_sorted['category'] == '🌟 成长红利双驱']

# ── 写报告 ──
out = ROOT / "data/siegel_all_a_2020_report.md"
with open(out, 'w', encoding='utf-8') as f:
    f.write("# 全A股十年股息再投 — 2020 Siegel 视角评估报告\n\n")
    f.write("> **评估框架**: Jeremy Siegel《投资者的未来》(The Future for Investors)\n")
    f.write("> **回测区间**: 2010-01-04 → 2020-01-02 (10年) | 初始投入: 10万元 | 股息全额再投\n")
    f.write(f"> **有效样本**: {len(df)} 只 (2010年前上市，剔除ST/退市)\n\n")

    f.write("## 一、分类统计\n\n")
    cats = df_sorted.groupby(['category', 'cat_desc']).size().reset_index(name='n')
    cats = cats.sort_values('n', ascending=False)
    f.write("| 分类 | 数量 | 占比 | 含义 |\n")
    f.write("|:---|:---:|:---:|:---|\n")
    for _, r in cats.iterrows():
        f.write(f"| {r['category']} | {r['n']} | {r['n']/len(df)*100:.1f}% | {r['cat_desc']} |\n")

    f.write(f"\n## 二、Siegel 评分 Top 50\n\n")
    f.write("| 排名 | S分 | 名称 | 代码 | 总收益 | CAGR | 股价 | 股息 | 分红/本 | 最差年 | 分类 | 行业 |\n")
    f.write("|:---:|:---:|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---|\n")
    for i, (_, r) in enumerate(df_sorted.head(50).iterrows()):
        f.write(f"| {i+1} | **{r['siegel_score']}** | {r['name']} | {r['code'][:6]} | "
                f"+{r['total_ret']:.1f}% | +{r['cagr']:.1f}% | "
                f"+{r['price_chg']:.1f}% | +{r['div_contrib']:.1f}% | "
                f"{r['div_ratio']:.0f}% | {r['max_yr_loss']:+.1f}% | "
                f"{r['category']} | {r['industry']} |\n")

    f.write(f"\n## 三、万宝路图谱 ({len(marlboro)} 只)\n\n")

    # 豪华万宝路
    luxury = marlboro[marlboro['category'] == '🚬 豪华万宝路'].sort_values('siegel_score', ascending=False)
    f.write(f"### 🚬 豪华万宝路 (分红股价双丰收) — {len(luxury)} 只\n\n")
    f.write("| S分 | 名称 | 总收益 | CAGR | 股价 | 股息贡献 | 分红/本 | 行业 |\n")
    f.write("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---|\n")
    for _, r in luxury.iterrows():
        f.write(f"| {r['siegel_score']} | {r['name']} | +{r['total_ret']:.0f}% | +{r['cagr']:.1f}% | "
                f"+{r['price_chg']:.0f}% | +{r['div_contrib']:.0f}% | {r['div_ratio']:.0f}% | {r['industry']} |\n")

    # 经典万宝路
    classic = marlboro[marlboro['category'] == '🚬 经典万宝路'].sort_values('siegel_score', ascending=False)
    f.write(f"\n### 🚬 经典万宝路 (股价温和+高分红) — {len(classic)} 只\n\n")
    f.write("| S分 | 名称 | 总收益 | CAGR | 股价 | 股息贡献 | 分红/本 | 行业 |\n")
    f.write("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---|\n")
    for _, r in classic.iterrows():
        f.write(f"| {r['siegel_score']} | {r['name']} | +{r['total_ret']:.0f}% | +{r['cagr']:.1f}% | "
                f"+{r['price_chg']:.0f}% | +{r['div_contrib']:.0f}% | {r['div_ratio']:.0f}% | {r['industry']} |\n")

    # 极致万宝路
    extreme = marlboro[marlboro['category'] == '🚬 极致万宝路'].sort_values('siegel_score', ascending=False)
    f.write(f"\n### 🚬 极致万宝路 (股价下跌仍赚钱) — {len(extreme)} 只\n\n")
    f.write("| S分 | 名称 | 总收益 | CAGR | 股价 | 股息贡献 | 分红/本 | 行业 |\n")
    f.write("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---|\n")
    for _, r in extreme.iterrows():
        f.write(f"| {r['siegel_score']} | {r['name']} | +{r['total_ret']:.0f}% | +{r['cagr']:.1f}% | "
                f"{r['price_chg']:+.0f}% | +{r['div_contrib']:.0f}% | {r['div_ratio']:.0f}% | {r['industry']} |\n")

    f.write(f"\n## 四、万宝路行业分布\n\n")
    ind_count = marlboro.groupby('industry').size().sort_values(ascending=False)
    f.write("| 行业 | 数量 | 代表标的 |\n")
    f.write("|:---|:---:|:---|\n")
    for ind, cnt in ind_count.head(15).items():
        reps = ', '.join(marlboro[marlboro['industry']==ind]['name'].head(4).tolist())
        f.write(f"| {ind} | {cnt} | {reps} |\n")

    f.write(f"\n## 五、2020 vs 2025 时期对比\n\n")

    # 跟当前报告做交叉对比
    curr_path = ROOT / "data/marlboro_all_a.csv"
    if curr_path.exists():
        curr = pd.read_csv(curr_path)
        curr['category'], _ = zip(*curr.apply(classify_siegel, axis=1))
        curr['siegel_score'] = curr.apply(siegel_score, axis=1)

        curr_marlboro = set(curr[curr['category'].str.contains('万宝路')]['code'].tolist())
        prev_marlboro = set(marlboro['code'].tolist())

        both = prev_marlboro & curr_marlboro
        only_2020 = prev_marlboro - curr_marlboro
        only_now = curr_marlboro - prev_marlboro

        f.write(f"| 指标 | 2020视角 (2010-2020) | 当前视角 (2015-2025) |\n")
        f.write(f"|:---|---:|---:|\n")
        f.write(f"| 有效样本 | {len(df)} 只 | {len(curr)} 只 |\n")
        f.write(f"| 万宝路型 | {len(marlboro)} 只 | {len(curr_marlboro)} 只 |\n")
        f.write(f"| 价值毁灭 | {len(destroy)} 只 | {len(curr[curr['category']=='💀 价值毁灭'])} 只 |\n")
        f.write(f"| 价值陷阱 | {len(trap)} 只 | {len(curr[curr['category']=='⚠️ 价值陷阱'])} 只 |\n\n")

        f.write(f"### 穿越周期的万宝路 ({len(both)} 只)\n\n")
        f.write(f"两个时期均被识别为万宝路的标的，最稳健的 Siegel 选择：\n\n")
        f.write(f"| 标的 | 行业 | 2010-2020收益 | 2015-2025收益 |\n")
        f.write(f"|:---|:---|:---:|:---:|\n")
        for code in sorted(both):
            r0 = df[df['code']==code].iloc[0]
            rc = curr[curr['code']==code]
            if not rc.empty:
                f.write(f"| {r0['name']} | {r0['industry']} | +{r0['total_ret']:.0f}% | +{rc.iloc[0]['total_ret']:.0f}% |\n")

        f.write(f"\n### 仅2020识别为万宝路 ({len(only_2020)} 只) — 后来退化\n\n")
        for code in sorted(list(only_2020)[:10]):
            r = df[df['code']==code].iloc[0]
            rc = curr[curr['code']==code]
            now_cat = rc.iloc[0]['category'] if not rc.empty else '无数据'
            now_ret = f"+{rc.iloc[0]['total_ret']:.0f}%" if not rc.empty else '-'
            f.write(f"| {r['name']} | {r['industry']} | +{r['total_ret']:.0f}% | {now_ret} ({now_cat}) |\n")
        if len(only_2020) > 10:
            f.write(f"| ... | | | 共 {len(only_2020)} 只 |\n")

        f.write(f"\n### 仅当前识别为万宝路 ({len(only_now)} 只) — 2020年后新晋\n\n")
        for code in sorted(list(only_now))[:10]:
            rc = curr[curr['code']==code].iloc[0]
            r0 = df[df['code']==code]
            prev_ret = f"+{r0.iloc[0]['total_ret']:.0f}%" if not r0.empty else '-'
            f.write(f"| {rc['name']} | {rc['industry']} | {prev_ret} | +{rc['total_ret']:.0f}% |\n")
        if len(only_now) > 10:
            f.write(f"| ... | | | 共 {len(only_now)} 只 |\n")

    f.write(f"\n## 六、全A股 Top 100 (2020视角 Siegel 评分排序)\n\n")
    f.write("| 排名 | S分 | 名称 | 总收益 | CAGR | 股价 | 股息 | 分红/本 | 最差年 | 分类 | 行业 |\n")
    f.write("|:---:|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---|\n")
    for i, (_, r) in enumerate(df_sorted.head(100).iterrows()):
        f.write(f"| {i+1} | {r['siegel_score']} | {r['name']} | +{r['total_ret']:.1f}% | +{r['cagr']:.1f}% | "
                f"+{r['price_chg']:.1f}% | +{r['div_contrib']:.1f}% | {r['div_ratio']:.0f}% | "
                f"{r['max_yr_loss']:+.1f}% | {r['category']} | {r['industry']} |\n")

print(f"✅ 报告已生成: {out}")
print(f"   共 {len(df)} 只标的, {len(marlboro)} 只万宝路")
