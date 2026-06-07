#!/usr/bin/env python3
"""
generate_enhanced_report.py — 增强版回测报告
包含: 指数对比(ZZ800/HS300)、年度收益、季度持仓分析、盈亏分析
"""
import sys, json, os, time
import pandas as pd, numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))
ROOT = Path(__file__).parent

# ──────────────────────────────────────────
# 1. 下载指数行情数据 (缓存到本地)
# ──────────────────────────────────────────
def download_index_daily(index_code, start_date="20150101", end_date="20260630"):
    """下载指数日线行情(收盘价), 缓存到本地CSV"""
    cache_path = ROOT / "data" / "index_daily" / f"{index_code}.csv"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        df['trade_date'] = df['trade_date'].astype(str)
        # 检查是否需要更新
        last_date = str(df['trade_date'].max())
        today = datetime.now().strftime("%Y%m%d")
        if last_date >= today:
            return df
        # 需要增量更新
        start_update = last_date
    else:
        start_update = start_date
    
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    import tushare as ts
    ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()
    
    end_dt = end_date
    df_new = pro.index_daily(ts_code=index_code, start_date=start_update, end_date=end_dt,
                              fields="ts_code,trade_date,close,pre_close,pct_chg")
    
    if df_new is None or df_new.empty:
        if cache_path.exists():
            df = pd.read_csv(cache_path)
            df['trade_date'] = df['trade_date'].astype(str)
            return df
        return pd.DataFrame()
    
    if cache_path.exists():
        df_old = pd.read_csv(cache_path)
        df_new['trade_date'] = df_new['trade_date'].astype(str)
        df_old['trade_date'] = df_old['trade_date'].astype(str)
        df = pd.concat([df_old, df_new], ignore_index=True)
        df = df.drop_duplicates(subset=['trade_date'], keep='last')
    else:
        df = df_new
        df['trade_date'] = df['trade_date'].astype(str)
    
    df = df.sort_values('trade_date').reset_index(drop=True)
    df.to_csv(cache_path, index=False)
    print(f"  ✅ {index_code}: {len(df)}条日线数据缓存到 {cache_path}")
    return df


def get_index_return(index_df, start_date, end_date):
    """计算指数在start_date~end_date期间的收益率"""
    # 转换日期格式
    start_key = start_date.replace("-", "")
    end_key = end_date.replace("-", "")
    
    # 找最近的交易日
    start_rows = index_df[index_df['trade_date'] >= start_key]
    end_rows = index_df[index_df['trade_date'] <= end_key]
    
    if start_rows.empty or end_rows.empty:
        return None
    
    start_close = start_rows.iloc[0]['close']
    end_close = end_rows.iloc[-1]['close']
    
    return (end_close / start_close - 1)


# ──────────────────────────────────────────
# 2. 持仓盈亏分析
# ──────────────────────────────────────────
def analyze_holding_pnl(baskets, adj_close_dir, nav_df, label="B版"):
    """逐期分析每只持仓股的盈亏"""
    CACHE_DIR = Path(adj_close_dir)
    all_periods = []
    
    for i in range(len(nav_df)):
        rb_date = str(nav_df.iloc[i]['rb_date'])[:10]
        next_rb = str(nav_df.iloc[i]['next_rb'])[:10]
        period_ret = nav_df.iloc[i]['ret']
        
        stocks = baskets.get(rb_date, [])
        if not stocks or len(stocks) == 0:
            continue
        
        period_detail = {
            'rb_date': rb_date, 'next_rb': next_rb,
            'period_ret': period_ret * 100,
            'stocks': []
        }
        
        for s in stocks:
            if not isinstance(s, dict):
                continue
            ts_code = s['ts_code']
            weight = s.get('weight', 0)
            
            # 从缓存获取价格
            cache_file = CACHE_DIR / f"{ts_code}.csv"
            if not cache_file.exists():
                period_detail['stocks'].append({
                    'ts_code': ts_code, 'name': s.get('name',''),
                    'weight': weight, 'start_price': None, 'end_price': None,
                    'stock_ret': None, 'contribution': None
                })
                continue
            
            price_df = pd.read_csv(cache_file)
            price_df['trade_date'] = price_df['trade_date'].astype(str)
            start_key = rb_date.replace("-", "")
            end_key = next_rb.replace("-", "")
            
            start_rows = price_df[price_df['trade_date'] >= start_key]
            end_rows = price_df[price_df['trade_date'] <= end_key]
            
            if start_rows.empty or end_rows.empty:
                period_detail['stocks'].append({
                    'ts_code': ts_code, 'name': s.get('name',''),
                    'weight': weight, 'start_price': None, 'end_price': None,
                    'stock_ret': None, 'contribution': None
                })
                continue
            
            start_price = start_rows.iloc[0]['adj_close'] if 'adj_close' in start_rows.columns else start_rows.iloc[0]['close']
            end_price = end_rows.iloc[-1]['adj_close'] if 'adj_close' in end_rows.columns else end_rows.iloc[-1]['close']
            
            if start_price > 0:
                stock_ret = (end_price / start_price - 1) * 100
                contribution = weight * stock_ret  # 权重贡献
            else:
                stock_ret = None
                contribution = None
            
            period_detail['stocks'].append({
                'ts_code': ts_code, 'name': s.get('name',''),
                'weight': weight,
                'start_price': round(start_price, 2),
                'end_price': round(end_price, 2),
                'stock_ret': round(stock_ret, 2) if stock_ret is not None else None,
                'contribution': round(contribution, 2) if contribution is not None else None,
            })
        
        # 分类: 盈利/亏损/缺失
        valid_stocks = [s for s in period_detail['stocks'] if s['stock_ret'] is not None]
        winners = [s for s in valid_stocks if s['stock_ret'] > 0]
        losers = [s for s in valid_stocks if s['stock_ret'] < 0]
        top3 = sorted(valid_stocks, key=lambda x: x.get('stock_ret',0), reverse=True)[:3]
        bottom3 = sorted(valid_stocks, key=lambda x: x.get('stock_ret',0))[:3]
        
        period_detail['n_valid'] = len(valid_stocks)
        period_detail['n_winners'] = len(winners)
        period_detail['n_losers'] = len(losers)
        period_detail['win_rate'] = len(winners)/len(valid_stocks)*100 if valid_stocks else 0
        period_detail['top3'] = top3
        period_detail['bottom3'] = bottom3
        
        all_periods.append(period_detail)
    
    return all_periods


# ──────────────────────────────────────────
# 3. 年度收益计算
# ──────────────────────────────────────────
def compute_annual_returns(nav_df, index_df, index_label):
    """按自然年汇总收益"""
    years = {}
    for i in range(len(nav_df)):
        rb = str(nav_df.iloc[i]['rb_date'])[:10]
        nxt = str(nav_df.iloc[i]['next_rb'])[:10]
        year = rb[:4]
        ret = nav_df.iloc[i]['ret']
        idx_ret = get_index_return(index_df, rb, nxt)
        
        if year not in years:
            years[year] = {'strategy_returns': [], 'index_returns': [], 'periods': []}
        years[year]['strategy_returns'].append(ret)
        if idx_ret is not None:
            years[year]['index_returns'].append(idx_ret)
        years[year]['periods'].append((rb, nxt, ret, idx_ret))
    
    annual_data = []
    for year, data in sorted(years.items()):
        strat_cum = (1 + np.array(data['strategy_returns'])).prod() - 1
        idx_cum = (1 + np.array(data['index_returns'])).prod() - 1 if data['index_returns'] else None
        annual_data.append({
            'year': year,
            'strategy_annual': round(strat_cum * 100, 2),
            'index_annual': round(idx_cum * 100, 2) if idx_cum is not None else None,
            'n_periods': len(data['periods']),
            'excess': round((strat_cum - idx_cum) * 100, 2) if idx_cum is not None else None,
        })
    return annual_data


# ──────────────────────────────────────────
# 4. 生成完整报告
# ──────────────────────────────────────────
def generate_report(b_nav, d_nav, a_nav, b_baskets, d_baskets,
                    zz800_idx, hs300_idx, b_holding_pnl, d_holding_pnl,
                    b_annual, d_annual, zz800_label, hs300_label):
    
    n_periods = len(b_nav)
    n_years = n_periods / 4
    
    # 基础指标
    b_final = b_nav.iloc[-1]['nav']
    d_final = d_nav.iloc[-1]['nav']
    b_annual_ret = (b_final ** (1/n_years) - 1) * 100
    d_annual_ret = (d_final ** (1/n_years) - 1) * 100
    
    # 指数总收益
    zz800_total = get_index_return(zz800_idx, "2016-06-13", "2026-06-04")
    hs300_total = get_index_return(hs300_idx, "2016-06-13", "2026-06-04")
    zz800_cum_nav = (1+zz800_total) if zz800_total else 1.0
    hs300_cum_nav = (1+hs300_total) if hs300_total else 1.0
    zz800_annual = ((zz800_cum_nav) ** (1/n_years) - 1)*100 if zz800_total else None
    hs300_annual = ((hs300_cum_nav) ** (1/n_years) - 1)*100 if hs300_total else None
    
    b_rets = b_nav['ret'].values * 100
    d_rets = d_nav['ret'].values * 100
    
    # 风险指标
    b_vol = np.std(b_rets) * np.sqrt(4)
    d_vol = np.std(d_rets) * np.sqrt(4)
    
    def max_dd(nav_series):
        peak = nav_series[0]
        dd = 0
        for n in nav_series:
            if n > peak: peak = n
            dd = max(dd, (peak - n) / peak * 100)
        return dd
    b_dd = max_dd(b_nav['nav'].values)
    d_dd = max_dd(d_nav['nav'].values)
    
    b_neg = [r for r in b_rets if r < 0]
    d_neg = [r for r in d_rets if r < 0]
    b_downside = np.std(b_neg) * np.sqrt(4) if b_neg else 0
    d_downside = np.std(d_neg) * np.sqrt(4) if d_neg else 0
    
    Rf = 2.0
    b_sharpe = (b_annual_ret - Rf) / b_vol if b_vol > 0 else 0
    d_sharpe = (d_annual_ret - Rf) / d_vol if d_vol > 0 else 0
    b_calmar = b_annual_ret / b_dd if b_dd > 0 else 0
    d_calmar = d_annual_ret / d_dd if d_dd > 0 else 0
    
    a_rets = a_nav['ret'].values * 100
    active_rets = d_rets[:len(a_rets)] - a_rets
    tracking_error = np.std(active_rets) * np.sqrt(4) if len(active_rets) > 0 else 0
    ir = np.mean(active_rets) * 4 / tracking_error if tracking_error > 0 else 0
    
    # 换手率
    dates = sorted(d_baskets.keys())
    b_turnover_rates = []
    d_turnover_rates = []
    for i in range(1, len(dates)):
        prev_b = set(s['ts_code'] for s in b_baskets[dates[i-1]] if isinstance(s, dict))
        curr_b = set(s['ts_code'] for s in b_baskets[dates[i]] if isinstance(s, dict))
        b_turnover_rates.append(len(curr_b - prev_b) / 50 * 100)
        prev_d = set(s['ts_code'] for s in d_baskets[dates[i-1]] if isinstance(s, dict))
        curr_d = set(s['ts_code'] for s in d_baskets[dates[i]] if isinstance(s, dict))
        d_turnover_rates.append(len(curr_d - prev_d) / 50 * 100)
    
    avg_b_turn = np.mean(b_turnover_rates)
    avg_d_turn = np.mean(d_turnover_rates)
    
    # 成分股重合度
    overlap_rates = []
    for i in range(n_periods):
        rb = str(b_nav.iloc[i]['rb_date'])[:10]
        b_codes = set(s['ts_code'] for s in b_baskets.get(rb, []) if isinstance(s, dict))
        d_codes = set(s['ts_code'] for s in d_baskets.get(rb, []) if isinstance(s, dict))
        if b_codes and d_codes:
            overlap_rates.append(len(b_codes & d_codes) / 50 * 100)
    avg_overlap = np.mean(overlap_rates) if overlap_rates else 0
    
    # ── 生成报告 ──
    report = f"""# ZZ800 FCF 精选指数 D版(缓冲区) vs B版 增强回测报告

> **指数**: 中证800 (000906.SH) | **基准指数**: {zz800_label} + {hs300_label}  
> **回测期间**: 2016-06-13 ~ 2026-06-04 ({n_years:.1f}年)  
> **D版策略**: B版baseline + 缓冲区±20%

---

## 一、收益指标 (含指数对比)

| 指标 | B版(宽松OCF) | D版(缓冲区) | {zz800_label} | {hs300_label} |
|------|-------------|-------------|--------------|--------------|
| 累计收益 | {b_final:.4f} | {d_final:.4f} | {zz800_cum_nav:.4f} | {hs300_cum_nav:.4f} |
| 年化收益 | {b_annual_ret:.2f}% | {d_annual_ret:.2f}% | {zz800_annual:.2f}% | {hs300_annual:.2f}% |
| 超额收益(vs {zz800_label}) | {(b_annual_ret-zz800_annual):.2f}pp | {(d_annual_ret-zz800_annual):.2f}pp | - | {(hs300_annual-zz800_annual):.2f}pp |
| 超额收益(vs {hs300_label}) | {(b_annual_ret-hs300_annual):.2f}pp | {(d_annual_ret-hs300_annual):.2f}pp | {(zz800_annual-hs300_annual):.2f}pp | - |
| 季度胜率 | {sum(1 for r in b_rets if r>0)/n_periods*100:.1f}% | {sum(1 for r in d_rets if r>0)/n_periods*100:.1f}% | - | - |

## 二、风险类指标

| 指标 | B版 | D版 | 差异 |
|------|-----|-----|------|
| 最大回撤 | {b_dd:.2f}% | {d_dd:.2f}% | {(d_dd-b_dd):+.2f}pp |
| 年化波动率 | {b_vol:.2f}% | {d_vol:.2f}% | {(d_vol-b_vol):+.2f}pp |
| 下行风险 | {b_downside:.2f}% | {d_downside:.2f}% | {(d_downside-b_downside):+.2f}pp |

## 三、风险调整后收益

| 指标 | B版 | D版 |
|------|-----|-----|
| 夏普比率 | {b_sharpe:.3f} | {d_sharpe:.3f} |
| 卡玛比率 | {b_calmar:.3f} | {d_calmar:.3f} |
| 信息比率(IR) | - | {ir:.3f} |
| 跟踪误差 | - | {tracking_error:.2f}% |

## 四、换手率分析

| 指标 | B版 | D版 | 变化 |
|------|-----|-----|------|
| 平均单期换手率 | {avg_b_turn:.1f}% | {avg_d_turn:.1f}% | {(avg_b_turn-avg_d_turn):.1f}pp ↓ |
| 成分股重合度 | - | {avg_overlap:.1f}% | - |

## 五、年度收益对比

| 年份 | B版 | D版 | {zz800_label} | {hs300_label} | D版超额(ZZ800) | D版超额(HS300) |
|------|-----|-----|--------------|--------------|----------------|----------------|
"""
    
    for ad in b_annual:
        year = ad['year']
        d_match = [x for x in d_annual if x['year'] == year]
        d_a = d_match[0] if d_match else {}
        b_a = ad['strategy_annual']
        d_a_val = d_a.get('strategy_annual', None)
        zz800_a = ad.get('index_annual', None)  # ZZ800年度收益
        hs300_a = ad.get('hs300_annual', None)   # HS300年度收益
        
        b_str = f"{b_a:+.2f}%" if b_a else "-"
        d_str = f"{d_a_val:+.2f}%" if d_a_val else "-"
        zz_str = f"{zz800_a:+.2f}%" if zz800_a else "-"
        hs_str = f"{hs300_a:+.2f}%" if hs300_a else "-"
        excess_dzz = f"{(d_a_val-zz800_a):+.2f}pp" if d_a_val and zz800_a else "-"
        excess_dhs = f"{(d_a_val-hs300_a):+.2f}pp" if d_a_val and hs300_a else "-"
        
        report += f"| {year} | {b_str} | {d_str} | {zz_str} | {hs_str} | {excess_dzz} | {excess_dhs} |\n"
    
    # ── 六、季度持仓分析 ──
    report += "\n## 六、季度持仓盈亏分析 (D版)\n\n"
    report += "| 期 | 调仓期 | 持仓盈/亏 | 胜率 | 贡献Top3 | 贡献Bottom3 |\n"
    report += "|----|--------|----------|------|----------|-------------|\n"
    
    for p in d_holding_pnl:
        top3_str = ", ".join(f"{s['name']}({s['stock_ret']:+.1f}%)" for s in p.get('top3', []))
        bottom3_str = ", ".join(f"{s['name']}({s['stock_ret']:+.1f}%)" for s in p.get('bottom3', []))
        report += f"| {p['rb_date'][:7]} | {p['rb_date']}→{p['next_rb']} | {p['n_winners']}/{p['n_losers']} | {p['win_rate']:.0f}% | {top3_str} | {bottom3_str} |\n"
    
    # ── 七、逐期收益对比 ──
    report += "\n## 七、逐期收益对比\n\n"
    report += "| 期 | 调仓期 | B版 | D版 | ZZ800 | HS300 | D版-B版 | D版-ZZ800 |\n"
    report += "|----|--------|-----|-----|-------|-------|---------|----------|\n"
    
    for i in range(n_periods):
        rb = str(b_nav.iloc[i]['rb_date'])[:10]
        nxt = str(b_nav.iloc[i]['next_rb'])[:10]
        br = b_nav.iloc[i]['ret'] * 100
        dr = d_nav.iloc[i]['ret'] * 100
        
        # 指数同期收益
        zz800_ret = get_index_return(zz800_idx, rb, nxt)
        hs300_ret = get_index_return(hs300_idx, rb, nxt)
        zz_str = f"{zz800_ret*100:.2f}%" if zz800_ret else "-"
        hs_str = f"{hs300_ret*100:.2f}%" if hs300_ret else "-"
        diff_bd = f"{(dr-br):+.2f}pp"
        diff_dzz = f"{(dr-zz800_ret*100):+.2f}pp" if zz800_ret else "-"
        
        report += f"| {i+1} | {rb}→{nxt} | {br:+.2f}% | {dr:+.2f}% | {zz_str} | {hs_str} | {diff_bd} | {diff_dzz} |\n"
    
    # ── 八、验收汇总 ──
    report += f"""
## 八、验收汇总

| # | 验收项 | B版 | D版 | 达标 |
|---|--------|-----|-----|------|
| 1 | 年化≥{zz800_label} | {b_annual_ret:.2f}% | {d_annual_ret:.2f}% | {"✅" if d_annual_ret>=zz800_annual else "❌"} |
| 2 | 夏普≥0.5 | {b_sharpe:.3f} | {d_sharpe:.3f} | {"✅" if d_sharpe>=0.5 else "❌"} |
| 3 | 卡玛≥0.5 | {b_calmar:.3f} | {d_calmar:.3f} | {"✅" if d_calmar>=0.5 else "❌"} |
| 4 | 最大回撤≤25% | {b_dd:.2f}% | {d_dd:.2f}% | {"✅" if d_dd<=25 else "❌"} |
| 5 | 换手率D≤B | {avg_b_turn:.1f}% | {avg_d_turn:.1f}% | {"✅" if avg_d_turn<=avg_b_turn else "❌"} |
| 6 | 季度胜率≥60% | - | {sum(1 for r in d_rets if r>0)/n_periods*100:.1f}% | {"✅" if sum(1 for r in d_rets if r>0)/n_periods*100>=60 else "❌"} |

## 九、结论

1. **收益**: D版年化{d_annual_ret:.2f}% vs B版{b_annual_ret:.2f}%，超额{(d_annual_ret-b_annual_ret):+.2f}pp
2. **指数对比**: D版年化{d_annual_ret:.2f}% vs {zz800_label}{zz800_annual:.2f}%，超额{(d_annual_ret-zz800_annual):+.2f}pp
3. **换手率**: D版缓冲区降低换手{(avg_b_turn-avg_d_turn):.1f}pp
4. **持仓胜率**: 平均{np.mean([p['win_rate'] for p in d_holding_pnl]):.0f}%的持仓股盈利
"""
    
    return report


# ──────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────
def main():
    print("=" * 60)
    print("增强版回测报告: 指数对比 + 年度收益 + 持仓分析")
    print("=" * 60)
    
    # Step 1: 下载指数行情
    print("\nStep1: 下载指数行情数据...")
    zz800_idx = download_index_daily("000906.SH")  # ZZ800
    hs300_idx = download_index_daily("000300.SH")   # HS300
    
    # Step 2: 加载NAV和篮子数据
    print("\nStep2: 加载回测数据...")
    b_nav = pd.read_csv(ROOT / 'output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv')
    d_nav = pd.read_csv(ROOT / 'output/zz800_fcf_lenient_buffer/backtest_nav_tr.csv')
    a_nav = pd.read_csv(ROOT / 'output/zz800_fcf/backtest_nav_tr.csv')
    
    with open(ROOT / 'output/zz800_fcf_fixed_lenient/all_baskets_2015_2026.json') as f:
        b_baskets = json.load(f)
    with open(ROOT / 'output/zz800_fcf_lenient_buffer/all_baskets_2015_2026.json') as f:
        d_baskets = json.load(f)
    
    # Step 3: 持仓盈亏分析
    print("\nStep3: 持仓盈亏分析...")
    adj_dir = ROOT / 'data' / 'adj_close_cache'
    b_holding_pnl = analyze_holding_pnl(b_baskets, adj_dir, b_nav, "B版")
    d_holding_pnl = analyze_holding_pnl(d_baskets, adj_dir, d_nav, "D版")
    print(f"  B版: {len(b_holding_pnl)}期, D版: {len(d_holding_pnl)}期")
    
    # Step 4: 年度收益对比
    print("\nStep4: 年度收益对比...")
    b_annual_zz = compute_annual_returns(b_nav, zz800_idx, "ZZ800")
    d_annual_zz = compute_annual_returns(d_nav, zz800_idx, "ZZ800")
    b_annual_hs = compute_annual_returns(b_nav, hs300_idx, "HS300")
    d_annual_hs = compute_annual_returns(d_nav, hs300_idx, "HS300")
    
    # 合并: ZZ800作为主对比, HS300作为辅助
    for i, ad in enumerate(b_annual_zz):
        hs_match = [x for x in b_annual_hs if x['year'] == ad['year']]
        ad['hs300_annual'] = hs_match[0]['index_annual'] if hs_match and hs_match[0].get('index_annual') else None
    for i, ad in enumerate(d_annual_zz):
        hs_match = [x for x in d_annual_hs if x['year'] == ad['year']]
        ad['hs300_annual'] = hs_match[0]['index_annual'] if hs_match and hs_match[0].get('index_annual') else None
    
    # Step 5: 生成报告
    print("\nStep5: 生成报告...")
    report = generate_report(b_nav, d_nav, a_nav, b_baskets, d_baskets,
                             zz800_idx, hs300_idx, b_holding_pnl, d_holding_pnl,
                             b_annual_zz, d_annual_zz, "ZZ800", "HS300")
    
    # 保存
    out_path = ROOT / 'docs/zz800_fcf_d_vs_b_report.md'
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(report)
    print(f"  ✅ 报告: {out_path}")
    
    s_dir = ROOT / 'strategies/zz800_fcf_lenient_buffer'
    s_dir.mkdir(exist_ok=True)
    with open(s_dir / 'README.md', 'w') as f:
        f.write(report)
    print(f"  ✅ 策略报告: {s_dir / 'README.md'}")
    
    # 保存持仓分析JSON
    pnl_path = ROOT / 'output/zz800_fcf_lenient_buffer/holding_pnl.json'
    with open(pnl_path, 'w') as f:
        json.dump(d_holding_pnl, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 持仓分析: {pnl_path}")
    
    # 保存年度对比CSV
    annual_df = pd.DataFrame(d_annual_zz)
    annual_path = ROOT / 'output/zz800_fcf_lenient_buffer/annual_comparison.csv'
    annual_df.to_csv(annual_path, index=False)
    print(f"  ✅ 年度对比: {annual_path}")
    
    print("\n✅ 增强版报告生成完成!")


if __name__ == "__main__":
    main()