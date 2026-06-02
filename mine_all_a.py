"""
全A股万宝路挖掘 — 批量10年股息再投评估
=====================================
上市满10年(2015年前)的A股，逐只计算股息再投收益
"""
import sys, time
from pathlib import Path
from datetime import datetime

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

import tushare as ts
import pandas as pd
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()

from stock_10y_hold import simulate as simulate_10y

# ─── 1. 获取全A股列表 + 过滤 ──────────────────────────────────
print("=" * 70)
print("  全A股万宝路挖掘 — 10年股息再投扫描")
print("=" * 70)

print("\n[1/3] 获取全A股列表...")
# 获取所有正常交易的主板/创业板股票
all_stocks = pro.stock_basic(exchange='', list_status='L', 
                              fields='ts_code,name,industry,list_date')
print(f"  上市股票总数: {len(all_stocks)}")

# 过滤：2015-01-01 前上市
all_stocks['list_date'] = pd.to_datetime(all_stocks['list_date'], errors='coerce')
valid = all_stocks[all_stocks['list_date'] < '2015-01-01']
# 排除ST、退市整理
valid = valid[~valid['name'].str.contains('ST|退', na=False)]
codes = sorted(valid['ts_code'].tolist())
name_map = {}
for _, r in valid.iterrows():
    name_map[r['ts_code']] = (r['name'], str(r.get('industry', '') or ''))
print(f"  2015年前上市且正常交易: {len(codes)} 只")

# ─── 2. 逐只评估 ─────────────────────────────────────────────
output_dir = _PROJECT_ROOT / "data"
output_dir.mkdir(exist_ok=True)
csv_path = output_dir / "marlboro_all_a.csv"

# 断点续跑：检查已有进度
done_codes = set()
if csv_path.exists():
    existing = pd.read_csv(csv_path)
    done_codes = set(existing['code'].tolist())
    print(f"  已有 {len(done_codes)} 只已评估，续跑...")
else:
    # 初始化CSV
    pd.DataFrame(columns=['code','name','industry','total_ret','cagr','price_chg',
                          'div_contrib','div_ratio','div_amplify','div_total',
                          'buy_price','last_price','max_yr_loss']).to_csv(
        csv_path, index=False, encoding='utf-8-sig')

pending = [c for c in codes if c not in done_codes]
print(f"\n[2/3] 逐只评估 10年股息再投...")
print(f"  待评估: {len(pending)} 只 (已完成 {len(done_codes)})")
print(f"  预计耗时: ~{len(pending) * 0.3 / 60:.0f} 分钟")

errors = 0
batch_buffer = []
last_save = time.time()

for idx, code in enumerate(pending):
    name, industry = name_map.get(code, (code[:6], ''))
    
    if idx % 50 == 0:
        elapsed = time.time() - last_save
        print(f"  [{idx}/{len(pending)}] {name} ... (错误{errors}只)")
    
    try:
        rows, final_val, cagr, split_factor = simulate_10y(code, "2015-01-05", 100000, verbose=False)
        if not rows:
            continue
        
        buy_price = rows[0].start_value / rows[0].shares if rows[0].shares > 0 else 0
        last_price = rows[-1].price
        # 用 split_factor 换算"送转后等效买入价"，消除送转股的价格跳跃失真
        eff_buy = buy_price / split_factor if split_factor > 0 else buy_price
        price_chg = (last_price / eff_buy - 1) * 100 if eff_buy > 0 else 0
        total_ret = (final_val / 100000 - 1) * 100
        div_contrib = total_ret - price_chg
        div_total = sum(r.div_cash for r in rows)
        div_ratio = div_total / 100000 * 100
        div_amplify = (rows[-1].shares / rows[0].shares - 1) * 100 if rows[0].shares > 0 else 0
        max_yr_loss = min(r.total_return for r in rows)
        
        batch_buffer.append({
            'code': code, 'name': name, 'industry': industry,
            'total_ret': total_ret, 'cagr': cagr,
            'price_chg': price_chg, 'div_contrib': div_contrib,
            'div_ratio': div_ratio, 'div_amplify': div_amplify,
            'buy_price': buy_price, 'last_price': last_price,
            'div_total': div_total, 'max_yr_loss': max_yr_loss,
        })
        
    except Exception as e:
        errors += 1
        time.sleep(0.5)
    
    # 每50只存盘
    if len(batch_buffer) >= 50 or (idx == len(pending) - 1 and batch_buffer):
        df_batch = pd.DataFrame(batch_buffer)
        df_batch.to_csv(csv_path, mode='a', header=False, index=False, encoding='utf-8-sig')
        batch_buffer = []
        last_save = time.time()
    
    time.sleep(0.12)

print(f"\n  完成: 新增 {len(codes) - len(done_codes) - errors} 只, 错误 {errors} 只")

# ─── 3. 快速展示 ─────────────────────────────────────────────
df_all = pd.read_csv(csv_path)
print(f"\n[3/3] 全A股总计 {len(df_all)} 只有效数据")
print(f"  万宝路型 (股息贡献>60%, 分红>50%): "
      f"{len(df_all[(df_all['total_ret']>0) & (df_all['div_contrib']/df_all['total_ret']>0.6) & (df_all['div_ratio']>50)])}")
print(f"  💾 数据文件: data/marlboro_all_a.csv")
