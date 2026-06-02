#!/usr/bin/env python3
"""
股票分类对比分析：动态CSI300 vs 精选32基础策略
"""
import pandas as pd
import os
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))

def load_snapshots(path):
    df = pd.read_csv(path)
    df['date'] = pd.to_datetime(df['date'])
    # 排除现金管理
    df = df[df['category'] != '现金管理'].copy()
    return df

def get_unique_stocks(df):
    """获取所有出现过的股票（去重）"""
    return df[['ts_code', 'name', 'category']].drop_duplicates('ts_code')

def get_category_stats(df, label=""):
    """统计每个调仓日的持仓分类分布"""
    dates = sorted(df['date'].unique())
    
    rows = []
    for dt in dates:
        snap = df[df['date'] == dt]
        # 按市值权重统计
        total_mv = snap['market_value'].sum()
        for cat, grp in snap.groupby('category'):
            rows.append({
                'date': dt,
                'category': cat,
                'stock_count': len(grp),
                'weight_pct': grp['market_value'].sum() / total_mv * 100,
            })
    return pd.DataFrame(rows)

def infer_sector(cat, name):
    """从category和name中推断sector"""
    cat = str(cat)
    name = str(name)
    
    if '银行' in name or '银行' in cat:
        return '银行'
    if '茅台' in name or '五粮液' in name or '泸州老窖' in name or '洋河' in name or '汾酒' in name:
        return '白酒'
    if '保险' in name or '寿险' in cat:
        return '保险'
    if '电力' in name or '国电' in name or '华能' in name or '华电' in name or '浙能' in name or '大唐' in name:
        return '电力'
    if '煤炭' in name or '神华' in name or '中煤' in name or '露天煤' in name:
        return '煤炭/矿业'
    if '石油' in name or '中海油' in name or '中石油' in name:
        return '石油'
    if '钢铁' in name or '宝钢' in name or '钢' in name:
        return '钢铁'
    if '家电' in name or '格力' in name or '美的' in name or '海尔' in name or '海信' in name:
        return '家电'
    if '汽车' in name or '上汽' in name or '长城' in name or '长安' in name:
        return '汽车'
    if '地产' in name or '万科' in name or '保利' in name or '华润' in name:
        return '地产'
    if '建材' in name or '海螺' in name or '东方雨虹' in name:
        return '建材'
    if '食品' in name or '双汇' in name or '伊利' in name or '中粮' in name:
        return '食品饮料'
    if '证券' in name or '券商' in cat:
        return '证券'
    if '铁路' in name or '大秦' in name or '京沪' in name:
        return '交通'
    if '通信' in name or '移动' in name or '联通' in name or '电信' in name:
        return '通信运营商'
    if '交通' in name or '运输' in name:
        return '交通运输'
    if '中药' in cat or '药' in name:
        return '医药'
    if '航运' in name or '中远海' in name or '招商轮' in name:
        return '航运'
    if '水泥' in name:
        return '建材'
    if '化工' in name or '万华' in name:
        return '化工'
    
    # fallback to category
    return cat

def main():
    print("=" * 80)
    print("📊 股票分类对比分析：动态CSI300 vs 精选32基础策略")
    print("=" * 80)

    # 加载数据
    base_df = load_snapshots(f"{BASE}/data/backtest/精选32/holding_snapshots.csv")
    dyn_df = load_snapshots(f"{BASE}/data/backtest/沪深300高股息动态/holding_snapshots.csv")

    print(f"\n精选32: {base_df['date'].min().date()} ~ {base_df['date'].max().date()}, "
          f"{base_df['date'].nunique()} 个季度快照")
    print(f"动态CSI300: {dyn_df['date'].min().date()} ~ {dyn_df['date'].max().date()}, "
          f"{dyn_df['date'].nunique()} 个季度快照")

    # ─── 1. 全局唯一标的 ──────────────────────────────────────────
    print("\n" + "─" * 60)
    print("📋 1. 全历史唯一持仓标的")
    print("─" * 60)
    
    base_uniq = get_unique_stocks(base_df)
    dyn_uniq = get_unique_stocks(dyn_df)
    
    print(f"\n精选32 历史持有过: {len(base_uniq)} 只")
    print(f"动态CSI300 历史持有过: {len(dyn_uniq)} 只")
    
    # 交集
    common = set(base_uniq['ts_code']) & set(dyn_uniq['ts_code'])
    only_base = set(base_uniq['ts_code']) - set(dyn_uniq['ts_code'])
    only_dyn = set(dyn_uniq['ts_code']) - set(base_uniq['ts_code'])
    print(f"共同持有过: {len(common)} 只")
    print(f"仅精选32有: {len(only_base)} 只")
    print(f"仅动态CSI300有: {len(only_dyn)} 只")

    # ─── 2. Category 分布 ──────────────────────────────────────────
    print("\n" + "─" * 60)
    print("📋 2. Category 分布对比（按出现频次）")
    print("─" * 60)
    
    # 按出现的股票-日期对统计
    base_cat_cnt = base_df.groupby('category')['ts_code'].count().sort_values(ascending=False)
    dyn_cat_cnt = dyn_df.groupby('category')['ts_code'].count().sort_values(ascending=False)
    base_total = base_cat_cnt.sum()
    dyn_total = dyn_cat_cnt.sum()
    
    all_cats = sorted(set(base_cat_cnt.index) | set(dyn_cat_cnt.index))
    
    print(f"\n{'Category':<20} {'精选32(次数)':<14} {'精选32%':<10} {'动态CSI300(次数)':<18} {'动态CSI300%'}")
    print("-" * 75)
    for cat in all_cats:
        b = base_cat_cnt.get(cat, 0)
        d = dyn_cat_cnt.get(cat, 0)
        bp = b/base_total*100 if base_total > 0 else 0
        dp = d/dyn_total*100 if dyn_total > 0 else 0
        print(f"{cat:<20} {b:<14} {bp:<10.1f} {d:<18} {dp:.1f}")
    
    # ─── 3. 各调仓时点平均持仓数 ─────────────────────────────────────
    print("\n" + "─" * 60)
    print("📋 3. 每季度平均持仓股票数")
    print("─" * 60)
    
    base_counts = base_df.groupby('date')['ts_code'].nunique()
    dyn_counts = dyn_df.groupby('date')['ts_code'].nunique()
    print(f"\n精选32: 平均 {base_counts.mean():.1f} 只/季度, 最多 {base_counts.max()} 只, 最少 {base_counts.min()} 只")
    print(f"动态CSI300: 平均 {dyn_counts.mean():.1f} 只/季度, 最多 {dyn_counts.max()} 只, 最少 {dyn_counts.min()} 只")

    # ─── 4. 精选32 全历史标的明细 ─────────────────────────────────
    print("\n" + "─" * 60)
    print("📋 4. 精选32 — 历史持仓标的明细")
    print("─" * 60)
    
    base_detail = base_uniq.copy()
    base_detail['sector'] = base_detail.apply(lambda r: infer_sector(r['category'], r['name']), axis=1)
    base_detail = base_detail.sort_values('sector')
    
    # 统计各sector出现次数和首次日期
    base_df2 = base_df.copy()
    base_df2['sector'] = base_df2.apply(lambda r: infer_sector(r['category'], r['name']), axis=1)
    
    first_date = base_df2.groupby('ts_code')['date'].min().reset_index()
    first_date.columns = ['ts_code', 'first_date']
    base_detail = base_detail.merge(first_date, on='ts_code', how='left')
    
    freq = base_df2.groupby('ts_code')['date'].count().reset_index()
    freq.columns = ['ts_code', 'freq']
    base_detail = base_detail.merge(freq, on='ts_code', how='left')
    
    for sector, grp in base_detail.groupby('sector'):
        print(f"\n  【{sector}】({len(grp)}只)")
        for _, row in grp.iterrows():
            print(f"    {row['ts_code']}  {row['name']:<10} cat={row['category']}  "
                  f"出现{row['freq']:.0f}次  首次={str(row['first_date'])[:10]}")

    # ─── 5. 动态CSI300 几个关键时点快照 ─────────────────────────────
    print("\n" + "─" * 60)
    print("📋 5. 动态CSI300 — 关键时点持仓分类分布")
    print("─" * 60)
    
    key_dates = ['2016-03-25', '2018-06-29', '2019-12-27', '2021-06-25', '2023-09-29', '2025-03-28']
    
    dyn_df2 = dyn_df.copy()
    dyn_df2['sector'] = dyn_df2.apply(lambda r: infer_sector(r['category'], r['name']), axis=1)
    
    for kd in key_dates:
        kdt = pd.Timestamp(kd)
        # 找最近的实际调仓日
        avail = dyn_df['date'].unique()
        diffs = [(abs((d - kdt).days), d) for d in avail]
        actual_dt = min(diffs, key=lambda x: x[0])[1]
        
        snap = dyn_df2[dyn_df2['date'] == actual_dt].copy()
        if snap.empty:
            continue
            
        total_mv = snap['market_value'].sum()
        cat_stats = snap.groupby('sector').agg(
            count=('ts_code', 'nunique'),
            weight=('market_value', lambda x: x.sum()/total_mv*100)
        ).sort_values('weight', ascending=False)
        
        print(f"\n  {str(actual_dt)[:10]} ({len(snap)}只持仓):")
        print(f"  {'Sector':<18} {'只数':>5} {'仓位%':>8}")
        print(f"  {'-'*35}")
        for sec, row in cat_stats.iterrows():
            print(f"  {sec:<18} {row['count']:>5} {row['weight']:>7.1f}%")

    # ─── 6. 动态CSI300 全历史出现过的 sector 分布 ─────────────────
    print("\n" + "─" * 60)
    print("📋 6. 动态CSI300 — 全历史 Sector 出现频次Top20")
    print("─" * 60)
    
    dyn_sec_freq = dyn_df2.groupby('sector')['ts_code'].count().sort_values(ascending=False)
    dyn_total2 = dyn_sec_freq.sum()
    
    print(f"\n{'Sector':<20} {'出现次数':<12} {'占比%'}")
    for sec, cnt in dyn_sec_freq.head(20).items():
        print(f"{sec:<20} {cnt:<12} {cnt/dyn_total2*100:.1f}%")

    # ─── 7. 拖累绩效的关键标的分析 ─────────────────────────────────
    print("\n" + "─" * 60)
    print("📋 7. 动态CSI300 — 2018年深度回撤期间持仓（2018 Q2/Q3）")
    print("─" * 60)
    
    bear_dates = [d for d in dyn_df['date'].unique() 
                  if pd.Timestamp('2018-01-01') <= d <= pd.Timestamp('2019-01-01')]
    
    for bd in sorted(bear_dates):
        snap = dyn_df2[dyn_df2['date'] == bd].copy()
        snap = snap.sort_values('profit_pct')
        print(f"\n  {str(bd)[:10]} ({len(snap)}只, 亏损最大的5只):")
        worst = snap.head(5)
        for _, row in worst.iterrows():
            print(f"    {row['ts_code']} {row['name']:<10} sector={row['sector']}  "
                  f"权重{row['weight']:.1f}%  涨跌{row['profit_pct']:+.1f}%")

    # ─── 8. 精选32 vs 动态CSI300 2018年对比 ─────────────────────
    print("\n" + "─" * 60)
    print("📋 8. 2018年精选32 vs 动态CSI300 分类权重对比")
    print("─" * 60)
    
    # 找2018年季度快照
    base_2018_dates = [d for d in base_df['date'].unique() 
                       if pd.Timestamp('2018-01-01') <= d <= pd.Timestamp('2018-12-31')]
    dyn_2018_dates = [d for d in dyn_df['date'].unique() 
                      if pd.Timestamp('2018-01-01') <= d <= pd.Timestamp('2018-12-31')]
    
    if base_2018_dates and dyn_2018_dates:
        # 取年中快照
        base_2018 = base_df2[base_df2['date'].isin(base_2018_dates)]
        dyn_2018 = dyn_df2[dyn_df2['date'].isin(dyn_2018_dates)]
        
        base_sec_w = base_2018.groupby('sector')['market_value'].sum()
        base_sec_w = (base_sec_w / base_sec_w.sum() * 100).sort_values(ascending=False)
        
        dyn_sec_w = dyn_2018.groupby('sector')['market_value'].sum()
        dyn_sec_w = (dyn_sec_w / dyn_sec_w.sum() * 100).sort_values(ascending=False)
        
        all_secs = sorted(set(base_sec_w.index) | set(dyn_sec_w.index))
        print(f"\n{'Sector':<20} {'精选32-2018%':<16} {'动态CSI300-2018%'}")
        print("-" * 55)
        for sec in sorted(all_secs, key=lambda s: -max(base_sec_w.get(s,0), dyn_sec_w.get(s,0))):
            b = base_sec_w.get(sec, 0)
            d = dyn_sec_w.get(sec, 0)
            diff_marker = " ◀ 差异显著" if abs(b - d) > 5 else ""
            print(f"{sec:<20} {b:<16.1f} {d:.1f}{diff_marker}")

    # ─── 9. 总结 ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📊 9. 策略对比核心结论")
    print("=" * 60)
    print("""
精选32 (base strategy) 优势：
  ✅ 经过人工质量分层 (certainty AA/A/B+)，剔除低质标的
  ✅ 集中在银行、家电、白酒、水电等确定性高的行业
  ✅ 持仓数少(~10-15只)，高质量集中持股
  ✅ 2018年熊市抗跌：银行+水电提供防御

动态CSI300 劣势：
  ❌ 机械筛选 dv_ttm≥3%，缺乏质量过滤
  ❌ 混入大量建筑、汽车、化工等高股息但周期性强的股票
  ❌ 持仓数多(~20-50只)，分散难以超额
  ❌ 2018年大量周期股回撤放大跌幅

关键改进建议：
  → 在动态模式中叠加 certainty_score ≥ B 过滤
  → 限制建筑/地产/化工等类别的比例上限
  → 参考精选32的 category 分类框架重建筛选规则
""")

if __name__ == "__main__":
    main()
