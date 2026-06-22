---
name: h30269-dividend-lowvol-replication
overview: 复现中证红利低波动指数（H30269）：CSI 800为样本空间，税后股息率+EPS增长过滤+支付率过滤，年度调仓（12月），50只样本，15%上限
todos:
  - id: download-eps
    content: 下载 basic_eps 数据：通过 Tushare pro.income() 按年批量拉取2012-2025年全市场EPS，存到 data/income/income_{year}.csv
    status: pending
  - id: create-engine
    content: 创建 dividend_h30269.py 引擎：继承 DividendLowvolEngine，新增 _load_eps_cache()、覆盖 _load_dividend_cache() 增加DPS计算、覆盖 select_basket() 增加支付率和DPS增长过滤、实现纯股息率加权+15%上限
    status: pending
    dependencies:
      - download-eps
  - id: create-backtest
    content: 创建 run_h30269_full.py 回测入口：年度12月11期调仓全流程（preload→选股→NAV→报告），与800红利、E版FCF、930955红利低波100对比
    status: pending
    dependencies:
      - create-engine
  - id: run-and-report
    content: 运行回测并生成报告：执行完整回测，生成 docs/2026-06-14_H30269红利低波50回测报告.md
    status: pending
    dependencies:
      - create-backtest
  - id: create-strategy-yaml
    content: 创建 strategies/h30269_lowvol/strategy.yaml 策略档案
    status: pending
    dependencies:
      - run-and-report
  - id: archive-log
    content: 归档实验记录：更新 docs/research_log.md
    status: pending
    dependencies:
      - run-and-report
---

## 用户需求

复现中证红利低波动指数 H30269（红利低波50），基于现有的 930955（红利低波100）引擎改造。

### 与当前930955的关键差异

| 维度 | 当前930955 | 目标H30269 |
| --- | --- | --- |
| 样本数 | Top **100** | Top **50** |
| 股息率类型 | 税前 | **税后**（`cash_div_tax`） |
| 支付率过滤 | 无 | **剔除前5%过高 + 剔除为负** |
| DPS增长过滤 | 无 | **剔除过去三年每股股利增长率非正** |
| 调仓频率 | 季度（45期） | **年度12月**（~11期） |
| 加权方式 | 股息率/波动率 | **纯股息率加权** |
| 单股上限 | 10% | **15%** |
| 行业上限 | 20% | **无** |
| 选股流程 | 股息率Top300→波动率Top100 | 支付率+DPS增长过滤→税后股息率Top75→波动率Top50 |
| 样本空间 | CSI 800 | **CSI 800（保持一致）** |


### 保持不变

- 样本空间：CSI 800（000906.SH）
- 连续三年分红过滤
- 波动率计算方式
- 流动性过滤：跳过（CSI 800自带）

## 核心功能

- **数据下载**：按年批量拉取 Tushare `pro.income()` 获取 `basic_eps`，存入 `data/income/`
- **支付率过滤**：计算 `DPS / basic_eps`，剔除为负及排名前5%过高的证券
- **DPS增长过滤**：计算过去三年每股股利，剔除任一年增长率非正的证券
- **税后股息率排名**：使用 `cash_div_tax` 计算三年平均税后股息率，降序取Top75
- **波动率排名**：从Top75中按波动率升序取Top50
- **纯股息率加权**：单股上限15%，无行业上限

## 技术方案

### 整体策略

新建独立引擎 `DividendH30269Engine`，**继承** `DividendLowvolEngine`（现有930955引擎），仅覆盖差异方法，复用数据加载、波动率缓存、价格获取等公共逻辑。不修改已有引擎文件。

### 数据下载策略

| 数据 | 下载方式 | 缓存位置 | 备注 |
| --- | --- | --- | --- |
| basic_eps | `pro.income()` 按年批量 | `data/income/income_{year}.csv` | 首次全量下载，后续读盘 |
| DPS（每股） | 从 `dividend_history` 计算 | 内存计算 | `cash_div / (base_share*10000)` |


### 关键公式

```
每股股利 DPS = cash_div / (base_share * 10000)
税后DPS = cash_div_tax / (base_share * 10000)
税后股息率_i = 税后DPS_i / 年末股价_i
三年平均税后股息率 = AVG(税后股息率_1, 税后股息率_2, 税后股息率_3)
支付率 = DPS / basic_eps
每股股利增长率 = DPS_year / DPS_year-1 - 1
```

### 选股流水线

```
获取CSI 800成分股
  → 连续3年分红检查（复用父类）
  → 支付率过滤：DPS/basic_eps，剔除为负+剔除前5%过高
  → DPS增长过滤：过去三年股利增长率均>0
  → 税后股息率降序 → Top 75
  → 波动率升序（复用父类磁盘缓存）→ Top 50
  → 纯股息率加权 + 15%单股上限
  → 输出篮子
```

### 调仓日期

年度12月第二个周五下一交易日，约11期：

```
2015-12-14, 2016-12-12, 2017-12-11, 2018-12-17, 2019-12-16,
2020-12-14, 2021-12-13, 2022-12-12, 2023-12-11, 2024-12-16, 2025-12-15
```

### 目录结构

```
weekly_harness/
├── dividend_lowvol.py            # [EXISTING] 930955引擎（不改动）
├── dividend_h30269.py            # [NEW] H30269引擎（继承DividendLowvolEngine）
│                                 #   覆盖方法：
│                                 #   - _load_dividend_cache()：增加DPS计算和EPS加载
│                                 #   - _load_eps_cache()：新增，批量下载basic_eps
│                                 #   - select_basket()：新增支付率+DPS增长过滤
│                                 #   - _apply_weighting()：纯股息率加权+15%上限
│                                 #   - get_rebalance_dates()：年度12月日期
│
run_h30269_full.py                # [NEW] H30269回测入口
│                                 #   - preload(): 下载EPS数据
│                                 #   - select_baskets(): 年度调仓
│                                 #   - calc_nav(): 与基准对比
│
data/
└── income/                       # [NEW] basic_eps缓存目录
    ├── income_2015.csv           #   每年一个文件（ts_code, end_date, basic_eps）
    ├── income_2016.csv
    └── ...（12个文件）

output/h30269_lowvol/             # [NEW] 回测输出
    ├── all_baskets_2015_2026.json
    └── backtest_nav_tr.csv

strategies/h30269_lowvol/         # [NEW] 策略档案
    └── strategy.yaml

docs/
└── 2026-06-14_H30269红利低波50回测报告.md  # [NEW] 报告
```

### 性能预估

- EPS下载：12批 × ~1s = ~12秒（首次），后续纯读盘
- 选股：年度11期，全读盘计算 < 2秒
- 总体：极轻量，远快于季度调仓的930955（45期）