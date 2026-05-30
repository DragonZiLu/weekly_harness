"""
Planner — 每周评估规划器
========================
对应 Harness 框架中的 Planner 角色：
  - 读取 criteria.json 配置
  - 确定本周评估范围（股票列表）
  - 查询当前10年国债利率（tushare / fallback）
  - 输出 weekly_plan.json（Planner → Generator 的 artifact）

职责边界：只管"评估什么、用什么标准"，不管"怎么计算评分"。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import sys
# ─── 路径设置 ─────────────────────────────────────────────────
_HARNESS_DIR = Path(__file__).parent
_PROJECT_ROOT = _HARNESS_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import tushare_cfg

# 默认10年国债收益率（tushare 无法获取时使用）
_DEFAULT_BOND_YIELD = 1.65  # 2026年5月约值

# ─── 导入 COMPANIES 定义（股票列表来源）────────────────────────
from dividend_evaluator import COMPANIES  # noqa: E402


class WeeklyPlanner:
    """
    每周评估规划器

    输出：weekly_plan.json
    {
        "week": "2026-W21",
        "date": "2026-05-22",
        "companies": ["600900.SH", ...],
        "bond_yield_10y": 1.65,
        "evaluation_config": { ... }
    }
    """

    def __init__(self, criteria_path: Optional[Path] = None):
        self.criteria_path = criteria_path or (_HARNESS_DIR / "criteria.json")
        self.criteria = self._load_criteria()

    def _load_criteria(self) -> Dict:
        """加载评估标准配置"""
        try:
            with open(self.criteria_path, encoding="utf-8") as f:
                criteria = json.load(f)
            print(f"  ✅ criteria.json 加载成功 (v{criteria.get('version', '?')})")
            return criteria
        except Exception as e:
            print(f"  ⚠️  criteria.json 加载失败: {e}，使用默认配置")
            return {}

    def _get_bond_yield_tushare(self) -> Optional[float]:
        """
        获取10年国债收益率（多源后备）

        数据源优先级：
          1. tushare yc_cb 接口（国债收益率曲线，需付费账号）
          2. 华尔街见闻 API（免费、公开，当前收益率快照）
        """
        # ── 数据源 1：tushare yc_cb ──
        try:
            import tushare as ts
            ts.set_token(tushare_cfg.token)
            pro = ts.pro_api()

            today = datetime.now().strftime("%Y%m%d")
            from datetime import timedelta
            start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

            try:
                df_yc = pro.yc_cb(
                    ts_code="1000010.IB",
                    start_date=start,
                    end_date=today,
                    fields="ts_code,trade_date,yield"
                )
                if df_yc is not None and not df_yc.empty:
                    latest = df_yc.sort_values("trade_date", ascending=False).iloc[0]
                    yield_val = float(latest["yield"])
                    if 0.5 <= yield_val <= 6.0:
                        print(f"  ✅ 10年国债收益率 (tushare yc_cb): {yield_val:.2f}%")
                        return yield_val
            except Exception:
                pass
        except Exception:
            pass

        # ── 数据源 2：华尔街见闻实时行情 ──
        try:
            import urllib.request
            url = (
                "https://api-ddc-wscn.awtmt.com/market/real"
                "?fields=prod_name,last_px&prod_code=CN10YR.OTC"
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://www.wallstreetcn.com",
            })
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read().decode("utf-8"))
            snapshot = data.get("data", {}).get("snapshot", {})
            cn10yr = snapshot.get("CN10YR.OTC", [])
            if len(cn10yr) >= 2:
                yield_val = float(cn10yr[1])
                if 0.5 <= yield_val <= 6.0:
                    print(f"  ✅ 10年国债收益率 (华尔街见闻): {yield_val:.2f}%")
                    return yield_val
        except Exception:
            pass

        return None

    def get_bond_yield(self) -> float:
        """
        获取10年国债收益率
        优先顺序：tushare → 默认值
        """
        yield_val = self._get_bond_yield_tushare()
        if yield_val:
            return yield_val

        print(f"  ⚠️  无法从 tushare 获取国债收益率，使用默认值 {_DEFAULT_BOND_YIELD}%")
        return _DEFAULT_BOND_YIELD

    def build_company_list(self) -> list:
        """从 COMPANIES 定义提取所有 ts_code 列表"""
        codes = []
        for sector, companies in COMPANIES.items():
            for name, meta in companies.items():
                codes.append(meta["ts_code"])
        return codes

    def run(self, artifacts_dir: Optional[Path] = None, week_override: Optional[str] = None) -> Dict:
        """
        执行规划，生成 weekly_plan.json

        Parameters
        ----------
        week_override : str, optional
            强制使用指定的周次（如 "2026-W21"），用于历史重跑

        Returns
        -------
        dict : weekly_plan artifact
        """
        print("\n" + "─" * 50)
        print("  [Phase 1] Planner — 生成评估规划")
        print("─" * 50)

        now = datetime.now()
        iso_week = week_override if week_override else now.strftime("%G-W%V")
        today_str = now.strftime("%Y-%m-%d")

        # 获取国债收益率
        bond_yield = self.get_bond_yield()

        # 构建评估股票列表
        companies = self.build_company_list()
        print(f"  📋 评估范围: {len(companies)} 只股票")
        print(f"  📅 周期标识: {iso_week}")
        print(f"  📡 10年国债: {bond_yield:.2f}%")

        plan = {
            "week": iso_week,
            "date": today_str,
            "companies": companies,
            "bond_yield_10y": bond_yield,
            "evaluation_config": {
                "data_sources": ["tushare", "fallback"],
                "validation_enabled": True,
                "cross_check_threshold": self.criteria.get(
                    "alert_rules", {}
                ).get("div_yield_ttm_ratio", {}).get("too_high", 1.5),
                "criteria_version": self.criteria.get("version", "1.0"),
            },
        }

        # 保存 artifact
        if artifacts_dir:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            plan_path = artifacts_dir / "weekly_plan.json"
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            print(f"  💾 weekly_plan.json → {plan_path}")

        print(f"  ✅ Planner 完成\n")
        return plan
