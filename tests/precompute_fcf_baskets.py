"""Step 1 (v2): 按正确调仓日预计算 FCF 选股篮子。

调仓日 = 3/6/9/12月第二个星期五的下一交易日
"""
import sys, json, argparse
from pathlib import Path
from collections import OrderedDict
from datetime import datetime, timedelta

_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from weekly_harness.fcf_universe import FcfUniverse


def get_rebalance_dates(start_ym: str, end_ym: str) -> list:
    """生成 3/6/9/12 月第二个周五的下一交易日"""
    dates = []
    start_dt = datetime.strptime(start_ym[:7] + "-01", "%Y-%m-%d")
    end_dt = datetime.strptime(end_ym[:7] + "-01", "%Y-%m-%d")

    y, m = start_dt.year, start_dt.month
    while True:
        for rm in [3, 6, 9, 12]:
            d = datetime(y, rm, 1)
            if d < start_dt:
                continue
            if d > end_dt:
                return dates
            # 第二个周五 → 下一交易日（周一）
            days_to_fri = (4 - d.weekday()) % 7
            first_fri = d + timedelta(days=days_to_fri)
            second_fri = first_fri + timedelta(days=7)
            next_trade = second_fri + timedelta(days=3)  # 周五→周一
            dates.append(next_trade.strftime("%Y-%m-%d"))
        y += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="000300.SH")
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument("--ttm", action="store_true", default=False)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    suffix = "300" if "300" in args.index else args.index.replace(".", "_")
    mode = "ttm" if args.ttm else "annual"
    out = args.output or str(_PROJ / "data" / f"fcf_baskets_{suffix}_top{args.top_n}_{mode}.json")

    print(f"样本空间: {args.index}  Top {args.top_n}  口径: {mode}")
    print(f"输出: {out}")

    uni = FcfUniverse(index_code=args.index)
    uni.preload_all(download=False)

    dates = get_rebalance_dates(args.start, args.end)
    print(f"调仓日: {len(dates)} 个")

    results = OrderedDict()
    for i, date_str in enumerate(dates):
        print(f"  [{i+1}/{len(dates)}] {date_str} ...", end=" ", flush=True)
        try:
            basket = uni.get_fcf_basket(date_str, top_n=args.top_n, use_ttm=args.ttm, verbose=False)
            stocks = {}
            for code, meta in basket.items():
                if code == "__quality_warnings__":
                    continue
                stocks[code] = {
                    "name": meta.get("name", ""),
                    "weight": round(meta.get("weight", 0), 6),
                    "fcf": meta.get("fcf"),
                    "fcf_yield": meta.get("fcf_yield"),
                    "ev": meta.get("ev"),
                    "industry": meta.get("industry", ""),
                }
            results[date_str] = stocks
            n = len(stocks)
            print(f"{n} 只" + ("" if n >= args.top_n else f" ⚠️"))
        except Exception as e:
            print(f"❌ ERROR: {e}")

    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n完成: {len(results)} 期, 保存至 {out}")

    # 摘要
    total_set = set()
    empty = 0
    for d, s in results.items():
        total_set |= set(s.keys())
        if len(s) < args.top_n // 2:
            empty += 1
    print(f"去重标的: {len(total_set)}  有效期数: {len(results)-empty}/{len(results)}")

    years_seen = set(d[:4] for d in results)
    print(f"覆盖年份: {sorted(years_seen)}")

    if results:
        first = list(results.keys())[0]
        print(f"\n首期({first}) Top5:")
        items = sorted(results[first].items(), key=lambda x: -x[1]["weight"])
        for code, meta in items[:5]:
            fy = meta.get("fcf_yield", 0) or 0
            print(f"  {code} {meta['name']}: w={meta['weight']*100:.1f}% FCF率={fy*100:.1f}%")


if __name__ == "__main__":
    main()
