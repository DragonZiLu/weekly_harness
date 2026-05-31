"""
run_positions.py — 持仓管理工具
================================

管理你的真实持仓，用于生成基于实际的调仓计划。

使用方法：
  # 查看当前持仓
  python run_positions.py show

  # 添加/更新一只持仓（用股数+成本）
  python run_positions.py add --code 515180.SH --shares 25000 --cost 1.40

  # 添加/更新一只持仓（用金额+成本，自动算股数）
  python run_positions.py add --code 515180.SH --amount 35000 --cost 1.40

  # 更新现金余额
  python run_positions.py cash 50000

  # 删除某只持仓
  python run_positions.py remove --code 515180.SH

  # 重置全部持仓
  python run_positions.py reset

数据存储：data/portfolio_state.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# 路径设置
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from weekly_harness.portfolio import Portfolio

# 持仓状态文件路径
_STATE_FILE = _PROJECT_ROOT / "data" / "portfolio_state.json"

# 股票代码 → 名称映射（与 dividend_evaluator.py 保持同步）
_STOCK_NAMES: Dict[str, str] = {}

def _load_name_map():
    """加载股票名称映射"""
    global _STOCK_NAMES
    if _STOCK_NAMES:
        return
    try:
        from dividend_evaluator import COMPANIES
        for industry_stocks in COMPANIES.values():
            for name, info in industry_stocks.items():
                _STOCK_NAMES[info["ts_code"]] = name
    except Exception:
        pass


def _load_portfolio() -> Optional[Portfolio]:
    """加载持仓状态"""
    if _STATE_FILE.exists():
        try:
            return Portfolio.load_state(str(_STATE_FILE))
        except Exception as e:
            print(f"  ⚠️ 加载持仓文件失败: {e}")
    return None


def _save_portfolio(portfolio: Portfolio):
    """保存持仓状态"""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    portfolio.save_state(str(_STATE_FILE))
    print(f"  💾 已保存 → {_STATE_FILE}")


def _round_lot(shares: int) -> int:
    """取整到整手（100股）"""
    return (shares // 100) * 100


def cmd_show(args):
    """查看当前持仓"""
    _load_name_map()
    portfolio = _load_portfolio()

    if not portfolio or not portfolio.positions:
        print("\n  📭 暂无持仓记录")
        print(f"  使用 'python run_positions.py add --code <代码> --shares <股数> --cost <成本>' 添加持仓\n")
        return

    any_holding = any(p.shares > 0 for p in portfolio.positions.values())
    if not any_holding:
        print(f"\n  💰 现金: {portfolio.cash:,.0f} 元（全部现金，无持仓）\n")
        return

    total_mv = sum(p.shares * p.current_price for p in portfolio.positions.values())
    total_val = portfolio.cash + total_mv
    cost_total = sum(p.shares * p.cost_price for p in portfolio.positions.values())

    print(f"\n{'='*75}")
    print(f"  📊 持仓总览  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*75}")
    print(f"  💰 现金:     {portfolio.cash:>12,.0f} 元")
    print(f"  📦 持仓市值:  {total_mv:>12,.0f} 元")
    print(f"  🏦 总资产:    {total_val:>12,.0f} 元")
    print(f"  💵 总成本:    {cost_total:>12,.0f} 元")
    print(f"  📈 浮动盈亏:  {portfolio.cash + total_mv - cost_total - (portfolio.initial_cash - portfolio.cash + cost_total):>+12,.0f} 元" if False else "")
    print(f"{'='*75}")

    # 持仓明细
    print(f"\n  {'标的':<16} {'代码':<12} {'股数':>8} {'成本':>8} {'现价':>8} {'市值':>12} {'权重':>6} {'盈亏':>8}")
    print(f"  {'-'*78}")

    rows = []
    for code, pos in sorted(portfolio.positions.items(), key=lambda x: -x[1].shares * x[1].current_price):
        if pos.shares <= 0:
            continue
        mv = pos.shares * pos.current_price
        weight = mv / total_val * 100 if total_val > 0 else 0
        profit = (pos.current_price / pos.cost_price - 1) * 100 if pos.cost_price > 0 else 0
        name = _STOCK_NAMES.get(code, pos.name or code)
        rows.append((name, code, pos.shares, pos.cost_price, pos.current_price, mv, weight, profit))

    for name, code, shares, cost, price, mv, weight, profit in rows:
        print(f"  {name:<16} {code:<12} {shares:>8} {cost:>8.3f} {price:>8.3f} {mv:>12,.0f} {weight:>5.1f}% {profit:>+7.1f}%")

    print(f"\n  📝 持仓数: {len(rows)} 只  |  现金占比: {(portfolio.cash / total_val * 100):.1f}%\n")


def cmd_add(args):
    """添加/更新持仓"""
    _load_name_map()
    portfolio = _load_portfolio() or Portfolio()

    code = args.code.upper()
    name = _STOCK_NAMES.get(code, code)

    # 计算股数
    if args.amount is not None:
        shares = _round_lot(int(args.amount / args.cost))
    else:
        shares = args.shares

    if shares <= 0:
        print(f"  ❌ 股数/金额无效")
        return

    # 获取最新价格（尝试从 tushare 获取）
    current_price = args.price if args.price else args.cost

    from weekly_harness.portfolio import Position
    pos = Position(
        ts_code=code,
        name=name,
        category="",
        shares=shares,
        cost_price=args.cost,
        current_price=current_price,
    )

    portfolio.positions[code] = pos
    portfolio.current_date = datetime.now().strftime("%Y-%m-%d")

    # 同步更新初始资金
    total_cost = sum(p.shares * p.cost_price for p in portfolio.positions.values())
    portfolio.initial_cash = portfolio.cash + total_cost

    _save_portfolio(portfolio)

    mv = shares * current_price
    print(f"  ✅ {name} ({code}): {shares}股 × ¥{args.cost:.3f} = ¥{shares * args.cost:,.0f} (市值 ¥{mv:,.0f})")


def cmd_cash(args):
    """更新现金余额"""
    portfolio = _load_portfolio()
    if not portfolio:
        portfolio = Portfolio(initial_cash=args.amount)

    portfolio.cash = args.amount
    total_cost = sum(p.shares * p.cost_price for p in portfolio.positions.values())
    portfolio.initial_cash = portfolio.cash + total_cost
    portfolio.current_date = datetime.now().strftime("%Y-%m-%d")
    _save_portfolio(portfolio)
    print(f"  ✅ 现金余额: ¥{args.amount:,.0f}")


def cmd_remove(args):
    """删除持仓"""
    portfolio = _load_portfolio()
    if not portfolio:
        print("  📭 无持仓记录")
        return

    code = args.code.upper()
    if code in portfolio.positions:
        pos = portfolio.positions.pop(code)
        total_cost = sum(p.shares * p.cost_price for p in portfolio.positions.values())
        portfolio.initial_cash = portfolio.cash + total_cost
        _save_portfolio(portfolio)
        print(f"  🗑️ 已删除: {pos.name} ({code})")
    else:
        print(f"  ⚠️ 未找到持仓: {code}")


def cmd_reset(args):
    """重置持仓"""
    if not args.force:
        confirm = input("  ⚠️ 确认清空所有持仓? (y/N): ")
        if confirm.lower() != "y":
            print("  已取消")
            return

    portfolio = Portfolio()
    _save_portfolio(portfolio)
    print("  ✅ 持仓已重置")


def main():
    parser = argparse.ArgumentParser(
        description="红利投资系统 — 持仓管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 查看持仓
  python run_positions.py show

  # 添加易方达红利ETF 25000股，成本1.40
  python run_positions.py add --code 515180.SH --shares 25000 --cost 1.40

  # 用金额添加（自动算股数，取整到整手）
  python run_positions.py add --code 601318.SH --amount 50000 --cost 56.95

  # 更新现金
  python run_positions.py cash 200000

  # 删除某只持仓
  python run_positions.py remove --code 515180.SH

  # 重置
  python run_positions.py reset --force
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # show
    p_show = subparsers.add_parser("show", help="查看当前持仓")

    # add
    p_add = subparsers.add_parser("add", help="添加/更新持仓")
    p_add.add_argument("--code", required=True, help="股票代码 (如 515180.SH, 600036.SH)")
    p_add.add_argument("--shares", type=int, default=0, help="股数")
    p_add.add_argument("--amount", type=float, default=None, help="投入金额（自动算股数）")
    p_add.add_argument("--cost", type=float, required=True, help="成本价")
    p_add.add_argument("--price", type=float, default=None, help="当前价格（默认取成本）")

    # cash
    p_cash = subparsers.add_parser("cash", help="更新现金余额")
    p_cash.add_argument("amount", type=float, help="现金余额")

    # remove
    p_remove = subparsers.add_parser("remove", help="删除持仓")
    p_remove.add_argument("--code", required=True, help="要删除的股票代码")

    # reset
    p_reset = subparsers.add_parser("reset", help="重置全部持仓")
    p_reset.add_argument("--force", action="store_true", help="跳过确认")

    args = parser.parse_args()

    if args.command == "show" or args.command is None:
        cmd_show(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "cash":
        cmd_cash(args)
    elif args.command == "remove":
        cmd_remove(args)
    elif args.command == "reset":
        cmd_reset(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
