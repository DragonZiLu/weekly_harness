"""
对比分析：2020年 vs 2025年 CSI 300 的万宝路标的
=============================================
1. 两个时期（2010-2020, 2015-2025）各识别万宝路
2. 2020年的万宝路，后续5年（2020-2025）实际表现
3. 两个时期的万宝路交集
"""
import pandas as pd
from pathlib import Path
ROOT = Path(__file__).parent

# ─── Siegel 分类 ──────────────────────────────────────────────
def classify_siegel(row):
    total = row['total_ret']
    div_contrib = row['div_contrib']
    div_ratio = row['div_ratio']
    price_chg = row['price_chg']
    if total <= 0:
        return "💀 价值毁灭"
    div_pct = div_contrib / total * 100 if total > 0 else 0
    
    if div_pct >= 60 and div_ratio >= 50 and total > 0:
        if price_chg < 0:
            return "🚬 极致万宝路"
        elif price_chg < 50:
            return "🚬 经典万宝路"
        else:
            return "🚬 豪华万宝路"
    if div_pct >= 40 and div_ratio >= 40 and total > 30:
        return "💰 红利复利"
    if price_chg > 100 and div_ratio >= 50 and total > 200:
        return "🌟 成长红利双驱"
    if price_chg > 200 and total > 200 and div_ratio < 30:
        return "🚀 纯成长"
    if price_chg > 100 and total > 100:
        return "📈 成长驱动"
    if total < 50 and price_chg < 0:
        return "⚠️ 价值陷阱"
    if total < 80:
        return "😐 平庸"
    return "📊 一般"

def is_marlboro(cat):
    return '万宝路' in cat

# ─── 加载两期数据 ──────────────────────────────────────────────
df2020 = pd.read_csv(ROOT / "data/marlboro_hs300_2020.csv")
df_now = pd.read_csv(ROOT / "data/marlboro_hs300_all.csv")

df2020['category'] = df2020.apply(classify_siegel, axis=1)
df_now['category'] = df_now.apply(classify_siegel, axis=1)

marlboro_2020 = df2020[df2020['category'].apply(is_marlboro)].copy()
marlboro_now = df_now[df_now['category'].apply(is_marlboro)].copy()

codes_2020 = set(marlboro_2020['code'].tolist())
codes_now = set(marlboro_now['code'].tolist())

# ─── 交集分析 ──────────────────────────────────────────────────
# 两期都有数据的交集（代码相同）
common_2020 = df2020[df2020['code'].isin(codes_now)].copy()
common_now = df_now[df_now['code'].isin(codes_2020)].copy()

both_marlboro = codes_2020 & codes_now  # 两期都是万宝路
only_2020 = codes_2020 - codes_now
only_now = codes_now - codes_2020

print(f"=== 万宝路对比 ===")
print(f"2020年万宝路: {len(marlboro_2020)} 只")
print(f"当前万宝路:   {len(marlboro_now)} 只")
print(f"两期交集(持续万宝路): {len(both_marlboro)} 只")
print(f"仅2020识别: {len(only_2020)} 只 (2020是万宝路, 现在不是)")
print(f"仅当前识别: {len(only_now)} 只 (现在才是万宝路)")

# ─── 2020年万宝路后续5年(2020-2025)实际表现 ──────────────────────
# 读取当前数据中有这些股票的记录（它们2015-2025的回测结果）
# 但要单独算2020-2025的表现
print(f"\n=== 2020年万宝路 {len(marlboro_2020)} 只, 后续5年表现 ===")

import sys
sys.path.insert(0, str(ROOT))
from stock_10y_hold import simulate as simulate_10y
import time

subsequent = []
for idx, (_, r) in enumerate(marlboro_2020.iterrows()):
    code, name = r['code'], r['name']
    for retry in range(3):
        try:
            rows, final_val, cagr, _ = simulate_10y(code, "2020-01-02", 100000, verbose=False)
            if not rows:
                subsequent.append({'code': code, 'name': name, 'ret_5y': None, 'cagr_5y': None})
                break
            total_5y = (final_val / 100000 - 1) * 100
            yrs = 5
            cagr_5y = ((final_val / 100000) ** (1/yrs) - 1) * 100 if final_val > 0 else 0
            subsequent.append({
                'code': code, 'name': name,
                'ret_5y': total_5y, 'cagr_5y': cagr_5y,
                'still_marlboro_in_2025': code in codes_now,
            })
            break
        except:
            if retry == 2:
                subsequent.append({'code': code, 'name': name, 'ret_5y': None, 'cagr_5y': None})
        time.sleep(0.5)
    time.sleep(0.2)

df_sub = pd.DataFrame(subsequent)
valid_sub = df_sub[df_sub['ret_5y'].notna()]

print(f"  {len(valid_sub)} 只有后续数据")
if len(valid_sub) > 0:
    print(f"  后续5年平均收益: {valid_sub['ret_5y'].mean():+.1f}%")
    print(f"  正收益占比: {len(valid_sub[valid_sub['ret_5y']>0])}/{len(valid_sub)}")
    winners = valid_sub[valid_sub['ret_5y'] > 0].sort_values('ret_5y', ascending=False)
    losers = valid_sub[valid_sub['ret_5y'] <= 0].sort_values('ret_5y')
    print(f"\n  Top 5 后续表现:")
    for _, r in winners.head(5).iterrows():
        print(f"    {r['name']}: 5年 +{r['ret_5y']:.1f}%, 仍在万宝路: {r['still_marlboro_in_2025']}")
    if len(losers) > 0:
        print(f"\n  最差 5 只:")
        for _, r in losers.head(5).iterrows():
            print(f"    {r['name']}: 5年 {r['ret_5y']:+.1f}%, 仍在万宝路: {r['still_marlboro_in_2025']}")

# ─── 报告 ──────────────────────────────────────────────────────
report_path = ROOT / "data/marlboro_compare_report.md"
with open(report_path, 'w', encoding='utf-8') as f:
    f.write("# CSI 300 万宝路标的：2020年 vs 当前 对比报告\n\n")
    f.write("> **回测区间**: 2020视角(2010-2020) vs 当前视角(2015-2025)\n")
    f.write("> **前视偏差控制**: 各期严格使用对应日期的指数成分 + 剔除回测起点后上市标的\n\n")
    
    f.write("## 一、概况\n\n")
    f.write(f"| 时期 | 回测区间 | 成分日期 | 有效样本 | 万宝路数量 |\n")
    f.write(f"|:---|:---|:---|:---:|:---:|\n")
    f.write(f"| 2020年视角 | 2010-2020 | 2020-01-02 | {len(df2020)} | {len(marlboro_2020)} |\n")
    f.write(f"| 当前视角 | 2015-2025 | 2026-05-29 | {len(df_now)} | {len(marlboro_now)} |\n\n")
    
    f.write(f"## 二、两期都识别为万宝路的标的 ({len(both_marlboro)} 只)\n\n")
    f.write("这是最可靠的万宝路——穿越两个不同十年区间，依然保持万宝路特征。\n\n")
    f.write("| 名称 | 代码 | 行业 | 2020总收益 | 2020股息贡献 | 当前总收益 | 当前股息贡献 |\n")
    f.write("|:---|:---|:---|:---:|:---:|:---:|:---:|\n")
    for code in sorted(both_marlboro):
        r20 = marlboro_2020[marlboro_2020['code'] == code].iloc[0]
        rn = marlboro_now[marlboro_now['code'] == code].iloc[0]
        f.write(f"| {r20['name']} | {code} | {r20['industry']} | "
                f"+{r20['total_ret']:.1f}% | +{r20['div_contrib']:.1f}% | "
                f"+{rn['total_ret']:.1f}% | +{rn['div_contrib']:.1f}% |\n")
    
    f.write(f"\n## 三、仅2020年识别为万宝路 ({len(only_2020)} 只)\n\n")
    f.write("这些在2020年看起来是万宝路，但2025年回测中不再是。可能是行业变化、分红减少或股价暴涨改变了模式。\n\n")
    for code in sorted(only_2020):
        r = marlboro_2020[marlboro_2020['code'] == code].iloc[0]
        now_cat = df_now[df_now['code'] == code]['category'].iloc[0] if code in df_now['code'].values else '不在当前成分'
        f.write(f"- **{r['name']}** ({r['industry']}): 2020总收益+{r['total_ret']:.1f}%, "
                f"当前分类: {now_cat}\n")
    
    f.write(f"\n## 四、仅当前识别为万宝路 ({len(only_now)} 只)\n\n")
    f.write("这些在2020年还不是万宝路，但最近十年成了万宝路。可能是近十年分红大增、或股价走弱凸显了分红价值。\n\n")
    for code in sorted(only_now):
        r = marlboro_now[marlboro_now['code'] == code].iloc[0]
        old_cat = df2020[df2020['code'] == code]['category'].iloc[0] if code in df2020['code'].values else '不在2020成分'
        f.write(f"- **{r['name']}** ({r['industry']}): 当前总收益+{r['total_ret']:.1f}%, "
                f"2020分类: {old_cat}\n")
    
    if len(valid_sub) > 0:
        f.write(f"\n## 五、2020年万宝路后续5年(2020-2025)真实表现\n\n")
        f.write(f"> 如果2020年买入万宝路并持有5年，实际赚了多少？\n\n")
        f.write(f"| 名称 | 代码 | 5年总收益 | 5年CAGR | 仍为万宝路? |\n")
        f.write(f"|:---|:---|:---:|:---:|:---:|\n")
        for _, r in df_sub.sort_values('ret_5y', ascending=False, na_position='last').iterrows():
            ret = f"+{r['ret_5y']:.1f}%" if pd.notna(r['ret_5y']) else "N/A"
            cagr = f"+{r['cagr_5y']:.1f}%" if pd.notna(r['cagr_5y']) else "N/A"
            f.write(f"| {r['name']} | {r['code']} | {ret} | {cagr} | "
                    f"{'✅' if r['still_marlboro_in_2025'] else '❌'} |\n")
        
        avg = valid_sub['ret_5y'].mean()
        pos_pct = len(valid_sub[valid_sub['ret_5y']>0])/len(valid_sub)*100
        f.write(f"\n**统计**: 平均 +{avg:.1f}%, 正收益比例 {pos_pct:.0f}%\n")

print(f"\n✅ 对比报告: {report_path}")
