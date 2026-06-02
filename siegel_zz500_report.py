"""
中证500 Siegel 视角评估报告
"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent

# ─── 分类 ──────────────────────────────────────────────────
def classify_siegel(row):
    total = row['total_ret']
    div_contrib = row['div_contrib']
    div_ratio = row['div_ratio']
    price_chg = row['price_chg']
    if total <= 0:
        return "💀 价值毁灭", "股价+分红全亏"
    div_pct = div_contrib / total * 100 if total > 0 else 0
    
    if div_pct >= 60 and div_ratio >= 50 and total > 0:
        if price_chg < 0:
            return "🚬 极致万宝路", "股价下跌仍盈利，纯分红机器"
        elif price_chg < 50:
            return "🚬 经典万宝路", "股价温和+高分红，Siegel最爱"
        else:
            return "🚬 豪华万宝路", "分红股价双丰收"
    if div_pct >= 40 and div_ratio >= 40 and total > 30:
        return "💰 红利复利", "分红驱动为主"
    if price_chg > 100 and div_ratio >= 50 and total > 200:
        return "🌟 成长红利双驱", "成长+分红双引擎"
    if price_chg > 200 and total > 200 and div_ratio < 30:
        return "🚀 纯成长", "股价为主，分红薄"
    if price_chg > 100 and total > 100:
        return "📈 成长驱动", "股价为主"
    if total < 50 and price_chg < 0:
        return "⚠️ 价值陷阱", "股价跌+分红少"
    if total < 80:
        return "😐 平庸", "勉强跑赢通胀"
    return "📊 一般", "不上不下"


def siegel_score(row):
    score = 0
    if row['total_ret'] > 0:
        div_pct = row['div_contrib'] / row['total_ret'] * 100
    else:
        div_pct = 0
    score += min(30, div_pct * 0.3)
    score += min(20, row['div_ratio'] * 0.2)
    
    total = row['total_ret']
    if total < 0: score += 0
    elif total < 50: score += total / 50 * 5
    elif total < 200: score += 5 + (total - 50) / 150 * 5
    elif total < 500: score += 10 + (total - 200) / 300 * 3
    else: score += 13
    
    cagr = row['cagr']
    if cagr < 0: score += 0
    elif cagr < 5: score += cagr / 5 * 5
    elif cagr < 15: score += 5 + (cagr - 5) / 10 * 5
    else: score += 10 - min(5, (cagr - 15) * 0.5)
    
    max_loss = abs(row['max_yr_loss'])
    if max_loss < 15: score += 10
    elif max_loss < 25: score += 10 - (max_loss - 15) / 10 * 4
    elif max_loss < 40: score += 6 - (max_loss - 25) / 15 * 4
    else: score += max(0, 2 - (max_loss - 40) / 20 * 2)
    
    score += min(10, row['div_amplify'] * 0.1)
    
    industry = str(row['industry'])
    favored = ['银行','煤炭','石油','钢铁','水泥','食品','铁路','高速','电力','水力','消费','家电','白酒']
    non_fav = ['半导体','芯片','软件','互联网','通信设备','元器件','电气设备','新能源','航空']
    if any(kw in industry for kw in favored): score += 5
    elif any(kw in industry for kw in non_fav): score += 1
    
    return round(min(100, score))


# ─── 加载 ──────────────────────────────────────────────────
df = pd.read_csv(ROOT / "data/marlboro_zz500_all.csv")
df['category'], df['category_reason'] = zip(*df.apply(classify_siegel, axis=1))
df['siegel_score'] = df.apply(siegel_score, axis=1)
df = df.sort_values('siegel_score', ascending=False).reset_index(drop=True)
df['rank'] = df.index + 1

# ─── 报告 ──────────────────────────────────────────────────
path = ROOT / "data/siegel_zz500_report.md"
with open(path, 'w', encoding='utf-8') as f:
    f.write("# 中证500 十年股息再投 — Siegel 视角评估报告\n\n")
    f.write("> **评估框架**: Jeremy Siegel《投资者的未来》\n")
    f.write("> **回测区间**: 2015-01-05 → 2025-01-02 (10年) | 初始投入: 10万元 | 股息全额再投\n")
    f.write(f"> **有效样本**: {len(df)} 只 (剔除2015年后上市及无数据标的)\n\n")
    
    cat_counts = df['category'].value_counts()
    f.write("## 一、Siegel 分类统计\n\n")
    f.write("| 分类 | 数量 | 含义 |\n")
    f.write("|:---|:---:|:---|\n")
    for cat in ["🌟 成长红利双驱", "🚬 豪华万宝路", "🚬 经典万宝路", "🚬 极致万宝路",
                "💰 红利复利", "📈 成长驱动", "🚀 纯成长",
                "📊 一般", "😐 平庸", "⚠️ 价值陷阱", "💀 价值毁灭"]:
        cnt = cat_counts.get(cat, 0)
        if cnt > 0:
            reason = df[df['category'] == cat]['category_reason'].iloc[0]
            f.write(f"| {cat} | {cnt} | {reason} |\n")
    
    f.write(f"\n## 二、Siegel 评分 Top 50\n\n")
    f.write("| 排名 | S分 | 名称 | 代码 | 总收益 | CAGR | 股价 | 股息贡献 | 分红/本 | 最差年 | 分类 | 行业 |\n")
    f.write("|:---:|:---:|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---|\n")
    for _, r in df.head(50).iterrows():
        f.write(f"| {int(r['rank'])} | **{int(r['siegel_score'])}** | {r['name']} | "
                f"{r['code'].replace('.SH','').replace('.SZ','')} | "
                f"{r['total_ret']:+.1f}% | {r['cagr']:+.1f}% | {r['price_chg']:+.1f}% | "
                f"{r['div_contrib']:+.1f}% | {r['div_ratio']:.0f}% | {r['max_yr_loss']:+.1f}% | "
                f"{r['category']} | {r['industry']} |\n")
    
    # 万宝路详解
    marlboro = df[df['category'].str.contains('万宝路')]
    if not marlboro.empty:
        f.write(f"\n## 三、🚬 万宝路型标的 ({len(marlboro)} 只)\n\n")
        f.write("| 名称 | 代码 | 行业 | 总收益 | CAGR | 股价 | 股息贡献 | 分红/本 | 分类 |\n")
        f.write("|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|:---|\n")
        for _, r in marlboro.sort_values('siegel_score', ascending=False).iterrows():
            f.write(f"| {r['name']} | {r['code']} | {r['industry']} | "
                    f"{r['total_ret']:+.1f}% | {r['cagr']:+.1f}% | {r['price_chg']:+.1f}% | "
                    f"{r['div_contrib']:+.1f}% | {r['div_ratio']:.0f}% | {r['category']} |\n")
    
    # 行业分布
    f.write(f"\n## 四、万宝路行业分布\n\n")
    m_ind = marlboro['industry'].value_counts()
    f.write("| 行业 | 数量 | 代表标的 |\n")
    f.write("|:---|:---:|:---|\n")
    for ind, cnt in m_ind.head(10).items():
        reps = ', '.join(marlboro[marlboro['industry']==ind]['name'].head(3).tolist())
        f.write(f"| {ind} | {cnt} | {reps} |\n")
    
    # 完整排名
    f.write(f"\n## 五、完整排名 ({len(df)} 只)\n\n")
    f.write("| 排名 | S分 | 名称 | 总收益 | 股价 | 股息 | 分红/本 | 分类 | 行业 |\n")
    f.write("|:---:|:---:|:---|:---:|:---:|:---:|:---:|:---|:---|\n")
    for _, r in df.iterrows():
        f.write(f"| {int(r['rank'])} | {int(r['siegel_score'])} | {r['name']} | "
                f"{r['total_ret']:+.1f}% | {r['price_chg']:+.1f}% | {r['div_contrib']:+.1f}% | "
                f"{r['div_ratio']:.0f}% | {r['category']} | {r['industry']} |\n")

print(f"✅ 报告: {path}")
print(f"   共 {len(df)} 只, 万宝路 {len(marlboro)} 只")
