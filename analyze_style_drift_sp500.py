#!/usr/bin/env python3
"""
SP500 风格选股 · 风格漂移检测

SP500 风格特点：
  - NI + FCF 双正净利润过滤
  - D'Hondt 行业平衡分配席位
  - 自由流通市值加权 (free_float_mv)
  - 单股权重上限 10%
  
检测维度：
  1. 行业权重漂移（D'Hondt 是否真的做到了行业中性?）
  2. 规模偏好漂移（自由流通市值 vs 总市值趋势）
  3. 权重集中度漂移（Top N 集中度变化）
  4. 换手率归因

用法：
  python analyze_style_drift_sp500.py
  python analyze_style_drift_sp500.py --version top100
  python analyze_style_drift_sp500.py --version top300 --plot
"""

import json
import sys
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ─── 申万行业 → CSI 大类映射 ─────────────────────
SECTOR_MAP = {
    "银行": "金融", "多元金融": "金融", "证券": "金融", "保险": "金融",
    "全国地产": "房地产", "区域地产": "房地产", "房产服务": "房地产", "园区开发": "房地产",
    "水力发电": "公用事业", "火力发电": "公用事业", "新型电力": "公用事业",
    "供气供热": "公用事业", "水务": "公用事业", "环境保护": "公用事业",
    "电信运营": "电信业务", "通信设备": "电信业务",
    "白酒": "主要消费", "啤酒": "主要消费", "食品": "主要消费",
    "乳制品": "主要消费", "软饮料": "主要消费", "种植业": "主要消费",
    "农业综合": "主要消费", "百货": "主要消费", "服饰": "可选消费",
    "家用电器": "可选消费", "家居用品": "可选消费", "汽车整车": "可选消费",
    "汽车配件": "可选消费", "摩托车": "可选消费", "文教休闲": "可选消费",
    "广告包装": "可选消费", "日用化工": "可选消费", "出版业": "可选消费",
    "中成药": "医药卫生", "化学制药": "医药卫生", "医药商业": "医药卫生",
    "医疗保健": "医药卫生", "生物制药": "医药卫生",
    "煤炭开采": "能源", "石油开采": "能源", "石油加工": "能源",
    "铝": "原材料", "铜": "原材料", "特种钢": "原材料", "普钢": "原材料",
    "其他建材": "原材料", "水泥": "原材料", "农药化肥": "原材料",
    "化工原料": "原材料", "染料涂料": "原材料",
    "建筑工程": "工业", "专用机械": "工业", "工程机械": "工业",
    "电气设备": "工业", "运输设备": "工业", "铁路": "工业",
    "路桥": "工业", "港口": "工业", "水运": "工业", "仓储物流": "工业",
    "互联网": "信息技术", "IT设备": "信息技术", "软件服务": "信息技术", "元器件": "信息技术",
}

VER_DIRS = {
    'full': 'output/sp500_style/baskets',
    'top100': 'output/sp500_style_100/baskets',
    'top200': 'output/sp500_style_200/baskets',
    'top300': 'output/sp500_style_300/baskets',
}

VERSION_LABELS = {
    'full': 'SP500风格·全量',
    'top100': 'SP500风格·Top100',
    'top200': 'SP500风格·Top200',
    'top300': 'SP500风格·Top300',
}


def get_sector(industry):
    if not industry or str(industry) == 'nan':
        return '其他'
    return SECTOR_MAP.get(str(industry), '其他')


def load_all_baskets(version):
    ver_dir = VER_DIRS.get(version)
    if not ver_dir:
        raise ValueError(f"未知版本: {version}，可选: {list(VER_DIRS.keys())}")
    basket_path = ROOT / ver_dir / 'all_baskets.json'
    if not basket_path.exists():
        raise FileNotFoundError(f"篮子文件不存在: {basket_path}")
    with open(basket_path) as f:
        baskets = json.load(f)
    dates = sorted(baskets.keys())
    print(f"加载 {VERSION_LABELS[version]}: {len(dates)} 期, {basket_path}")
    return baskets, dates


def build_time_series(baskets, dates):
    """
    SP500 风格篮子格式: {date: {ts_code: {name, industry, total_mv, circ_mv, free_float_mv, circ_ratio, profit, weight}}}
    """
    records = []
    sector_weights_ts = defaultdict(list)

    for d in dates:
        holdings_dict = baskets[d]
        n = len(holdings_dict)

        rec = {'date': d, 'n_stocks': n}
        sector_w = defaultdict(float)
        sector_count = defaultdict(int)

        total_w = 0.0
        total_mvs = []
        free_float_mvs = []
        profits = []

        for code, s in holdings_dict.items():
            w = s.get('weight', 1.0 / n)
            total_w += w

            industry = s.get('industry', '其他')
            sector = get_sector(industry)
            sector_w[sector] += w
            sector_count[sector] += 1

            mv = s.get('total_mv', None)
            if mv and mv > 0:
                total_mvs.append(float(mv))

            ff_mv = s.get('free_float_mv', None)
            if ff_mv and ff_mv > 0:
                free_float_mvs.append(float(ff_mv))

            profit = s.get('profit', None)
            if profit is not None:
                profits.append(float(profit))

        # 归一化
        if total_w > 0:
            for sec in sector_w:
                sector_w[sec] /= total_w
                sector_weights_ts[sec].append(sector_w[sec])

        # 确保所有已有的 sector 都有值（补0）
        for sec in sector_weights_ts:
            if sec not in sector_w:
                sector_weights_ts[sec].append(0.0)

        rec['sector_count'] = len(sector_w)

        # HHI 集中度
        weights_arr = np.array(list(holdings_dict[k]['weight'] for k in holdings_dict))
        rec['hhi'] = float(np.sum(weights_arr ** 2))
        rec['top1_w'] = float(np.max(weights_arr)) if len(weights_arr) > 0 else 0
        rec['top5_w'] = float(np.sum(np.sort(weights_arr)[-5:])) if len(weights_arr) >= 5 else float(np.sum(weights_arr))
        rec['top10_w'] = float(np.sum(np.sort(weights_arr)[-10:])) if len(weights_arr) >= 10 else float(np.sum(weights_arr))
        rec['mean_w'] = float(np.mean(weights_arr))

        # 行业 HHI
        if len(sector_w) > 0:
            sw = np.array(list(sector_w.values()))
            rec['sector_hhi'] = float(np.sum(sw ** 2))

        # 规模统计（原始数据单位：万元，/1e4 转亿）
        if total_mvs:
            arr = np.array(total_mvs) / 1e4  # 万元 → 亿
            rec['mv_median_yi'] = float(np.median(arr))
            rec['mv_mean_yi'] = float(np.mean(arr))

        if free_float_mvs:
            arr = np.array(free_float_mvs) / 1e4
            rec['ff_mv_median_yi'] = float(np.median(arr))
            rec['ff_mv_mean_yi'] = float(np.mean(arr))

        if profits:
            arr = np.array(profits) / 1e8  # 元 → 亿（profit 是元，不同于 MV 的万元）
            rec['profit_median_yi'] = float(np.median(arr))
            rec['profit_mean_yi'] = float(np.mean(arr))

        records.append(rec)

    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    return df, dict(sector_weights_ts)


def compute_sector_weight_stats(sector_ts):
    results = []
    for sector, weights in sorted(sector_ts.items()):
        arr = np.array(weights)
        if len(arr) == 0 or np.mean(arr) < 0.0001:
            continue
        results.append({
            'sector': sector,
            'mean': float(np.mean(arr)),
            'std': float(np.std(arr)),
            'min': float(np.min(arr)),
            'max': float(np.max(arr)),
            'range': float(np.max(arr) - np.min(arr)),
            'cv': float(np.std(arr) / np.mean(arr)) if np.mean(arr) > 0.0001 else 999,
        })
    return pd.DataFrame(results).sort_values('mean', ascending=False)


def compute_drift_scores(df):
    scores = {}

    # 1. 行业集中度漂移
    hhi = df['sector_hhi'].dropna()
    if len(hhi) > 1:
        scores['sector_hhi_drift'] = float(np.std(hhi) / np.mean(hhi))
    else:
        scores['sector_hhi_drift'] = 0.0

    # 2. 权重集中度漂移
    hhi2 = df['hhi'].dropna()
    if len(hhi2) > 1:
        scores['weight_hhi_drift'] = float(np.std(hhi2) / np.mean(hhi2))

    # 3. 规模漂移
    mv = df['mv_median_yi'].dropna()
    if len(mv) > 1:
        log_mv = np.log(mv)
        scores['mv_median_drift'] = float(np.std(log_mv) / np.mean(log_mv))

    ff_mv = df['ff_mv_median_yi'].dropna()
    if len(ff_mv) > 1:
        log_ff = np.log(ff_mv)
        scores['ff_mv_median_drift'] = float(np.std(log_ff) / np.mean(log_ff))

    # 4. 利润规模漂移
    profit = df['profit_median_yi'].dropna()
    if len(profit) > 1:
        log_p = np.log(np.abs(profit) + 1)
        scores['profit_median_drift'] = float(np.std(log_p) / np.mean(log_p))

    # 综合评分
    vals = [scores.get(k, 0) for k in ['sector_hhi_drift', 'weight_hhi_drift', 'mv_median_drift', 'ff_mv_median_drift']]
    clamped = [min(v / 0.5, 1.0) for v in vals if v is not None]

    if clamped:
        score_sector = clamped[0] if len(clamped) > 0 else 0
        score_weight = clamped[1] if len(clamped) > 1 else 0
        score_size = np.mean(clamped[2:]) if len(clamped) > 2 else 0
        scores['overall'] = 0.4 * score_sector + 0.3 * score_weight + 0.3 * score_size
    else:
        scores['overall'] = 0.0

    return scores


def compute_turnover_analysis(baskets, dates):
    periods = []
    for i in range(len(dates) - 1):
        prev_codes = set(baskets[dates[i]].keys())
        curr_codes = set(baskets[dates[i + 1]].keys())

        overlap = prev_codes & curr_codes
        removed = prev_codes - curr_codes
        added = curr_codes - prev_codes

        n_total = max(len(prev_codes), len(curr_codes), 1)
        turnover = (len(removed) + len(added)) / (2 * n_total)

        periods.append({
            'from': dates[i],
            'to': dates[i + 1],
            'turnover': turnover,
            'n_prev': len(prev_codes),
            'n_curr': len(curr_codes),
            'n_overlap': len(overlap),
            'n_removed': len(removed),
            'n_added': len(added),
            'keep_ratio': len(overlap) / n_total if n_total > 0 else 0,
        })

    return pd.DataFrame(periods)


# ─── 报告输出 ─────────────────────────────────────

def print_header(title):
    print(f"\n{'='*75}")
    print(f"  {title}")
    print(f"{'='*75}")


def print_sub(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def run_analysis(version='top100', do_plot=False):
    baskets, dates = load_all_baskets(version)
    df, sector_ts = build_time_series(baskets, dates)
    label = VERSION_LABELS[version]

    # ── 概览 ──
    print_header(f"{label} · 风格漂移检测报告")
    print(f"覆盖区间: {dates[0]} → {dates[-1]}  ({len(dates)} 期)")
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    sample_size = np.mean(df['n_stocks'])
    print(f"平均持仓数: {sample_size:.1f} 只")

    # ── 一、行业权重漂移 ──
    print_header("一、行业权重漂移（D'Hondt 行业平衡 vs 实际行业暴露）")
    sector_stats = compute_sector_weight_stats(sector_ts)

    print(f"\n{'板块':<10} {'均值权重':>7} {'标准差':>7} {'最小':>7} {'最大':>7} {'全距':>7} {'CV':>7} {'漂移等级':>8}")
    print("-" * 80)
    for _, r in sector_stats.iterrows():
        cv = r['cv']
        if cv < 0.15:
            level = "★ 极稳"
        elif cv < 0.30:
            level = "★★ 稳"
        elif cv < 0.50:
            level = "★★★ 中等"
        elif cv < 0.80:
            level = "⚠️ 偏高"
        else:
            level = "🚨 漂移"

        print(f"{r['sector']:<10} {r['mean']*100:>6.1f}% {r['std']*100:>6.1f}% "
              f"{r['min']*100:>6.1f}% {r['max']*100:>6.1f}% {r['range']*100:>6.1f}pp {cv:>6.3f} {level:>8}")

    # 判断行业中性程度
    non_zero = sector_stats[sector_stats['mean'] > 0.01]
    cv_mean = non_zero['cv'].mean() if len(non_zero) > 0 else 0
    print(f"\n  板块数: {len(non_zero)}  |  平均CV: {cv_mean:.3f}")

    if cv_mean < 0.25:
        print(f"  ✅ D'Hondt 行业平衡有效，板块权重高度稳定")
    elif cv_mean < 0.5:
        print(f"  📌 行业权重有一定波动，D'Hondt 基本有效")
    else:
        print(f"  ⚠️ 行业权重波动较大，D'Hondt 未能完全中性化")

    # ── 二、权重集中度漂移 ──
    print_header("二、权重集中度漂移")
    print(f"\n{'指标':<18} {'均值':>8} {'标准差':>8} {'CV':>8}")
    print("-" * 45)

    for label_col, col in [('HHI 集中度', 'hhi'), ('Top1 权重', 'top1_w'),
                            ('Top5 权重', 'top5_w'), ('Top10 权重', 'top10_w')]:
        if col in df.columns:
            s = df[col].dropna()
            cv = float(np.std(s) / np.mean(s)) if np.mean(s) > 0 else 0
            print(f"{label_col:<18} {np.mean(s):>7.4f} {np.std(s):>7.4f} {cv:>7.3f}")

    # 权重上限检测
    top1 = df['top1_w'].dropna()
    over_cap = (top1 > 0.1001).sum()
    if over_cap > 0:
        print(f"\n  ⚠️ 有 {over_cap}/{len(top1)} 期单股权重超过 10% 上限")
    else:
        print(f"\n  ✅ 所有 {len(top1)} 期单股权重均 ≤ 10%，上限生效")

    # ── 三、规模偏好漂移 ──
    print_header("三、规模偏好漂移")

    for label, col in [('总市值中位数 (亿)', 'mv_median_yi'),
                        ('自由流通市值中位数 (亿)', 'ff_mv_median_yi'),
                        ('净利润中位数 (亿)', 'profit_median_yi')]:
        if col in df.columns and df[col].notna().sum() > 1:
            series = df[col].dropna()
            log_s = np.log(np.abs(series) + 1)
            cv = float(np.std(log_s) / np.mean(log_s))

            first_half = series.iloc[:len(series)//2].mean()
            second_half = series.iloc[len(series)//2:].mean()

            print(f"\n  {label}:")
            print(f"    均值: {np.mean(series):,.0f}  |  中位数: {np.median(series):,.0f}")
            print(f"    区间: {np.min(series):,.0f} ~ {np.max(series):,.0f}")
            print(f"    对数CV: {cv:.4f}  |  前后半变化: {((second_half/first_half-1)*100):+.1f}%")

            if cv > 0.12:
                print(f"    ⚠️ 规模漂移明显")
            elif cv > 0.06:
                print(f"    📌 中等漂移")
            else:
                print(f"    ✅ 规模偏好稳定")

    # ── 四、换手率归因 ──
    print_header("四、换手率归因")
    turnover_df = compute_turnover_analysis(baskets, dates)

    if len(turnover_df) > 0:
        avg_to = float(turnover_df['turnover'].mean())
        max_to = float(turnover_df['turnover'].max())
        std_to = float(turnover_df['turnover'].std())

        print(f"\n  平均换手率: {avg_to*100:.1f}%")
        print(f"  换手率区间: {float(turnover_df['turnover'].min())*100:.1f}% ~ {max_to*100:.1f}%")
        print(f"  换手率标准差: {std_to*100:.2f}%")

        # 前后半对比
        if len(turnover_df) >= 8:
            first_half = turnover_df['turnover'].iloc[:len(turnover_df)//2].mean()
            second_half = turnover_df['turnover'].iloc[len(turnover_df)//2:].mean()
            change = (second_half - first_half) * 100
            print(f"\n  前半段均换手: {first_half*100:.1f}%")
            print(f"  后半段均换手: {second_half*100:.1f}%")
            print(f"  趋势: {'📈 上升' if change > 2 else '📉 下降' if change < -2 else '→ 持平'} ({change:+.1f}pp)")

        # 高换手期
        high_to = turnover_df[turnover_df['turnover'] > avg_to + 1.5 * std_to]
        if len(high_to) > 0:
            print(f"\n  ⚠️ 异常高换手期 ({len(high_to)} 期):")
            for _, r in high_to.iterrows():
                print(f"    {r['from']} → {r['to']}: 换手 {r['turnover']*100:.1f}% "
                      f"(保留{r['keep_ratio']*100:.0f}%, ±{r['n_added']}/{r['n_removed']})")

    # ── 五、综合评分 ──
    print_header("五、综合风格漂移评分")
    scores = compute_drift_scores(df)

    dim_labels = {
        'sector_hhi_drift': '行业集中度漂移',
        'weight_hhi_drift': '权重集中度漂移',
        'mv_median_drift': '市值规模漂移',
        'ff_mv_median_drift': '自由流通市值漂移',
        'profit_median_drift': '利润规模漂移',
    }

    print(f"\n{'维度':<20} {'评分':>8} {'解读':>10}")
    print(f"  {'-'*40}")
    for key, label in dim_labels.items():
        if key in scores:
            v = scores[key]
            clamped = min(v / 0.5, 1.0)
            if clamped < 0.1:
                interp = "✅ 极稳"
            elif clamped < 0.3:
                interp = "✅ 稳定"
            elif clamped < 0.5:
                interp = "📌 中等"
            elif clamped < 0.7:
                interp = "⚠️ 偏高"
            else:
                interp = "🚨 漂移"
            print(f"  {label:<20} {clamped:>7.3f} {interp:>10}")

    overall = scores.get('overall', 0)
    if overall < 0.15:
        verdict = "高度稳定"
        symbol = "✅"
    elif overall < 0.3:
        verdict = "基本稳定"
        symbol = "✅"
    elif overall < 0.5:
        verdict = "中等漂移"
        symbol = "📌"
    elif overall < 0.7:
        verdict = "明显漂移"
        symbol = "⚠️"
    else:
        verdict = "严重漂移"
        symbol = "🚨"

    print(f"\n  {'─'*40}")
    print(f"  {symbol} 综合评分: {overall:.3f} — {verdict}")

    # ── 六、博格视角总结 ──
    print_header("六、博格「均值回归」视角总结")
    print(f"""
  SP500 风格选股 = NI+FCF双正过滤 + D'Hondt行业平衡 + 自由流通市值加权 + 10%上限

  1. D'Hondt 机制能否阻止风格漂移？
     → D'Hondt 强制各行业按净利润权重分配席位
     → 理论上应实现行业中性，削弱追热点的冲动
     → 但净利润本身也有行业周期，可能在行业景气时集中买入

  2. 自由流通市值加权是否引入规模漂移？
     → 自由流通市值 = 市场价格 × 自由流通比例
     → 若市场整体上涨时，大盘股会被自动加高权重 → 是天然的追涨机制

  3. 与 FCF E 版的关键差异：
     → E版：FCF率排名 → 天然偏向"便宜"的行业（均值回归受益者）
     → SP500风格：NI+FCF双正 + 市值加权 → 可能偏向"贵且好"的公司
""")

    print(f"\n{'='*75}\n")
    return df, scores, sector_stats, turnover_df


def make_plots(df, sector_ts, version):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("⚠️ matplotlib 不可用，跳过图表")
        return

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    dates = df['date']

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'{VERSION_LABELS[version]} · 风格漂移检测', fontsize=16, fontweight='bold')

    # 子图1：板块权重演变
    ax1 = axes[0, 0]
    top_sectors = sorted(sector_ts.keys(), key=lambda s: np.mean(sector_ts[s]), reverse=True)[:8]
    for sec in top_sectors:
        ax1.plot(dates[:len(sector_ts[sec])], [w * 100 for w in sector_ts[sec]],
                 marker='.', label=sec, alpha=0.8)
    ax1.set_title('板块权重演变 (Top 8)', fontsize=12)
    ax1.set_ylabel('权重 (%)')
    ax1.legend(fontsize=7, loc='upper left', ncol=2)
    ax1.grid(True, alpha=0.3)

    # 子图2：市值趋势
    ax2 = axes[0, 1]
    if 'mv_median_yi' in df.columns and 'ff_mv_median_yi' in df.columns:
        ax2.plot(dates, df['mv_median_yi'], 'b-', linewidth=2, label='总市值中位数(亿)')
        ax2.plot(dates, df['ff_mv_median_yi'], 'g--', linewidth=2, label='自由流通市值中位数(亿)')
        ax2.set_title('规模偏好演变', fontsize=12)
        ax2.set_ylabel('市值 (亿)')
        ax2.legend(fontsize=8)
        ax2.grid(True, alpha=0.3)

    # 子图3：权重集中度
    ax3 = axes[1, 0]
    if 'top1_w' in df.columns:
        ax3.fill_between(dates, 0, df['top1_w'] * 100, alpha=0.3, label='Top1', color='red')
        ax3.fill_between(dates, df['top1_w'] * 100, df['top5_w'] * 100,
                         alpha=0.3, label='Top2-5', color='orange')
        ax3.fill_between(dates, df['top5_w'] * 100, df['top10_w'] * 100,
                         alpha=0.3, label='Top6-10', color='green')
        ax3.plot(dates, df['top10_w'] * 100, 'k-', linewidth=1.5, label='Top10合计')
        ax3.set_title('权重集中度', fontsize=12)
        ax3.set_ylabel('权重 (%)')
        ax3.legend(fontsize=7, loc='upper left')
        ax3.grid(True, alpha=0.3)

    # 子图4：HHI 行业集中度
    ax4 = axes[1, 1]
    if 'sector_hhi' in df.columns:
        ax4.plot(dates, df['sector_hhi'], 'r-', linewidth=2, marker='.')
        mean_hhi = df['sector_hhi'].mean()
        ax4.axhline(y=mean_hhi, color='gray', linestyle='--', alpha=0.5, label=f'均值={mean_hhi:.3f}')
        ax4.set_title('行业集中度 (HHI)', fontsize=12)
        ax4.set_ylabel('HHI')
        ax4.legend(fontsize=8)
        ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = ROOT / f'output/style_drift_sp500_{version}.png'
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f"\n图表已保存: {out_path}")
    plt.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SP500风格选股 · 风格漂移检测')
    parser.add_argument('--version', default='top100',
                        help='top100/top200/top300/full')
    parser.add_argument('--plot', action='store_true', help='生成图表')
    args = parser.parse_args()

    df, scores, sector_stats, turnover_df = run_analysis(args.version, args.plot)

    if args.plot:
        baskets, dates = load_all_baskets(args.version)
        _, sector_ts = build_time_series(baskets, dates)
        make_plots(df, sector_ts, args.version)
