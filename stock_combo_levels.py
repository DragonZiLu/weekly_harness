"""
stock_combo_levels.py — 电力+神华组合：年度估值/质量水平 & 当前历史分位
============================================================================

计算组合（华能国际25% + 国电电力25% + 中国神华50%）的逐年（2016-2025）
组合级别指标：
  · PE(TTM)      —— 市值加权（调和：ΣMV / Σ(MV/PE)）
  · PB           —— 市值加权（调和：ΣMV / Σ(MV/PB)）
  · 股息率(TTM)  —— 市值加权均值
  · ROE          —— 市值加权均值（年报口径）
  · 净利润增速   —— 市值加权均值（归母净利同比，年报口径）
  · PEG          —— 组合PE / 组合净利润增速

并给出今天组合 PE/PB/股息率 在 2016-2025 年末序列中的历史分位。

数据：
  · Tushare daily_basic       —— close/pe_ttm/pb/dv_ttm/total_mv
  · Tushare fina_indicator    —— roe/netprofit_yoy（年报 end_date=YYYY1231）

用法：
  python stock_combo_levels.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import tushare as ts

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

STOCKS = {
    "600011.SH": "华能国际",
    "600795.SH": "国电电力",
    "601088.SH": "中国神华",
}
WEIGHTS = {"600011.SH": 0.25, "600795.SH": 0.25, "601088.SH": 0.50}


def init_tushare():
    from config.settings import tushare_cfg
    ts.set_token(tushare_cfg.token)
    return ts.pro_api()


def fetch_daily_basic(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    df = pro.daily_basic(
        ts_code=ts_code, start_date=start, end_date=end,
        fields="ts_code,trade_date,close,pe_ttm,pb,dv_ttm,total_mv",
    )
    if df is None or df.empty:
        raise ValueError(f"无 daily_basic 数据: {ts_code}")
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
    return df


def fetch_fina(pro, ts_code: str) -> dict[str, dict]:
    """{年份: {roe, netprofit_yoy}}，取年报(end_date=YYYY1231)"""
    for _ in range(3):
        try:
            df = pro.fina_indicator(
                ts_code=ts_code,
                fields="ts_code,end_date,roe,netprofit_yoy",
            )
            break
        except Exception:
            time.sleep(1.0)
    else:
        return {}
    if df is None or df.empty:
        return {}
    df["end_date"] = df["end_date"].astype(str)
    df = df[df["end_date"].str.endswith("1231")]
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        y = r["end_date"][:4]
        # 同年可能多条，保留首条（最新报告）
        if y in out:
            continue
        out[y] = {
            "roe": float(r["roe"]) if pd.notna(r["roe"]) else None,
            "np_yoy": float(r["netprofit_yoy"]) if pd.notna(r["netprofit_yoy"]) else None,
        }
    return out


def fetch_div(pro, ts_code: str, start: str, end: str) -> dict[str, float]:
    try:
        div = pro.dividend(ts_code=ts_code)
    except Exception:
        return {}
    if div is None or div.empty:
        return {}
    div = div[div["ex_date"].notna()]
    div["ex_date"] = div["ex_date"].astype(str)
    div = div[(div["ex_date"] >= start) & (div["ex_date"] <= end)]
    out: dict[str, float] = {}
    for _, r in div.iterrows():
        ex = str(r["ex_date"])
        cash = float(r["cash_div"]) if pd.notna(r["cash_div"]) else 0.0
        if cash > 0:
            out[ex] = out.get(ex, 0.0) + cash
    return out


def year_end(df: pd.DataFrame, year: int) -> dict | None:
    sub = df[df["trade_date"] <= f"{year}1231"]
    if sub.empty:
        return None
    r = sub.iloc[-1]
    return {
        "date": r["trade_date"],
        "close": float(r["close"]),
        "pe": float(r["pe_ttm"]) if pd.notna(r["pe_ttm"]) else None,
        "pb": float(r["pb"]) if pd.notna(r["pb"]) else None,
        "dv": float(r["dv_ttm"]) if pd.notna(r["dv_ttm"]) else None,
        "mv": float(r["total_mv"]),  # 万元
    }


def pct_rank(hist: list[float], current: float) -> float:
    vals = [v for v in hist if v is not None]
    if not vals:
        return float("nan")
    below = sum(1 for v in vals if v <= current)
    return below / len(vals) * 100.0


def combo_metrics(per_stock: dict) -> dict:
    """给定 {code: {pe,pb,dv,roe,np_yoy}} 按仓位加权（25/25/50）计算组合级指标"""
    w_sum = 0.0
    inv_pe = 0.0   # Σ W/PE（调和）
    inv_pb = 0.0   # Σ W/PB（调和）
    dv_w = 0.0     # Σ dv*W（加权均值）
    roe_w = 0.0
    npy_w = 0.0
    w_roe = 0.0
    w_npy = 0.0
    for code, s in per_stock.items():
        w = WEIGHTS.get(code, 0.0)
        w_sum += w
        if s["pe"] and s["pe"] > 0:
            inv_pe += w / s["pe"]
        if s["pb"] and s["pb"] > 0:
            inv_pb += w / s["pb"]
        if s["dv"] is not None:
            dv_w += s["dv"] * w
        if s.get("roe") is not None:
            roe_w += s["roe"] * w
            w_roe += w
        if s.get("np_yoy") is not None:
            npy_w += s["np_yoy"] * w
            w_npy += w
    pe = w_sum / inv_pe if inv_pe > 0 else None
    pb = w_sum / inv_pb if inv_pb > 0 else None
    dv = dv_w / w_sum if w_sum > 0 else None
    roe = roe_w / w_roe if w_roe > 0 else None
    npy = npy_w / w_npy if w_npy > 0 else None
    peg = (pe / npy) if (pe and npy and npy > 0) else None
    return {"pe": pe, "pb": pb, "dv": dv, "roe": roe, "np_yoy": npy, "peg": peg}


def main():
    pro = init_tushare()
    start = "20151230"
    end = datetime.now().strftime("%Y%m%d")

    print(f"\n{'#' * 90}")
    print(f"  # 电力+神华组合 年度估值/质量水平（PE/PB/股息率/ROE/净利增速/PEG）")
    print(f"  # 组合: 华能25% + 国电25% + 神华50%（仓位加权）")
    print(f"  # 区间: 2016 ~ 2025 年末，对照今天 {end}")
    print(f"{'#' * 90}")

    data = {}
    for code, name in STOCKS.items():
        df = fetch_daily_basic(pro, code, start, end)
        div = fetch_div(pro, code, start, end)
        fina = fetch_fina(pro, code)
        data[code] = {"df": df, "div": div, "fina": fina, "name": name}
        print(f"  📡 {name}({code}): {len(df)} 交易日, 年报年份 {sorted(fina.keys())}")

    years = list(range(2016, 2026))

    # ── 逐年组合指标 ──
    print(f"\n  ── 逐年组合级指标（年末值，仓位加权 25/25/50） ──")
    hdr = (f"  {'年份':>5} | {'PE':>6} | {'PB':>5} | {'股息率%':>7} | "
           f"{'ROE%':>6} | {'净利增速%':>9} | {'PEG':>6}")
    print("  " + "-" * (len(hdr) + 2))
    print(hdr)
    print("  " + "-" * (len(hdr) + 2))

    combo_pe_hist, combo_pb_hist, combo_dv_hist = [], [], []
    for y in years:
        per = {}
        for code in STOCKS:
            s = year_end(data[code]["df"], y)
            f = data[code]["fina"].get(str(y), {})
            s["roe"] = f.get("roe")
            s["np_yoy"] = f.get("np_yoy")
            per[code] = s
        m = combo_metrics(per)
        combo_pe_hist.append(m["pe"])
        combo_pb_hist.append(m["pb"])
        combo_dv_hist.append(m["dv"])
        pe_s = f"{m['pe']:.1f}" if m["pe"] else "-"
        pb_s = f"{m['pb']:.2f}" if m["pb"] else "-"
        dv_s = f"{m['dv']:.2f}" if m["dv"] is not None else "-"
        roe_s = f"{m['roe']:.1f}" if m["roe"] is not None else "-"
        npy_s = f"{m['np_yoy']:+.1f}" if m["np_yoy"] is not None else "-"
        peg_s = f"{m['peg']:.2f}" if m["peg"] else "-"
        print(f"  {y:>5} | {pe_s:>6} | {pb_s:>5} | {dv_s:>7} | "
              f"{roe_s:>6} | {npy_s:>9} | {peg_s:>6}")

    # ── 当前（今天） ──
    print(f"\n  ── 当前（今天 {end}） ──")
    per_now = {}

    def _last_valid(df, col):
        sub = df[df[col].notna()]
        return float(sub.iloc[-1][col]) if not sub.empty else None

    for code in STOCKS:
        df = data[code]["df"]
        r = df.iloc[-1]
        close = float(r["close"])
        # 最新年报 ROE / 净利增速
        fkeys = sorted(data[code]["fina"].keys())
        f = data[code]["fina"].get(fkeys[-1], {}) if fkeys else {}
        # 股息率：最新完整财年（FY2025）每股分红合计 ÷ 当前股价
        # 注：不用 dv_ttm（易遗漏近除权/口径偏差）也不用机械12月除权切片（会切掉已公告未除权）
        fy_div = pro.dividend(ts_code=code)
        if fy_div is not None and not fy_div.empty:
            fy_div = fy_div[fy_div["end_date"].notna()]
            fy_div["end_date"] = fy_div["end_date"].astype(str)
            fy_2025 = fy_div[fy_div["end_date"].isin(["20250630", "20251231"])]
            dps_fy2025 = sum(float(r2["cash_div"]) for _, r2 in fy_2025.iterrows() if pd.notna(r2["cash_div"]))
        else:
            dps_fy2025 = 0.0
        dv_manual = (dps_fy2025 / close * 100) if close and dps_fy2025 else None
        s = {
            "pe": _last_valid(df, "pe_ttm"),
            "pb": _last_valid(df, "pb"),
            "dv": dv_manual,
            "roe": f.get("roe"),
            "np_yoy": f.get("np_yoy"),
        }
        per_now[code] = s
        _pe = f"{s['pe']:.1f}" if s["pe"] else "-"
        _pb = f"{s['pb']:.2f}" if s["pb"] else "-"
        _dv = f"{s['dv']:.2f}" if s["dv"] is not None else "-"
        _roe = f"{s['roe']:.1f}" if s["roe"] is not None else "-"
        print(f"    · {data[code]['name']}: 价{float(r['close']):.2f} "
              f"PE{_pe} PB{_pb} 股息{_dv}% ROE{_roe}%")
    mnow = combo_metrics(per_now)
    print(f"    ⇒ 组合PE={mnow['pe']:.2f}  PB={mnow['pb']:.2f}  "
          f"股息率={mnow['dv']:.2f}%  ROE={mnow['roe']:.1f}%  "
          f"净利增速={mnow['np_yoy']:+.1f}%  PEG={mnow['peg'] if mnow['peg'] else '-'}")

    # ── 分位判断 ──
    print(f"\n  ── 当前组合处于历史什么水平（对照 2016-2025 年末，{len(years)} 个样本） ──")
    pe_rank = pct_rank(combo_pe_hist, mnow["pe"])
    pb_rank = pct_rank(combo_pb_hist, mnow["pb"])
    dv_rank = pct_rank(combo_dv_hist, mnow["dv"])
    _pe = [v for v in combo_pe_hist if v]
    _pb = [v for v in combo_pb_hist if v]
    _dv = [v for v in combo_dv_hist if v is not None]
    print(f"    PE : 历史 {min(_pe):.1f}~{max(_pe):.1f}（均值{sum(_pe)/len(_pe):.1f}）"
          f"，当前 {mnow['pe']:.1f} → 分位 {pe_rank:.0f}%（越低越便宜）")
    print(f"    PB : 历史 {min(_pb):.2f}~{max(_pb):.2f}（均值{sum(_pb)/len(_pb):.2f}）"
          f"，当前 {mnow['pb']:.2f} → 分位 {pb_rank:.0f}%（越低越便宜）")
    print(f"    股息率: 历史 {min(_dv):.2f}%~{max(_dv):.2f}%（均值{sum(_dv)/len(_dv):.2f}%）"
          f"，当前 {mnow['dv']:.2f}% → 分位 {dv_rank:.0f}%（越高越便宜）")

    print(f"\n  📌 结论：")
    def _lvl(rank, cheap_high=False):
        r = (100 - rank) if cheap_high else rank
        if r <= 33:
            return "历史低位（便宜）"
        if r >= 67:
            return "历史高位（偏贵）"
        return "历史中部"
    print(f"    · PE {mnow['pe']:.1f}x → {_lvl(pe_rank)}")
    print(f"    · PB {mnow['pb']:.2f}x → {_lvl(pb_rank)}")
    print(f"    · 股息率 {mnow['dv']:.2f}% → {_lvl(dv_rank, cheap_high=True)}")

    print(f"\n{'#' * 90}")
    print("  注：ROE/净利增速为年报口径（当年年报，次年披露）；PEG=组合PE/组合净利增速，")
    print("      净利增速为负时 PEG 无意义（显示 -）。估值为税前近似，未计交易成本。")


if __name__ == "__main__":
    main()
