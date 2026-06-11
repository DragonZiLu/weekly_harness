# 策略总览

本目录包含所有量化选股策略的实现和文档。

## 策略列表

| 策略 | 版本 | 基线? | 样本空间 | EV | TTM | 5yr OCF | 年化收益 | 对标指数 | 目录 |
|------|------|-------|---------|-----|-----|---------|---------|---------|------|
| **沪深300 FCF** | A: 原始宽松 | | 沪深300(300只) | circ_mv | 无 | 宽松 | 14.69% | 932366.CSI | [hs300_fcf/](hs300_fcf/) |
| **沪深300 FCF** | B: fixed+宽松 | ✅**基线** | 沪深300(300只) | total_mv | 有 | 宽松 | 14.58% | 932366.CSI | [hs300_fcf_lenient/](hs300_fcf_lenient/) |
| **沪深300 FCF** | C: fixed+严格 | | 沪深300(300只) | total_mv | 有 | 严格 | 10.84% | 932366.CSI | [hs300_fcf_fixed/](../output/hs300_fcf_fixed/) |
| **中证800 FCF** | A: 原始宽松 | | 中证800(800只) | circ_mv | 无 | 宽松 | 14.02% | 932368.CSI | [zz800_fcf/](zz800_fcf/) |
| **中证800 FCF** | B: fixed+宽松 | ✅**基线** | 中证800(800只) | total_mv | 有 | 宽松 | TBD | 932368.CSI | [zz800_fcf_lenient/](zz800_fcf_lenient/) |
| **中证800 FCF** | C: fixed+严格 | | 中证800(800只) | total_mv | 有 | 严格 | 10.48% | 932368.CSI | [zz800_fcf_fixed/](../output/zz800_fcf_fixed/) |
| **中证全指 FCF** | — | | 中证全指(3000+只) | total_mv | 有 | 严格 | — | 932365.CSI | [fcf100/](fcf100/) |
| **沪深300 FCF** | D: B版+缓冲区 | | 沪深300(300只) | total_mv | 有 | 宽松+缓冲区 | TBD | 932366.CSI | [hs300_fcf_lenient_buffer/](hs300_fcf_lenient_buffer/) |
| **S&P 500风格** | v5-Top100 | | 中证800(800只) | — | — | — | **10.69%** | X版/932368/HS300 | [sp500_style/](sp500_style/) |
| **800红利(931644)** | Top100 | | 中证800(800只) | — | — | — | **5.72%** | 931644.CSI | [800div/](800div/) |
| **红利精选32** | — | | 精选池 | — | — | — | — | 自研 | [selected_32/](selected_32/) |

## 基线版本 (B版: fixed+宽松OCF)

B版(total_mv+TTM+宽松OCF)为当前**策略基线版本**，核心规则:
- **选样**: FCF率(FCF/EV)降序 → Top50
- **加权**: FCF绝对值加权 + 10%封顶迭代(⚠️ 选样指标≠加权指标)
- **5yr OCF**: 宽松模式(上市不足5年截断)

所有后续改进(如PQ调整、缓冲区规则)均以B版为基线对比。

三个版本的设计是为了**隔离各规则变量的影响**：

| 对比 | 隔离变量 | 其他变量 |
|------|---------|---------|
| A vs B | EV(circ_mv→total_mv) + TTM引入 | OCF规则相同（宽松） |
| B vs C | 5yr OCF规则（宽松→严格） | EV+TTM相同（total_mv+TTM） |
| A vs C | 综合影响（三个变量全变） | — |

## 快速使用

```bash
# A版(原始宽松)
python run_fcf_strategy.py --strategy hs300_fcf
python run_fcf_strategy.py --strategy zz800_fcf

# B版(fixed+宽松OCF) — 用于隔离EV+TTM影响
python regenerate_b_baskets.py --index hs300
python regenerate_b_baskets.py --index zz800
```

```python
# B版 Python API
from weekly_harness.fcf_universe import FcfUniverse

uni = FcfUniverse(index_code="000906.SH", strict_ocf=False)  # B版: 宽松OCF
uni.preload_all(download=False)
basket = uni.get_fcf_basket("2025-06-16", top_n=50, use_ttm=True)
```

## S&P 500 风格指数（宽基增强）

对标标普500编制理念：**盈利门槛 → 行业平衡 → 自由流通市值加权**。

| 项目 | 内容 |
|:---|:---|
| 样本空间 | 中证800（000906.SH） |
| 选股 | NI+FCF双正过滤(S&P 500滚动4Q) → 流动性过滤 → 申万108行业平衡 |
| 推荐数量 | **100只**（可选300只） |
| 加权 | 自由流通市值(free_share×close)，10%封顶 |
| 年化 | **10.69%**（vs HS300全收益 4.68%，+6.01pp） |
| 对标 | X版FCF加权 10.12%、932368 11.19% |

**与FCF策略的本质区别**：
- FCF策略是**主动选股**（FCF率排名选TopN），SP500风格是**被动筛选**（盈利门槛过滤后行业平衡）
- SP500风格更接近指数编制方案，换手率更低，适合作为基准增强
- 收益来源：长期持有优质大盘股（茅台贡献22.49%，白酒+家电+通信=63%）

```bash
# 推荐用法
python run_sp500_style.py --use-both --target-n 100
```

详见：[sp500_style/](sp500_style/) | [完整回测报告](../docs/2026-06-10_sp500_style_300_report.md)

## 策略对比

### FCF现金流系列

三个FCF策略共享核心逻辑(行业剔除 → FCF率排名 → FCF加权10%封顶), 差异在:

| 维度 | HS300 FCF | ZZ800 FCF | CSI全指FCF |
|------|-----------|-----------|------------|
| 样本量 | 300 | 800 | 3000+ |
| 选股数 | 50 | 50 | 100 |
| CAPEX口径 | 购建固定资产 | 含无形资产+其他长期资产 | 含无形资产+其他长期资产 |
| TTM回退 | ✅ 年报 | ✅ 年报 | ✅ 年报 |
| 5年OCF | TTM优先 | TTM优先 | TTM优先 |
| 权重封顶 | 10% | 10% | 10% |
| 行业过滤 | 申万+关键词 | 申万+关键词 | 申万+关键词 |

### 与红利策略的区别

| 维度 | FCF策略 | 红利策略 |
|------|---------|---------|
| 选股逻辑 | FCF率(FCF/EV) | 股息率+股债息差+确定性 |
| 核心指标 | 现金创造能力 | 分红能力 |
| 持仓数量 | 50-100只(高度分散) | ≤15只(集中) |
| 加权方式 | FCF加权10%封顶 | 评分→阶梯仓位 |
| 调仓频率 | 季度(第二周五) | 季度末 |

### 800红利（931644复现）

对标中证800红利指数，纯被动红利策略：

| 项目 | 内容 |
|:---|:---|
| 样本空间 | 中证800 |
| 过滤 | 连续3年分红 + 股利支付率∈(0,1) |
| 选股 | 三年平均股息率 Top 100 |
| 加权 | 股息率加权 + 10%封顶 |
| 调仓 | 半年度（6月/12月） |
| 换手限制 | ≤20% |
| 年化 | **5.72%**（vs 932368 10.02%, HS300 1.54%） |
| 重合度 | vs 931644官方 **87%** |
| 期末NAV | 1.844x (2015-06→2026-06) |

```bash
python run_800div_full.py
```

## 目录结构

```
strategies/
├── README.md               ← 本文件
├── 800div/                 ← 800红利（931644复现）
├── hs300_fcf/              ← 沪深300 FCF策略
│   ├── README.md               详细编制规则+验证报告
│   └── strategy.yaml           可被 run_fcf_strategy.py 加载
├── zz800_fcf/              ← 中证800 FCF策略
│   ├── README.md
│   └── strategy.yaml
├── fcf100/                 ← 中证全指FCF策略
│   └── README.md
└── selected_32/            ← 红利精选32策略
    ├── README.md
    ├── rules.md
    ├── stock_pool.md
    └── changelog.md
```

## 数据目录

```
data/
├── fcf_financials/         ← 财务数据缓存 (cashflow/income/balance 年报+季度)
└── index_weights/          ← 指数权重快照 (HS300/ZZ500/ZZ800/932366/932368)
```

## 验证报告

| 报告 | 位置 |
|------|------|
| 沪深300FCF(932366)+562080ETF | [docs/hs300_fcf_932366_validation.md](../docs/hs300_fcf_932366_validation.md) |
| 中证800FCF(932368) | [docs/zz800_fcf_932368_validation.md](../docs/zz800_fcf_932368_validation.md) |
