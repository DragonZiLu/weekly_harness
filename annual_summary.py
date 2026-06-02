"""逐年 Siegel 万宝路汇总报告"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "annual_siegel_summary.md"

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

# ── 加载所有年度数据 ──
years_300 = [2020, 2021, 2022, 2023, 2024, 2025]
years_500 = [2019, 2020, 2021, 2022, 2023, 2024, 2025]

data_300 = {}
data_500 = {}

for yr in years_300:
    p = ROOT / "data" / f"siegel_csi300_{yr}.csv"
    if p.exists():
        df = pd.read_csv(p)
        df['cat'] = df.apply(classify, axis=1)
        data_300[yr] = df

for yr in years_500:
    p = ROOT / "data" / f"siegel_csi500_{yr}.csv"
    if p.exists():
        df = pd.read_csv(p)
        df['cat'] = df.apply(classify, axis=1)
        data_500[yr] = df

# ── 写报告 ──
with open(OUT, 'w', encoding='utf-8') as f:
    f.write("# CSI 300 & CSI 500 逐年 Siegel 万宝路评估 (2019-2025)\n\n")
    f.write("> 每年使用前10年数据 + 当年指数成分，股息全额再投\n\n")

    # === 第一部分: CSI 300 ===
    f.write("## 一、CSI 300 逐年万宝路数量\n\n")
    f.write("| 评估年 | 回测区间 | 有效样本 | 🚬万宝路 | 💰红利复利 | 🌟成长红利 | 💀毁灭 | ⚠️陷阱 |\n")
    f.write("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|\n")
    for yr in years_300:
        if yr not in data_300: continue
        df = data_300[yr]
        marlboro = df[df['cat'].str.contains('万宝路')]
        div_rec = df[df['cat']=='💰 红利复利']
        grow_div = df[df['cat']=='🌟 成长红利双驱']
        destroy = df[df['cat']=='💀 价值毁灭']
        trap = df[df['cat']=='⚠️ 价值陷阱']
        f.write(f"| {yr} | {yr-10}-{yr} | {len(df)} | {len(marlboro)} | {len(div_rec)} | "
                f"{len(grow_div)} | {len(destroy)} | {len(trap)} |\n")

    f.write(f"\n### CSI 300 各年 Top 5 万宝路\n\n")
    for yr in years_300:
        if yr not in data_300: continue
        df = data_300[yr]
        marlboro = df[df['cat'].str.contains('万宝路')].sort_values('total_ret', ascending=False).head(5)
        f.write(f"**{yr} 评估** (回测 {yr-10}-{yr}):\n\n")
        f.write("| 标的 | 总收益 | 股价 | 股息 | 分红/本 | 分类 | 行业 |\n")
        f.write("|:---|:---:|:---:|:---:|:---:|:---|:---|\n")
        for _, r in marlboro.iterrows():
            f.write(f"| {r['name']} | +{r['total_ret']:.0f}% | +{r['price_chg']:.0f}% | "
                    f"+{r['div_contrib']:.0f}% | {r['div_ratio']:.0f}% | "
                    f"{r['cat']} | {r['industry']} |\n")
        f.write("\n")

    # === 第二部分: CSI 500 ===
    f.write("## 二、CSI 500 逐年万宝路数量\n\n")
    valid_500 = sorted([yr for yr in years_500 if yr in data_500])
    f.write("| 评估年 | 回测区间 | 有效样本 | 🚬万宝路 | 💰红利复利 | 🌟成长红利 | 💀毁灭 | ⚠️陷阱 |\n")
    f.write("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|\n")
    for yr in valid_500:
        df = data_500[yr]
        marlboro = df[df['cat'].str.contains('万宝路')]
        div_rec = df[df['cat']=='💰 红利复利']
        grow_div = df[df['cat']=='🌟 成长红利双驱']
        destroy = df[df['cat']=='💀 价值毁灭']
        trap = df[df['cat']=='⚠️ 价值陷阱']
        f.write(f"| {yr} | {yr-10}-{yr} | {len(df)} | {len(marlboro)} | {len(div_rec)} | "
                f"{len(grow_div)} | {len(destroy)} | {len(trap)} |\n")

    f.write(f"\n### CSI 500 各年 Top 5 万宝路\n\n")
    for yr in valid_500:
        df = data_500[yr]
        marlboro = df[df['cat'].str.contains('万宝路')].sort_values('total_ret', ascending=False).head(5)
        f.write(f"**{yr} 评估** (回测 {yr-10}-{yr}):\n\n")
        f.write("| 标的 | 总收益 | 股价 | 股息 | 分红/本 | 分类 | 行业 |\n")
        f.write("|:---|:---:|:---:|:---:|:---:|:---|:---|\n")
        for _, r in marlboro.iterrows():
            f.write(f"| {r['name']} | +{r['total_ret']:.0f}% | +{r['price_chg']:.0f}% | "
                    f"+{r['div_contrib']:.0f}% | {r['div_ratio']:.0f}% | "
                    f"{r['cat']} | {r['industry']} |\n")
        f.write("\n")

    # === 第三部分: 穿越周期的万宝路 ===
    f.write("## 三、CSI 300 穿越周期的万宝路\n\n")
    f.write("各年均被识别为万宝路的标的（最稳健选择）：\n\n")

    # 找每年都出现的万宝路
    all_300_codes = set()
    for yr in years_300:
        if yr in data_300:
            df = data_300[yr]
            mc = set(df[df['cat'].str.contains('万宝路')]['code'])
            all_300_codes.update(mc)

    persistent_300 = []
    for code in sorted(all_300_codes):
        years_present = []
        for yr in years_300:
            if yr in data_300:
                df = data_300[yr]
                m = df[df['code']==code]
                if not m.empty and '万宝路' in m.iloc[0]['cat']:
                    years_present.append(yr)
        if len(years_present) >= 4:  # 至少4年出现
            r = data_300[years_300[-1]][data_300[years_300[-1]]['code']==code].iloc[0]
            persistent_300.append((code, r['name'], r['industry'], years_present))

    if persistent_300:
        f.write("| 标的 | 行业 | 出现年份 | 频次 |\n")
        f.write("|:---|:---|:---|:---:|\n")
        for code, name, ind, yrs in persistent_300:
            f.write(f"| {name} | {ind} | {', '.join(str(y) for y in yrs)} | {len(yrs)}/6 |\n")
    else:
        f.write("无标的在4年以上均被识别为万宝路。\n")

    # CSI 500 穿越
    f.write(f"\n## 四、CSI 500 穿越周期的万宝路\n\n")
    all_500_codes = set()
    for yr in valid_500:
        df = data_500[yr]
        mc = set(df[df['cat'].str.contains('万宝路')]['code'])
        all_500_codes.update(mc)

    persistent_500 = []
    for code in sorted(all_500_codes):
        years_present = []
        for yr in valid_500:
            df = data_500[yr]
            m = df[df['code']==code]
            if not m.empty and '万宝路' in m.iloc[0]['cat']:
                years_present.append(yr)
        if len(years_present) >= 4:
            # 取最后出现的年份来拿名字
            last_yr = years_present[-1]
            r = data_500[last_yr][data_500[last_yr]['code']==code].iloc[0]
            persistent_500.append((code, r['name'], r['industry'], years_present))

    if persistent_500:
        f.write("| 标的 | 行业 | 出现年份 | 频次 |\n")
        f.write("|:---|:---|:---|:---:|\n")
        for code, name, ind, yrs in persistent_500:
            f.write(f"| {name} | {ind} | {', '.join(str(y) for y in yrs)} | {len(yrs)}/{len(valid_500)} |\n")

    # === 第五部分: 趋势分析 ===
    f.write(f"\n## 五、关键趋势\n\n")

    # CSI 300 trend
    f.write("### CSI 300 万宝路数量趋势\n\n")
    for yr in years_300:
        if yr in data_300:
            df = data_300[yr]
            m = len(df[df['cat'].str.contains('万宝路')])
            bar = '█' * (m // 2)
            f.write(f"**{yr}**: {bar} {m} 只\n")
    
    f.write("\n### CSI 500 万宝路数量趋势\n\n")
    for yr in valid_500:
        df = data_500[yr]
        m = len(df[df['cat'].str.contains('万宝路')])
        bar = '█' * (m // 2)
        f.write(f"**{yr}**: {bar} {m} 只\n")

    # 行业变迁
    f.write("\n### CSI 300 万宝路行业变迁\n\n")
    f.write("| 行业 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |\n")
    f.write("|:---|:---:|:---:|:---:|:---:|:---:|:---:|\n")
    
    all_300_inds = set()
    for yr in years_300:
        if yr in data_300:
            df = data_300[yr]
            inds = df[df['cat'].str.contains('万宝路')]['industry'].value_counts()
            all_300_inds.update(inds.index)
    
    for ind in sorted(all_300_inds, key=lambda x: -sum(
            len(data_300[yr][(data_300[yr]['cat'].str.contains('万宝路')) & (data_300[yr]['industry']==x)]) 
            for yr in years_300 if yr in data_300)):
        row = f"| {ind} |"
        for yr in years_300:
            if yr in data_300:
                df = data_300[yr]
                cnt = len(df[(df['cat'].str.contains('万宝路')) & (df['industry']==ind)])
                row += f" {cnt} |"
            else:
                row += " - |"
        if sum(int(c.strip()) for c in row.split('|')[2:-1] if c.strip().isdigit()) > 0:
            f.write(row + "\n")

print(f"✅ 汇总报告已生成: {OUT}")
