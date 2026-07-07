---
name: ex-right-recovery-stats
overview: 新建分析脚本，统计中证红利800持仓期间的分红除权事件中，纯价格填权（不复权收盘价在10个交易日内回到除权前水平）的比例，输出总体 + 分年度统计
todos:
  - id: build-data-loading
    content: "搭建数据加载和持仓区间索引：实现load_baskets()、build_holding_index()、load_trade_cal()，构建{ts_code: [(start,end), ...]}索引和交易日列表"
    status: completed
  - id: implement-recovery-check
    content: 实现除权事件筛选和填权判断核心逻辑：遍历持仓股票→读dividend_history→筛选div_proc='实施'且在持仓区间的ex_date→读adj_close_cache→取P0+后10日close→判断填权
    status: completed
    dependencies:
      - build-data-loading
  - id: generate-report
    content: 实现统计汇总和报告生成：计算总体填权率和分年度填权率，打印控制台表格，生成docs/2026-07-03_800红利填权统计.md报告
    status: completed
    dependencies:
      - implement-recovery-check
  - id: execute-and-verify
    content: 执行脚本生成统计结果，验证输出数据合理性和完整性
    status: completed
    dependencies:
      - generate-report
---

## 用户需求

统计中证红利800策略选出的股票，在持仓期间发生的分红除权事件中，股价在2周（10个交易日）内恢复到除权前水平的比例，即"填权成功率"。

## 核心功能

- **数据加载**：从本地缓存读取800红利23期持仓篮（`output/800div/all_baskets_2015_2026.json`）、分红除权历史（`data/dividend_history/`）、不复权日线价格（`data/adj_close_cache/`）、交易日历（`data/trade_cal.csv`），全程零API调用
- **持仓区间计算**：对每只持仓过的股票，构建其在800红利篮子内的时间区间列表（start=本期调仓日，end=下期调仓日-1；最后一期 end=start+180天）
- **除权事件筛选**：读取每只股票的分红历史，仅保留`div_proc='实施'`且有有效`ex_date`、且ex_date落在持仓区间内的事件
- **填权判断**：对每个事件，取除权前一日不复权收盘价P0，取除权后10个交易日的收盘价序列，10个交易日内任一收盘价≥P0即为填权成功
- **统计汇总**：输出总体填权率（成功/总数/%）、分年度填权率（每年成功/失败/成功率/样本数），生成Markdown报告存入`docs/`

## 技术栈

- **语言**: Python 3
- **依赖**: pandas, numpy, json（均已在项目中广泛使用）
- **数据源**: 全部本地CSV/JSON缓存，零API调用
- **复用模式**: 参考`dividend_universe.py`的数据加载逻辑（`_load_stock_basic`、`_load_trading_calendar`）；参考`run_800div_full.py`的持仓数据结构理解

## 实现方案

### 整体策略

新建单文件分析脚本`analyze_800div_ex_right_recovery.py`，按"加载持仓→构建区间索引→遍历分红事件→读取日线→判断填权→汇总输出"六步流水线执行。所有数据从本地缓存读取，无需Tushare API，运行时间取决于股票数量，预计总事件数约2000-4000条，总耗时控制在2分钟内。

### 技术决策

1. **用`adj_close_cache`的`close`字段而非`adj_close`**：`close`是原始不复权收盘价，直接对应除权当日的实际股价跳空缺口；`adj_close`是后复权价已消除除权影响，无法判断填权。此选择与用户"纯价格填权"定义严格一致。

2. **用`trade_cal.csv`计算交易日**：除权后"2周=10个交易日"而非14个自然日，必须用交易日历精确计算，避免周末/节假日导致交易日不足10天误判。

3. **持仓区间用调仓日边界**：`[本期调仓日, 下期调仓日)`，左闭右开。ex_date必须严格落在此区间内才算持仓期间事件。最后一期（2026-06-15）无下期，用 start+180天近似（半年度调仓周期）。

4. **单文件独立脚本**：不修改`dividend_universe.py`或`run_800div_full.py`，保持核心引擎纯净；统计逻辑自包含，便于复现和迭代。

### 实现细节

#### 性能优化

- 持仓区间用`defaultdict(list)`预索引，O(1)查某只股票的持仓时间段
- 除权事件用`div_proc='实施'`预过滤，跳过大量预案/股东大会通过记录
- 日线价格读入后用`dict(zip(df['trade_date'], df['close']))`构建O(1)查找
- 交易日历提前加载为`sorted(set)`，10个交易日用`bisect_left`定位ex_date后取slice，O(log n)

#### 日志与进度

- 大规模循环处理（遍历1946只股票的分红文件）时，每100只打印进度条
- 最终汇总表格直接打印到控制台，同时写入Markdown文件

#### 边界处理

- `ex_date`为空字符串或NaN → 跳过该事件（预案阶段常见）
- `adj_close_cache`文件缺失 → 该事件标注为"数据缺失"
- 除权后不足10个交易日（如最近除权且数据未到）→ 用实际可用交易日判断，记录可用天数
- 除权前一日价格缺失（ex_date前最近交易日无数据）→ 标注为"数据缺失"

#### 防御性设计

- 不修改任何现有文件（`dividend_universe.py`、`run_800div_full.py`）
- 不覆盖任何现有`docs/`文件
- 所有输出独立可复现

## 目录结构

```
weekly_harness/
├── analyze_800div_ex_right_recovery.py  # [NEW] 核心分析脚本
│   # 功能：六步流水线：加载持仓→构建区间索引→筛选除权事件→读取日线→判断填权→汇总输出
│   # 实现：
│   #   1. load_baskets() — 读取 output/800div/all_baskets_2015_2026.json，返回 {date: [stock_dicts]}
│   #   2. build_holding_index() — 构建 {ts_code: [(start, end), ...]} 区间索引
│   #   3. load_trade_cal() — 读 trade_cal.csv，返回 sorted list of 交易日
│   #   4. get_post_ex_dates() — 给定 ex_date 和 trade_cal，返回后10个交易日列表
│   #   5. check_recovery() — 给定价格序列和P0，判断10日内是否填权
│   #   6. process_events() — 主循环：遍历所有持仓过的股票，筛选事件，判断填权
│   #   7. generate_report() — 汇总统计，打印表格 + 输出 Markdown 报告
│
└── docs/
    └── 2026-07-03_800红利填权统计.md     # [NEW] 统计报告
        # 内容：总体填权率 + 分年度填权率表格 + 数据说明 + 结论
```

## 关键数据结构

```python
# 持仓区间索引类型
HoldingIndex = Dict[str, List[Tuple[str, str]]]
# {"600507.SH": [("2015-06-15", "2015-12-13"), ...], ...}

# 单次除权事件记录
EventRecord = Dict[str, Any]
# {
#     "ts_code": "600507.SH",
#     "name": "方大特钢",
#     "ex_date": "2016-06-08",
#     "cash_div_tax": 0.15,
#     "price_before": 5.20,      # 除权前收盘价(P0)
#     "recovered": True,          # 是否10日内填权
#     "recovery_day": 3,          # 第几个交易日填权（-1表示未填权）
#     "recovery_price": 5.25,     # 填权当日收盘价
#     "available_days": 10,       # 实际可用交易日数
# }
```