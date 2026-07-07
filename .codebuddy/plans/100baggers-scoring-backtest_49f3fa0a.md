---
name: 100baggers-scoring-backtest
overview: 回溯验证：在2023年6月用当前SQGLP打分体系对中证800+中证1000成分股打分，对比2023-06→2026-06实际收益率，评估打分系统的预测能力（precision/recall）。
todos:
  - id: verify-data-availability
    content: 使用[subagent:code-explorer]验证数据可用性：检查adj_close_cache覆盖范围、index_weight历史数据格式、fina_indicator_vip对2019-2022年度的字段支持
    status: completed
  - id: create-backtest-script
    content: 创建backtest_baggers_scoring.py主脚本：实现历史成分股获取、前视偏差过滤、历史时点评分、3年收益计算四阶段逻辑
    status: completed
    dependencies:
      - verify-data-availability
  - id: run-backtest
    content: 执行回测：对1800+只成分股完成打分+收益追踪，生成output/baggers_backtest_202306.csv
    status: completed
    dependencies:
      - create-backtest-script
  - id: precision-recall-analysis
    content: 分析precision/recall：按分数阈值和Layer层级切分，计算TP/FP/FN/TN，输出完整的分类指标表
    status: completed
    dependencies:
      - run-backtest
  - id: generate-report
    content: 生成回测分析报告docs/2026-06-24_baggers_scoring_backtest.md：包含指标汇总、高分10倍股详情、漏网之鱼分析、框架改进建议
    status: completed
    dependencies:
      - precision-recall-analysis
---

## 用户需求

定量回测100 Baggers SQGLP打分系统的预测能力：站在2023年6月的时间点，对中证800+中证1000成分股打分，然后追踪这些股票在此后3年（2023-06→2026-06）的实际收益，计算precision/recall，评估框架有效性。

## 产品概述

一个独立的回测脚本，在不修改现有find_baggers.py的前提下，复用其打分核心逻辑，针对历史时间点执行打分 + 收益追踪，输出分类指标报告。

## 核心功能

- **历史成分股获取**：获取2023年6月中证800（000906.SH）和中证1000（000852.SH）的成分股列表，合并去重
- **前视偏差防护**：剔除2023年6月之后上市的股票；财务数据仅使用2019-2022年度年报（当时最新可用数据为2022年报）
- **历史时点评分**：对每只成分股，使用2023年6月的估值数据（PE/PB/总市值）和2019-2022年财务数据，执行完整的SQGLP五维打分（S销售增长、Q质量、G盈利增长、L赛道、P估值）并记录总分及Layer1/2/3层级判断
- **3年收益计算**：基于后复权价格计算每只股票从2023年6月到2026年6月的总收益率（含股息复投），标识是否达到10倍
- **Precision/Recall分析**：按不同分数阈值（总分≥80/70/60/50/40）和层级（Layer1/2/3通过）分别计算precision（该组中10倍股占比）和recall（全体10倍股中被该组捕获的比例），输出完整的分类报告

## 技术栈

- Python 3 + pandas
- Tushare Pro API（fina_indicator_vip、daily_basic、index_weight、stock_basic、trade_cal）
- 本地数据缓存（data/adj_close_cache/、data/index_weights/、data/baggers/）

## 实现方案

### 核心策略：完全复用现有打分引擎，零修改

不修改find_baggers.py，而是通过以下方式实现历史回测：

1. **动态CFG注入**：在回测脚本中创建一个历史版CFG，将`data_years`改为`[2019, 2020, 2021, 2022]`，其余参数保持不变
2. **函数级复用**：从find_baggers.py直接import `score_stock`、`HIGH_QUALITY_INDUSTRIES`、`LOW_QUALITY_INDUSTRIES`等核心函数，不复制代码
3. **数据获取层覆写**：回测脚本自行实现历史版本的数据获取函数（get_fina_indicator_batch、get_daily_basic_at_date等），使用历史参数
4. **价格追踪独立**：复用data/adj_close_cache/已有缓存，使用后复权价格计算3年收益

### 前视偏差防护机制（铁律级）

| 检查项 | 实施方式 |
| --- | --- |
| 成分股时点 | 使用DynamicIndexUniverse或直接index_weight API获取2023年6月快照 |
| 财务数据截止 | fina_indicator_vip只拉取2019-2022年度（period=YYYY1231格式） |
| 估值数据时点 | daily_basic传入trade_date=202306附近的交易日 |
| 剔除后上市标的 | 通过stock_basic的list_date字段，排除list_date > 20230630的股票 |
| 行业分类 | 使用2023年6月时点的stock_basic，不用当前行业分类 |


### 性能优化

- 批量API拉取：fina_indicator_vip不传ts_code，一次拉取全年全市场数据（约5000条），然后按成分股过滤
- 价格数据本地读取：data/adj_close_cache/已有2162+只股票，直接读CSV，不触发Tushare API
- 指数成分股缓存：拉取后缓存到data/index_weights/，避免重复API调用
- 逐只打分时无API调用：所有数据已预加载到内存dict中

## 实现细节

### 文件规划

```
weekly_harness/
├── backtest_baggers_scoring.py    # [NEW] 回测主脚本（约500行）
├── output/
│   └── baggers_backtest_202306.csv  # [NEW] 输出：所有股票得分+收益
├── docs/
│   └── 2026-06-24_baggers_scoring_backtest.md  # [NEW] 回测分析报告
```

### backtest_baggers_scoring.py 核心结构

**第1阶段：数据准备**

- 通过index_weight API获取2023年6月中证800+中证1000成分股（缓存到data/index_weights/）
- 通过stock_basic获取全A股基本信息（名称、行业、上市日期）
- 过滤：仅保留list_date ≤ 20230630的标的
- 通过fina_indicator_vip批量拉取2019/2020/2021/2022四个年度的年报数据，构建{year: {ts_code: row}}映射

**第2阶段：历史时点评分**

- 获取2023年6月最近交易日的daily_basic（PE/PB/PS/总市值）
- 对每只成分股调用score_stock()，传入历史CFG（data_years=[2019,2020,2021,2022]）
- 记录：总分、S/Q/G/L/P分项得分、Layer1/2/3是否通过、reject_reason

**第3阶段：3年收益追踪**

- 从data/adj_close_cache/{ts_code}.csv读取后复权价格序列
- 使用find_nearest_price（参考find_10x_stocks.py的模式）：取2023-06之后第一个交易日的adj_close作为起点，2026-06之前最后一个交易日作为终点
- 计算总收益率 = end_price / start_price - 1
- 标识：is_10bagger = (总收益率 >= 900%)

**第4阶段：Precision/Recall分析**

- 按不同分数阈值切分：total_score >= 80/70/60/50/40
- 按层级切分：Layer1通过、Layer2通过、Layer3通过
- 对每个切分组计算：TP（高分且10倍）、FP（高分非10倍）、FN（低分但10倍）、TN（低分非10倍）
- 输出precision = TP/(TP+FP)、recall = TP/(TP+FN)、F1

**输出格式**：

- CSV：每只股票一行，包含ts_code、name、industry、mktcap_亿、各分项得分、total_score、layer层级、reject_reason、start_price、end_price、total_return_pct、is_10bagger
- Markdown报告：precision/recall汇总表、高分10倍股详情、漏网之鱼（低分但10倍）分析、框架改进建议

### 关键边界处理

- 退市股票：2023年6月之后退市的，其adj_close数据截止退市日，使用最后可用价格作为终点
- 停牌股票：起始日若停牌则向前找最近交易日
- 数据缺失：财务数据缺失超过2年则跳过该标的，记录缺失原因
- API限速：fina_indicator_vip每次调用间隔≥0.35秒（与find_baggers.py一致）

## Agent Extensions

### SubAgent

- **code-explorer**
- 目的：在实现过程中需要确认DynamicIndexUniverse的具体API签名、index_weight数据格式、adj_close_cache文件的具体列名，以及Tushare fina_indicator_vip对历史年份（2019-2022）的字段可用性
- 预期结果：获取精确的API调用方式、数据字段名、缓存文件结构，确保代码一次写对