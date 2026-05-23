# 红利周期投资 — 周度量化评估系统

基于**红利周期三层估值体系**的 A 股红利股周度自动评估工具。

## 项目结构

```
weekly_harness/
├── run_weekly.py           # 主入口（每周运行这个）
├── dividend_evaluator.py  # 核心评分引擎（TushareDataFetcher + DividendCycleEvaluator）
├── config/
│   └── settings.py         # 配置（tushare token 等）
├── weekly_harness/
│   ├── criteria.json       # 评估标准配置
│   ├── planner.py          # Phase 1: 确定评估范围 + 国债利率
│   ├── generator.py        # Phase 2: 拉取数据 + 计算评分
│   ├── validator.py        # Phase 3: 数据质量校验
│   └── reporter.py         # Phase 4: 生成周报 + 历史对比
├── data/
│   ├── weekly_history.csv  # 历史时间序列（累积）
│   └── weekly_reports/     # 每周报告目录
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
