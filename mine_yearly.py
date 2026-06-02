"""逐年 Siegel 挖掘 — CSI 300 + CSI 500 每年独立评估"""
import sys, time, pandas as pd
from pathlib import Path
sys.path.insert(0, '.')

import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()
from stock_10y_hold import simulate as simulate_10y

BATCH_SAVE = 30
OUT = Path("data")

def get_index_codes(idx_code, trade_date):
    """获取指定日期的指数成分"""
    # 尝试多个日期
    for d in [trade_date, 
              f"{int(trade_date[:4])-1}1231", f"{int(trade_date[:4])-1}1230",
              f"{trade_date[:4]}0102", f"{trade_date[:4]}0701"]:
        try:
            df = pro.index_weight(index_code=idx_code, trade_date=d)
            if not df.empty:
                return sorted(set(df['con_code'].tolist()))
        except:
            pass
    return []

def fetch_names_industries(codes):
    """批量获取名称和行业"""
    nm, ind, ld = {}, {}, {}
    for i in range(0, len(codes), 200):
        batch = codes[i:i+200]
        try:
            sb = pro.stock_basic(ts_code=','.join(batch),
                                 fields='ts_code,name,industry,list_date')
            for _, r in sb.iterrows():
                nm[r['ts_code']] = r['name']
                ind[r['ts_code']] = r.get('industry', '') or ''
                ld[r['ts_code']] = r.get('list_date', '')
        except:
            pass
        time.sleep(0.15)
    return nm, ind, ld

def run_one_year(idx_code, idx_name, eval_year, start_date):
    """
    对指定指数和评估年份，跑全部成分股的 10 年股息再投评估
    start_date = 10年回测起点
    """
    eval_date = f"{eval_year}0102"
    codes = get_index_codes(idx_code, eval_date)
    if not codes:
        print(f"  ❌ 无成分数据")
        return
    
    nm, ind, ld = fetch_names_industries(codes)
    
    # 过滤：上市日期 < 回测起点
    eligible = [c for c in codes if ld.get(c, '9999') < start_date[:8]]
    print(f"  {idx_name} {eval_year}: 成分{len(codes)} → 合格{len(eligible)} (上市<{start_date[:4]})")
    
    csv_path = OUT / f"siegel_{idx_name.replace(' ','_').lower()}_{eval_year}.csv"
    done = set()
    if csv_path.exists():
        existing = pd.read_csv(csv_path)
        done = set(existing['code'].tolist())
        print(f"    已有 {len(done)} 只，续跑...")
    else:
        pd.DataFrame(columns=[
            'code','name','industry','total_ret','cagr','price_chg',
            'div_contrib','div_ratio','div_amplify','div_total',
            'buy_price','last_price','max_yr_loss'
        ]).to_csv(csv_path, index=False, encoding='utf-8-sig')
    
    pending = [c for c in eligible if c not in done]
    if not pending:
        print(f"    全部完成 ✓")
        return
    
    print(f"    待评估: {len(pending)} 只...")
    batch = []
    errors = 0
    
    for i, code in enumerate(pending):
        name = nm.get(code, code[:6])
        industry = ind.get(code, '')
        
        if i % 30 == 0:
            print(f"      [{i}/{len(pending)}] err={errors}")
        
        for retry in range(3):
            try:
                rows, final_val, cagr, split_factor = simulate_10y(code, start_date, 100000, verbose=False)
                if not rows:
                    break
                buy = rows[0].start_value / rows[0].shares if rows[0].shares > 0 else 0
                last = rows[-1].price
                # 用 split_factor 换算"送转后等效买入价"，消除送转股的价格跳跃失真
                eff_buy = buy / split_factor if split_factor > 0 else buy
                pc = (last / eff_buy - 1) * 100 if eff_buy > 0 else 0
                tr = (final_val / 100000 - 1) * 100
                dc = tr - pc
                dt = sum(r.div_cash for r in rows)
                dr = dt / 100000 * 100
                da = (rows[-1].shares / rows[0].shares - 1) * 100 if rows[0].shares > 0 else 0
                ml = min(r.total_return for r in rows)
                
                batch.append({
                    'code': code, 'name': name, 'industry': industry,
                    'total_ret': tr, 'cagr': cagr, 'price_chg': pc,
                    'div_contrib': dc, 'div_ratio': dr, 'div_amplify': da,
                    'div_total': dt, 'buy_price': buy, 'last_price': last,
                    'max_yr_loss': ml,
                })
                break
            except:
                if retry == 2:
                    errors += 1
            time.sleep(0.5)
        time.sleep(0.1)
        
        if len(batch) >= BATCH_SAVE:
            pd.DataFrame(batch).to_csv(csv_path, mode='a', header=False, index=False, encoding='utf-8-sig')
            batch = []
    
    if batch:
        pd.DataFrame(batch).to_csv(csv_path, mode='a', header=False, index=False, encoding='utf-8-sig')
    
    df = pd.read_csv(csv_path)
    print(f"    ✅ 完成 {len(df)} 只, 错误 {errors}")


# ============================================================
# 主流程
# ============================================================
print("=" * 60)
print("  逐年 Siegel 挖掘 — CSI 300 + CSI 500")
print("=" * 60)

# 年份配置：(指数代码, 名称, [(评估年, 回测起点), ...])
tasks = [
    # CSI 300: 2020-2025
    ('000300.SH', 'CSI300', [
        (2020, '2010-01-04'),
        (2021, '2011-01-04'),
        (2022, '2012-01-04'),
        (2023, '2013-01-02'),
        (2024, '2014-01-02'),
        (2025, '2015-01-05'),
    ]),
    # CSI 500: 2019-2025
    ('000905.SH', 'CSI500', [
        (2019, '2009-01-05'),
        (2020, '2010-01-04'),
        (2021, '2011-01-04'),
        (2022, '2012-01-04'),
        (2023, '2013-01-02'),
        (2024, '2014-01-02'),
        (2025, '2015-01-05'),
    ]),
]

for idx_code, idx_name, years in tasks:
    print(f"\n{'='*60}")
    print(f"  {idx_name} ({idx_code})")
    print(f"{'='*60}")
    for eval_year, start_date in years:
        t0 = time.time()
        run_one_year(idx_code, idx_name, eval_year, start_date)
        print(f"    耗时: {time.time()-t0:.0f}s")

print(f"\n{'='*60}")
print("  全部完成！")
