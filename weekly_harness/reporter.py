"""
Reporter — 周报生成器
======================
对应 Harness 框架中的输出汇总角色：
  - 读取 raw_scores + validation_report
  - 与历史数据对比，检测评分变化和信号变化
  - 生成本周周报（Markdown + 可视化图表）
  - 追加本周数据到 weekly_history.csv（历史时间序列）
  - 输出 signals.json（信号变化告警）

职责边界：只管"产出可读报告和历史存档"，评分逻辑在 Generator，数据质量在 Validator。
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib_cache")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import font_manager
import warnings
warnings.filterwarnings("ignore")

# ─── 路径设置 ─────────────────────────────────────────────────
_HARNESS_DIR = Path(__file__).parent
_PROJECT_ROOT = _HARNESS_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import tushare_cfg
from dividend_evaluator import HEDGE_PAIRS  # noqa: E402

# 中文字体
def _setup_font():
    fonts = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ]
    for fp in fonts:
        if Path(fp).exists():
            font_manager.fontManager.addfont(fp)
            prop = font_manager.FontProperties(fname=fp)
            plt.rcParams["font.family"] = prop.get_name()
            plt.rcParams["axes.unicode_minus"] = False
            return
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

_setup_font()

# 颜色方案
COLORS_CAT = {
    "弱周期红利":  "#4ecdc4",
    "消费成长红利": "#f9ca24",
    "周期资源红利": "#e17055",
}


class WeeklyReporter:
    """
    周报生成器

    从 weekly_history.csv 读取历史，对比本周评分，
    生成 Markdown 周报、可视化图表和信号告警 JSON。
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or (_PROJECT_ROOT / "data")
        self.history_csv = self.data_dir / "weekly_history.csv"
        self.weekly_reports_dir = self.data_dir / "weekly_reports"
        self.weekly_reports_dir.mkdir(parents=True, exist_ok=True)

    # ─── 历史数据管理 ─────────────────────────────────────────

    def _get_dividend_history_table(self, ts_code: str, bond_yield: float, lookback_years: int = 10) -> Optional[str]:
        """
        获取单只股票近N年分红+年内最高最低价+股息率对比表

        当年度仅有中期分红时，基于上年中期/全年比例推算预期分红(标预)
        """
        try:
            import tushare as ts
            ts.set_token(tushare_cfg.token)
            pro = ts.pro_api()
        except Exception:
            return None

        try:
            # 1. 分红历史
            df_div = pro.dividend(ts_code=ts_code, fields='ts_code,end_date,cash_div')
            if df_div is None or df_div.empty:
                return None
            df_div = df_div.sort_values('end_date')
            df_div = df_div[df_div['cash_div'].astype(float) > 0]
            df_div['year'] = df_div['end_date'].astype(str).str[:4]

            # 区分中期分红(end_date非1231)和年度分红
            end_suffix = df_div['end_date'].astype(str).str[4:8]
            df_div['is_interim'] = ~end_suffix.isin(['1231'])

            # 每年总DPS
            annual_dps = df_div.groupby('year')['cash_div'].sum().reset_index()
            annual_dps.columns = ['year', 'total_dps']

            # 每年中期DPS
            interim_dps = df_div[df_div['is_interim']].groupby('year')['cash_div'].sum().reset_index()
            interim_dps.columns = ['year', 'interim_dps']

            annual_dps = pd.merge(annual_dps, interim_dps, on='year', how='left')
            annual_dps['interim_dps'] = annual_dps['interim_dps'].fillna(0)

            annual_dps = annual_dps.sort_values('year')

            # 只取近N年
            cutoff_year = str(datetime.now().year - lookback_years)
            annual_dps = annual_dps[annual_dps['year'] >= cutoff_year]

            if annual_dps.empty:
                return None

            # 2. 当年预期分红推算
            current_year = str(datetime.now().year)
            is_estimated = {}  # year -> bool

            if current_year in annual_dps['year'].values:
                curr_idx = annual_dps[annual_dps['year'] == current_year].index[0]
                curr_total = float(annual_dps.loc[curr_idx, 'total_dps'])
                curr_interim = float(annual_dps.loc[curr_idx, 'interim_dps'])

                prev_year = str(int(current_year) - 1)
                prev_rows = annual_dps[annual_dps['year'] == prev_year]

                if not prev_rows.empty:
                    prev_total = float(prev_rows.iloc[0]['total_dps'])
                    prev_interim = float(prev_rows.iloc[0]['interim_dps'])

                    # 当年DPS明显偏低（不到上年60%），视为仅有中期分红
                    if curr_total < prev_total * 0.6:
                        if curr_interim > 0 and prev_interim > 0:
                            # 按上年中期/全年比例推算
                            estimated = curr_interim * (prev_total / prev_interim)
                        else:
                            # 无中期数据，直接沿用上年全年DPS
                            estimated = prev_total

                        annual_dps.loc[curr_idx, 'total_dps'] = round(estimated, 2)
                        is_estimated[current_year] = True

            # 3. 每日行情（年内最高最低）
            start_date = f"{cutoff_year}0101"
            end_date = datetime.now().strftime("%Y%m%d")
            df_daily = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date,
                                 fields='ts_code,trade_date,close,high,low')
            if df_daily is None or df_daily.empty:
                return None

            time.sleep(0.3)  # 限流

            df_daily = df_daily.sort_values('trade_date')
            df_daily['year'] = df_daily['trade_date'].astype(str).str[:4]
            yearly_hl = df_daily.groupby('year').agg(
                year_high=('high', 'max'),
                year_low=('low', 'min'),
            ).reset_index()

            # 4. 合并
            merged = pd.merge(annual_dps[['year', 'total_dps']], yearly_hl, on='year', how='inner')
            merged = merged.sort_values('year')

            if merged.empty:
                return None

            # 5. 生成表格
            lines = []
            lines.append("| 年度 | 分红(元) | 年内最高 | 年内最低 | 最高股息率 | 最低股息率 | 国债收益率 | 最高息差(BP) |")
            lines.append("|------|---------|---------|---------|-----------|-----------|-----------|-------------|")

            for _, row in merged.iterrows():
                y = row['year']
                dps = float(row['total_dps'])
                yh = float(row['year_high'])
                yl = float(row['year_low'])

                if yh <= 0 or yl <= 0 or dps <= 0:
                    continue

                max_dy = dps / yl * 100   # 最低价 → 最高股息率
                min_dy = dps / yh * 100   # 最高价 → 最低股息率
                max_spread = (max_dy - bond_yield) * 100

                # 标注
                markers = []
                if y == current_year:
                    markers.append("YTD")
                if is_estimated.get(y, False):
                    markers.append("预")
                marker_str = f" ({','.join(markers)})" if markers else ""

                lines.append(
                    f"| {y}{marker_str} | {dps:.2f} | {yh:.2f} | {yl:.2f} | "
                    f"{max_dy:.2f}% | {min_dy:.2f}% | {bond_yield:.2f}% | {max_spread:.0f} |"
                )

            if len(lines) <= 1:
                return None

            lines.append(f"\n> * 当前年份数据截至 {datetime.now().strftime('%Y-%m-%d')}")
            lines.append(f"> * 国债收益率统一使用当前值 {bond_yield:.2f}%")
            lines.append(f"> * 最高股息率 = 分红÷年内最低价；最低股息率 = 分红÷年内最高价")
            if is_estimated:
                lines.append(f"> * (预) = 当年仅公布中期分红，全年分红按上年中期/全年比例推算")

            return "\n".join(lines)

        except Exception as e:
            print(f"    ⚠️  {ts_code} 历史对比表生成失败: {e}")
            return None

    def _get_etf_dividend_history_table(self, ts_code: str, bond_yield: float, lookback_years: int = 10) -> Optional[str]:
        """获取 ETF 近N年分红+年内最高最低净值+股息率对比表"""
        try:
            import tushare as ts
            ts.set_token(tushare_cfg.token)
            pro = ts.pro_api()
        except Exception:
            return None

        try:
            # 1. 分红历史（fund_div）
            df_div = pro.fund_div(ts_code=ts_code)
            if df_div is None or df_div.empty:
                return None

            df_div["div_cash"] = pd.to_numeric(df_div["div_cash"], errors="coerce").fillna(0)
            df_div = df_div[df_div["div_cash"] > 0]
            if df_div.empty:
                return None

            df_div["ex_date"] = df_div["ex_date"].astype(str)
            df_div = df_div[df_div["ex_date"].str.len() >= 4]

            # 去重：同一 ex_date 只保留一条（fund_div 可能对同一笔分红存多条记录）
            df_div = df_div.drop_duplicates(subset=["ex_date"], keep="first")

            df_div["year"] = df_div["ex_date"].str[:4]
            df_div = df_div.sort_values("ex_date")

            # 按年汇总分红
            annual_div = df_div.groupby("year")["div_cash"].sum().reset_index()
            annual_div.columns = ["year", "total_div"]

            cutoff_year = str(datetime.now().year - lookback_years)
            annual_div = annual_div[annual_div["year"] >= cutoff_year]
            if annual_div.empty:
                return None

            # 2. 每日净值（年内最高最低）
            start_date = f"{cutoff_year}0101"
            end_date = datetime.now().strftime("%Y%m%d")

            time.sleep(0.3)
            df_daily = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date,
                                      fields='ts_code,trade_date,close,high,low')
            if df_daily is None or df_daily.empty:
                return None

            df_daily = df_daily.sort_values('trade_date')
            df_daily['year'] = df_daily['trade_date'].astype(str).str[:4]
            yearly_hl = df_daily.groupby('year').agg(
                year_high=('high', 'max'),
                year_low=('low', 'min'),
            ).reset_index()

            # 3. 合并
            merged = pd.merge(annual_div, yearly_hl, on='year', how='inner')
            merged = merged.sort_values('year')
            if merged.empty:
                return None

            # 4. 生成表格
            lines = []
            lines.append("| 年度 | 每份分红(元) | 年内最高 | 年内最低 | 最高股息率 | 最低股息率 | 国债收益率 | 最高息差(BP) |")
            lines.append("|------|-------------|---------|---------|-----------|-----------|-----------|-------------|")

            for _, row in merged.iterrows():
                yr = row['year']
                dps = float(row['total_div'])
                yh = float(row['year_high'])
                yl = float(row['year_low'])

                max_div = dps / yl * 100 if yl > 0 else 0
                min_div = dps / yh * 100 if yh > 0 else 0
                max_spread = (max_div - bond_yield) * 100

                lines.append(
                    f"| {yr} | {dps:.4f} | {yh:.3f} | {yl:.3f} | "
                    f"{max_div:.2f}% | {min_div:.2f}% | {bond_yield:.2f}% | {max_spread:.0f} |"
                )

            lines.append(f"\n> * 当前年份数据截至 {datetime.now().strftime('%Y-%m-%d')}")
            lines.append(f"> * 国债收益率统一使用当前值 {bond_yield:.2f}%")
            lines.append(f"> * ETF 分红来自 fund_div 接口，股息率 = 每份分红÷净值")

            return "\n".join(lines)

        except Exception as e:
            print(f"    ⚠️  {ts_code} ETF历史对比表生成失败: {e}")
            return None

    def _load_history(self) -> pd.DataFrame:
        """加载历史周评分数据"""
        if not self.history_csv.exists():
            return pd.DataFrame()
        try:
            df = pd.read_csv(self.history_csv, encoding="utf-8")
            return df
        except Exception as e:
            print(f"  ⚠️  历史数据读取失败: {e}")
            return pd.DataFrame()

    def _append_to_history(self, raw_scores: Dict, validation: Dict, force: bool = False):
        """
        将本周评分追加到 weekly_history.csv

        采用 (week, ts_code) upsert 策略：
        - 同一周同一股票的数据会被覆盖更新
        - 新的股票数据会追加
        - force=True 时整周覆盖（删除旧周全部数据再写入）

        这样在修正 fallback 数据、token 恢复后重新运行时，
        weekly_history.csv 会更新为新值，而非保留旧值。
        """
        week = raw_scores.get("week", "?")
        date = raw_scores.get("timestamp", "")[:10]
        bond_yield = raw_scores.get("bond_yield_10y", 1.65)
        confidence_map = validation.get("confidence", {})

        rows = []
        for ts_code, score in raw_scores.get("scores", {}).items():
            rows.append({
                "week": week,
                "date": date,
                "ts_code": ts_code,
                "name": score.get("name", ""),
                "category": score.get("category", ""),
                "total_score": score.get("total_score", 0),
                "div_yield": score.get("div_yield", 0),
                "eff_yield": score.get("eff_yield", 0),
                "bond_spread_bp": score.get("bond_spread_bp", 0),
                "roe": score.get("roe", 0),
                "close": score.get("close", 0),
                "pe_ttm": score.get("pe_ttm", 0),
                "verdict": score.get("verdict", ""),
                "s1_div": score.get("s1_div", 0),
                "s2_spread": score.get("s2_spread", 0),
                "s3_eff": score.get("s3_eff", 0),
                "s4_certainty": score.get("s4_certainty", 0),
                "s5_growth": score.get("s5_growth", 0),
                "bond_yield_10y": bond_yield,
                "confidence": confidence_map.get(ts_code, "high"),
                "source": score.get("source", ""),
            })

        new_df = pd.DataFrame(rows)

        if self.history_csv.exists():
            existing = pd.read_csv(self.history_csv, encoding="utf-8")

            if force:
                # force 模式：删除该周全部旧数据，写入新数据
                existing = existing[existing["week"] != week]
                combined = pd.concat([existing, new_df], ignore_index=True)
                print(f"  🔄 {week} 数据已覆盖（force模式）")
            elif week in existing["week"].values:
                # upsert 模式：按 (week, ts_code) 更新
                # 删除该周已存在的股票数据，写入新数据
                old_count = len(existing[existing["week"] == week])
                existing = existing[~((existing["week"] == week) & (existing["ts_code"].isin(new_df["ts_code"])))]
                combined = pd.concat([existing, new_df], ignore_index=True)
                new_count = len(new_df)
                print(f"  🔄 {week} 数据已更新（upsert: 更新{old_count}条中的{new_count}只股票）")
            else:
                combined = pd.concat([existing, new_df], ignore_index=True)
                print(f"  💾 {week} 数据已追加")
        else:
            combined = new_df

        combined.to_csv(self.history_csv, index=False, encoding="utf-8")
        print(f"  💾 历史数据 → {self.history_csv} (共 {len(combined)} 条记录)")

    # ─── 信号变化检测 ─────────────────────────────────────────

    def _signal_tier(self, score: float) -> str:
        """将分数映射到信号层级"""
        if score >= 80:
            return "strong_buy"
        elif score >= 65:
            return "buy"
        elif score >= 50:
            return "watch"
        elif score >= 35:
            return "hold"
        return "avoid"

    def _detect_signals(self, raw_scores: Dict, history: pd.DataFrame) -> Dict:
        """
        对比上周数据，检测信号变化

        Returns
        -------
        dict : {
            "new_strong_buy": [...],
            "new_buy": [...],
            "signal_upgrades": [...],
            "signal_downgrades": [...],
            "score_changes": [...],
            "watch_list": [...]
        }
        """
        if history.empty:
            print("  ℹ️  无历史数据，跳过信号对比")
            return {}

        current_week = raw_scores.get("week", "?")
        scores = raw_scores.get("scores", {})

        # 找上一周数据
        weeks_sorted = sorted(history["week"].unique())
        prev_weeks = [w for w in weeks_sorted if w < current_week]
        if not prev_weeks:
            return {}

        prev_week = prev_weeks[-1]
        prev_data = history[history["week"] == prev_week].set_index("ts_code")

        signals = {
            "new_strong_buy": [],     # 本周新进入大胆攒股
            "new_buy": [],            # 本周新进入积极布局
            "signal_upgrades": [],    # 信号层级上升
            "signal_downgrades": [],  # 信号层级下降
            "score_changes": [],      # 评分显著变化（±15分）
            "watch_list": [],         # 当前观察等待的标的
        }

        for ts_code, score in scores.items():
            curr_total = score.get("total_score", 0)
            curr_tier = self._signal_tier(curr_total)
            name = score.get("name", ts_code)
            div_yield = score.get("div_yield", 0)

            if ts_code in prev_data.index:
                prev_total = float(prev_data.loc[ts_code, "total_score"])
                prev_tier = self._signal_tier(prev_total)
                delta = curr_total - prev_total

                # 评分显著变化
                if abs(delta) >= 15:
                    signals["score_changes"].append({
                        "ts_code": ts_code,
                        "name": name,
                        "prev_score": round(prev_total),
                        "curr_score": round(curr_total),
                        "delta": round(delta),
                        "trend": "📈" if delta > 0 else "📉",
                    })

                # 信号层级变化
                tier_order = ["avoid", "hold", "watch", "buy", "strong_buy"]
                prev_idx = tier_order.index(prev_tier)
                curr_idx = tier_order.index(curr_tier)

                if curr_idx > prev_idx:
                    entry = {
                        "ts_code": ts_code,
                        "name": name,
                        "from": prev_tier,
                        "to": curr_tier,
                        "score": round(curr_total),
                        "div_yield": div_yield,
                    }
                    signals["signal_upgrades"].append(entry)
                    if curr_tier == "strong_buy":
                        signals["new_strong_buy"].append(entry)
                    elif curr_tier == "buy":
                        signals["new_buy"].append(entry)
                elif curr_idx < prev_idx:
                    signals["signal_downgrades"].append({
                        "ts_code": ts_code,
                        "name": name,
                        "from": prev_tier,
                        "to": curr_tier,
                        "score": round(curr_total),
                        "div_yield": div_yield,
                    })
            else:
                # 首次出现
                if curr_tier == "strong_buy":
                    signals["new_strong_buy"].append({
                        "ts_code": ts_code, "name": name,
                        "score": round(curr_total), "div_yield": div_yield, "note": "首次评估"
                    })

            # 观察等待列表
            if curr_tier == "watch":
                signals["watch_list"].append({
                    "ts_code": ts_code,
                    "name": name,
                    "score": round(curr_total),
                    "div_yield": div_yield,
                    "verdict": score.get("verdict", ""),
                })

        return signals

    # ─── Markdown 周报 ────────────────────────────────────────

    def _generate_markdown(
        self,
        raw_scores: Dict,
        validation: Dict,
        signals: Dict,
        history: pd.DataFrame,
        week_dir: Path,
        market_signals: Optional[Dict] = None,
    ) -> str:
        """生成 Markdown 周报
        
        Parameters
        ----------
        market_signals : dict, optional
            预获取的市场信号（来自 MarketSignals.get_all_signals()）。
            若不传则自动获取，获取失败时优雅降级（不输出市场环境章节）。
        """
        week = raw_scores.get("week", "?")
        bond_yield = raw_scores.get("bond_yield_10y", 1.65)
        scores = raw_scores.get("scores", {})
        confidence_map = validation.get("confidence", {})
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 按分数排序
        sorted_scores = sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True)

        lines = [
            f"# 🌊 红利周期投资 — 周度评估报告 {week}",
            f"\n> **评估周期**: {week}  ",
            f"> **生成时间**: {now_str}  ",
            f"> **10年国债收益率**: {bond_yield:.2f}%  ",
            f"> **评估股票数量**: {len(scores)} 只  \n",
            "---\n",
        ]

        # ── 市场环境（板块轮动 + 牛熊周期） ──
        try:
            if market_signals is None:
                from weekly_harness.market_signals import MarketSignals
                from config.settings import tushare_cfg
                ms = MarketSignals(tushare_token=tushare_cfg.token)
                market_signals = ms.get_all_signals()
            rotation = market_signals["rotation"]
            bullbear = market_signals["bullbear"]
            position_suggestion = market_signals["position_suggestion"]

            lines.append("## 🌐 市场环境\n")

            # 板块轮动
            style_icons = {"红利": "🔴", "成长": "🟢", "均衡": "⚪"}
            style_icon = style_icons.get(rotation.style, "⚪")
            lines.append(f"### 板块轮动信号\n")
            lines.append(f"| 指标 | 值 |")
            lines.append(f"|------|-----|")
            lines.append(f"| 当前强势风格 | {style_icon} **{rotation.style}** |")
            lines.append(f"| 轮动强度 | {rotation.strength:.0%} |")
            lines.append(f"| 原因 | {rotation.reason} |")
            lines.append(f"| 操作建议 | {rotation.suggestion} |")
            lines.append("")

            # 牛熊周期
            phase_icons = {"牛市": "🐂", "熊市": "🐻", "震荡": "↔️"}
            phase_icon = phase_icons.get(bullbear.phase, "↔️")
            lines.append(f"### 牛熊周期信号\n")
            lines.append(f"| 指标 | 值 |")
            lines.append(f"|------|-----|")
            lines.append(f"| 当前周期 | {phase_icon} **{bullbear.phase}** |")
            lines.append(f"| 信心度 | {bullbear.confidence:.0%} |")
            lines.append(f"| 原因 | {bullbear.reason} |")
            lines.append(f"| 操作建议 | {bullbear.suggestion} |")
            lines.append("")

            # 综合仓位建议
            pos_icons = {"加仓": "🟢", "减仓": "🔴", "谨慎加仓": "🟡", "谨慎减仓": "🟡", "正常": "⚪"}
            pos_icon = pos_icons.get(position_suggestion, "⚪")
            lines.append(f"> 🎯 **综合仓位建议**: {pos_icon} **{position_suggestion}**\n")
            lines.append("---\n")

        except Exception as e:
            lines.append(f"<!-- 市场环境信号获取失败: {e} -->\n")

        # ── 信号摘要 ──
        lines.append("## 🚦 本周信号摘要\n")
        summary = raw_scores.get("summary", {})
        lines.append(f"| 信号级别 | 数量 | 股票 |")
        lines.append(f"|---------|------|------|")
        for tier, label, codes in [
            ("strong_buy", "🔥 大胆攒股", summary.get("strong_buy", [])),
            ("buy",        "✅ 积极布局", summary.get("buy", [])),
            ("watch",      "👀 观察等待", summary.get("watch", [])),
            ("hold",       "⏸️ 暂缓",    summary.get("hold", [])),
            ("avoid",      "🚫 回避",    summary.get("avoid", [])),
        ]:
            names = [scores[c]["name"] for c in codes if c in scores]
            lines.append(f"| {label} | {len(names)} | {', '.join(names) if names else '—'} |")

        # ── 信号变化（与上周对比）──
        if signals:
            lines.append("\n## 📡 信号变化（vs 上周）\n")

            if signals.get("new_strong_buy"):
                lines.append("### 🔥 新进入「大胆攒股」")
                for s in signals["new_strong_buy"]:
                    lines.append(f"- **{s['name']}** — 评分 {s['score']}, 股息率 {s['div_yield']:.1f}%")

            if signals.get("signal_upgrades"):
                lines.append("\n### 📈 信号升级")
                for s in signals["signal_upgrades"]:
                    tier_labels = {
                        "strong_buy": "大胆攒股", "buy": "积极布局",
                        "watch": "观察等待", "hold": "暂缓", "avoid": "回避"
                    }
                    lines.append(
                        f"- **{s['name']}**: {tier_labels.get(s['from'], s['from'])} → "
                        f"{tier_labels.get(s['to'], s['to'])} (评分 {s['score']})"
                    )

            if signals.get("signal_downgrades"):
                lines.append("\n### 📉 信号下调")
                for s in signals["signal_downgrades"]:
                    tier_labels = {
                        "strong_buy": "大胆攒股", "buy": "积极布局",
                        "watch": "观察等待", "hold": "暂缓", "avoid": "回避"
                    }
                    lines.append(
                        f"- **{s['name']}**: {tier_labels.get(s['from'], s['from'])} → "
                        f"{tier_labels.get(s['to'], s['to'])} (评分 {s['score']})"
                    )

            if signals.get("score_changes"):
                lines.append("\n### 🔄 评分显著变化（±15分）")
                for s in signals["score_changes"]:
                    lines.append(
                        f"- {s['trend']} **{s['name']}**: {s['prev_score']} → {s['curr_score']} "
                        f"({'+' if s['delta'] > 0 else ''}{s['delta']})"
                    )

        # ── 综合排行榜 ──
        lines.append("\n---\n\n## 📊 综合排行榜（本周评分）\n")
        lines.append("| 排名 | 企业 | 类别 | 股息率 | 息差BP | 等效分红 | ROE | 总分 | 信号 | 置信度 |")
        lines.append("|------|------|------|--------|--------|---------|-----|------|------|--------|")
        for i, (ts_code, s) in enumerate(sorted_scores, 1):
            conf = confidence_map.get(ts_code, "high")
            conf_icon = "✅" if conf == "high" else "⚠️" if conf == "medium" else "❌"
            cat_short = {"弱周期红利": "弱周期", "消费成长红利": "消费成长", "周期资源红利": "周期资源", "ETF红利": "ETF红利"}.get(
                s["category"], s["category"][:4]
            )
            lines.append(
                f"| {i} | **{s['name']}** | {cat_short} | "
                f"{s['div_yield']:.1f}% | {s['bond_spread_bp']:.0f} | "
                f"{s['eff_yield']:.1f}% | {s['roe']:.1f}% | "
                f"**{s['total_score']:.0f}** | {s['verdict']} | {conf_icon}{conf} |"
            )

        # ── 分类详情 ──
        for cat, cat_label in [
            ("弱周期红利", "第一类：弱周期红利"),
            ("消费成长红利", "第二类：消费/成长红利"),
            ("周期资源红利", "第三类：周期资源红利"),
            ("ETF红利", "第四类：ETF红利"),
        ]:
            cat_items = [(c, s) for c, s in sorted_scores if s["category"] == cat]
            if not cat_items:
                continue

            emoji = {"弱周期红利": "💧", "消费成长红利": "📦", "周期资源红利": "⛏️", "ETF红利": "📈"}.get(cat, "📊")
            lines.append(f"\n---\n\n## {emoji} {cat_label}\n")

            for ts_code, s in cat_items:
                conf = confidence_map.get(ts_code, "high")
                lines.append(f"### {s['name']} ({ts_code}) — {s['verdict']}\n")
                lines.append(f"| 指标 | 值 | 评分详情 |")
                lines.append(f"|------|-----|---------|")

                if cat == "ETF红利":
                    # ETF 专用展示
                    lines.append(f"| 单位净值 | {s['close']:.3f}元 | 来源: {s.get('source', '?')} |")
                    if s.get('pe_ttm', 0) > 0:
                        lines.append(f"| 持仓加权PE | {s['pe_ttm']:.1f}x | 前10大持仓加权 |")
                    lines.append(f"| 股息率 | **{s['div_yield']:.2f}%** | S1={s['s1_div']}/30: {s.get('r1','')} |")
                    lines.append(f"| 股债息差 | **{s['bond_spread_bp']:.0f}BP** | S2={s['s2_spread']}/25: {s.get('r2','')} |")
                    lines.append(f"| 等效分红 | **{s['eff_yield']:.2f}%** | S3={s['s3_eff']}/20: {s.get('r3','')} |")
                    lines.append(f"| 确定性 | {s.get('certainty','')} | S4={s['s4_certainty']}/15: {s.get('r4','')} |")
                    if s.get('roe', 0) > 0:
                        lines.append(f"| 持仓加权ROE | {s['roe']:.1f}% | S5={s['s5_growth']}/10: {s.get('r5','')} |")
                    else:
                        lines.append(f"| 成长性 | — | S5={s['s5_growth']}/10: {s.get('r5','')} |")
                    lines.append(f"| **总分** | **{s['total_score']:.0f}/100** | 置信度: {conf} |")
                else:
                    # 个股展示
                    lines.append(f"| 股价 | {s['close']:.2f}元 | 来源: {s.get('source', '?')} |")
                    lines.append(f"| PE | {s['pe_ttm']:.1f}x | |")
                    lines.append(f"| 股息率 | **{s['div_yield']:.2f}%** | S1={s['s1_div']}/30: {s.get('r1','')} |")
                    if s.get('forward_div_yield', 0) > 0:
                        lines.append(f"| 预期股息率 | **{s['forward_div_yield']:.2f}%** | 下年预测DPS {s.get('forward_dps',0):.2f}元 |")
                    lines.append(f"| 股债息差 | **{s['bond_spread_bp']:.0f}BP** | S2={s['s2_spread']}/25: {s.get('r2','')} |")
                    lines.append(f"| 等效分红 | **{s['eff_yield']:.2f}%** | S3={s['s3_eff']}/20: {s.get('r3','')} |")
                    lines.append(f"| 确定性 | {s.get('certainty','')} | S4={s['s4_certainty']}/15: {s.get('r4','')} |")
                    lines.append(f"| ROE | {s['roe']:.1f}% | S5={s['s5_growth']}/10: {s.get('r5','')} |")
                    lines.append(f"| **总分** | **{s['total_score']:.0f}/100** | 置信度: {conf} |")
                lines.append("")

                # ── 阶梯攒股价格表 ──
                ladder = s.get("ladder", {})
                if ladder and ladder.get("buy", 0) > 0:
                    lines.append("**阶梯攒股价格表**\n")
                    lines.append("| 档位 | 目标价 | 股息率阈值 | 说明 |")
                    lines.append("|------|--------|-----------|------|")
                    close = s["close"]
                    # 标注当前价格所处档位
                    def _mark(price, close):
                        return " ← 当前" if close <= price * 1.01 and close >= price * 0.99 else ""
                    lines.append(f"| 👀 观察 | {ladder['watch']:.2f}元 | — | 股息率到达观察线 |")
                    lines.append(f"| ✅ 买入 | {ladder['buy']:.2f}元 | — | 建仓底仓{_mark(ladder['buy'], close)} |")
                    lines.append(f"| 📈 加仓 | {ladder['add']:.2f}元 | — | 加大仓位{_mark(ladder['add'], close)} |")
                    lines.append(f"| 🔥 满仓 | {ladder['full']:.2f}元 | — | 极佳买点{_mark(ladder['full'], close)} |")
                    if ladder.get("sector_anchor"):
                        lines.append(f"\n> {ladder['sector_anchor']}")
                    lines.append("")

                # ── 网格交易区间 ──
                grid = s.get("grid", {})
                if grid and grid.get("zone", "未知") != "未知":
                    zone_icon = {"低吸": "🟢", "持有": "🟡", "减仓": "🔴"}.get(grid["zone"], "⚪")
                    lines.append(f"**网格交易**: {zone_icon} **{grid['zone']}区** — {grid['desc']}\n")

                # ── 分红奶牛信号 ──
                cow = s.get("dividend_cow", {})
                if cow and cow.get("signal") != "无":
                    cow_icons = {"强": "🐄", "中": "🥛", "弱": "💊"}
                    icon = cow_icons.get(cow["signal"], "")
                    lines.append(f"{icon} **分红奶牛信号**（{cow['signal']}）: {cow.get('reason', '')}\n")

                # ── 行业对冲提示 ──
                sector = s.get("sector", "")
                hedge_sector = None
                hedge_desc = ""
                if sector in HEDGE_PAIRS:
                    hedge_sector = HEDGE_PAIRS[sector]
                else:
                    # 反向查找：火电 → 煤炭
                    for k, v in HEDGE_PAIRS.items():
                        if v == sector:
                            hedge_sector = k
                            break
                if hedge_sector:
                    hedge_names = []
                    for ts, sc in scores.items():
                        if sc.get("sector") == hedge_sector:
                            hedge_names.append(sc["name"])
                    if hedge_names:
                        # 根据对冲组合给出不同的描述
                        hedge_descs = {
                            "煤炭": "煤价跌利好火电、煤价涨利好煤炭",
                            "火电": "煤价跌利好火电、煤价涨利好煤炭",
                            "银行": "金融板块内均衡配置",
                            "保险": "金融板块内均衡配置",
                            "石油": "上下游对冲",
                        }
                        desc = hedge_descs.get(sector, "天然对冲，分散风险")
                        lines.append(f"> 🔄 **行业对冲**: {sector} ↔ {hedge_sector}（{', '.join(hedge_names)}），{desc}\n")

                # ── 历史股息率横向对比 ──
                if cat == "ETF红利":
                    hist_table = self._get_etf_dividend_history_table(ts_code, bond_yield)
                else:
                    hist_table = self._get_dividend_history_table(ts_code, bond_yield)
                if hist_table:
                    lines.append("**历年股息率横向对比**\n")
                    lines.append(hist_table)
                    lines.append("")

        # ── 观察等待清单 ──
        if signals.get("watch_list"):
            lines.append("\n---\n\n## 👀 观察等待清单\n")
            lines.append("以下标的有一定吸引力，等待更好买点：\n")
            for w in signals["watch_list"]:
                lines.append(f"- **{w['name']}** (评分{w['score']}) — 当前股息率 {w['div_yield']:.1f}%")

        # ── 数据质量摘要 ──
        conf_summary = validation.get("confidence_summary", {})
        lines.append("\n---\n\n## 🔍 数据质量摘要\n")
        lines.append(f"| 置信度 | 数量 |")
        lines.append(f"|--------|------|")
        lines.append(f"| ✅ 高 (tushare直接获取) | {conf_summary.get('high', 0)} |")
        lines.append(f"| ⚠️ 中 (fallback或校准) | {conf_summary.get('medium', 0)} |")
        lines.append(f"| ❌ 低 (数据异常) | {conf_summary.get('low', 0)} |")

        # ── 自验证告警区块 ──
        sv = validation.get("self_validation", {})
        stale_stocks    = sv.get("stale_stocks", [])
        conflict_stocks = sv.get("conflict_stocks", [])
        sv_alerts = [a for a in validation.get("alerts", [])
                     if a["severity"] == "warning"
                     and ("fallback股价" in a.get("issue","") or "三源" in a.get("issue",""))]

        if stale_stocks or conflict_stocks:
            lines.append("\n---\n\n## 🧪 自验证告警\n")
            lines.append("> 以下告警由系统自动检测，需人工核对 FALLBACK_DATA 或等待数据更新。\n")

            if stale_stocks:
                lines.append(f"### 📅 Fallback 数据可能过时 ({len(stale_stocks)} 只)\n")
                lines.append("| 股票 | 问题描述 |")
                lines.append("|------|---------|")
                for a in sv_alerts:
                    if "fallback股价" in a.get("issue",""):
                        lines.append(f"| **{a['name']}** | {a['issue']} |")
                lines.append("")
                lines.append("💡 **建议**：更新 `dividend_evaluator.py` 中对应股票的 `FALLBACK_DATA` 字段\n")

            if conflict_stocks:
                lines.append(f"### 🔀 三源股息率存在矛盾 ({len(conflict_stocks)} 只)\n")
                lines.append("| 股票 | 原因分析 |")
                lines.append("|------|---------|")
                for a in sv_alerts:
                    if "三源" in a.get("issue",""):
                        lines.append(f"| **{a['name']}** | {a['issue']} |")
                lines.append("")
                lines.append(
                    "💡 **建议**：若为「年报分红尚未公告」→ 等待7-8月除权后自动修正；"
                    "若为「fallback过时」→ 立即更新；若为「dv_ttm含特别分红」→ 忽略即可\n"
                )
        else:
            lines.append("\n\n> ✅ **自验证通过**：所有股票三源股息率一致，fallback 数据未发现明显过时。\n")

        lines.append(f"\n---\n\n> ⚠️ **免责声明**: 本报告仅供学习研究，不构成投资建议。\n")
        lines.append(f"*生成时间: {now_str} | 框架: 红利周期投资 + Harness 周期评估*")

        content = "\n".join(lines)
        report_path = week_dir / "report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  💾 周报 → {report_path}")
        return content

    # ─── 可视化图表 ──────────────────────────────────────────

    def _generate_chart(
        self,
        raw_scores: Dict,
        history: pd.DataFrame,
        week_dir: Path,
    ):
        """生成可视化图表（含历史趋势）"""
        scores = raw_scores.get("scores", {})
        week = raw_scores.get("week", "?")

        df = pd.DataFrame([
            {
                "ts_code": ts, "name": s["name"], "category": s["category"],
                "total_score": s["total_score"], "div_yield": s["div_yield"],
                "eff_yield": s["eff_yield"], "bond_spread_bp": s["bond_spread_bp"],
                "roe": s["roe"], "pe_ttm": s["pe_ttm"], "buyback_yield": s.get("buyback_yield", 0),
            }
            for ts, s in scores.items()
        ])
        df_sorted = df.sort_values("total_score", ascending=True)

        has_history = not history.empty and len(history["week"].unique()) >= 2

        # 图表布局：有历史时增加趋势图
        if has_history:
            fig = plt.figure(figsize=(22, 24))
            fig.patch.set_facecolor("#0d1117")
            gs = fig.add_gridspec(4, 3, hspace=0.5, wspace=0.35)
        else:
            fig = plt.figure(figsize=(20, 18))
            fig.patch.set_facecolor("#0d1117")
            gs = fig.add_gridspec(3, 3, hspace=0.45, wspace=0.35)

        bar_colors = [COLORS_CAT.get(c, "#aaa") for c in df_sorted["category"]]

        # ── 图1: 综合总分排行（横向） ─────────────────────────
        ax1 = fig.add_subplot(gs[0, :])
        ax1.set_facecolor("#161b22")
        names = df_sorted["name"].tolist()
        scores_vals = df_sorted["total_score"].tolist()
        bars = ax1.barh(names, scores_vals, color=bar_colors, alpha=0.85, height=0.6)
        ax1.set_xlim(0, 115)
        ax1.axvline(80, color="#ff6b6b", linestyle="--", alpha=0.6, label="极佳(80+)")
        ax1.axvline(65, color="#ffd93d", linestyle="--", alpha=0.6, label="布局(65+)")
        ax1.axvline(50, color="#6bcb77", linestyle="--", alpha=0.4, label="观察(50+)")
        for bar, score in zip(bars, scores_vals):
            ax1.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                     f"{score:.0f}分", va="center", ha="left", color="white", fontsize=9)
        ax1.set_title(f"红利周期投资综合评分 — {week}", color="white", pad=12, fontsize=13, fontweight="bold")
        ax1.tick_params(colors="white", labelsize=9)
        ax1.spines[:].set_color("#30363d")
        patches = [mpatches.Patch(color=v, label=k, alpha=0.85) for k, v in COLORS_CAT.items()]
        ax1.legend(handles=patches, loc="lower right", facecolor="#161b22",
                   edgecolor="#30363d", labelcolor="white", fontsize=8)

        # ── 图2: 股债息差 ──────────────────────────────────
        ax2 = fig.add_subplot(gs[1, 0])
        ax2.set_facecolor("#161b22")
        spread_colors = ["#ff6b6b" if x >= 230 else "#ffd93d" if x >= 130 else "#6bcb77"
                         for x in df_sorted["bond_spread_bp"]]
        ax2.barh(df_sorted["name"], df_sorted["bond_spread_bp"], color=spread_colors, alpha=0.85, height=0.6)
        ax2.axvline(100, color="#aaa", linestyle="--", alpha=0.6, label="中枢~100BP")
        ax2.axvline(230, color="#ff6b6b", linestyle="--", alpha=0.6, label="极佳>230BP")
        ax2.set_title("股债息差 (BP)", color="white", fontsize=10, pad=8)
        ax2.tick_params(colors="white", labelsize=8)
        ax2.spines[:].set_color("#30363d")
        ax2.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图3: 等效分红率（含回购）─────────────────────────
        ax3 = fig.add_subplot(gs[1, 1])
        ax3.set_facecolor("#161b22")
        x = range(len(df))
        ax3.bar(x, df["div_yield"], color="#4ecdc4", alpha=0.85, label="现金股息率")
        ax3.bar(x, df["buyback_yield"], bottom=df["div_yield"], color="#f9ca24",
                alpha=0.85, label="回购收益率")
        ax3.axhline(5.0, color="#ff6b6b", linestyle="--", alpha=0.7, label="5%目标线")
        ax3.axhline(8.0, color="#e17055", linestyle="--", alpha=0.7, label="8%优秀线")
        ax3.set_xticks(list(x))
        ax3.set_xticklabels([n[:2] for n in df["name"]], color="white", fontsize=8)
        ax3.set_title("等效分红率 (现金+回购)", color="white", fontsize=10, pad=8)
        ax3.tick_params(colors="white", labelsize=8)
        ax3.spines[:].set_color("#30363d")
        ax3.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图4: PE vs 股息率 散点图 ─────────────────────────
        ax4 = fig.add_subplot(gs[1, 2])
        ax4.set_facecolor("#161b22")
        for cat, grp in df.groupby("category"):
            ax4.scatter(grp["pe_ttm"], grp["div_yield"],
                        c=COLORS_CAT.get(cat, "#aaa"), s=120, alpha=0.85, label=cat[:4], zorder=3)
            for _, row in grp.iterrows():
                ax4.annotate(row["name"][:2], (row["pe_ttm"], row["div_yield"]),
                             fontsize=7, color="white", xytext=(3, 3), textcoords="offset points")
        ax4.axhline(5.0, color="#ffd93d", linestyle="--", alpha=0.5, label="5%红利线")
        ax4.set_xlabel("PE(TTM)", color="#8b949e", fontsize=9)
        ax4.set_ylabel("股息率 (%)", color="#8b949e", fontsize=9)
        ax4.set_title("估值 vs 股息率", color="white", fontsize=10, pad=8)
        ax4.tick_params(colors="white", labelsize=8)
        ax4.spines[:].set_color("#30363d")
        ax4.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图5: TOP5 分红复投曲线 ────────────────────────────
        ax5 = fig.add_subplot(gs[2, :2])
        ax5.set_facecolor("#161b22")
        top5 = df.sort_values("total_score", ascending=False).head(5)
        years = list(range(1, 11))
        for _, row in top5.iterrows():
            cy = row["div_yield"]
            cost_yields = []
            for y in years:
                cy = cy * (1 + row["div_yield"] / 100)
                cost_yields.append(cy)
            ax5.plot(years, cost_yields,
                     marker="o", markersize=4, label=row["name"],
                     color=COLORS_CAT.get(row["category"], "#aaa"),
                     linewidth=2, alpha=0.85)
        ax5.axhline(8.0, color="#ff6b6b", linestyle="--", alpha=0.6, label="8%复利目标")
        ax5.set_xlabel("持有年数", color="#8b949e", fontsize=9)
        ax5.set_ylabel("成本股息率 (%)", color="#8b949e", fontsize=9)
        ax5.set_title("分红复投复利曲线（100万，假设股价不涨）", color="white", fontsize=10, pad=8)
        ax5.tick_params(colors="white", labelsize=8)
        ax5.spines[:].set_color("#30363d")
        ax5.legend(fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图6: TOP1 雷达图 ──────────────────────────────────
        top1_ts = df.sort_values("total_score", ascending=False).iloc[0]
        top1_full = scores.get(top1_ts["ts_code"], {})
        ax6 = fig.add_subplot(gs[2, 2], projection="polar")
        ax6.set_facecolor("#161b22")
        categories_radar = ["股息率\n(×30)", "股债息差\n(×25)", "等效分红\n(×20)", "确定性\n(×15)", "成长性\n(×10)"]
        values = [
            top1_full.get("s1_div", 0) / 30 * 100,
            top1_full.get("s2_spread", 0) / 25 * 100,
            top1_full.get("s3_eff", 0) / 20 * 100,
            top1_full.get("s4_certainty", 0) / 15 * 100,
            top1_full.get("s5_growth", 0) / 10 * 100,
        ]
        values += values[:1]
        N = len(categories_radar)
        angles = [n / float(N) * 2 * np.pi for n in range(N)]
        angles += angles[:1]
        color = COLORS_CAT.get(top1_full.get("category", ""), "#4ecdc4")
        ax6.plot(angles, values, color=color, linewidth=2, linestyle="solid")
        ax6.fill(angles, values, alpha=0.25, color=color)
        ax6.set_xticks(angles[:-1])
        ax6.set_xticklabels(categories_radar, size=8, color="white")
        ax6.set_ylim(0, 100)
        ax6.tick_params(colors="white", labelsize=7)
        ax6.set_title(f"TOP1: {top1_full.get('name', '?')}\n总分{top1_full.get('total_score', 0):.0f}/100",
                      color="white", fontsize=10, pad=20)
        ax6.set_facecolor("#161b22")
        ax6.spines["polar"].set_color("#30363d")
        ax6.yaxis.set_tick_params(labelcolor="#8b949e")

        # ── 图7: 历史趋势折线图（如有历史数据）────────────────
        if has_history:
            # 选取评分最高的 6 只绘制趋势
            top6_codes = df.sort_values("total_score", ascending=False).head(6)["ts_code"].tolist()
            ax7 = fig.add_subplot(gs[3, :])
            ax7.set_facecolor("#161b22")

            hist_pivot = history[history["ts_code"].isin(top6_codes)].pivot_table(
                index="week", columns="ts_code", values="total_score", aggfunc="last"
            )
            # 只取最近52周
            hist_pivot = hist_pivot.tail(52)

            color_cycle = ["#4ecdc4", "#f9ca24", "#e17055", "#a29bfe", "#fd79a8", "#55efc4"]
            for i, (ts_code, col) in enumerate(hist_pivot.items()):
                name_label = scores.get(ts_code, {}).get("name", ts_code)
                ax7.plot(hist_pivot.index, col, marker="o", markersize=3,
                         label=name_label, color=color_cycle[i % len(color_cycle)],
                         linewidth=1.8, alpha=0.85)

            ax7.axhline(80, color="#ff6b6b", linestyle="--", alpha=0.5, label="大胆攒股(80)")
            ax7.axhline(65, color="#ffd93d", linestyle="--", alpha=0.4, label="积极布局(65)")
            ax7.set_title("TOP6 评分历史趋势", color="white", fontsize=11, pad=8)
            ax7.tick_params(colors="white", labelsize=8)
            ax7.spines[:].set_color("#30363d")
            ax7.legend(fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor="white",
                       loc="upper left", ncol=3)
            plt.xticks(rotation=45, ha="right")

        fig.suptitle(f"🌊 红利周期投资 — 周度评估报告 {week}",
                     fontsize=14, color="white", y=0.99, fontweight="bold")

        chart_path = week_dir / "chart.png"
        plt.savefig(chart_path, dpi=150, bbox_inches="tight",
                    facecolor="#0d1117", edgecolor="none")
        plt.close()
        print(f"  💾 图表 → {chart_path}")

    # ─── 主入口 ──────────────────────────────────────────────

    def run(
        self,
        raw_scores: Dict,
        validation: Dict,
        artifacts_dir: Optional[Path] = None,
        force: bool = False,
        market_signals: Optional[Dict] = None,
    ) -> Dict:
        """
        执行周报生成

        Parameters
        ----------
        raw_scores : dict
            来自 Generator 的 raw_scores artifact
        validation : dict
            来自 Validator 的 validation_report artifact
        artifacts_dir : Path, optional
            artifact 输出目录
        force : bool, optional
            强制覆盖该周全部历史数据（默认 upsert 模式）
        market_signals : dict, optional
            预获取的市场信号（来自 MarketSignals.get_all_signals()）。
            若不传则自动获取，获取失败时优雅降级。

        Returns
        -------
        dict : signals artifact
        """
        print("\n" + "─" * 50)
        print("  [Phase 4] Reporter — 周报生成")
        print("─" * 50)

        week = raw_scores.get("week", datetime.now().strftime("%G-W%V"))

        # 创建本周存档目录
        week_dir = self.weekly_reports_dir / week
        week_dir.mkdir(parents=True, exist_ok=True)

        # 加载历史数据（先追加前，读取原有历史用于对比）
        history = self._load_history()

        # 检测信号变化
        signals = self._detect_signals(raw_scores, history)

        # 生成 Markdown 周报
        self._generate_markdown(raw_scores, validation, signals, history, week_dir, market_signals=market_signals)

        # 生成可视化图表
        self._generate_chart(raw_scores, history, week_dir)

        # 追加历史数据（upsert 或 force 覆盖）
        self._append_to_history(raw_scores, validation, force=force)

        # 同步覆盖最新报告（向后兼容原有路径）
        latest_md = self.data_dir / "dividend_report.md"
        import shutil
        shutil.copy(week_dir / "report.md", latest_md)
        shutil.copy(week_dir / "chart.png", self.data_dir / "dividend_chart.png")
        print(f"  🔄 同步更新最新报告 → data/dividend_report.md")

        # 保存信号 artifact
        signals_data = {
            "week": week,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **signals,
        }

        if artifacts_dir:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            signals_path = artifacts_dir / "signals.json"
            with open(signals_path, "w", encoding="utf-8") as f:
                json.dump(signals_data, f, ensure_ascii=False, indent=2)
            print(f"  💾 signals.json → {signals_path}")

        # 打印信号摘要
        if signals.get("new_strong_buy"):
            print(f"\n  🔥 新进入大胆攒股: {[s['name'] for s in signals['new_strong_buy']]}")
        if signals.get("signal_downgrades"):
            print(f"  📉 信号下调: {[s['name'] for s in signals['signal_downgrades']]}")

        print(f"\n  📁 本周报告目录: {week_dir}")
        print(f"  ✅ Reporter 完成\n")
        return signals_data
