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
│   ├── strategy.py         # 红利周期轮动策略（评分→仓位映射+类别约束）
│   └── backtest.py         # 回测引擎（历史模拟+绩效指标）
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
- 与沪深300基准对比
- 净值曲线 + 交易明细
- `data/backtest/` 目录下完整报告
