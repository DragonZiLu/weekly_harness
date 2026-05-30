"""
Validator — 数据质量校验器
============================
对应 Harness 框架中的 Evaluator 角色：
  - 接收 Generator 输出的 raw_scores
  - 根据 criteria.json 的规则校验每只股票的数据质量
  - 标注置信度（high / medium / low）
  - 输出 validation_report.json（Validator → Reporter 的 artifact）

职责边界：只评判"数据可信度"，不修改数据，不做投资建议。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ─── 路径设置 ─────────────────────────────────────────────────
_HARNESS_DIR = Path(__file__).parent
_PROJECT_ROOT = _HARNESS_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))


class WeeklyValidator:
    """
    数据质量校验器

    校验 Generator 产出的 raw_scores，输出 validation_report.json：
    {
        "week": "2026-W21",
        "timestamp": "...",
        "alerts": [ {ts_code, field, value, issue, confidence_impact} ],
        "confidence": { ts_code: "high" | "medium" | "low" },
        "confidence_summary": { "high": 10, "medium": 3, "low": 2 }
    }
    """

    # 数据合理范围（来自 criteria.json，这里内联一份默认值）
    _RANGE_RULES = {
        "div_yield":  (0.5, 15.0),
        "pe_ttm":     (2.0, 60.0),
        "roe":        (0.0, 60.0),
        "close":      (0.5, 5000.0),
        "bond_spread_bp": (-200.0, 1000.0),
    }

    # 已知可能出现 dv_ttm 偏差的季节（年报分红公告期）
    # 通常3月-7月是A股年报分红公告集中期，此时 tushare TTM 可能偏低
    _DIV_YIELD_CALIBRATION_KEYWORDS = [
        "div校准", "fallback"
    ]

    def __init__(self, criteria_path: Optional[Path] = None):
        self.criteria_path = criteria_path or (_HARNESS_DIR / "criteria.json")
        self.criteria = self._load_criteria()

    def _load_criteria(self) -> Dict:
        try:
            with open(self.criteria_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _check_range(self, ts_code: str, name: str, field: str, value: float) -> Optional[Dict]:
        """检查字段值是否在合理范围内"""
        lo, hi = self._RANGE_RULES.get(field, (None, None))
        if lo is None:
            return None
        if not (lo <= value <= hi):
            return {
                "ts_code": ts_code,
                "name": name,
                "field": field,
                "value": value,
                "expected_range": [lo, hi],
                "issue": f"{field}={value:.2f} 超出合理范围 [{lo}, {hi}]",
                "severity": "warning",
            }
        return None

    def _check_source_calibration(self, ts_code: str, name: str, source: str) -> Optional[Dict]:
        """检测是否使用了 fallback 或触发了 div 校准"""
        if "fallback" in source.lower():
            return {
                "ts_code": ts_code,
                "name": name,
                "field": "source",
                "value": source,
                "issue": "使用了 fallback 数据（tushare 未能获取）",
                "severity": "info",
            }
        if any(kw in source for kw in self._DIV_YIELD_CALIBRATION_KEYWORDS):
            return {
                "ts_code": ts_code,
                "name": name,
                "field": "div_yield",
                "value": source,
                "issue": f"股息率校准已触发: {source}",
                "severity": "info",
            }
        return None

    def _check_fallback_staleness(self, ts_code: str, name: str, score: Dict) -> Optional[Dict]:
        """
        L3: Fallback 数据新鲜度检测

        如果 fallback 里的股价与当前实时股价偏差 > 20%，说明 fallback 数据
        已过时，div_yield / buyback_yield 等依赖股价的字段可能不准确。
        """
        close_live = score.get("close")
        close_fb   = score.get("_close_fallback")
        if not close_live or not close_fb or close_fb <= 0:
            return None
        drift = abs(close_live - close_fb) / close_fb
        if drift > 0.20:
            return {
                "ts_code": ts_code,
                "name": name,
                "field": "close_fallback",
                "value": close_fb,
                "issue": (
                    f"fallback股价{close_fb:.2f}元 vs 当前{close_live:.2f}元，"
                    f"偏移{drift*100:.0f}%（>20%），fallback数据可能已过时，"
                    f"建议更新 FALLBACK_DATA"
                ),
                "severity": "warning",
            }
        return None

    def _check_cross_source(self, ts_code: str, name: str, score: Dict) -> List[Dict]:
        """
        L2: 三源交叉验证

        对比 dividend接口自算 / tushare dv_ttm / fallback 三个来源的股息率，
        若任意两者偏差 > 1.5个百分点，标注原因分析。

        偏差原因分类（智能诊断）：
          [A] TTM窗口滚动型  — dv_ttm偏低，自算与fallback接近 → 可解释，无需人工复核
          [B] dv_ttm含特别分红 — dv_ttm偏高，自算与fallback接近 → 可解释
          [C] fallback已过时  — 自算>fallback 20%+ → 需更新fallback
          [D] 真实矛盾       — 无法归类 → 需人工复核
        """
        sources = {
            "自算": score.get("_div_self_calc"),
            "dv_ttm": score.get("_div_dv_ttm"),
            "fallback": score.get("_div_fallback"),
        }
        valid = {k: v for k, v in sources.items() if v and v > 0.3}
        if len(valid) < 2:
            return []

        vals = list(valid.values())
        spread = max(vals) - min(vals)
        alerts = []
        if spread > 1.5:
            detail = "  /  ".join(f"{k}={v:.2f}%" for k, v in valid.items())
            self_calc = valid.get("自算", 0)
            fb        = valid.get("fallback", 0)
            dv        = valid.get("dv_ttm", 0)

            # [A] TTM滚动型：dv_ttm远低于自算，但自算与fallback接近
            #   原因：年报除权日（通常4~5月）刚好滚出TTM窗口，只剩中期一次分红
            if (dv > 0 and self_calc > 0 and dv < self_calc * 0.6
                    and (fb <= 0 or abs(self_calc - fb) / max(self_calc, 0.01) < 0.25)):
                reason = (
                    "dv_ttm因TTM窗口滚动偏低（年报除权日滚出窗口，TTM仅含中期1次分红），"
                    "自算含预案结果可信，无需人工复核"
                )
                severity = "info"  # 降级为info，不计入低置信
            # [B] dv_ttm含特别/重复历史分红，偏高失真
            elif dv > 0 and self_calc > 0 and dv > self_calc * 1.3 and (fb <= 0 or dv > fb * 1.1):
                reason = "dv_ttm含历史跨年重叠分红（TTM窗口同时含上一年年报+本年中期），偏高失真；自算结果更准确"
                severity = "info"
            # [C] 年报分红预案尚未录入（自算偏低 vs fallback）
            elif fb > 0 and self_calc > 0 and self_calc < fb * 0.7:
                reason = "年报分红预案/公告可能尚未录入tushare，自算偏低，已用fallback兜底"
                severity = "info"
            # [D-1] 自算显著高于fallback，fallback过时
            elif fb > 0 and self_calc > 0 and self_calc > fb * 1.2:
                reason = "自算高于fallback 20%+，fallback数据可能已过时，建议更新FALLBACK_DATA"
                severity = "warning"
            # [D-2] 真实矛盾，无法归类
            else:
                reason = "来源矛盾，建议人工复核"
                severity = "warning"

            alerts.append({
                "ts_code": ts_code,
                "name": name,
                "field": "div_yield",
                "value": spread,
                "issue": f"三源股息率偏差{spread:.2f}%: {detail}  →  {reason}",
                "severity": severity,
            })
        return alerts

    def _determine_confidence(self, alerts_for_code: List[Dict]) -> str:
        """
        根据该股票的告警列表判断整体数据置信度

        high:   无 warning 级别告警（info 不影响置信度）
        medium: 有 info 级别告警（来自 fallback 使用，数据源降级但可解释）
        low:    有 warning 级别告警（数据超出合理范围，或存在真实来源矛盾）

        注意：三源交叉验证中的 TTM滚动型/dv_ttm含特别分红型 均降级为 info，
              不计入 low 置信度，避免误报。
        """
        severities = {a["severity"] for a in alerts_for_code}
        if "warning" in severities:
            return "low"
        elif "info" in severities:
            # 只有「source fallback」类的info才降为medium
            source_infos = [a for a in alerts_for_code if a["severity"] == "info"
                            and a.get("field") == "source"]
            if source_infos:
                return "medium"
            return "high"
        return "high"

    def _check_score_sanity(self, ts_code: str, name: str, score: Dict, bond_yield: float = 1.65) -> List[Dict]:
        """对评分结果做基本合理性检查"""
        alerts = []
        total = score.get("total_score", 0)
        div_yield = score.get("div_yield", 0)
        bond_spread_bp = score.get("bond_spread_bp", 0)

        # 检查评分与股息率的一致性
        if total >= 80 and div_yield < 2.0:
            alerts.append({
                "ts_code": ts_code,
                "name": name,
                "field": "total_score",
                "value": total,
                "issue": f"评分{total}分较高但股息率仅{div_yield:.1f}%，请人工复查",
                "severity": "warning",
            })

        if total < 30 and div_yield > 6.0:
            alerts.append({
                "ts_code": ts_code,
                "name": name,
                "field": "total_score",
                "value": total,
                "issue": f"股息率{div_yield:.1f}%较高但评分仅{total}分，数据可能异常",
                "severity": "warning",
            })

        # 检查债息差与股息率的一致性
        expected_spread = (div_yield - bond_yield) * 100
        actual_spread = bond_spread_bp
        if abs(expected_spread - actual_spread) > 50:
            alerts.append({
                "ts_code": ts_code,
                "name": name,
                "field": "bond_spread_bp",
                "value": actual_spread,
                "issue": f"债息差({actual_spread:.0f}BP)与预期({expected_spread:.0f}BP)偏差>50BP",
                "severity": "info",
            })

        return alerts

    def run(
        self,
        raw_scores: Dict,
        artifacts_dir: Optional[Path] = None,
    ) -> Dict:
        """
        执行数据质量校验

        Parameters
        ----------
        raw_scores : dict
            来自 Generator 的 raw_scores artifact
        artifacts_dir : Path, optional
            artifact 输出目录

        Returns
        -------
        dict : validation_report artifact
        """
        print("\n" + "─" * 50)
        print("  [Phase 3] Validator — 数据质量校验")
        print("─" * 50)

        week = raw_scores.get("week", "?")
        scores = raw_scores.get("scores", {})
        bond_yield = raw_scores.get("bond_yield_10y", 1.65)

        all_alerts: List[Dict] = []
        confidence: Dict[str, str] = {}

        for ts_code, score in scores.items():
            name = score.get("name", ts_code)
            code_alerts: List[Dict] = []

            # 1. 范围检查
            # ETF 类别跳过 pe_ttm/roe 的范围检查（ETF无个股PE/ROE，持仓加权值可能有偏差）
            category = score.get("category", "")
            skip_fields_for_etf = {"pe_ttm", "roe", "pb"}
            for field in ["div_yield", "pe_ttm", "roe", "close", "bond_spread_bp"]:
                if category == "ETF红利" and field in skip_fields_for_etf:
                    continue
                val = score.get(field)
                if val is not None:
                    alert = self._check_range(ts_code, name, field, float(val))
                    if alert:
                        code_alerts.append(alert)

            # 2. 数据源检查
            source = score.get("source", "")
            src_alert = self._check_source_calibration(ts_code, name, source)
            if src_alert:
                code_alerts.append(src_alert)

            # 3. 评分合理性检查
            sanity_alerts = self._check_score_sanity(ts_code, name, score, bond_yield)
            code_alerts.extend(sanity_alerts)

            # 4. 自验证：fallback 新鲜度检测（P1）
            staleness_alert = self._check_fallback_staleness(ts_code, name, score)
            if staleness_alert:
                code_alerts.append(staleness_alert)

            # 5. 自验证：三源交叉验证（P2）
            cross_alerts = self._check_cross_source(ts_code, name, score)
            code_alerts.extend(cross_alerts)

            # 6. 计算置信度
            confidence[ts_code] = self._determine_confidence(code_alerts)
            all_alerts.extend(code_alerts)

        # 汇总置信度统计
        conf_summary = {"high": 0, "medium": 0, "low": 0}
        for v in confidence.values():
            conf_summary[v] += 1

        # 打印结果
        print(f"  🔍 数据置信度分布：")
        print(f"     ✅ 高置信: {conf_summary['high']} 只")
        print(f"     ⚠️  中置信: {conf_summary['medium']} 只 (使用了fallback或校准)")
        print(f"     ❌ 低置信: {conf_summary['low']} 只 (数据超出合理范围)")

        # 分类打印自验证告警
        cross_warns   = [a for a in all_alerts if a["severity"] == "warning" and "三源" in a.get("issue","")]
        cross_infos   = [a for a in all_alerts if a["severity"] == "info"    and "三源" in a.get("issue","")]
        stale_warns   = [a for a in all_alerts if a["severity"] == "warning" and "fallback股价" in a.get("issue","")]
        other_warns   = [a for a in all_alerts if a["severity"] == "warning"
                         and a not in cross_warns and a not in stale_warns]

        if stale_warns:
            print(f"\n  📅 Fallback 数据过时告警 ({len(stale_warns)} 条):")
            for w in stale_warns:
                print(f"     [{w['name']}] {w['issue']}")

        if cross_warns:
            print(f"\n  🔀 三源矛盾(需人工复核) ({len(cross_warns)} 条):")
            for w in cross_warns:
                print(f"     [{w['name']}] {w['issue']}")

        if cross_infos:
            print(f"\n  ℹ️  三源可解释偏差 ({len(cross_infos)} 条，自动忽略):")
            for w in cross_infos:
                print(f"     [{w['name']}] {w['issue']}")

        if other_warns:
            print(f"\n  🚨 其他告警 ({len(other_warns)} 条):")
            for w in other_warns:
                print(f"     [{w['name']}] {w['issue']}")

        # 汇总自验证统计放入 report
        self_validation = {
            "fallback_stale_count": len(stale_warns),
            "cross_source_conflict_count": len(cross_warns),
            "cross_source_explainable_count": len(cross_infos),
            "stale_stocks": [w["name"] for w in stale_warns],
            "conflict_stocks": [w["name"] for w in cross_warns],
            "explainable_stocks": [w["name"] for w in cross_infos],
        }

        report = {
            "week": week,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "bond_yield_10y": bond_yield,
            "alerts": all_alerts,
            "confidence": confidence,
            "confidence_summary": conf_summary,
            "self_validation": self_validation,
        }

        # 保存 artifact
        if artifacts_dir:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            report_path = artifacts_dir / "validation_report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"  💾 validation_report.json → {report_path}")

        print(f"  ✅ Validator 完成\n")
        return report
