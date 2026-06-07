# FCF 实验数据本地缓存设计

> 创建：2026-06-06 | 目标：统一管理 ZZ800/HS300 FCF 实验所需的所有数据，支持快速迭代

---

## 一、当前数据现状

### 数据总量

| 目录 | 大小 | 内容 | 问题 |
|------|------|------|------|
| `data/fcf_financials/` | 25M | 88个CSV（cashflow/income/balance 年报+季度） | ⚠️ 有重复文件名（balance vs balancesheet） |
| `data/fcf_financials/daily_basic_cache/` | — | 45个CSV（调仓日市值快照） | 命名不一致 |
| `data/adj_close_cache/` | 83M | 840个个股复权价CSV | OK |
| `data/index_weights/` | 5.7M | 37个文件（成分权重+历史） | ⚠️ 命名混乱（932368_202412 vs index_weight_932368.CSI_20241231） |
| `data/index_daily/` | 600K | 指数日行情 | OK |
| `data/stock_daily/` | 106M | 个股日行情 | OK |
| `data/stock_daily_hfq/` | 100M | 个股后复权行情 | OK |
| `output/*/` | 6M | 篮子JSON+NAV | OK |

### 核心痛点

1. **财务数据重复**：`balance_2011.csv` vs `balancesheet_2011.csv`，两种命名共存
2. **市值缓存散落**：`daily_basic_cache/` 放在 `fcf_financials/` 子目录里，但属于行情数据
3. **指数权重命名混乱**：同一数据有3种命名格式
4. **版本标记缺失**：无法区分"旧PQ口径"篮子和"新PQ口径"篮子
5. **无实验元数据**：不知道某个篮子JSON是用什么参数生成的

---

## 二、目标缓存架构

```
cache/                          ← 统一数据缓存根目录（替代散落的 data/）
├── financials/                 ← 财务数据（最核心，25M）
│   ├── cashflow/               ← 按表+年份组织
│   │   ├── annual/             ← 年报
│   │   │   ├── 2011.parquet    ← 统一用parquet（压缩4x,读取快10x）
│   │   │   ├── ...
│   │   │   └── 2025.parquet
│   │   └── quarterly/          ← 季报
│   │       ├── 2023Q1.parquet
│   │       ├── ...
│   │       └── 2024Q3.parquet
│   ├── income/                 ← 利润表（同结构）
│   │   ├── annual/
│   │   └── quarterly/
│   ├── balance/                ← 资产负债表（同结构）
│   │   ├── annual/
│   │   └── quarterly/
│   └── stock_list.parquet      ← 股票基础信息（名称/行业）
│
├── market/                     ← 行情数据
│   ├── daily_basic/            ← 每日指标（市值/换手率等）
│   │   ├── 20150313.parquet    ← 调仓日快照（原来45个CSV）
│   │   ├── ...
│   │   └── 20260313.parquet
│   ├── adj_close/              ← 复权价格（原来840个CSV → 合并为parquet）
│   │   ├── zz800.parquet       ← 按指数范围合并
│   │   └── hs300.parquet
│   ├── stock_daily/            ← 个股日行情
│   │   ├── zz800.parquet
│   │   └── hs300.parquet
│   └── index_daily/            ← 指数日行情
│       ├── 000300.SH.parquet
│       ├── 000905.SH.parquet
│       ├── 000906.SH.parquet
│       ├── 932366.CSI.parquet
│       └── 932368.CSI.parquet
│
├── index/                      ← 指数成分&权重
│   ├── members/                ← 成分股列表
│   │   ├── 000300.SH.parquet
│   │   ├── 000906.SH.parquet
│   │   └── 000985.SH.parquet
│   ├── weights/                ← 权重快照（统一命名）
│   │   ├── 932366/             ← 每个指数一个子目录
│   │   │   ├── 20241231.parquet
│   │   │   ├── 20250127.parquet
│   │   │   ├── ...
│   │   │   └── 20250630.parquet
│   │   ├── 932368/
│   │   │   ├── 20241231.parquet
│   │   │   ├── ...
│   │   ├── 562080/
│   │   └── hs300/             ← 000300.SH
│   └── history.json            ← 权重下载历史记录
│
├── experiment/                 ← ⭐ 实验结果（新增！核心改进）
│   ├── zz800_fcf/              ← 每个实验一个目录
│   │   ├── config.yaml         ← ⭐ 实验参数（FcfCalcConfig快照）
│   │   ├── baskets.parquet     ← 所有调仓期篮子（替代all_baskets_2015_2026.json）
│   │   ├── nav.parquet         ← NAV曲线
│   │   ├── metrics.json        ← 绩效指标摘要
│   │   └── vs_932368.json       ← 与官方对比指标
│   │   └── meta.yaml           ← ⭐ 实验元数据（时间/版本/备注）
│   ├── zz800_fcf_pq_fixed/     ← PQ修复后的实验
│   │   ├── config.yaml         ← pq_base=non_financial
│   │   ├── baskets.parquet
│   │   ├── nav.parquet
│   │   └── ...
│   ├── hs300_fcf/
│   │   ├── config.yaml
│   │   └── ...
│   └── hs300_fcf_lenient_buffer/
│       ├── config.yaml         ← buffer_zone=20
│       └── ...
│
└── manifest.json               ← ⭐ 全局缓存清单（什么数据+什么版本+什么时间）
```

---

## 三、核心设计：experiment/config.yaml

每个实验必须附带配置快照，这是**最重要的改进**：

```yaml
# cache/experiment/zz800_fcf_pq_fixed/config.yaml
experiment_id: zz800_fcf_pq_fixed
created: 2026-06-06T08:30:00
description: "Bug-1修复：PQ基数不含金融地产"

# 策略参数
strategy:
  index_code: "000906.SH"       # 中证800
  universe_size: 800
  top_n: 50
  weight_cap: 0.10
  rebalance_freq: quarterly      # 3/6/9/12月第二周五后

# FCF计算口径
calc_config:
  ev_mv_field: total_mv          # total_mv | circ_mv
  use_ttm: false                 # true=TTM | false=年报优先
  capex_field: c_pay_acq_const_fiolta
  pq_base: non_financial         # ⭐ all | non_financial ← Bug-1修复点
  pq_cutoff_percentile: 20       # 剔除后20%
  ocf_mode: lenient              # lenient | strict
  buffer_zone: null              # null | 20(±20% rank)
  5yr_ocf_check: true

# 依赖数据版本
data_version:
  financials: "2025_annual+2024Q3"
  daily_basic: "2026Q1_rebalance_dates"
  index_weights: "932368_20250630"
```

**好处**：
- 每个实验结果**自描述**，不需要翻代码就知道用的什么参数
- 参数改了 → 新建一个实验目录，旧结果**不会被覆盖**
- 可以用 `diff config.yaml` 快速对比两个实验的参数差异

---

## 四、核心设计：manifest.json

全局缓存清单，追踪所有本地数据的版本和来源：

```json
{
  "last_updated": "2026-06-06T08:30:00",
  "financials": {
    "annual_years": [2011, 2012, ..., 2025],
    "quarterly_periods": ["2023Q1", "2023Q2", ..., "2024Q3"],
    "format": "parquet",
    "row_counts": {
      "cashflow_2024": 5230,
      "income_2024": 5230,
      "balance_2024": 5230
    }
  },
  "market": {
    "daily_basic_dates": ["20150313", "20150612", ..., "20260313"],
    "adj_close_stocks": 840,
    "format": "parquet"
  },
  "index": {
    "members": ["000300.SH", "000906.SH", "000985.SH"],
    "weights": {
      "932366": ["20241231", "20250127", ..., "20250630"],
      "932368": ["20241231", "20250331", "20250630"]
    }
  },
  "experiments": [
    {"id": "zz800_fcf", "created": "2025-01", "pq_base": "all"},
    {"id": "zz800_fcf_pq_fixed", "created": "2026-06-06", "pq_base": "non_financial"},
    {"id": "hs300_fcf_lenient_buffer", "created": "2025-03", "buffer_zone": 20}
  ]
}
```

---

## 五、 baskets.parquet 格式设计

替代当前的 `all_baskets_2015_2026.json`（880KB），用 parquet 更高效：

```python
# Schema
import pandas as pd

basket_df = pd.DataFrame({
    'rb_date': ['2015-06-15', ..., '2026-03-16'],  # 调仓日期
    'ts_code': ['601919.SH', ...],                   # 股票代码
    'name': ['中远海控', ...],                        # 名称
    'industry': ['水运', ...],                        # 申万行业
    'rank': [1, ...],                                 # FCF率排名
    'fcf': [433.1e8, ...],                            # FCF（元）
    'ev': [137.13e10, ...],                           # EV（元）
    'fcf_yield': [0.161, ...],                        # FCF率
    'profit_quality': [0.0047, ...],                  # PQ
    'weight': [0.0704, ...],                          # 权重
    'is_financial': [False, ...],                     # 是否金融地产
})
# 约 50只 × 45期 = 2250行 → parquet约50KB（压缩比17x vs JSON）
```

---

## 六、实施计划

| 步骤 | 任务 | 预计时间 | 优先级 |
|------|------|---------|--------|
| 1 | 创建 `cache/experiment/` 目录结构 | 30分钟 | P0 |
| 2 | 把现有 output JSON 转为 experiment 格式（含config.yaml） | 2小时 | P0 |
| 3 | 修复PQ基数bug，生成 zz800_fcf_pq_fixed 实验 | 1小时 | P0 |
| 4 | 财务数据 CSV→parquet 转换脚本 | 3小时 | P2 |
| 5 | 指数权重统一命名+整理 | 1小时 | P2 |
| 6 | 市值缓存 daily_basic 整理 | 1小时 | P2 |
| 7 | manifest.json 自动生成 | 1小时 | P3 |
| 8 | `DataClient` 统一接口封装 | 3天 | P3 |

### 步骤1-3 是最小可行方案（MVP），先执行

最小改动：不迁移旧数据格式，只新增 `cache/experiment/` 目录 + config.yaml + meta.yaml。

---

## 七、与现有代码的兼容

### 短期（不改 fcf_universe.py 读取逻辑）

```python
# 现有代码继续用 CSV + JSON，新增实验目录只是额外层
# experiment/config.yaml 是纯文档，不影响运行
# baskets 继续用 JSON，未来可选转 parquet
```

### 中期（加 DataClient）

```python
from weekly_harness.data import DataClient

client = DataClient(cache_dir="cache")

# 自动从 cache/ 加载，格式透明（CSV/parquet/JSON 都行）
fin = client.get_annual_financials("600938.SH", year=2024)
mv = client.get_market_cap("600938.SH", date="2026-03-16")
members = client.get_index_members("000906.SH", date="2026-03-16")

# 实验结果
exp = client.load_experiment("zz800_fcf_pq_fixed")
print(exp.config)       # FcfCalcConfig
print(exp.baskets)      # DataFrame
print(exp.metrics)      # dict
```