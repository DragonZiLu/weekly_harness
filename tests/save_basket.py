"""Step 1: save basket to file (runs get_fcf_basket once)"""
import sys, json
from pathlib import Path
_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))
from weekly_harness.fcf_universe import FcfUniverse

uni = FcfUniverse()
uni.preload_all(download=False)
basket = uni.get_fcf_basket("2026-03-20", top_n=100, verbose=False)

# Convert to serializable
out = {}
for k, v in basket.items():
    if k == "__quality_warnings__":
        continue
    out[k] = {kk: vv for kk, vv in v.items()}

with open(_PROJ / "data" / "fcf_financials" / "basket_20260320.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"✅ Saved {len(out)} stocks to basket_20260320.json")
