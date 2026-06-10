# weekly_harness — A股量化投资研究系统

本项目包含两大策略体系：

- **🧊 FCF自由现金流策略**：中证800/沪深300成分池FCF选股与多版本回测（主力方向）
- **🔥 红利周期轮动策略**：红利股三层估值体系与周度评估

> 📋 **[FCF策略项目进展追踪文档](docs/project_progress_fcf_strategy.md)** — 整体进展、框架架构、项目能力、版本迭代、待办事项一览

---

## FCF 自由现金流策略

基于 FCF=OCF-Capex、EV加权、TTM口径的量化选股与回测系统，覆盖中证800（ZZ800）和沪深300（HS300）两个成分池。

### 最新回测结果（ZZ800, 2015-03→2026-06, 45期）

| 版本 | 年化 | 最大回撤 | 夏普 | 期末NAV | 说明 |
|------|------|----------|------|---------|------|
| **E版** | **15.80%** | -39.66% | **0.536** | 5.21x | ±40%缓冲，最优版本 |
| D版 | 15.34% | -39.47% | 0.517 | 4.98x | ±20%缓冲 |
| B版 | 14.80% | -39.99% | 0.504 | 4.72x | ±0%缓冲，Top50精选 |
| F版 | 15.11% | -39.73% | 0.515 | 4.87x | ±50%缓冲 |
| X版 | 10.12% | -40.82% | 0.356 | 2.96x | 全成分FCF加权 |
| 932368 | 11.19% | -39.90% | 0.358 | 3.30x | 官方基准 |

### 七版策略架构

| 版本 | 缓冲区 | 选股方式 | 定位 |
|------|--------|----------|------|
| X版 | — | 全成分FCF加权 | FCF因子纯暴露（Smart Beta） |
| B版 | ±0% | Top50 | 纯FCF率排名精选 |
| D版 | ±20% | Top50（缓冲区） | 减少换手，保留上期持仓 |
| E版 | ±40% | Top50（缓冲区） | 最优，换手率23.1% |
| F版 | ±50% | Top50（缓冲区） | 最大缓冲，换手率22.0% |
| G版 | 自适应 | 动态TopN | 年化18.38%, Calmar最优 |
| H版 | — | FCF绝对值排序Top50 | 验证排序方式差异 |

### 核心文档索引

| 文档 | 说明 |
|------|------|
| **[项目进展追踪](docs/project_progress_fcf_strategy.md)** | 整体进展、框架、能力、版本迭代、待办事项 |
| [B/D/E/F/X 五版对比报告](docs/zz800_bdefx_strategy_comparison.md) | 主报告：核心指标+逐年+逐期+超额+X版专项 |
| [七版对比报告](docs/zz800_bdefgxh_strategy_comparison.md) | 含G/H版扩展对比 |
| [D/B版全面对比](docs/zz800_fcf_full_comparison.md) | vs 多基准详细对比 |
| [B版复盘](docs/zz800_b_version_review.md) | 53.5%胜率、1.54盈亏比 |
| [E版持仓分析](docs/zz800_e_holdings_return_analysis.md) | 2250条个股-期收益分布、板块归因、历史暴雷率 |
| [季度持仓分析模板](analysis/quarterly_holdings_analysis.py) | 一键分析任意版本+任意调仓日的板块分布与归因 |
| [HS300 FCF对比框架](docs/hs300_fcf_vs_932366_comparison_framework.md) | vs 932366官方指数 |
| [数据诊断](docs/zz800_fcf_data_diagnostic.md) | 数据覆盖率与质量 |
| [932368验证](docs/932368_validation_report.md) | 官方指数交叉验证 |

### 快速运行

```bash
# 完整五版回测（选股→NAV→报告）
python run_bdefx_full.py

# 仅X版选股（保存排名池）
python run_bdefx_full.py --x-only

# 跳过选股，用已有数据算NAV+报告
python run_bdefx_full.py --nav-only

# G版（自适应TopN）
python run_g_full.py

# H版（FCF绝对值排序）
python run_h_full.py
```

### 季度持仓分析工具

```bash
# 任意版本 + 任意调仓日，一键输出四维分析
python analysis/quarterly_holdings_analysis.py E 2026-03-16
python analysis/quarterly_holdings_analysis.py X 2025-12-15
python analysis/quarterly_holdings_analysis.py B 2024-06-17
```

**四部分输出：**

| 部分 | 内容 | 说明 |
|------|------|------|
| **一、板块权重** | 策略 vs HS300 权重对比 | 🔥🔥 超配 / ❄️❄️ 低配标注 |
| **二、本期收益** | 逐板块收益 & 贡献 | 右侧对照 HS300 同期表现 |
| **三、差异归因** | 不持有板块错失 + 持有板块选股拖累 | 自动识别四大天然排除板块 |
| **四、历史分位** | 可比口径排名 + 历史类似大跌 | HS300 全收益日线 + 调仓日期序列 |

**关键设计：**

- 自动识别策略不持有板块（电子/通信/银行/非银），计算剔除后的 HS300 可比收益
- 申万行业 90+ 种自动归并为 30 个一级板块
- 历史分位基于 HS300 全收益日线 + 46 期调仓日期序列
- 支持 B/D/E/F/X 全部版本

**典型输出示例（E版 2026-03-16）：**

```
E版总收益: -14.91%  |  HS300总收益: +3.44%
HS300去掉4板块后: -9.93%  (vs E版差距 -5.0pp)
可比口径历史分位: 最差的 5%（仅2期更差）

E版完全不持有的板块收益:
  通信: +5.04pp | 电子: +5.25pp  ← 错过的科技涨势

共有板块E版额外拖累:
  汽车: E版-19.7% vs HS300-8.7% → 额外-1.7pp
  有色: E版-23.4% vs HS300-13.7% → 额外-0.9pp
```

---

## 红利周期投资 — 周度量化评估系统

## 项目结构

```
weekly_harness/
├── run_bdefx_full.py      # ★ FCF五版回测主入口（B/D/E/F/X）
├── run_g_full.py           # G版（自适应TopN）回测
├── run_h_full.py           # H版（FCF绝对值排序）回测
├── run_weekly.py           # 周度评估入口
├── run_backtest.py         # 红利策略回测入口
├── weekly_harness/
│   ├── fcf_universe.py     # ★ FCF选股核心引擎（1605行）
│   ├── strategy.py         # 红利周期轮动策略
│   ├── scanner.py          # 红利股扫描器
│   ├── backtest.py         # 回测引擎
│   ├── portfolio.py        # 持仓管理
│   ├── generator.py        # 数据拉取+评分计算
│   ├── validator.py        # 数据质量校验
│   └── reporter.py         # 周报生成
├── analysis/
│   ├── fund_tracker.py     # 基金/指数/策略净值跟踪
│   ├── tracker_config.py   # 跟踪标的配置（16只）
│   ├── index_backtest.py   # 指数回测对比
│   └── quarterly_holdings_analysis.py  # ★ 季度持仓板块分析模板
├── docs/                    # ★ 报告文档（保留在git中）
│   ├── project_progress_fcf_strategy.md  # 项目进展追踪
│   ├── zz800_bdefx_strategy_comparison.md # FCF五版主报告
│   └── ...                  # 详见 docs/ 目录
├── data/                    # 数据目录（.gitignore排除）
├── cache/                   # 实验缓存（.gitignore排除）
└── output/                  # 回测输出（.gitignore排除）
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

---

## 基金 & 指数跟踪工具

> 文件：`analysis/fund_tracker.py`  
> 配置：`analysis/tracker_config.py`（在此添加/删除跟踪标的）  
> 报告输出：`docs/tracker/{mode}_{date}.md`

### 快速使用

```bash
# 快照：所有标的多区间收益 + 最大回撤 + 夏普（默认截至今天）
python analysis/fund_tracker.py

# 周报：快照 + 近4周逐周涨跌
python analysis/fund_tracker.py --mode weekly

# 月报：快照 + 近12月逐月涨跌
python analysis/fund_tracker.py --mode monthly

# 季报：快照 + 全部 ETF 最新季末持仓 Top10
python analysis/fund_tracker.py --mode quarter

# 指定截止日期（回溯历史）
python analysis/fund_tracker.py --mode quarter --end 20251231

# 仅打印，不保存文件
python analysis/fund_tracker.py --no-save
```

### 当前跟踪标的（16 只）

| 分类 | 代码 | 名称 | 备注 |
|------|------|------|------|
| 自建FCF策略 | MY_B/D/E/F | 自建ZZ800 FCF B/D/E/F版 | 季度节点，线性插值估算短周期 |
| 现金流指数 | 980092.CNI | 国证自由现金流（价格） | +股息补偿 |
| 现金流指数 | 480092.CNI | 国证FCF全收益 | 官方全收益 |
| 现金流指数 | 932368.CSI | 中证800现金流（价格） | +股息补偿 |
| 现金流指数 | 932365.CSI | 中证全指现金流（价格） | +股息补偿 |
| 现金流ETF | 159229.SZ | 中证800现金流ETF | 华夏 |
| 现金流ETF | 159201.SZ | 自由现金流ETF | 华夏，跟踪国证 |
| 红利指数 | H00922.CSI | 中证红利全收益 | 官方全收益，无估算误差 |
| 红利ETF | 515180.SH | 红利ETF | 易方达 |
| 红利ETF | 510880.SH | 红利ETF | 华泰柏瑞 |
| 宽基基准 | H00300.CSI | 沪深300全收益 ▶ | 基准 |
| 宽基基准 | H00905.CSI | 中证500全收益 ▶ | 基准 |
| 宽基基准 | H00852.CSI | 中证1000全收益 ▶ | 基准 |

### 报告说明

- **快照区间**：1W / 1M / 3M / 6M / YTD / 1Y / 3Y
- **自建策略**：季度节点之间用**线性插值**估算短周期收益；最大回撤/夏普/波动基于前向填充的日序列
- **注意**：高股息价格指数 + 估算股息在短周期（≤3M）可能因年末一次性补偿产生时序误差，建议以全收益指数版本为准

### 新增跟踪标的

编辑 `analysis/tracker_config.py`，在 `WATCHLIST` 列表中追加：

```python
{
    "code":      "XXXXXX.SH",          # Tushare 代码
    "name":      "自定义名称",
    "category":  "分类标签",
    "type":      "etf",                # etf / index_tr / index_price / strategy
    "benchmark": False,
}
```

---

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
