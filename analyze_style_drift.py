#!/usr/bin/env python3
"""
风格漂移检测：从均值回归角度分析 FCF 策略是否存在风格漂移

检测维度（Bogle 均值回归框架）：
  1. 行业权重漂移 — 板块权重时间序列稳定性（高漂移 = 追热点板块）
  2. 因子暴露漂移 — FCF率均值/分布随时间变化（高漂移 = 选股标准漂移）
  3. 市值偏好漂移 — 持仓市值中位数是否随市场风格摇摆
  4. 换手归因 — 高换手板块是否对应"近期热门板块"
  5. 综合风格漂移评分

用法：
  python analyze_style_drift.py
  python analyze_style_drift.py --version E
  python analyze_style_drift.py --version E --plot
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
    'B': 'output/zz800_fcf_fixed_lenient',
    'D': 'output/zz800_fcf_lenient_buffer',
    'E': 'output/zz800_fcf_lenient_buffer_e40',
    'F': 'output/zz800_fcf_lenient_buffer_f50',
    'X': 'output/zz800_fcf_full_universe',
    'B_300': 'output/hs300_fcf_fixed_lenient',
    'D_300': 'output/hs300_fcf_lenient_buffer',
    'E_300': 'output/hs300_fcf_lenient_buffer_e40',
    'F_300': 'output/hs300_fcf_lenient_buffer_f50',
    'X_300': 'output/hs300_fcf_full_universe',
}


def get_sector(industry):
    if not industry or str(industry) == 'nan':
        return '其他'
    return SECTOR_MAP.get(str(industry), '其他')


def load_all_baskets(version):
    ver_dir = VER_DIRS.get(version.upper())
    if not ver_dir:
        raise ValueError(f"未知版本: {version}，可选: {list(VER_DIRS.keys())}")
    basket_path = ROOT / ver_dir / 'all_baskets_2015_2026.json'
    if not basket_path.exists():
        raise FileNotFoundError(f"篮子文件不存在: {basket_path}")
    with open(basket_path) as f:
        baskets = json.load(f)
    dates = sorted(baskets.keys())
    print(f"加载 {version} 版: {len(dates)} 期, {basket_path}")
    return baskets, dates


def build_time_series(baskets, dates):
    """
    构建每期持仓特征的时间序列。
    返回 (DataFrame, sector_ts: dict[str->list[float]])
    """
    records = []
    sector_weights_ts = defaultdict(list)  # sector -> [w1, w2, ...]

    for d in dates:
        holdings = baskets[d]
        n = len(holdings)

        rec = {'date': d, 'n_stocks': n}
        sector_w = defaultdict(float)
        sector_fcf_y = defaultdict(list)

        total_w = 0.0
        fcf_yields, evs, total_mvs = [], [], []

        for s in holdings:
            w = s.get('weight', 1.0 / n)

            industry = s.get('industry', '其他')
            sector = s.get('sector', get_sector(industry))
            sector_w[sector] += w

            fy = s.get('fcf_yield', None)
            if fy is not None and not (isinstance(fy, float) and (np.isnan(fy) or fy == 0)):
                fcf_yields.append(float(fy))
                sector_fcf_y[sector].append(float(fy))

            ev = s.get('ev', s.get('ev_1e8', None))
            if ev is not None and not (isinstance(ev, float) and np.isnan(ev)) and ev > 0:
                evs.append(float(ev))

            mv = s.get('total_mv', s.get('total_mv_1e8', None))
            if mv is not None and not (isinstance(mv, float) and np.isnan(mv)) and mv > 0:
                total_mvs.append(float(mv))

        rec['n_effective'] = len(fcf_yields)

        for sec in sector_w:
            sector_w[sec] /= 1.0  # 保持原始权重和
            sector_weights_ts[sec].append(sector_w[sec])

        for sec in sector_weights_ts:
            if sec not in sector_w:
                sector_weights_ts[sec].append(0.0)

        # FCF 率统计
        if fcf_yields:
            arr = np.array(fcf_yields)
            rec['fcf_mean'] = float(np.mean(arr))
            rec['fcf_median'] = float(np.median(arr))
            rec['fcf_std'] = float(np.std(arr))
            rec['fcf_p25'] = float(np.percentile(arr, 25))
            rec['fcf_p75'] = float(np.percentile(arr, 75))

        # EV 统计 (亿元)
        if evs:
            arr = np.array(evs)
            rec['ev_median_1e8'] = float(np.median(arr))
            rec['ev_mean_1e8'] = float(np.mean(arr))

        # 市值统计
        if total_mvs:
            arr = np.array(total_mvs)
            rec['mv_median_1e8'] = float(np.median(arr))

        # 板块权重 HHI
        weights_arr = np.array(list(sector_w.values()))
        rec['sector_hhi'] = float(np.sum(weights_arr ** 2))
        rec['sector_count'] = len(sector_w)

        records.append(rec)

    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])

    # 对齐 sector_weights_ts
    return df, dict(sector_weights_ts)


def compute_sector_weight_stats(sector_ts):
    """计算板块权重的漂移指标"""
    results = []
    for sector, weights in sorted(sector_ts.items()):
        arr = np.array(weights)
        results.append({
            'sector': sector,
            'mean': float(np.mean(arr)),
            'std': float(np.std(arr)),
            'min': float(np.min(arr)),
            'max': float(np.max(arr)),
            'range': float(np.max(arr) - np.min(arr)),
            'cv': float(np.std(arr) / np.mean(arr)) if np.mean(arr) > 0.001 else float('inf'),
            'n_nonzero': int(np.sum(arr > 0.001)),
        })
    return pd.DataFrame(results).sort_values('mean', ascending=False)


def compute_drift_scores(df):
    """
    计算综合风格漂移评分 (0=完全稳定, 1=严重漂移)
    """
    scores = {}

    # 1. 行业维度: 板块 HHI 的标准差 / 均值 = 行业集中度漂移
    hhi = df['sector_hhi'].dropna()
    if len(hhi) > 1:
        scores['sector_hhi_drift'] = float(np.std(hhi) / np.mean(hhi))
    else:
        scores['sector_hhi_drift'] = 0.0

    # 2. 因子维度: FCF 率均值的标准差 / 均值
    fcf = df['fcf_mean'].dropna()
    if len(fcf) > 1:
        scores['fcf_mean_drift'] = float(np.std(fcf) / np.mean(fcf))
    else:
        scores['fcf_mean_drift'] = 0.0

    # 3. 因子维度: FCF 率中位数的标准差 / 均值
    fcf_med = df['fcf_median'].dropna()
    if len(fcf_med) > 1:
        scores['fcf_median_drift'] = float(np.std(fcf_med) / np.mean(fcf_med))
    else:
        scores['fcf_median_drift'] = 0.0

    # 4. 规模维度: EV 中位数的标准差 / 均值 (对数化后)
    ev = df['ev_median_1e8'].dropna()
    if len(ev) > 1:
        log_ev = np.log(ev)
        scores['ev_median_drift'] = float(np.std(log_ev) / np.mean(log_ev))
    else:
        scores['ev_median_drift'] = 0.0

    # 5. 规模维度: 市值中位数的标准差 / 均值 (对数化后)
    mv = df['mv_median_1e8'].dropna()
    if len(mv) > 1:
        log_mv = np.log(mv)
        scores['mv_median_drift'] = float(np.std(log_mv) / np.mean(log_mv))
    else:
        scores['mv_median_drift'] = 0.0

    # 综合分：各维度的几何平均
    vals = list(scores.values())
    # 统一映射到 0-1 区间 (CV > 0.5 视为严重漂移)
    clamped = [min(v / 0.5, 1.0) for v in vals]

    # 加权：行业 40%、因子 30%、规模 30%
    score_sector = clamped[0]
    score_factor = np.mean(clamped[1:3])
    score_size = np.mean(clamped[3:5])

    scores['overall'] = 0.4 * score_sector + 0.3 * score_factor + 0.3 * score_size

    return scores


def compute_turnover_drift(baskets, dates, sector_ts):
    """
    计算板块换手率，识别哪些板块驱动换手。
    返回 (期换手列表, 板块换手率 dict)
    """
    period_turnovers = []
    sector_changes = defaultdict(list)  # 各期各板块换出/换入

    for i in range(len(dates) - 1):
        prev = set(s['ts_code'] for s in baskets[dates[i]])
        curr = set(s['ts_code'] for s in baskets[dates[i + 1]])

        overlap = prev & curr
        removed = prev - curr
        added = curr - prev

        # 总换手率
        n_total = max(len(prev), len(curr))
        turnover = (len(removed) + len(added)) / (2 * n_total) if n_total > 0 else 0
        period_turnovers.append({
            'from': dates[i],
            'to': dates[i + 1],
            'turnover': turnover,
            'n_prev': len(prev),
            'n_curr': len(curr),
            'n_overlap': len(overlap),
            'n_removed': len(removed),
            'n_added': len(added),
            'keep_ratio': len(overlap) / n_total if n_total > 0 else 0,
        })

    return pd.DataFrame(period_turnovers)


def compute_market_correlation(df):
    """
    检测风格漂移与市场环境的相关性：
    - 在牛市/熊市中，持仓是否系统性地偏向某些板块？
    - 使用 HS300 全收益数据（如有），计算板块权重变化与市场收益的相关性
    """
    # 尝试加载 HS300 数据
    hs300_path = ROOT / 'data/hs300_total_return.csv'
    if not hs300_path.exists():
        return None, "HS300 全收益数据不可用"

    hs300 = pd.read_csv(hs300_path, parse_dates=['trade_date'])
    hs300 = hs300.sort_values('trade_date').set_index('trade_date')

    # 第 i 期到 i+1 期的 HS300 收益
    bench_returns = []
    for i in range(len(df) - 1):
        try:
            p0 = hs300.loc[:df.iloc[i]['date']].iloc[-1]['close']
            p1 = hs300.loc[:df.iloc[i + 1]['date']].iloc[-1]['close']
            bench_returns.append(float(p1 / p0 - 1))
        except Exception:
            bench_returns.append(np.nan)

    return np.array(bench_returns), None


# ─── 报告输出 ─────────────────────────────────────

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_sub(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def run_analysis(version='E', do_plot=False):
    baskets, dates = load_all_baskets(version)
    df, sector_ts = build_time_series(baskets, dates)

    # ── 概览 ──
    print_header(f"FCF {version}版 · 风格漂移检测报告")
    print(f"覆盖区间: {dates[0]} → {dates[-1]}  ({len(dates)} 期)")
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ── 一、行业权重漂移 ──
    print_header("一、行业权重漂移")
    sector_stats = compute_sector_weight_stats(sector_ts)

    print(f"\n{'板块':<10} {'均值权重':>7} {'标准差':>7} {'最小':>7} {'最大':>7} {'全距':>7} {'CV':>7} {'漂移等级':>8}")
    print("-" * 80)
    for _, r in sector_stats.iterrows():
        cv = r['cv']
        if cv < 0.1:
            level = "★ 极稳"
        elif cv < 0.25:
            level = "★★ 稳"
        elif cv < 0.5:
            level = "★★★ 中等"
        elif cv < 1.0:
            level = "⚠️ 偏高"
        else:
            level = "🚨 漂移"

        print(f"{r['sector']:<10} {r['mean']*100:>6.1f}% {r['std']*100:>6.1f}% "
              f"{r['min']*100:>6.1f}% {r['max']*100:>6.1f}% {r['range']*100:>6.1f}pp {cv:>6.3f} {level:>8}")

    # 板块演变描述
    print_sub("板块权重演变描述")
    top3 = sector_stats.head(3)['sector'].tolist()
    print(f"  长期重仓板块: {', '.join(top3)}")
    print(f"  长期权重合计: {sector_stats.head(3)['mean'].sum()*100:.1f}%")

    # 检测是否有「追热点」迹象
    high_drift_sectors = sector_stats[sector_stats['cv'] > 0.5]
    if len(high_drift_sectors) > 0:
        print(f"\n  ⚠️ 高漂移板块 (CV > 0.5): {', '.join(high_drift_sectors['sector'].tolist())}")
        for _, r in high_drift_sectors.iterrows():
            print(f"     {r['sector']}: 权重区间 {r['min']*100:.1f}%→{r['max']*100:.1f}%, CV={r['cv']:.2f}")
    else:
        print(f"\n  ✅ 所有板块 CV < 0.5，未检测到显著的行业风格漂移")

    # ── 二、因子暴露漂移 ──
    print_header("二、因子暴露漂移 (FCF 率)")
    fcf_cols = ['fcf_mean', 'fcf_median', 'fcf_std', 'fcf_p25', 'fcf_p75']
    available = [c for c in fcf_cols if c in df.columns and df[c].notna().sum() > 1]

    if available:
        drift_metrics = []
        for col in available:
            series = df[col].dropna()
            drift_metrics.append({
                '指标': col,
                '均值': float(np.mean(series)),
                '标准差': float(np.std(series)),
                'CV': float(np.std(series) / np.mean(series)) if np.mean(series) > 0 else 0,
            })

        print(f"\n{'指标':<12} {'均值':>8} {'标准差':>8} {'CV':>8}")
        print("-" * 45)
        for m in drift_metrics:
            fmt = f"{m['均值']*100:>7.2f}%" if 'fcf' in m['指标'] else f"{m['均值']:>8.2f}"
            print(f"{m['指标']:<12} {fmt} {m['标准差']*100:>7.2f}% {m['CV']:>7.3f}")

        # 因子漂移趋势描述
        fcf_mean = df['fcf_mean'].dropna()
        if len(fcf_mean) >= 4:
            first_half = fcf_mean.iloc[:len(fcf_mean)//2].mean()
            second_half = fcf_mean.iloc[len(fcf_mean)//2:].mean()
            change = (second_half - first_half) / first_half * 100 if first_half > 0 else 0
            print(f"\n  前半段均值 FCF 率: {first_half*100:.2f}%")
            print(f"  后半段均值 FCF 率: {second_half*100:.2f}%")
            if abs(change) > 15:
                print(f"  ⚠️ FCF 率中枢偏移 {change:+.1f}%，因子暴露出现明显漂移")
            elif abs(change) > 5:
                print(f"  📌 FCF 率中枢偏移 {change:+.1f}%，轻微偏移，尚可接受")
            else:
                print(f"  ✅ FCF 率中枢变化 {change:+.1f}%，因子暴露高度稳定")
    else:
        print("  ⚠️ 无有效 FCF 率数据，无法评估因子漂移")

    # ── 三、市值偏好漂移 ──
    print_header("三、市值偏好漂移")
    ev_col = 'ev_median_1e8'
    mv_col = 'mv_median_1e8'

    for label, col in [('EV 中位数 (亿)', ev_col), ('市值中位数 (亿)', mv_col)]:
        if col in df.columns and df[col].notna().sum() > 1:
            series = df[col].dropna()
            log_s = np.log(series)
            cv = float(np.std(log_s) / np.mean(log_s))
            print(f"\n  {label}:")
            print(f"    均值: {np.mean(series):.1f}亿  |  中位数: {np.median(series):.1f}亿")
            print(f"    最小: {np.min(series):.1f}亿  |  最大: {np.max(series):.1f}亿")
            print(f"    对数CV: {cv:.4f}")

            # 判断规模风格是否稳定
            if cv > 0.15:
                print(f"    ⚠️ 规模偏好漂移较大 (对数CV={cv:.4f})，持仓市值中枢不稳定")
            elif cv > 0.08:
                print(f"    📌 中等漂移 (对数CV={cv:.4f})")
            else:
                print(f"    ✅ 规模偏好高度稳定 (对数CV={cv:.4f})")

    # ── 四、换手归因 ──
    print_header("四、换手率归因")
    turnover_df = compute_turnover_drift(baskets, dates, sector_ts)

    if len(turnover_df) > 0:
        avg_turnover = float(turnover_df['turnover'].mean())
        max_turnover = float(turnover_df['turnover'].max())
        min_turnover = float(turnover_df['turnover'].min())
        std_turnover = float(turnover_df['turnover'].std())

        print(f"\n  平均换手率: {avg_turnover*100:.1f}%")
        print(f"  换手率区间: {min_turnover*100:.1f}% ~ {max_turnover*100:.1f}%")
        print(f"  换手率标准差: {std_turnover*100:.2f}%")

        # 检测换手率是否有趋势（如近期换手率是否上升）
        if len(turnover_df) >= 8:
            to_series = turnover_df['turnover']
            first_half_to = to_series.iloc[:len(to_series)//2].mean()
            second_half_to = to_series.iloc[len(to_series)//2:].mean()
            to_change = (second_half_to - first_half_to)
            print(f"\n  前半段均换手: {first_half_to*100:.1f}%")
            print(f"  后半段均换手: {second_half_to*100:.1f}%")
            if abs(to_change) > 0.05:
                print(f"  ⚠️ 换手率趋势变化 {to_change*100:+.1f}pp，风格漂移信号")
            else:
                print(f"  ✅ 换手率趋势稳定 {to_change*100:+.1f}pp")

        # 高换手期标记
        high_to = turnover_df[turnover_df['turnover'] > avg_turnover + 1.5 * std_turnover]
        if len(high_to) > 0:
            print(f"\n  ⚠️  异常高换手期 ({len(high_to)} 期):")
            for _, r in high_to.iterrows():
                print(f"    {r['from']} → {r['to']}: 换手 {r['turnover']*100:.1f}% "
                      f"(保留{r['keep_ratio']*100:.0f}%, 调入{r['n_added']}/调出{r['n_removed']})")

    # ── 五、综合评分 ──
    print_header("五、综合风格漂移评分 (0=完全稳定, 1=严重漂移)")
    scores = compute_drift_scores(df)

    print(f"\n  {'维度':<20} {'评分':>8} {'解读':>10}")
    print(f"  {'-'*40}")
    dim_labels = {
        'sector_hhi_drift': ('行业集中度漂移', 0.4),
        'fcf_mean_drift': ('FCF率均值漂移', 0.15),
        'fcf_median_drift': ('FCF率中位数漂移', 0.15),
        'ev_median_drift': ('EV规模漂移', 0.15),
        'mv_median_drift': ('市值规模漂移', 0.15),
    }

    for key, (label, _) in dim_labels.items():
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
        verdict = "高度稳定，不存在风格漂移"
        symbol = "✅"
    elif overall < 0.3:
        verdict = "基本稳定，微量漂移可忽略"
        symbol = "✅"
    elif overall < 0.5:
        verdict = "中等漂移，建议定期监控"
        symbol = "📌"
    elif overall < 0.7:
        verdict = "明显风格漂移，需审视策略"
        symbol = "⚠️"
    else:
        verdict = "严重风格漂移，策略可能追热点"
        symbol = "🚨"

    print(f"\n  {'─'*40}")
    print(f"  {symbol} 综合评分: {overall:.3f} — {verdict}")

    # ── 六、博格视角总结 ──
    print_header("六、博格「均值回归」视角总结")

    print(f"""
  从博格的均值回归框架审视 FCF {version}版策略：

  1. 行业暴露：能否抵御行业轮动的均值回归？
     → 若行业权重高度稳定，说明策略是「买入规则」驱动而非「追热点」驱动
     → 若行业权重随市场风格摇摆，则策略可能在追逐已表现好的行业
        （这些行业随后均值回归，拖累策略表现）

  2. 因子暴露：FCF 率是否始终指向同一类资产？
     → FCF 率稳定 = 策略始终在买「便宜的高现金流公司」
     → FCF 率漂移 = 选股标准在改变（可能是市场环境影响）

  3. 规模偏好：是否在不同市值风格间摇摆？
     → 大盘 vs 小盘风格切换是 A 股最重要的风格因子之一
     → 稳定在某一端的策略更符合「买入持有」原则

  4. 换手率：是否频繁换仓追逐短期趋势？
     → 博格痛斥高换手率（助长投机而非投资）
     → 季度换手 <30% 可视为「低频」，符合长期投资理念
""")

    # 是否建议
    if overall < 0.3:
        print(f"  ✅ 结论：FCF {version}版策略风格高度稳定，符合博格「低成本、长期持有」")
        print(f"     的投资哲学。策略不会因为追逐短期热点而遭受均值回归惩罚。")
    elif overall < 0.5:
        print(f"  📌 结论：FCF {version}版策略存在轻微风格漂移，但整体可控。")
        print(f"     建议每半年审视一次行业权重变化，确保偏离在可接受范围内。")
    else:
        print(f"  ⚠️ 结论：FCF {version}版策略存在明显风格漂移，需要排查原因。")
        print(f"     建议：1) 加大小行业权重上限 2) 降低缓冲区以强化风格锚定")

    print(f"\n{'='*70}\n")
    return df, scores, sector_stats, turnover_df


# ─── 可选的图表 ─────────────────────────────────────
def make_plots(df, sector_ts, version):
    """生成 matplotlib 图"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("⚠️ matplotlib 不可用，跳过图表")
        return

    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    dates = df['date']

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'FCF {version}版 · 风格漂移检测', fontsize=16, fontweight='bold')

    # 子图1：板块权重热力图
    ax1 = axes[0, 0]
    top_sectors = sorted(sector_ts.keys(), key=lambda s: np.mean(sector_ts[s]), reverse=True)[:8]
    for sec in top_sectors:
        ax1.plot(dates[:len(sector_ts[sec])], [w * 100 for w in sector_ts[sec]],
                 marker='.', label=sec if len(sec) <= 6 else sec[:4] + '..', alpha=0.8)
    ax1.set_title('板块权重演变 (Top 8)', fontsize=12)
    ax1.set_ylabel('权重 (%)')
    ax1.legend(fontsize=7, loc='upper left', ncol=2)
    ax1.grid(True, alpha=0.3)

    # 子图2：FCF 率均值漂移
    ax2 = axes[0, 1]
    if 'fcf_mean' in df.columns:
        ax2.fill_between(dates,
                         df['fcf_p25'] * 100,
                         df['fcf_p75'] * 100,
                         alpha=0.2, label='25%-75%区间')
        ax2.plot(dates, df['fcf_median'] * 100, 'b-', linewidth=2, label='中位数')
        ax2.plot(dates, df['fcf_mean'] * 100, 'b--', alpha=0.6, label='均值')
        ax2.set_title('FCF 率因子暴露漂移', fontsize=12)
        ax2.set_ylabel('FCF 率 (%)')
        ax2.legend(fontsize=8)
        ax2.grid(True, alpha=0.3)

    # 子图3：规模漂移 (EV 中位数)
    ax3 = axes[1, 0]
    if 'ev_median_1e8' in df.columns:
        ax3.plot(dates, df['ev_median_1e8'] / 10000, 'g-', linewidth=2, label='EV中位数(万亿)')
        ax3.fill_between(dates,
                         df['ev_median_1e8'] / 10000 * 0.8,
                         df['ev_median_1e8'] / 10000 * 1.2,
                         alpha=0.15, color='green')
        ax3.set_title('规模偏好漂移 (EV中位数)', fontsize=12)
        ax3.set_ylabel('EV (万亿)')
        ax3.grid(True, alpha=0.3)

    # 子图4：HHI 集中度指数
    ax4 = axes[1, 1]
    ax4.plot(dates, df['sector_hhi'], 'r-', linewidth=2, marker='.')
    ax4.axhline(y=df['sector_hhi'].mean(), color='gray', linestyle='--', alpha=0.5, label='均值')
    ax4.set_title('行业集中度漂移 (HHI)', fontsize=12)
    ax4.set_ylabel('HHI')
    ax4.legend(fontsize=8)
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = ROOT / f'output/style_drift_{version}.png'
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f"\n图表已保存: {out_path}")
    plt.close()


# ─── 命令行入口 ─────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FCF策略风格漂移检测')
    parser.add_argument('--version', default='E',
                        help='策略版本 (B/D/E/F/X)')
    parser.add_argument('--plot', action='store_true',
                        help='生成图表')
    args = parser.parse_args()

    df, scores, sector_stats, turnover_df = run_analysis(args.version, args.plot)

    if args.plot:
        baskets, dates = load_all_baskets(args.version)
        _, sector_ts = build_time_series(baskets, dates)
        make_plots(df, sector_ts, args.version)
