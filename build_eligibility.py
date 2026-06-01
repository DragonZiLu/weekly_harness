"""回测: HS300+ZZ500池 + 动态 ROE≥8% & PE≤25
修复: ROE批量拉取 + 季度日正确提取
"""
import tushare as ts
from config.settings import tushare_cfg
ts.set_token(tushare_cfg.token)
pro = ts.pro_api()
import json, time
from collections import defaultdict

# ==================== 1. 股票池 ====================
def get_constituents(idx_code):
    df = pro.index_weight(index_code=idx_code, trade_date='20260529')
    return list(set(df['con_code'].tolist()))

hs300 = get_constituents('000300.SH')
zz500 = get_constituents('000905.SH')
all_codes = list(set(hs300 + zz500))
print(f'HS300: {len(hs300)}, ZZ500: {len(zz500)}, 去重: {len(all_codes)}')

# 年份范围
years = list(range(2012, 2026))  # 2012-2025

# ==================== 2. ROE 历史 (批量，每次100只) ====================
print('\n获取历史ROE (批量100只/次)...')
roe_data = {}

for year in years:
    period = f'{year}1231'
    year_data = {}
    for i in range(0, len(all_codes), 100):
        batch = all_codes[i:i+100]
        try:
            df = pro.fina_indicator(ts_code=','.join(batch), period=period,
                fields='ts_code,roe')
            for _, r in df.iterrows():
                code = r['ts_code']
                roe = float(r.get('roe', 0) or 0)
                year_data[code] = roe
        except Exception as e:
            pass
        time.sleep(0.15)
    for code, roe in year_data.items():
        if code not in roe_data:
            roe_data[code] = {}
        roe_data[code][year] = roe
    print(f'  {year}: {len(year_data)} 只')

with open('/tmp/historical_roe.json', 'w') as f:
    json.dump(roe_data, f, indent=1)

# ==================== 3. 季度末调仓日 ====================
print('\n获取季度末日期...')
cal = pro.trade_cal(exchange='SSE', start_date='20141001', end_date='20260601', is_open='1')
all_trading = sorted(cal['cal_date'].tolist())

def get_quarter_end_dates(trading_dates):
    """每季度最后一个交易日"""
    result = []
    current_q = None
    for d in trading_dates:
        yr, mo = int(d[:4]), int(d[5:7])
        q = (mo - 1) // 3 + 1
        qk = f'{yr}Q{q}'
        if qk != current_q:
            if current_q is not None:
                result.append(last_in_q)
            current_q = qk
        last_in_q = d
    result.append(last_in_q)  # last quarter
    return result

all_q = get_quarter_end_dates(all_trading)
q_dates = [d for d in all_q if '20150101' <= d <= '20260601']
print(f'季度末交易日: {len(q_dates)}')
print(f'  first: {q_dates[0]}, last: {q_dates[-1]}')

# ==================== 4. PE 历史 (每季度末) ====================
print('\n获取历史PE...')
pe_data = {}

for i, date_str in enumerate(q_dates):
    try:
        df = pro.daily_basic(trade_date=date_str,
            fields='ts_code,pe_ttm')
        for _, r in df.iterrows():
            code = r['ts_code']
            pe = float(r.get('pe_ttm', 0) or 0)
            if code not in pe_data:
                pe_data[code] = {}
            pe_data[code][date_str] = pe
    except:
        pass
    if (i+1) % 8 == 0:
        print(f'  PE: {i+1}/{len(q_dates)}')
    time.sleep(0.12)

with open('/tmp/historical_pe.json', 'w') as f:
    json.dump(pe_data, f, indent=1)
print(f'  PE: {len(pe_data)} 只股票')

# ==================== 5. eligibility ====================
print('\n计算 eligibility...')

def get_avail_roe_years(date_str):
    dt_yr = int(date_str[:4])
    dt_mo = int(date_str[4:6])
    latest = dt_yr - 2 if dt_mo <= 4 else dt_yr - 1
    return [latest - 2, latest - 1, latest]

def avg_roe_3yr(code, date_str):
    if code not in roe_data:
        return 0
    yrs = get_avail_roe_years(date_str)
    vals = []
    for y in yrs:
        v = roe_data[code].get(y)
        if v is not None:
            vals.append(v)
    return sum(vals) / len(vals) if len(vals) >= 2 else 0

eligibility = {}
fail_pe_count = 0
fail_roe_count = 0
ok_count = 0

for date_str in q_dates:
    date_ok = 0
    for code in all_codes:
        # PE check
        pe = pe_data.get(code, {}).get(date_str, 999)
        pe_ok = (0 < pe <= 25)
        
        # ROE check
        roe3 = avg_roe_3yr(code, date_str)
        roe_ok = (roe3 >= 8)
        
        ok = pe_ok and roe_ok
        
        if code not in eligibility:
            eligibility[code] = {}
        eligibility[code][date_str] = ok
        
        if ok:
            ok_count += 1
            date_ok += 1
        else:
            if not pe_ok:
                fail_pe_count += 1
            if not roe_ok:
                fail_roe_count += 1
    
    if date_ok > 0:
        print(f'  {date_str}: eligible={date_ok}/{len(all_codes)}')

with open('/tmp/eligibility.json', 'w') as f:
    json.dump(eligibility, f)

# Save q_dates too
with open('/tmp/q_dates.json', 'w') as f:
    json.dump(q_dates, f)

print(f'\nTotal: ok={ok_count}, failPE={fail_pe_count}, failROE={fail_roe_count}')
print('写入 /tmp/eligibility.json')
