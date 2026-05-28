# 红利周期投资 — 周度量化评估系统

基于**红利周期三层估值体系**的 A 股红利股周度自动评估工具，支持**季度调仓**和**策略回测**。

## 项目结构

```
weekly_harness/
├── run_weekly.py           # 周度评估入口（每周运行这个）
├── run_backtest.py         # 回测入口（策略验证）
├── dividend_evaluator.py  # 核心评分引擎（TushareDataFetcher + DividendCycleEvaluator）
├── config/
│   └── settings.py         # 配置（tushare token 等）
├── weekly_harness/
│   ├── criteria.json       # 评估标准配置
│   ├── planner.py          # Phase 1: 确定评估范围 + 国债利率
│   ├── generator.py        # Phase 2: 拉取数据 + 计算评分
│   ├── validator.py        # Phase 3: 数据质量校验
│   ├── reporter.py         # Phase 4: 生成周报 + 历史对比
│   ├── portfolio.py        # 持仓管理（资金、仓位、交易记录）
│   ├── strategy.py         # 红利周期轮动策略（评分→仓位映射+类别约束+对冲约束）
│   ├── market_signals.py   # 市场环境信号（板块轮动/牛熊周期）
│   └── backtest.py         # 回测引擎（历史模拟+绩效指标+多基准对比）
├── data/
│   ├── weekly_history.csv  # 历史时间序列（累积）
│   ├── weekly_reports/     # 每周报告目录
│   └── backtest/           # 回测结果目录
└── logs/                   # 运行日志
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 tushare token
cp .env.example .env
# 编辑 .env，填入 TUSHARE_TOKEN

# 3. 运行周度评估
python run_weekly.py

# 4. 生成当季调仓计划
python run_backtest.py --plan-only

# 5. 运行策略回测（默认季度调仓）
python run_backtest.py --start 2024-01-01 --end 2026-05-01

# 6. 周度调仓回测
python run_backtest.py --start 2024-01-01 --freq weekly
```

## 自动化（cron）

每周五 16:30 收盘后自动运行：

```cron
30 16 * * 5 cd /Users/luzilong/Work/weekly_harness && python run_weekly.py >> logs/weekly.log 2>&1
```

## Harness 框架

```
Planner → Generator → Validator → Reporter
```

- **Planner**：确定本周评估股票范围，获取10年国债收益率
- **Generator**：调用 tushare 拉取数据，运行红利周期评分模型
- **Validator**：校验数据质量，标注置信度（high/medium/low）
- **Reporter**：与历史对比，检测信号变化，生成 Markdown 周报 + 图表

## 评估标准

| 信号 | 分数 | 含义 |
|------|------|------|
| 🔥 大胆攒股 | ≥80 | 黄金坑，底仓+逢跌加仓 |
| ✅ 积极布局 | ≥65 | 性价比高，建3-4成底仓 |
| 👀 观察等待 | ≥50 | 有吸引力但未到极佳买点 |
| ⏸️ 暂缓 | ≥35 | 估值偏高或不确定性大 |
| 🚫 回避 | <35 | 不符合红利周期投资标准 |

## 红利周期轮动策略

### 策略规则

评分 → 目标仓位映射：

| 评分区间 | 信号 | 单标的权重 |
|---------|------|----------|
| ≥80 | 🔥 大胆攒股 | 15% |
| 65-79 | ✅ 积极布局 | 10% |
| 50-64 | 👀 观察等待 | 5% |
| <50 | ⏸️/🚫 | 0%（清仓） |

### 类别权重上限

| 类别 | 权重上限 | 说明 |
|------|---------|------|
| 弱周期红利 | 40% | 核心底仓 |
| 消费成长红利 | 25% | 成长补充 |
| 周期资源红利 | 20% | 周期博弈 |
| ETF红利 | 15% | 分散配置 |

### 风控约束

- 单标的硬上限：20%
- 最大持仓数：12只
- 最低现金保留：5%
- 调仓阈值：权重偏离>2%触发

### 回测参数

```bash
# 默认参数（季度调仓）
python run_backtest.py --start 2024-01-01

# 周度调仓
python run_backtest.py --start 2024-01-01 --freq weekly

# 自定义仓位
python run_backtest.py --max-weight 12 --mid-weight 8 --min-weight 3

# 调整费率
python run_backtest.py --commission 0.15 --slippage 0.05
```

回测输出：
- 年化收益率、最大回撤、夏普比率、卡尔玛比率
- 与沪深300 + 红利ETF双基准对比
- 净值曲线 + 交易明细
- `data/backtest/` 目录下完整报告

## 核心修正记录

### 回测引擎修正（v2）

| # | 问题 | 修正 | 影响 |
|---|------|------|------|
| 1 | S2 BP单位错误：`spread_bp` 存的是百分比点（3.59），但后续按基点处理 | 乘以100转为真实基点，线性评分改为分级表对齐 `dividend_evaluator` | 美的等价差股S2评分偏差达24.6分 |
| 2 | 回测无回购数据，大量回购的股票等效分红被低估 | 新增9只股票历史回购收益率，S1/S3使用 `effective_yield`（现金+回购） | 美的回购收益率~2%，等效股息率显著提升 |
| 3 | 特别分红被计入TTM，茅台2022/2023特别分红导致股息率虚高至5.17% | 过滤 `end_date` 非标准财报期（0331/0630/0930/1231）的分红 | 茅台不再被误判为高股息标的 |
| 4 | TTM滚动窗口6月同时纳入两年年报，股息率翻倍虚高 | 改为最近完整年度DPS：按 `end_date` 自然年度归组取已除权的最近年度分红之和 | 与周报"全年分红/股价"逻辑一致 |

### 策略增强

| # | 特性 | 说明 |
|---|------|------|
| 5 | 多基准对比 | 默认沪深300+红利ETF(515180)双基准 |
| 6 | 行业对冲约束 | 煤炭↔火电、银行↔保险、石油↔化工，持有A时配对B至少配A的50%权重 |
| 7 | 火电行业 | 新增华能/华电，buy=4.5%/full=6.5%，作为煤炭对冲标的 |
| 8 | 行业生命周期标注 | 成熟/成长/夕阳转奶牛分类，资本开支趋势+分红潜力评估 |
| 9 | 市场环境章节 | 周报新增板块轮动信号、牛熊周期、综合仓位建议 |
| 10 | 分红奶牛信号 | 个股卡片新增🐄强/中/弱信号，对冲提示支持双向查找 |
