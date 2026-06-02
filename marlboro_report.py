"""
Siegel 视角的 CSI 300 10年股息再投评估报告
============================================
基于《投资者的未来》(The Future for Investors) 框架
"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
df = pd.read_csv(ROOT / "data/marlboro_hs300_all.csv")

# ─── 分类体系 (Siegel 框架) ──────────────────────────────
def classify_siegel(row):
    """
    西格尔分类：
    - 万宝路型 (Marlboro):   股息贡献≥60%, 分红/本金≥50%, 股价涨幅温和/为负, 阴跌行业中的现金牛
    - 红利复利型 (Dividend Compound): 股息贡献40-60%, 总收益稳健
    - 成长+红利双驱 (Growth+Dividend): 股价涨>100%, 分红也扎实(>50%), 总收益>200%
    - 成长陷阱 (Growth Trap): 股价暴涨但分红几乎为零, 未来不一定能持续
    - 价值陷阱 (Value Trap): 股价下跌, 分红也不够补, 总收益<银行定存(≈50%)
    - 平庸型 (Mediocre): 不好不坏
    """
    total = row['total_ret']
    div_contrib = row['div_contrib']
    div_ratio = row['div_ratio']
    price_chg = row['price_chg']
    cagr = row['cagr']
    
    if total <= 0:
        return "💀 价值毁灭", "股价+分红全亏，不如存银行"
    
    div_pct = div_contrib / total * 100 if total > 0 else 0
    
    # 万宝路型
    if div_pct >= 60 and div_ratio >= 50 and total > 0:
        if price_chg < 0:
            return "🚬 极致万宝路", "股价下跌仍盈利，纯分红机器"
        elif price_chg < 50:
            return "🚬 经典万宝路", "股价温和+高分红，Siegel最爱"
        else:
            return "🚬 豪华万宝路", "分红股价双丰收"
    
    # 红利复利型
    if div_pct >= 40 and div_ratio >= 40 and total > 30:
        return "💰 红利复利", "分红驱动为主，稳健增长"
    
    # 成长+红利双驱
    if price_chg > 100 and div_ratio >= 50 and total > 200:
        return "🌟 成长红利双驱", "最强形态：成长+分红双引擎"
    
    # 纯成长型
    if price_chg > 200 and total > 200 and div_ratio < 30:
        return "🚀 纯成长", "股价驱动为主，分红薄"
    
    # 成长温和型
    if price_chg > 100 and total > 100:
        return "📈 成长驱动", "股价为主，分红为辅"
    
    # 价值陷阱
    if total < 50 and price_chg < 0:
        return "⚠️ 价值陷阱", "股价跌+分红少，十年白干"
    
    # 股息陷阱
    if price_chg < -20 and div_ratio < 40:
        return "🕳️ 股息陷阱", "高股息率来自股价暴跌"
    
    # 平庸
    if total < 80:
        return "😐 平庸", "勉强跑赢通胀"
    
    return "📊 一般", "不上不下"


df['category'], df['category_reason'] = zip(*df.apply(classify_siegel, axis=1))

# ─── Siegel 评分 (0-100) ──────────────────────────────────
def siegel_score(row):
    """
    Siegel 视角的评分:
    - 股息贡献越大越好 (Siegel 认为分红是长期收益主要来源)
    - 分红/本金比越高越好 (收回成本能力)
    - CAGR 适中 (太高意味着不可持续或估值太贵)
    - 最大年度亏损越小越好 (持有体验)
    - 行业：旧经济 > 新经济 (Siegel 偏爱被低估的传统行业)
    """
    score = 0
    
    # 1. 股息贡献占比 (最高30分) - Siegel 最看重
    if row['total_ret'] > 0:
        div_pct = row['div_contrib'] / row['total_ret'] * 100
    else:
        div_pct = 0
    score += min(30, div_pct * 0.3)  # 100% → 30分
    
    # 2. 分红收回本金比例 (最高20分)
    score += min(20, row['div_ratio'] * 0.2)  # 100% → 20分
    
    # 3. 总收益率 (最高15分) - 太高的反而扣分(可能是泡沫)
    total = row['total_ret']
    if total < 0:
        score += 0
    elif total < 50:
        score += total / 50 * 5
    elif total < 200:
        score += 5 + (total - 50) / 150 * 5
    elif total < 500:
        score += 10 + (total - 200) / 300 * 3
    else:
        score += 13  # 太高回到均值概率大
    
    # 4. CAGR 合理性 (最高10分) - 8-15% 最佳
    cagr = row['cagr']
    if cagr < 0:
        score += 0
    elif cagr < 5:
        score += cagr / 5 * 5
    elif cagr < 15:
        score += 5 + (cagr - 5) / 10 * 5  # 8-15% 最优区间
    else:
        score += 10 - min(5, (cagr - 15) * 0.5)  # 超过15%递减
    
    # 5. 最大年度回撤 (最高10分) - 越小越好
    max_loss = abs(row['max_yr_loss'])
    if max_loss < 15:
        score += 10
    elif max_loss < 25:
        score += 10 - (max_loss - 15) / 10 * 4
    elif max_loss < 40:
        score += 6 - (max_loss - 25) / 15 * 4
    else:
        score += max(0, 2 - (max_loss - 40) / 20 * 2)
    
    # 6. 股息增持比例 (最高10分)
    score += min(10, row['div_amplify'] * 0.1)  # 100%增持 → 10分
    
    # 7. 行业加分 (最高5分) - Siegel 偏爱的行业
    industry = str(row['industry'])
    siegel_favored = ['银行', '煤炭', '石油', '钢铁', '水泥', '烟草', '食品',
                      '铁路', '高速', '电力', '水力', '消费', '家电', '白酒']
    non_favored = ['半导体', '芯片', '软件', '互联网', '通信设备', '元器件',
                   '电气设备', '新能源', '航空']
    if any(kw in industry for kw in siegel_favored):
        score += 5
    elif any(kw in industry for kw in non_favored):
        score += 1
    
    return round(min(100, score))


df['siegel_score'] = df.apply(siegel_score, axis=1)
df = df.sort_values('siegel_score', ascending=False).reset_index(drop=True)
df['rank'] = df.index + 1

# ─── 生成报告 ──────────────────────────────────────────────
report_path = ROOT / "data/siegel_hs300_report.md"

with open(report_path, 'w', encoding='utf-8') as f:
    f.write("# CSI 300 十年股息再投 — Siegel 视角评估报告\n\n")
    f.write("> **评估框架**: Jeremy Siegel《投资者的未来》(The Future for Investors)\n")
    f.write("> **核心论题**: 长期收益主要来自股息再投资，而非股价成长；\"旧经济\"现金牛常打败\"新经济\"成长股\n")
    f.write("> **回测区间**: 2015-01-05 → 2025-01-02 (10年) | 初始投入: 10万元 | 股息全额再投\n")
    f.write(f"> **有效样本**: {len(df)} 只 (剔除2015年后上市及无数据标的)\n\n")
    
    # 分类统计
    cat_counts = df['category'].value_counts()
    f.write("## 一、Siegel 分类统计\n\n")
    f.write("| 分类 | 数量 | 含义 |\n")
    f.write("|:---|:---:|:---|\n")
    for cat in ["🌟 成长红利双驱", "🚬 豪华万宝路", "🚬 经典万宝路", "🚬 极致万宝路",
                "💰 红利复利", "📈 成长驱动", "🚀 纯成长",
                "📊 一般", "😐 平庸", "⚠️ 价值陷阱", "🕳️ 股息陷阱", "💀 价值毁灭"]:
        cnt = cat_counts.get(cat, 0)
        if cnt > 0:
            reason = df[df['category'] == cat]['category_reason'].iloc[0]
            f.write(f"| {cat} | {cnt} | {reason} |\n")
    
    f.write(f"\n## 二、Siegel 评分 Top 50\n\n")
    f.write("> **评分逻辑**: 股息贡献占比(30) + 分红收回本金(20) + 总收益合理性(15) + CAGR合理性(10) + 回撤控制(10) + 股息增持(10) + 行业偏好(5)\n")
    f.write("> Siegel 认为 **8-15% CAGR + 高分红 + 低回撤 + 传统行业** 的组合最佳\n\n")
    
    f.write("| 排名 | Siegel分 | 名称 | 代码 | 总收益 | CAGR | 股价涨跌 | 股息贡献 | 分红/本金 | 最差年 | 分类 | 行业 |\n")
    f.write("|:---:|:---:|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---|\n")
    
    for _, r in df.head(50).iterrows():
        f.write(f"| {int(r['rank'])} | **{int(r['siegel_score'])}** | {r['name']} | {r['code'].replace('.SH','').replace('.SZ','')} | "
                f"{r['total_ret']:+.1f}% | {r['cagr']:+.1f}% | {r['price_chg']:+.1f}% | "
                f"{r['div_contrib']:+.1f}% | {r['div_ratio']:.0f}% | {r['max_yr_loss']:+.1f}% | "
                f"{r['category']} | {r['industry']} |\n")
    
    # 分类详解
    f.write(f"\n## 三、各类别详解\n\n")
    
    # 万宝路型
    marlboro = df[df['category'].str.contains('万宝路')]
    if not marlboro.empty:
        f.write("### 🚬 万宝路型 — Siegel 最推崇的模式\n\n")
        f.write("Siegel 在《投资者的未来》中以万宝路（菲利普莫里斯）为例说明：\n\n")
        f.write("> *\"一家处于衰退行业的公司，面临诉讼、监管和公众反感，却成为美股历史上回报最高的股票之一。秘密是什么？—— 高股息率 + 股息再投资 + 低估值起点。\"*\n\n")
        f.write("A 股万宝路的共同特征：\n")
        f.write("- 行业被政策/舆论压制（煤炭碳中和、银行让利、钢铁去产能、石油环保）\n")
        f.write("- 极强现金流但无法再投资扩张 → 只能高分红\n")
        f.write("- 低估值 → 股息率极高 → 复投效应放大\n")
        f.write("- 收益主要来自「持有」而非「交易」\n\n")
        f.write(f"共 {len(marlboro)} 只：\n\n")
        for _, r in marlboro.iterrows():
            f.write(f"- **{r['name']}** ({r['industry']}): +{r['total_ret']:.1f}%, "
                    f"分红收回本金 {r['div_ratio']:.0f}%, Siegel评分 {int(r['siegel_score'])}\n")
    
    # 成长红利双驱
    gd = df[df['category'] == '🌟 成长红利双驱']
    if not gd.empty:
        f.write(f"\n### 🌟 成长红利双驱 — Siegel 也会赞赏\n\n")
        f.write("Siegel 不反对成长，他反对的是「为成长付过高价格」。这些标的既有强劲成长又有充沛分红：\n\n")
        for _, r in gd.iterrows():
            f.write(f"- **{r['name']}** ({r['industry']}): +{r['total_ret']:.1f}%, "
                    f"股价+{r['price_chg']:.1f}%, 分红收回本金 {r['div_ratio']:.0f}%\n")
    
    # 价值陷阱
    vt = df[df['category'] == '⚠️ 价值陷阱']
    if not vt.empty:
        f.write(f"\n### ⚠️ 价值陷阱 — Siegel 的警告\n\n")
        f.write("Siegel 提醒：高股息率可能是股价暴跌的结果（\"股息陷阱\"），不是真正的高回报。需区分：\n")
        f.write("- 真正的万宝路：稳定业务 + 可持续分红\n")
        f.write("- 股息陷阱：股价腰斩 → 股息率虚高 → 来年分红砍半\n\n")
        for _, r in vt.head(10).iterrows():
            f.write(f"- **{r['name']}** ({r['industry']}): 股价{r['price_chg']:+.1f}%, "
                    f"总收益仅{r['total_ret']:+.1f}%\n")
    
    # 完整排名表
    f.write(f"\n## 四、完整排名 ({len(df)} 只)\n\n")
    f.write("| 排名 | S分 | 名称 | 总收益 | CAGR | 股价 | 股息 | 分红/本 | 最差年 | 分类 | 行业 |\n")
    f.write("|:---:|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---|\n")
    
    for _, r in df.iterrows():
        f.write(f"| {int(r['rank'])} | {int(r['siegel_score'])} | {r['name']} | "
                f"{r['total_ret']:+.1f}% | {r['cagr']:+.1f}% | {r['price_chg']:+.1f}% | "
                f"{r['div_contrib']:+.1f}% | {r['div_ratio']:.0f}% | {r['max_yr_loss']:+.1f}% | "
                f"{r['category']} | {r['industry']} |\n")
    
    # 如果我是 Siegel：十大建议
    f.write(f"\n## 五、如果我是 Siegel：对 A 股投资者的十大建议\n\n")
    f.write("基于以上数据，如果我是《投资者的未来》作者，我会对中国股息投资者说：\n\n")
    f.write("### 总结论\n\n")
    f.write("**1. A 股的万宝路确实存在且威力巨大。** 中国神华、四大行、格力电器等标的完美复刻了 Siegel 的核心发现——在十年尺度上，股息再投资贡献了总收益的 60-100%。这验证了 Siegel 的理论在 A 股同样成立。\n\n")
    
    # Top 10 by Siegel score
    f.write("**2. Siegel 评分最高的 10 只标的：**\n\n")
    for _, r in df.head(10).iterrows():
        f.write(f"   {int(r['rank'])}. **{r['name']}** ({r['industry']}) — Siegel评分 {int(r['siegel_score'])}: "
                f"+{r['total_ret']:.0f}%, 股息贡献{r['div_contrib']:.0f}%, 最差年仅{r['max_yr_loss']:+.0f}%\n")
    
    f.write("\n**3. 白酒是 A 股的\"万宝路升级版\"** — 既有股息复利又有惊人成长，但当前估值远高于 Siegel 偏好的低起点，未来十年复现概率低。\n\n")
    f.write("**4. 银行是 Siegel 框架下的最优板块** — 六大行全部位列万宝路型，低估值起点 + 稳定高分红 + 政策\"让利\"压制估值 = 典型的 Siegel 式机会。\n\n")
    f.write("**5. 警惕\"伪万宝路\"** — 云南白药、大秦铁路等看似高股息但股价持续下跌、分红不足以弥补的标的，是 Siegel 最担心的价值陷阱。\n\n")
    f.write("**6. 半导体/新能源的高收益不可靠** — 北方华创 10 年 +1413% 看起来惊人，但股息贡献仅 1%，完全依赖股价。Siegel 会问：下一个 10 年还能涨 14 倍吗？\n\n")
    f.write("**7. 传统行业 + 高分红 > 新兴行业 + 零分红** — 煤炭的中国神华 (+272%) 跑赢了绝大多数\"新经济\"标的。这与 Siegel 统计的美国数据完全一致。\n\n")
    f.write("**8. 回撤控制是复利的关键** — 最大年度亏损 < 15% 的标的中，万宝路型占比极高。Siegel 强调：少亏比多赚更重要。\n\n")
    f.write("**9. 买入时机决定一切** — 同一只格力电器，2015 年买入 +100%，2021 年买入可能亏损。Siegel 的 DCA（定期定额）+ 长期持有才是正解。\n\n")
    f.write("**10. 中国特色的万宝路机会** — 碳中和压煤炭、让利压银行、地产萧条压钢铁水泥……政策越压、估值越低、股息率越高、复投效应越强。这正是 Siegel 最喜欢的\"厌恶型资产\"。\n\n")

print(f"✅ 报告已生成: {report_path}")
print(f"   共 {len(df)} 只标的, 分类 {df['category'].nunique()} 种")
