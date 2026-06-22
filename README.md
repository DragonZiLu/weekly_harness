# weekly_harness — A股量化投资研究系统

本项目包含四大策略体系：

- **🧊 FCF 自由现金流策略**：中证800成分池，TTM 口径，多版本选股与回测（主力方向）
- **🔥 红利策略体系**：800红利 / 红利低波100 / H30269低波50 复现与增强
- **📊 SP500 风格指数**：宽基增强，NI+FCF双正 + D'Hondt行业平衡 + 自由流通市值加权
- **📈 交易执行研究**：网格买入、定投择时、ETF估值对比

---

## ★ FCF 自由现金流策略（主力）

基于 `FCF = OCF - Capex`、EV 加权、TTM 口径的量化选股系统，覆盖中证800（ZZ800）成分池。

### 最新回测结果（ZZ800，2015-03→2026-06，45期）

| 版本 | 年化 | 最大回撤 | 夏普 | 换手率 | 期末NAV | 说明 |
|------|------|----------|------|--------|---------|------|
| **I版** | **17.01%** | -38.97% | — | 32.7% | — | 二维质量过滤（PQ+OCF/利润>1.0），**年化最优** |
| **E版** | **15.80%** | -39.66% | **0.536** | 23.1% | 5.21x | ±40%缓冲，**夏普/NAV最优** |
| G版 | ~18.38% | — | — | — | — | 自适应TopN，**Calmar最优** |
| H版 | 对比中 | — | — | — | — | FCF绝对值排序Top50 |
| D版 | 15.34% | -39.47% | 0.517 | 25.5% | 4.98x | ±20%缓冲 |
| F版 | 15.11% | -39.73% | 0.515 | 22.0% | 4.87x | ±50%缓冲 |
| B版 | 14.80% | -39.99% | 0.504 | 31.4% | 4.72x | ±0%缓冲，纯排名 |
| X版 | 10.12% | -40.82% | 0.356 | — | 2.96x | 全成分FCF加权（Smart Beta） |
| 932368 | 11.19% | -39.90% | 0.358 | — | 3.30x | 中证800现金流官方指数 |

### 各版本定位

| 版本 | 缓冲区 | 选股方式 | 当前年化 | 备注 |
|------|--------|----------|---------|------|
| **I版** | ±40% | PQ+OCF/利润>1.0双过滤，Top50 | 17.01% | ✅ 年化最优 |
| **E版** | ±40% | Top50（必选30+缓冲20） | 15.80% | ✅ 夏普/NAV最优 |
| G版 | 自适应 | 动态TopN | ~18.38% | ✅ Calmar最优 |
| H版 | — | FCF绝对值Top50 | 对比中 | ✅ 已验证 |
| D版 | ±20% | Top50（必选40+缓冲10） | 15.34% | ✅ |
| F版 | ±50% | Top50（必选25+缓冲25） | 15.11% | ✅ |
| B版 | ±0% | Top50纯排名 | 14.80% | ✅ |
| X版 | — | 全成分FCF加权 | 10.12% | ✅ Smart Beta基线 |

### 快速运行

```bash
# FCF 五版完整回测（B/D/E/F/X）
python run_bdefx_full.py

# 仅重算NAV（跳过选股，用已有数据）
python run_bdefx_full.py --nav-only

# I版（二维质量过滤）★ 年化最优
python run_i_full.py

# G版（自适应TopN）
python run_g_full.py

# H版（FCF绝对值排序）
python run_h_full.py

# 季度持仓板块归因分析
python analysis/quarterly_holdings_analysis.py E 2026-03-16
```

---

## ★ 红利周期投资 — 周度评估

基于「红利周期三层估值体系」的**实战攒股系统**，每周自动评估 38 只精选红利标的，输出买卖信号。

```bash
# 每周运行（四阶段 Harness 框架：Planner → Generator → Validator → Reporter）
python run_weekly.py

# 强制覆盖本周历史
python run_weekly.py --force

# 历史周次重跑
python run_weekly.py --week 2026-W21
```

输出文件：
- 周报 Markdown：`data/weekly_reports/{周次}/report.md`
- 可视化图表：`data/weekly_reports/{周次}/chart.png`
- 最新报告：`data/dividend_report.md`（自动同步）

### 评估流程

```
Planner → Generator → Validator → Reporter
   │           │            │            │
  规划范围    拉取tushare   数据质量校验   生成周报+图表
  国债利率    计算评分      置信度标注    信号变化检测
```

---

## ★ 红利策略体系

### 策略矩阵

| 策略 | 选股方式 | 年化 | 最大回撤 | 调仓 | 备注 |
|------|----------|------|----------|------|------|
| **红利低波100 (930955)** | 股息率×波动率排名 Top100 | **11.31%** | -30.77% | 季度 | ✅ 复现成功 |
| **H30269 低波50** | DPS增长+波动率 Top50 | **8.95%** | — | 半年 | ✅ ≈官方8.65% |
| **800红利 (931644)** | 三年股息率 Top100 | **5.93%** | -15.23% | 半年 | ✅ 防御性最强 |
| 800红利+FCF过滤 | 三年股息率+FCF(TTM)>0 | 4.72% | -16.54% | 半年 | ❌ 不采纳 |
| 红利+回购增强 | 股东回报率替代股息率 | 5.14% | — | 半年 | ❌ 暂存 |

### 快速运行

```bash
# 800红利指数复现（931644）
python run_800div_full.py

# 红利低波100复现（930955）
python run_dividend_lowvol.py

# H30269红利低波50复现
python run_h30269_full.py

# 800红利+FCF过滤实验
python run_800div_fcf_filter.py

# 红利+回购增强版
python run_800div_buyback.py
```

---

## ★ SP500 风格指数

基于沪深300成分池，仿标普500风格指数的宽基增强策略。

| 版本 | 方案 | 年化 | 回撤 | 区间 | 备注 |
|------|------|------|------|------|------|
| v6 Top200 | NI+FCF双正+D'Hondt+自由流通市值加权 | **11.42%** | — | 2013-2026 | ✅ 全区间最优 |
| v6 Top100 | 同上，Top100 | 9.07% | -35.60% | 2015-2026 | ✅ |

```bash
python run_sp500_style.py
```

---

## ★ 交易执行研究

```bash
# 网格买入算法（515180，23种参数对比）
python run_grid_research.py

# 大额买入择时分析（300万场景）
python analyze_entry_timing.py

# PE估值三算法对比（加权/等权/均值）
python quick_val_weighted.py

# ETF月度定投IRR计算器
python fund_dca_calc.py --code 515180.SH --start 2020-01-01 --monthly 10000
```

---

## 项目结构

```
weekly_harness/
├── ★ 周度评估系统
├── run_weekly.py              # 红利周期投资周度评估（四阶段 Harness）
├── dividend_evaluator.py      # 企业量化评估引擎（三层估值体系）
│
├── ★ FCF策略入口
├── run_bdefx_full.py          # FCF五版回测主入口（B/D/E/F/X）
├── run_i_full.py              # I版（二维质量过滤）★ 年化17.01%
├── run_g_full.py              # G版（自适应TopN）
├── run_h_full.py              # H版（FCF绝对值排序）
│
├── ★ 红利策略入口
├── run_800div_full.py         # 800红利指数复现（931644）
├── run_800div_buyback.py      # 800红利+回购增强版
├── run_dividend_lowvol.py     # 红利低波100复现（930955）
├── run_h30269_full.py         # H30269红利低波50复现
├── run_800div_fcf_filter.py   # 800红利+FCF过滤实验
│
├── ★ SP500风格
├── run_sp500_style.py         # SP500风格指数（D'Hondt行业平衡）
│
├── ★ 执行研究
├── run_grid_research.py       # 网格买入算法
├── analyze_entry_timing.py    # 大额买入择时
├── quick_val_weighted.py      # PE估值三算法对比
├── fund_dca_calc.py           # ETF定投IRR计算器
│
├── weekly_harness/            # 核心引擎包
│   ├── fcf_universe.py        # ★★ FCF选股核心引擎
│   ├── dividend_universe.py   # 800红利选股引擎
│   ├── dividend_lowvol.py     # 红利低波引擎
│   ├── dividend_h30269.py     # H30269低波50引擎
│   ├── dividend_buyback.py    # 红利+回购增强引擎
│   ├── sp500_style.py         # SP500风格指数引擎
│   ├── backtest.py            # 回测引擎
│   ├── strategy.py            # 红利周期轮动策略
│   ├── scanner.py             # A股红利潜力股扫描器
│   ├── planner.py             # 周度评估规划器
│   ├── generator.py           # 数据拉取+评分
│   ├── validator.py           # 数据质量校验
│   ├── reporter.py            # 周报生成
│   ├── dca_planner.py         # 定投计划引擎
│   └── ...
│
├── strategies/                # 策略YAML配置（16个版本）
│   ├── zz800_fcf/             # E版（当前最优）
│   ├── zz800_fcf_2d_quality/  # I版（二维质量过滤）
│   ├── sp500_style/           # SP500风格
│   ├── 800div/                # 800红利
│   ├── div_lowvol_100/        # 红利低波100
│   └── h30269_lowvol/         # H30269低波50
│
├── analysis/
│   ├── quarterly_holdings_analysis.py  # 季度持仓板块归因
│   ├── fund_tracker.py                 # 基金/策略净值跟踪
│   └── tracker_config.py               # 跟踪标的配置
│
├── docs/                     # ★ 所有报告（git追踪）
├── data/                     # 数据缓存（.gitignore排除）
├── output/                   # 回测输出（.gitignore排除）
└── tests/                    # 单元测试
```

---

## 基金 & 指数跟踪工具

```bash
# 快照：所有标的多区间收益 + 最大回撤 + 夏普
python analysis/fund_tracker.py

# 周报 / 月报 / 季报
python analysis/fund_tracker.py --mode weekly
python analysis/fund_tracker.py --mode monthly
python analysis/fund_tracker.py --mode quarter
```

当前跟踪 **16 只标的**：自建 FCF 策略 B/D/E/F 版、国证/中证现金流指数、现金流 ETF、红利 ETF、沪深300/中证500/中证1000 全收益。

---

## 核心文档索引

| 文档 | 说明 |
|------|------|
| [CLAUDE.md](CLAUDE.md) | 项目指南 & Agent 操作手册 |
| [研究日志](docs/research_log.md) | 所有实验记录（按时间排列） |
| [FCF五版对比](docs/zz800_bdefx_strategy_comparison.md) | B/D/E/F/X 核心指标对比 |
| [800红利复现](docs/2026-06-11_800红利指数复现.md) | 931644 复现回测报告 |
| [红利低波100](docs/2026-06-13_红利低波100回测报告.md) | 930955 复现回测报告 |
| [H30269复现](docs/2026-06-14_H30269红利低波50回测报告.md) | H30269 官方复现报告 |
| [SP500风格](docs/2026-06-10_sp500_style_300_report.md) | SP500风格指数报告 |
| [FCF过滤实验](docs/2026-06-16_800红利FCF过滤实验.md) | 800红利+FCF过滤（不采纳） |
| [季度持仓分析](analysis/quarterly_holdings_analysis.py) | 一键分析任意版本+任意调仓日 |

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 tushare token
cp .env.example .env
# 编辑 .env，填入 TUSHARE_TOKEN

# 3. 运行周度评估（实战攒股）
python run_weekly.py

# 4. 运行 FCF 主回测
python run_bdefx_full.py
```

---

## 关键约定

- **数据来源**：Tushare Pro（本地缓存优先，零 API 重算）
- **回测区间**：2015-03 → 当前（保持历史一致性）
- **调仓频率**：FCF 策略季度调仓（3/6/9/12月），红利策略半年度调仓（6/12月）
- **禁止前视偏差**：成分股使用回测起点的实时成分，剔除回测起点后上市的标的
- **报告归档**：所有实验生成 `docs/{日期}_{描述}.md` 并追加 `research_log.md`
- **禁止修改**：`data/` 下缓存文件、已有策略版本核心逻辑、`docs/` 下已有报告
