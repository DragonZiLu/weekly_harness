"""CSI 300 FCF (932366) 验证：下载权重 → 下载 income → 测试选股"""
import sys, time, pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime as dt_dt, timedelta as dt_td

_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from weekly_harness.fcf_universe import FcfUniverse, IndexWeightCache
from config.settings import tushare_cfg
import tushare as ts

_DATE = "2026-03-20"
INDEX = "000300.SH"  # CSI 300
COMPARE_INDEX = "932366.CSI"  # CSI 300 FCF index

# ═══ Step 1: 确保 CSI 300 权重已下载 ═══
print("Step 1: 下载 CSI 300 成分股权重...")
iwc = IndexWeightCache(INDEX)
iwc.download()
iwc.load()
constituents = iwc.get_constituents(_DATE)
print(f"  CSI 300 成分股: {len(constituents)} 只")

# ═══ Step 2: 下载 income 数据（仅限 CSI 300 标的，~300只）═══
DATA_DIR = _PROJ / "data" / "fcf_financials"
pro = ts.pro_api(tushare_cfg.token)
_lock = threading.Lock()
_last = 0.0
_done = 0

def fetch_income(code, year, pro):
    global _last, _done
    try:
        with _lock:
            n = time.time(); w = _last + 0.04 - n
            if w > 0: time.sleep(w)
            _last = time.time()
        df = pro.income(ts_code=code, start_date=f'{year}0101', end_date=f'{year+1}0630',
                        fields='ts_code,ann_date,f_ann_date,end_date,report_type,revenue,oper_cost,biz_tax_surchg,sell_exp,admin_exp,fin_exp,invest_income')
        if df is not None and not df.empty:
            t = f'{year}1231'
            m = df[df['end_date'].astype(str).str[:8]==t]
            if not m.empty:
                if len(m)>1 and 'report_type' in m.columns:
                    c = m[m['report_type']=='1']
                    if not c.empty: m = c
                _done += 1
                return m.iloc[0].to_dict()
    except: pass
    return None

for year in [2024, 2025]:
    dst = DATA_DIR / f"income_{year}.csv"
    existing = set()
    if dst.exists():
        existing = set(pd.read_csv(dst, dtype={"ts_code": str})["ts_code"].unique())
    
    needed = [c for c in constituents if c not in existing]
    if not needed:
        print(f"  FY{year} income: already covered ({len(existing)}/{len(constituents)})")
        continue
    
    print(f"  FY{year} income: downloading {len(needed)} stocks...", end=" ", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=15) as pool:
        fs = {pool.submit(fetch_income, c, year, pro): c for c in needed}
        for i, f in enumerate(as_completed(fs)):
            if i % 100 == 0: print(f"{i}/{len(needed)}", end=" ", flush=True)
            r = f.result()
            if r: results.append(r)
    
    if results:
        df_new = pd.DataFrame(results)
        for c in ['ann_date','f_ann_date','end_date']:
            if c in df_new.columns: df_new[c] = df_new[c].astype(str)
        if dst.exists():
            df_old = pd.read_csv(dst, dtype={"ts_code": str})
            df_all = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df_all = df_new
        df_all.to_csv(dst, index=False)
    print(f"✅ {len(results)}/{len(needed)} new added")
    time.sleep(0.5)

# ═══ Step 3: 运行 FCF 选股 ═══
print(f"\nStep 3: 运行 {INDEX} FCF 选股...")
uni = FcfUniverse(index_code=INDEX)
uni.preload_all(download=False)
basket = uni.get_fcf_basket(_DATE, top_n=50, verbose=True)
our_codes = {k for k in basket if k != "__quality_warnings__"}
print(f"  选出 {len(our_codes)} 只标的")

# ═══ Step 4: 对比 932366 ═══
print(f"\nStep 4: 对比 {COMPARE_INDEX} 实际成分...")
avail = pro.index_weight(index_code=COMPARE_INDEX, trade_date="")
# Try recent dates
for d in ["20260331","20260320","20251231","20250930","20250630","20250331"]:
    try:
        actual = pro.index_weight(index_code=COMPARE_INDEX, trade_date=d)
        if actual is not None and not actual.empty:
            break
    except:
        pass

if actual is not None and not actual.empty:
    actual["trade_date"] = actual["trade_date"].astype(str)
    closest = actual["trade_date"].iloc[0]
    aw = dict(zip(actual["con_code"], actual["weight"]))
    actual_codes = set(aw.keys())
    
    overlap = our_codes & actual_codes
    only_theirs = actual_codes - our_codes
    only_ours = our_codes - actual_codes
    
    recall = len(overlap)/len(actual_codes)*100 if actual_codes else 0
    precision = len(overlap)/len(our_codes)*100 if our_codes else 0
    
    print(f"  932366 日期: {closest}, 成分股: {len(actual_codes)} 只")
    print(f"  Recall: {recall:.1f}% ({len(overlap)}/{len(actual_codes)})")
    print(f"  Precision: {precision:.1f}% ({len(overlap)}/{len(our_codes)})")
    
    if len(overlap) >= 3:
        from scipy.stats import spearmanr, pearsonr
        ow = [basket[c]["weight"]*100 for c in overlap]
        aw_v = [float(aw[c]) for c in overlap]
        print(f"  Spearman: {spearmanr(ow, aw_v)[0]:.4f}")
        print(f"  Pearson: {pearsonr(ow, aw_v)[0]:.4f}")
    
    if only_theirs:
        print(f"\n  仅 932366 ({len(only_theirs)}只):")
        for c in sorted(only_theirs, key=lambda x: -float(aw[x]))[:10]:
            w = float(aw[c])
            info = uni._stock_basic.set_index("ts_code").to_dict("index")
            name = info.get(c, {}).get("name", "")
            print(f"    {c} {name}: w={w:.1f}%")
    
    if only_ours:
        print(f"\n  仅我们 ({len(only_ours)}只):")
        for c in sorted(only_ours)[:5]:
            print(f"    {c}: w={basket[c]['weight']*100:.1f}%")
else:
    print("  ⚠️ 无法获取 932366 数据")

print(f"\n✅ 测试完成")
