---
name: zz800-integrity-cash-realization
overview: 基于 ZZ800 成分股，计算 8 季度累计 FCF / 累计净利润的"现金实现率"，生成诚信度排名，并做历史回测验证高诚信组 vs 低诚信组的收益差异。
todos:
  - id: download-annual-np
    content: 创建 download_zz800_annual_np.py，下载 ZZ800 成分股 2014-2025 年报 n_income_attr_p
    status: completed
  - id: build-integrity-engine
    content: 编写 analyze_integrity_zz800.py 核心引擎：单季拆解、8 窗口累积、现金实现率计算、前视偏差防护
    status: completed
    dependencies:
      - download-annual-np
  - id: current-ranking
    content: 实现当前时点（2026-06-11）ZZ800 全成分诚信度排名，输出表格和 CSV
    status: completed
    dependencies:
      - build-integrity-engine
  - id: quintile-backtest
    content: 实现 45 期五等分分组回测，等权配仓，调用 compute_nav_cached 输出五组净值曲线
    status: completed
    dependencies:
      - build-integrity-engine
  - id: report-and-compare
    content: 生成对比报告：五组 vs E版基准（年化/最大回撤/夏普/NAV），输出 docs/ 报告并更新 research_log
    status: completed
    dependencies:
      - current-ranking
      - quintile-backtest
---

## 产品概要

基于 ZZ800（中证800）成分股，计算每只股票过去 8 个季度的"现金实现率"（累计 FCF / 累计归母净利润），以此评估企业盈利诚信度。输出当前时点的全成分排名表，并做历史回测验证高诚信组与低诚信组的长期收益差异。

## 核心功能

- **现金实现率计算**：将 A 股累计制季报拆解为单季度数据，计算 8 季度窗口内累计 FCF 与累计归母净利润的比值。比值接近 1.0 表明利润有真金白银支撑；< 0.5 为利润虚高预警；> 1.5 为保守型会计。
- **当前时点排名表**：基于 2026-06-11 时点，输出 ZZ800 全成分股的诚信度排名（含现金实现率、8Q 累计 FCF、8Q 累计净利润、行业分类），按实现率从低到高排列。
- **历史回测（五等分分组）**：在 2015-03 至 2026-06 的每个调仓日，将 ZZ800 成分股按现金实现率分为五组，等权持有，跟踪各组净值曲线。与 E 版基准对比年化收益、最大回撤、夏普比率、期末 NAV。
- **报告归档**：生成 markdown 报告，含当前排名表摘要、回测对比表、结论建议。

## 技术栈

- 语言：Python 3
- 数据获取：Tushare Pro API
- 数据处理：Pandas + NumPy
- 回测框架：复用 `compute_nav_cached.get_adj_close_cached()` 做净值计算
- 现有组件：`fcf_universe.py`（IndexWeightCache, FinancialDataCache）

## 实现方案

### 总体策略

分三阶段：**补数据 → 算排名 → 做回测**。核心思路是最大程度复用现有数据基础设施（`quarterly_income.csv`、`quarterly_cashflow.csv`、现金流的年度缓存），仅补充缺失的年度 `n_income_attr_p` 数据。分析逻辑独立封装在一个新脚本中，不修改现有策略代码。

### 单季度拆解算法

A 股季报数据均为年初至今累计值，需拆为单季度：

```
单季 Q1 = Q1 累计值
单季 Q2 = Q2 累计值 - Q1 累计值
单季 Q3 = Q3 累计值 - Q2 累计值
单季 Q4 = 年报值 - Q3 累计值
```

### 8 季度窗口选择

以当前时点 2026-06-11 为例，最新完整季报为 2026Q1。8 个季度为：2026Q1 → 2025Q4 → 2025Q3 → 2025Q2 → 2025Q1 → 2024Q4 → 2024Q3 → 2024Q2。

公式：

```
8Q 累计 FCF = sum(8 个单季 FCF)
8Q 累计净利润 = sum(8 个单季 n_income_attr_p)
现金实现率 = 8Q 累计 FCF / 8Q 累计净利润
```

### 前视偏差防范

回测时每个调仓日只能使用该日期之前已公告的季报：

- 年报（Q4）：调仓日在 4/30 之后才可用（假设 4/30 为年报截止日）
- Q1 季报：调仓日在 4/30 之后才可用
- Q2 季报（半年报）：调仓日在 8/31 之后才可用
- Q3 季报：调仓日在 10/31 之后才可用
- 若某季报的 `ann_date` 晚于 `rebalance_date`，该季报不可用，向前用更早的完整窗口
- IPO 股票：`list_date` 晚于 `rebalance_date` 的直接排除

### 回测分组策略

每个调仓日：

1. 获取当日 ZZ800 成分股
2. 排除 IPO 晚于调仓日 + 上市不足 2 年（8 季度窗口需要 2 年数据）
3. 对剩余股票计算现金实现率
4. 按实现率五等分，等权配置
5. 跟踪各组下期收益（到下一个调仓日）
6. 构建完整的净值序列

### 性能考量

- 现金流、净利润数据均缓存在本地 CSV，读取为 Pandas DataFrame 后构建字典索引 `{(ts_code, end_date8): value}`，O(1) 查找
- 回测 45 期 × 平均 700 只成分股 ≈ 31,500 次计算，每期耗时约 2-3 秒，总耗时约 2 分钟
- 无 API 调用（全部离线数据），零网络延迟

## 实现细节

### 数据下载（年度归母净利润）

- 参照 `download_quarterly_income.py` 的断点续传模式
- 只下载 ZZ800 历史成分股，避免全市场下载
- 输出文件：`data/fcf_financials/annual_income_np.csv`
- 字段：`ts_code, end_date, n_income_attr_p`
- 覆盖年份：2014-2025（年报 end_date 格式为 YYYY1231）

### 日志与报告

- 复用项目现有日志格式（`print` 直接输出）
- 报告文件命名：`docs/2026-06-11_zz800_integrity_analysis.md`
- 报告内容包含：当前排名表 Top 20 / Bottom 20、五组回测对比表（vs E 版）、研究日志追加

### 兼容性

- 不修改 `fcf_universe.py`、`run_bdefx_full.py` 等现有策略文件
- 不修改 `docs/` 下已有报告
- 不删除 `data/` 下任何缓存文件
- 输出目录：`output/integrity/`

## 架构设计

```mermaid
flowchart TD
    A[download_zz800_annual_np.py] -->|生成| B[annual_income_np.csv]
    C[已有: quarterly_income.csv] --> D[analyze_integrity_zz800.py]
    E[已有: quarterly_cashflow.csv] --> D
    F[已有: cashflow_{year}.csv] --> D
    B --> D
    G[已有: IndexWeightCache] --> D
    D -->|Part 1| H[当前诚信度排名表 CSV]
    D -->|Part 2| I[五等分分组回测结果]
    D -->|Part 3| J[docs/ 报告]
    K[已有: compute_nav_cached] --> D
    L[已有: E版净值 JSON] --> D
```

## 目录结构

```
weekly_harness/
├── download_zz800_annual_np.py          # [NEW] 下载 ZZ800 年度归母净利润。
│                                         #   参照 download_quarterly_income.py 断点续传模式，
│                                         #   仅下载 ZZ800 历史成分股，字段 ts_code/end_date/n_income_attr_p，
│                                         #   覆盖 2014-2025 年报，输出到 data/fcf_financials/annual_income_np.csv
│
├── analyze_integrity_zz800.py           # [NEW] ★ 主分析脚本。实现：
│                                         #   1）加载 quarterly_income.csv + quarterly_cashflow.csv + annual_income_np.csv
│                                         #   2）按 (ts_code, end_date8) 构建 O(1) 字典查找索引
│                                         #   3）compute_standalone_quarters()：累计值 → 单季值
│                                         #   4）compute_cash_realization_ratio(code, ref_date)：
│                                         #      选择 ref_date 前最新的 8 个完整季度窗口，
│                                         #      计算 8Q 累计 FCF / 8Q 累计 NP
│                                         #   5）generate_current_ranking()：输出 TOP/BOTTOM 表格 + CSV
│                                         #   6）run_quintile_backtest()：45 期 × 五等分分组回测，
│                                         #      等权配仓，调用 compute_nav_cached 算净值
│                                         #   7）generate_report()：生成 markdown 报告
│
├── data/fcf_financials/
│   └── annual_income_np.csv             # [NEW] ZZ800 年度归母净利润缓存
│
├── output/integrity/
│   ├── ranking_current.csv              # [NEW] 当前时点诚信度排名
│   ├── quintile_baskets.json            # [NEW] 各期五等分持仓明细
│   └── quintile_nav.csv                 # [NEW] 五组净值曲线
│
└── docs/
    └── 2026-06-11_zz800_integrity_analysis.md  # [NEW] 完整分析报告
```

## Agent Extensions

### SubAgent

- **code-explorer**
- 用途：在实现过程中需要跨多文件搜索时，用于快速定位具体代码段（如 compute_nav_cached 的接口签名、E 版净值文件格式、IndexWeightCache 用法）
- 预期成果：提供精确的函数签名和数据结构引用，确保分析脚本与现有接口无缝对接