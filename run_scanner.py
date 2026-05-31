"""
run_scanner.py — A股红利潜力扫描入口
======================================

使用方法:
  # 全市场扫描（Top 50）
  python run_scanner.py

  # 自定义 Top N
  python run_scanner.py --top 100

  # 导出 JSON
  python run_scanner.py --output data/scanner_results.json

  # 与现有池对比（只展示新增候选）
  python run_scanner.py --compare

  # 完整扫描 + 对比 + 导出
  python run_scanner.py --top 50 --compare --output data/scanner_results.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.scanner import DividendScanner


def main():
    parser = argparse.ArgumentParser(
        description="A股红利潜力扫描器 — 全市场挖掘持续分红标的",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_scanner.py                      # 默认 Top 50
  python run_scanner.py --top 30 --compare   # Top 30 + 与现有池对比
  python run_scanner.py --output data/my_scan.json
        """,
    )
    parser.add_argument("--top", type=int, default=50, help="返回 Top N 候选 (默认: 50)")
    parser.add_argument("--output", type=str, default="", help="导出路径 (.json 或 .csv)")
    parser.add_argument("--compare", action="store_true", help="与当前评估池对比，展示新增标的")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  🔍 A股红利潜力扫描器")
    print(f"  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    scanner = DividendScanner(verbose=True)
    candidates = scanner.scan(top_n=args.top)

    if not candidates:
        print("\n⚠️ 未发现符合条件的候选标的")
        return

    # 打印报告
    scanner.print_report(candidates, top_n=min(args.top, len(candidates)))

    # 与现有池对比
    if args.compare:
        new_candidates, _ = scanner.compare_with_existing(candidates)
        if new_candidates:
            print(f"\n  ── 🆕 未在评估池中的候选 (Top {min(10, len(new_candidates))}) ──")
            print(f"  {'名称':<8} {'代码':<12} {'行业':<10} {'总分':>4} {'股息率':>6} {'类别'}")
            print("  " + "-" * 60)
            for c in new_candidates[:10]:
                print(
                    f"  {c.name:<6} {c.ts_code:<12} {c.industry:<8} "
                    f"{c.total_score:>4.0f} {c.current_div_yield:>5.1f}% {c.category}"
                )

    # 导出
    if args.output:
        scanner.export(candidates, args.output)
    else:
        # 默认导出
        default_path = _PROJECT_ROOT / "data" / "scanner_results.json"
        scanner.export(candidates, str(default_path))

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
