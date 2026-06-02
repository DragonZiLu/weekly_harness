"""为每年的 Siegel 数据生成独立分析报告 — CSI 300 & CSI 500"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data"

# ── 分类逻辑 ──
def classify(row):
    t, dc, dr, pc = row['total_ret'], row['div_contrib'], row['div_ratio'], row['price_chg']
    if t <= 0: return '💀 价值毁灭'
    dp = dc / t * 100 if t > 0 else 0
    if dp >= 60 and dr >= 50:
        if pc < 0: return '🚬 极致万宝路'
        if pc < 50: return '🚬 经典万宝路'
        return '🚬 豪华万宝路'
    if dp >= 40 and dr >= 40 and t > 30: return '💰 红利复利'
    if pc > 100 and dr >= 50 and t > 200: return '🌟 成长红利双驱'
    if pc > 200 and t > 200 and dr < 30: return '🚀 纯成长'
    if pc > 100 and t > 100: return '📈 成长驱动'
    if t < 50 and pc < 0: return '⚠️ 价值陷阱'
    if t < 80: return '😐 平庸'
    return '📊 一般'

# 分类排序优先级
CAT_ORDER = [
    '🌟 成长红利双驱',
    '🚬 豪华万宝路',
    '🚬 经典万宝路',
    '🚬 极致万宝路',
    '💰 红利复利',
    '📈 成长驱动',
    '🚀 纯成长',
    '📊 一般',
    '😐 平庸',
    '⚠️ 价值陷阱',
    '💀 价值毁灭',
]

# ── 年份配置 ──
YEARS_300 = [2020, 2021, 2022, 2023, 2024, 2025]
YEARS_500 = [2020, 2021, 2022, 2023, 2024, 2025]

def load_data():
    """加载所有数据"""
    data_300, data_500 = {}, {}
    for yr in YEARS_300:
        p = OUT / f"siegel_csi300_{yr}.csv"
        if p.exists():
            df = pd.read_csv(p)
            df['cat'] = df.apply(classify, axis=1)
            data_300[yr] = df
    for yr in YEARS_500:
        p = OUT / f"siegel_csi500_{yr}.csv"
        if p.exists():
            df = pd.read_csv(p)
            df['cat'] = df.apply(classify, axis=1)
            data_500[yr] = df
    return data_300, data_500

def generate_one_year_report(df, idx_name, yr, start_yr):
    """为单个年份生成完整报告"""
    lines = []
    lines.append(f"# {idx_name} {yr} 年度 Siegel 万宝路评估报告")
    lines.append("")
    lines.append(f"> **评估框架**: Jeremy Siegel《投资者的未来》- 长期收益主要来自股息再投资")
    lines.append(f"> **回测区间**: {start_yr}-01 → {yr}-01 (10年)")
    lines.append(f"> **初始投入**: 10万元 | 股息全额再投")
    lines.append(f"> **有效样本**: {len(df)} 只")
    lines.append("")

    # ── 一、分类统计 ──
    lines.append("## 一、Siegel 分类统计")
    lines.append("")
    lines.append("| 分类 | 数量 | 占比 | 含义 |")
    lines.append("|:---|:---:|:---:|:---|")
    for cat in CAT_ORDER:
        cnt = len(df[df['cat'] == cat])
        pct = cnt / len(df) * 100
        bar = '█' * max(1, int(cnt / max(1, len(df) * 0.02)))
        lines.append(f"| {cat} | {cnt} | {pct:.1f}% | {bar} |")
    lines.append("")

    # 万宝路总数
    marlboro_df = df[df['cat'].str.contains('万宝路')]
    lines.append(f"**万宝路型标的合计: {len(marlboro_df)} 只**")
    lines.append("")

    # ── 二、万宝路全列表 ──
    lines.append("## 二、🚬 万宝路型标的")
    lines.append("")
    lines.append('> Siegel 的核心发现：最赚钱的股票往往是分红高的\u201c旧经济\u201d现金牛，而非市场追捧的\u201c新经济\u201d成长股。')
    lines.append("> 万宝路型 = 股息贡献占总收益 ≥ 60% 且 分红收回本金 ≥ 50%。")
    lines.append("")

    if len(marlboro_df) > 0:
        # 按总收益排序
        marlboro_sorted = marlboro_df.sort_values('total_ret', ascending=False)
        lines.append("### 全部万宝路 (按总收益排序)")
        lines.append("")
        lines.append("| 名称 | 代码 | 行业 | 总收益 | CAGR | 股价涨跌 | 股息贡献 | 分红/本 | 股息增持 | 最差年 | 分类 |")
        lines.append("|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|")
        for _, r in marlboro_sorted.iterrows():
            lines.append(f"| {r['name']} | {r['code']} | {r['industry']} | "
                        f"+{r['total_ret']:.1f}% | +{r['cagr']:.1f}% | "
                        f"{'+' if r['price_chg'] >= 0 else ''}{r['price_chg']:.1f}% | "
                        f"+{r['div_contrib']:.1f}% | {r['div_ratio']:.0f}% | "
                        f"+{r['div_amplify']:.0f}% | {r['max_yr_loss']:.1f}% | {r['cat']} |")
        lines.append("")
    else:
        lines.append("> ⚠️ 该年份未发现万宝路型标的。")
        lines.append("")

    # ── 三、行业分布 ──
    lines.append("## 三、万宝路行业分布")
    lines.append("")
    if len(marlboro_df) > 0:
        ind_counts = marlboro_df['industry'].value_counts()
        lines.append("| 行业 | 数量 | 万宝路标的 |")
        lines.append("|:---|:---:|:---|")
        for ind_name, cnt in ind_counts.items():
            names = marlboro_df[marlboro_df['industry'] == ind_name]['name'].tolist()
            lines.append(f"| {ind_name} | {cnt} | {', '.join(names[:5])}{'...' if len(names) > 5 else ''} |")
    else:
        lines.append("无万宝路型标的。")
    lines.append("")

    # ── 四、Top 30 总收益 ──
    lines.append("## 四、Top 30 总收益排行")
    lines.append("")
    top30 = df.sort_values('total_ret', ascending=False).head(30)
    lines.append("| 排名 | 名称 | 代码 | 行业 | 总收益 | CAGR | 股价 | 股息贡献 | 分红/本 | 最差年 | 分类 |")
    lines.append("|:---:|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|")
    for rank, (_, r) in enumerate(top30.iterrows(), 1):
        lines.append(f"| {rank} | {r['name']} | {r['code']} | {r['industry']} | "
                    f"+{r['total_ret']:.1f}% | +{r['cagr']:.1f}% | "
                    f"{'+' if r['price_chg'] >= 0 else ''}{r['price_chg']:.1f}% | "
                    f"+{r['div_contrib']:.1f}% | {r['div_ratio']:.0f}% | {r['max_yr_loss']:.1f}% | {r['cat']} |")
    lines.append("")

    # ── 五、完整分类明细 ──
    lines.append("## 五、完整分类明细")
    lines.append("")
    for cat in CAT_ORDER:
        subset = df[df['cat'] == cat].sort_values('total_ret', ascending=False)
        if len(subset) == 0:
            continue
        lines.append(f"### {cat} ({len(subset)} 只)")
        lines.append("")
        lines.append("| 名称 | 代码 | 行业 | 总收益 | CAGR | 股价 | 股息贡献 | 分红/本 | 最差年 |")
        lines.append("|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
        for _, r in subset.iterrows():
            lines.append(f"| {r['name']} | {r['code']} | {r['industry']} | "
                        f"+{r['total_ret']:.1f}% | +{r['cagr']:.1f}% | "
                        f"{'+' if r['price_chg'] >= 0 else ''}{r['price_chg']:.1f}% | "
                        f"+{r['div_contrib']:.1f}% | {r['div_ratio']:.0f}% | {r['max_yr_loss']:.1f}% |")
        lines.append("")

    # ── 六、关键洞察 ──
    lines.append("## 六、关键洞察")
    lines.append("")
    # 统计信息
    total_stocks = len(df)
    positive = len(df[df['total_ret'] > 0])
    avg_ret = df['total_ret'].mean()
    median_ret = df['total_ret'].median()
    avg_cagr = df['cagr'].mean()
    avg_div_ratio = df['div_ratio'].mean()
    avg_div_contrib = df['div_contrib'].mean()
    avg_max_loss = df['max_yr_loss'].mean()

    lines.append(f"- **整体表现**: {positive}/{total_stocks} 只正收益 ({positive/total_stocks*100:.1f}%)")
    lines.append(f"- **平均总收益**: +{avg_ret:.1f}% | 中位数: +{median_ret:.1f}%")
    lines.append(f"- **平均 CAGR**: +{avg_cagr:.1f}%")
    lines.append(f"- **平均股息贡献**: +{avg_div_contrib:.1f}%")
    lines.append(f"- **平均分红/本金比**: {avg_div_ratio:.0f}%")
    lines.append(f"- **平均最差年回报**: {avg_max_loss:.1f}%")
    lines.append("")

    # 万宝路 vs 整体
    if len(marlboro_df) > 0:
        m_avg_ret = marlboro_df['total_ret'].mean()
        m_avg_cagr = marlboro_df['cagr'].mean()
        m_avg_loss = marlboro_df['max_yr_loss'].mean()
        lines.append(f"- **万宝路型 ({len(marlboro_df)} 只)**: 平均总收益 +{m_avg_ret:.1f}%, "
                    f"平均 CAGR +{m_avg_cagr:.1f}%, 平均最差年 {m_avg_loss:.1f}%")
        # 按子类
        for sub_cat in ['🚬 极致万宝路', '🚬 经典万宝路', '🚬 豪华万宝路']:
            sub = marlboro_df[marlboro_df['cat'] == sub_cat]
            if len(sub) > 0:
                sub_avg = sub['total_ret'].mean()
                lines.append(f"  - {sub_cat} ({len(sub)} 只): 平均总收益 +{sub_avg:.1f}%")
    lines.append("")

    # 行业洞察
    if len(marlboro_df) > 0:
        top_inds = marlboro_df['industry'].value_counts().head(5)
        lines.append(f"- **万宝路行业集中度**: {', '.join(f'{k}({v}只)' for k, v in top_inds.items())}")
    lines.append("")

    return '\n'.join(lines)


def main():
    data_300, data_500 = load_data()

    # ── 生成 CSI 300 各年报告 ──
    csi300_dir = OUT / "annual_reports_hs300"
    csi300_dir.mkdir(exist_ok=True)

    for yr in YEARS_300:
        if yr not in data_300:
            continue
        df = data_300[yr]
        start_yr = yr - 10
        report = generate_one_year_report(df, "沪深300 (CSI 300)", yr, start_yr)
        report_path = csi300_dir / f"siegel_hs300_{yr}_report.md"
        report_path.write_text(report, encoding='utf-8')
        print(f"✅ CSI 300 {yr}: {report_path}")

    # ── 生成 CSI 500 各年报告 ──
    csi500_dir = OUT / "annual_reports_zz500"
    csi500_dir.mkdir(exist_ok=True)

    for yr in YEARS_500:
        if yr not in data_500:
            continue
        df = data_500[yr]
        start_yr = yr - 10
        report = generate_one_year_report(df, "中证500 (CSI 500)", yr, start_yr)
        report_path = csi500_dir / f"siegel_zz500_{yr}_report.md"
        report_path.write_text(report, encoding='utf-8')
        print(f"✅ CSI 500 {yr}: {report_path}")

    print(f"\n{'='*60}")
    print("全部年度报告生成完毕！")
    print(f"  CSI 300: {csi300_dir}/")
    print(f"  CSI 500: {csi500_dir}/")


if __name__ == '__main__':
    main()
