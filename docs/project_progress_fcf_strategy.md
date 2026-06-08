# ZZ800 FCF & HS300 FCF 策略项目进展追踪

> **创建日期**：2026-06-08
> **最后更新**：2026-06-08
> **项目仓库**：https://github.com/DragonZiLu/weekly_harness.git

---

## 一、项目概览

本项目构建了一套完整的 **A股自由现金流（FCF）量化选股与回测系统**，覆盖中证800（ZZ800）和沪深300（HS300）两个指数成分池，支持多版本策略对比、净值曲线计算、报告生成和基金跟踪。

### 1.1 核心目标

| 目标 | 描述 | 状态 |
|------|------|------|
| FCF选股框架 | 基于FCF=OCF-Capex、EV加权、TTM口径的选股系统 | ✅ 已完成 |
| 多版本回测 | B/D/E/F/X/G/H 七版策略对比（ZZ800） | ✅ 已完成 |
| HS300 FCF | 沪深300成分池FCF策略 | ✅ 已完成 |
| 官方指数对比 | vs 932366/932368 官方现金流指数 | ✅ 已完成 |
| 基金跟踪 | ETF/指数/策略净值日常跟踪 | ✅ 已完成 |
| 数据管理 | 数据缓存、增量下载、git隔离 | ✅ 已完成 |

### 1.2 最新回测结果摘要（ZZ800，2015-03→2026-06，45期）

| 版本 | 年化 | 最大回撤 | 夏普 | Calmar | 期末NAV | 换手率 |
|------|------|----------|------|--------|---------|--------|
| **B版** | 14.80% | -39.99% | 0.504 | 0.370 | 4.72x | 31.1% |
| **D版** | 15.34% | -39.47% | 0.517 | 0.389 | 4.98x | 25.4% |
| **E版** | 15.80% | -39.66% | 0.536 | 0.398 | 5.21x | 23.1% |
| **F版** | 15.11% | -39.73% | 0.515 | 0.380 | 4.87x | 22.0% |
| **X版** | 10.12% | -40.82% | 0.356 | 0.248 | 2.96x | 18.6% |
| 932368 | 11.19% | -39.90% | 0.358 | 0.280 | 3.30x | — |
| 沪深300 | 2.36% | -41.28% | 0.015 | 0.057 | 1.30x | — |

> **最优版本**：E版（±40%缓冲），年化15.80%，夏普0.536
> **所有自建版本均跑赢932368官方基准（11.19%）和沪深300（2.36%）**

---

## 二、策略框架

### 2.1 七版策略架构

```
                          ┌──────────────────────┐
                          │   FcfUniverse 核心    │
                          │ (fcf_universe.py)     │
                          │ 1605行, 3个核心类     │
                          └──────┬───────────────┘
                                 │
                    get_fcf_basket(date, top_n=800)
                                 │
                          X版 全成分排名池
                     (rankings_2015_2026.json)
                                 │
              ┌──────────┬───────┴──────┬──────────┐
              │          │              │          │
             B版        D版           E版        F版
           Top50      Top50         Top50      Top50
           ±0%缓冲   ±20%缓冲      ±40%缓冲   ±50%缓冲
           (必选50)  (必选40+缓冲) (必选30+缓冲) (必选25+缓冲)
```

| 版本 | 缓冲区 | 必选区 | 缓冲区 | 描述 |
|------|--------|--------|--------|------|
| **X版** | — | 全成分 | — | 所有FCF>0合格股，FCF绝对值加权，无截断 |
| **B版** | ±0% | Top1-50 | — | 纯FCF率排名Top50，无缓冲 |
| **D版** | ±20% | Top1-40 | 41-60 | 前40必选，41-60优先保留上期持仓 |
| **E版** | ±40% | Top1-30 | 31-70 | 前30必选，31-70优先保留上期持仓 |
| **F版** | ±50% | Top1-25 | 26-75 | 前25必选，26-75优先保留上期持仓 |
| **G版** | 自适应 | 动态N | — | TopN = max(50, int(Q/100)*25)，Q为合格股数 |
| **H版** | — | Top50 | — | FCF绝对值排序Top50（非FCF率排序） |

### 2.2 核心选股逻辑

```
FCF = 经营现金流(OCF) - 资本支出(Capex)
EV  = 总市值(total_mv) + 总负债 - 现金
FCF率 = FCF / EV × 100%

筛选条件:
  1. 排除金融/房地产行业
  2. FCF > 0
  3. 盈利质量(PQ) = OCF/净利润 > 阈值（PQ百分位前80%）
  4. 5年OCF宽松检查（缺失年份跳过）
  5. TTM口径（缺季度数据时回退年报）
  6. 换手率过滤（可选，成交额/市值阈值）

加权方式（七版一致）:
  FCF绝对值加权 + 单股10%封顶迭代重分配
```

### 2.3 回测参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 调仓频率 | 季度 | 每季度第三个周一 |
| 回测区间 | 2015-03-16 → 2026-06-15 | 45期 |
| 价格类型 | 复权价（含分红再投资） | `adj_close = close × adj_factor` |
| 无风险利率 | 2.0% | 用于夏普比率计算 |
| NAV基准 | 1.0 | 2015-03-16 = 1.0 |

---

## 三、代码架构

### 3.1 核心模块

| 文件 | 行数 | 职责 |
|------|------|------|
| `weekly_harness/fcf_universe.py` | 1605 | **核心引擎**：`FinancialDataCache`(财务数据缓存) + `IndexWeightCache`(指数成分/权重) + `FcfUniverse`(FCF选股主类) |
| `weekly_harness/strategy.py` | 457 | 红利周期轮动策略（评分→仓位映射+约束） |
| `weekly_harness/scanner.py` | 865 | 红利股扫描器（股息质量+财务筛选+评分） |
| `weekly_harness/backtest.py` | — | 回测引擎（历史模拟+绩效指标） |
| `weekly_harness/portfolio.py` | — | 持仓管理（资金/仓位/交易记录） |
| `weekly_harness/generator.py` | — | 数据拉取+评分计算 |
| `weekly_harness/validator.py` | — | 数据质量校验 |
| `weekly_harness/reporter.py` | — | 周报生成+历史对比 |

### 3.2 回测脚本

| 脚本 | 行数 | 功能 | 输出目录 |
|------|------|------|----------|
| `run_bdefx_full.py` | 728 | **主力**：B/D/E/F/X 五版全流程（选股→NAV→报告） | `output/zz800_fcf_*/` |
| `run_g_full.py` | 481 | G版（自适应TopN）全流程 | `output/zz800_fcf_adaptive_top/` |
| `run_h_full.py` | 354 | H版（FCF绝对值排序Top50）全流程 | `output/zz800_fcf_top_by_fcf/` |
| `run_bdef_full.py` | 410 | B/D/E/F 四版（不含X）旧版全流程 | — |
| `regenerate_bd_baskets.py` | 174 | B/D版篮子重新生成 | — |
| `compute_nav_cached.py` | — | NAV计算（复权价缓存加速） | — |
| `cache_adj_close.py` | — | 复权价预缓存工具 | — |

### 3.3 HS300 FCF 脚本

| 脚本 | 功能 |
|------|------|
| `regenerate_hs300_bd_baskets.py` | HS300 B/D版篮子生成（逻辑与ZZ800对齐，index_code=000300.SH） |
| `regenerate_hs300_fcf_fixed.py` | HS300 FCF修正版篮子（total_mv + TTM + 宽松OCF） |
| `regenerate_hs300_strict.py` | HS300 严格版篮子 |
| `hs300_fcf_vs_932366.py` | HS300 FCF vs 932366官方指数五维对比 |
| `generate_hs300_bd_report.py` | HS300 B/D版报告生成 |

### 3.4 数据下载脚本

| 脚本 | 功能 |
|------|------|
| `download_fcf_financials.py` | FCF财务数据批量下载（年报+季报） |
| `download_fcf_parallel.py` | 并行加速版财务数据下载 |
| `download_fcf_fix.py` | 修正版下载（补缺数据） |
| `download_income_fast.py` | 利润表快速下载 |
| `download_income_only.py` | 仅利润表下载 |
| `download_early_financials.py` | 早期（2011-2015）财务数据补全 |
| `download_missing_early.py` | 缺失早期数据补下载 |
| `download_quarterly_fix.py` | 季度数据修正下载 |
| `download_932365_financials.py` | 932365成分股财务数据下载 |
| `download_daily_basic_cache.py` | 每日基本面数据缓存下载 |
| `download_zz800_full_history.py` | ZZ800完整历史数据下载 |
| `restore_income_2024.py` | 恢复2024年income年报（待执行） |
| `restore_income_annual.py` | 恢复income年报数据 |
| `restore_income_zz800.py` | 恢复ZZ800成分股income数据 |
| `preload_daily_basic.py` | 预加载每日基本面数据 |
| `cache_adj_close.py` | 复权价缓存预计算 |

### 3.5 分析模块

| 文件 | 行数 | 功能 |
|------|------|------|
| `analysis/fund_tracker.py` | 560 | 基金/指数/策略净值跟踪（快照/周报/月报/季报） |
| `analysis/tracker_config.py` | 147 | 跟踪标的配置（16只：自建策略+现金流指数+ETF+红利+宽基） |
| `analysis/index_backtest.py` | 414 | 指数回测对比工具 |
| `analysis/quarterly_holdings_analysis.py` | 408 | 季度持仓分析 |

### 3.6 诊断与验证脚本

| 脚本 | 功能 |
|------|------|
| `diagnose_data_coverage.py` | 数据覆盖率诊断 |
| `diagnose_zz800_fcf_data.py` | ZZ800 FCF数据质量诊断 |
| `validate_zz800_fcf.py` | ZZ800 FCF策略验证 |
| `fix_ocf_op_stats.py` | OCF统计修正 |
| `full_universe_stats.py` | 全成分股统计 |
| `verify_fast_vs_original.py` | 快速版 vs 原始版一致性验证 |
| `b_version_review.py` | B版策略复盘 |

### 3.7 报告生成脚本

| 脚本 | 功能 |
|------|------|
| `generate_zz800_baskets_fast.py` | ZZ800篮子快速生成 |
| `generate_full_comparison.py` | 全面对比报告 |
| `generate_enhanced_report.py` | 增强版报告 |
| `generate_annual_reports.py` | 年度报告批量生成 |
| `enhance_bdefx_report.py` | BDEFX报告增强 |
| `siegel_all_a_report.py` | Siegel全A股报告 |
| `marlboro_report.py` | Marlboro策略报告 |

---

## 四、数据架构

### 4.1 数据目录结构

```
data/
├── fcf_financials/              # 财务数据（320个CSV）
│   ├── balance_2011.csv         # 资产负债表（年报）
│   ├── balance_2011Q1.csv       # 资产负债表（季报）
│   ├── income_2011.csv          # 利润表（年报）
│   ├── income_2011Q1.csv        # 利润表（季报）
│   ├── cashflow_2011.csv        # 现金流量表（年报）
│   ├── cashflow_2011Q1.csv      # 现金流量表（季报）
│   ├── daily_basic_cache/       # 每日基本面缓存（市值/换手率）
│   └── adj_close_cache/         # 复权价缓存
├── index_daily/                 # 指数日线行情
│   ├── 000300.SH.csv            # 沪深300
│   ├── 000906.SH.csv            # 中证800
│   ├── 932368.CSI.csv           # 中证800现金流
│   ├── 932366_CSI.csv           # 沪深300现金流
│   └── H00300.CSI.csv           # 沪深300全收益
├── index_weights/               # 指数成分权重
│   ├── index_weight_000906.SH.csv
│   ├── index_weight_000300.SH.csv
│   ├── 932366_latest.csv
│   └── 932368_202412.csv
├── stock_daily/                 # 个股日线行情
├── stock_daily_hfq/             # 个股后复权行情
├── backtest/                    # 旧版回测结果
├── annual_reports_zz500/        # ZZ500年度报告
├── annual_reports_hs300/        # HS300年度报告
└── ...                          # 其他辅助数据

cache/
└── experiment/                  # 实验缓存（版本对比）
    ├── zz800_fcf_a_original/    # A版（原始：circ_mv, 无TTM）
    ├── zz800_fcf_b_lenient/     # B版（fixed+宽松）
    ├── zz800_fcf_b_annual/      # B版（年报口径）
    ├── zz800_fcf_d_buffer/      # D版（±20%缓冲）
    ├── zz800_fcf_pq_fixed/      # PQ修正版
    ├── hs300_fcf_b_lenient/     # HS300 B版
    └── hs300_fcf_d_buffer/      # HS300 D版

output/                          # 回测输出（已在.gitignore）
├── zz800_fcf_fixed_lenient/     # ZZ800 B版
├── zz800_fcf_lenient_buffer/    # ZZ800 D版
├── zz800_fcf_lenient_buffer_e40/# ZZ800 E版
├── zz800_fcf_lenient_buffer_f50/# ZZ800 F版
├── zz800_fcf_full_universe/     # ZZ800 X版
├── zz800_fcf_adaptive_top/     # ZZ800 G版
├── zz800_fcf_top_by_fcf/       # ZZ800 H版
├── hs300_fcf/                   # HS300 原始版
├── hs300_fcf_fixed/             # HS300 修正版
├── hs300_fcf_lenient_buffer/    # HS300 D版
├── hs300_fcf_full_universe/     # HS300 X版
└── hs300_fcf_comparison/        # HS300对比报告
```

### 4.2 数据来源

| 数据 | 来源 | API |
|------|------|-----|
| 财务报表（年报/季报） | Tushare | `income`, `balancesheet`, `cashflow_vip` |
| 每日行情 | Tushare | `daily` |
| 复权因子 | Tushare | `adj_factor` |
| 每日基本面 | Tushare | `daily_basic` |
| 指数成分权重 | Tushare | `index_weight` |
| 指数日线 | Tushare | `index_daily` |
| 行业分类 | Tushare | `stock_basic`, `industry` |

### 4.3 数据版本

| 数据集 | 版本/范围 | 说明 |
|--------|-----------|------|
| 年报财务 | 2011-2025 | 2011-2025年度报告 |
| 季报财务 | 2023Q1-2025Q3 | 近3年季度数据 |
| 每日基本面 | 45期调仓日, 2015-2026 | 800成分股×45期 |
| 指数权重 | 932366/932368 202412-202603 | 成分股+权重快照 |

---

## 五、项目能力清单

### 5.1 选股能力

| 能力 | 描述 | 关键函数 |
|------|------|----------|
| FCF计算 | FCF=OCF-Capex，支持TTM/年报两种口径 | `FcfUniverse._calc_fcf()` |
| EV计算 | EV=总市值+总负债-现金（total_mv版） | `FcfUniverse._calc_ev()` |
| FCF率排序 | FCF/EV降序排列，生成完整排名池 | `FcfUniverse.get_fcf_basket()` |
| 行业过滤 | 排除金融/房地产/保险等68个行业 | `_is_financial_or_real_estate()` |
| 5年OCF检查 | 宽松模式（缺失年份跳过） | `check_5yr_positive_ocf()` |
| 盈利质量筛选 | PQ=OCF/净利润，百分位前80% | `_calc_profit_quality()` |
| 换手率过滤 | 成交额/市值阈值（可配置） | `_apply_turnover_filter()` |
| TTM回退 | 缺季度数据时自动回退年报 | `get_ttm_financials()` |

### 5.2 加权能力

| 能力 | 描述 | 关键函数 |
|------|------|----------|
| FCF绝对值加权 | 权重=FCF_i/ΣFCF | `fcf_weights()` |
| 单股封顶 | 10%封顶+迭代重分配 | `fcf_weights(cap=0.10)` |
| 缓冲区选股 | 保留上期持仓+缓冲区粘性 | `apply_buffer()` |

### 5.3 回测能力

| 能力 | 描述 | 关键函数 |
|------|------|----------|
| NAV计算 | 逐期加权收益累积净值 | `calc_nav()` |
| 复权价缓存 | 自动缓存adj_close避免重复API调用 | `get_adj_close_cached()` |
| 全收益模式 | 含分红再投资，复权价计算 | adj_close = close × adj_factor |
| 阶段收益 | 按牛熊/蓝筹等阶段切分收益 | `phase_ret_from_nav()` |
| 绩效指标 | 年化/波动率/夏普/Calmar/胜率 | `stats()` |
| 换手率 | 逐期持仓变化率均值 | `turnover()` |
| 超额收益 | vs 932368/沪深300基准 | `ver_stats - idx_s` |

### 5.4 报告能力

| 能力 | 描述 | 输出 |
|------|------|------|
| 五版对比报告 | 核心指标+逐年+逐期+超额+X版专项 | `docs/zz800_bdefx_strategy_comparison.md` |
| 七版对比报告 | 含G/H版的扩展对比 | `docs/zz800_bdefgxh_strategy_comparison.md` |
| HS300对比报告 | vs 932366官方指数五维对比 | `docs/hs300_fcf_vs_932366_comparison_framework.md` |
| 持仓分析 | 特定股票在策略中的表现 | `docs/zz800_e_holdings_return_analysis.md` |
| 数据诊断 | 数据覆盖率/质量报告 | `docs/zz800_fcf_data_diagnostic.md` |
| B版复盘 | 胜率/盈亏比/最佳调仓日分析 | `docs/zz800_b_version_review.md` |
| 基金跟踪 | 16只标的日常快照/周报/月报/季报 | `docs/tracker/` |

### 5.5 FCF率三口径

| 口径 | 公式 | 含义 |
|------|------|------|
| **组合FCF率** | ΣFCF / ΣEV | 组合作为整体公司的FCF率，最反映估值水平 |
| **权重加权FCF率** | Σ(w_i × fcf_yield_i) | 按持仓权重加权，最贴近因子暴露 |
| **算术均值FCF率** | mean(FCF_i/EV_i) | 简单平均，小/大公司等权，仅作参考 |

### 5.6 基金跟踪能力（analysis/）

| 功能 | 命令 | 说明 |
|------|------|------|
| 快照 | `python analysis/fund_tracker.py` | 所有多区间收益+回撤+夏普 |
| 周报 | `--mode weekly` | 快照+近4周逐周涨跌 |
| 月报 | `--mode monthly` | 快照+近12月逐月涨跌 |
| 季报 | `--mode quarter` | 快照+ETF季末持仓Top10 |

**跟踪标的（16只）**：
- 自建FCF策略：B/D/E/F版（4只）
- 现金流指数：国证FCF、中证800现金流、中证全指现金流（4只，含价格版和全收益版）
- 现金流ETF：159229、159201（2只）
- 红利指数/ETF：中证红利全收益、515180、510880（3只）
- 宽基基准：沪深300TR、中证500TR、中证1000TR（3只）

---

## 六、版本迭代历史

### 6.1 策略版本演进

| 迭代 | 版本 | 关键变更 | 时间 |
|------|------|----------|------|
| v0 | A版 | 原始宽松：circ_mv（流通市值）、无TTM、宽松OCF截断 | ~2026-06-03 |
| v1 | B/D版 | Fixed修正：total_mv + TTM + 宽松OCF | 2026-06-04 |
| v2 | E/F版 | 新增±40%/±50%缓冲区版本 | 2026-06-05 |
| v3 | X版 | 全成分FCF加权（无Top50截断） | 2026-06-05 |
| v4 | G版 | 自适应TopN=max(50, int(Q/100)*25) | 2026-06-06 |
| v5 | H版 | FCF绝对值排序Top50（非FCF率排序） | 2026-06-06 |
| v6 | X版增强 | 净值曲线、FCF率三口径、沪深300对比、阶段收益 | 2026-06-07 |

### 6.2 关键修正记录

| 问题 | 修正 | 影响 |
|------|------|------|
| circ_mv→total_mv | EV改用总市值而非流通市值 | EV更准确反映企业价值 |
| 无TTM→TTM口径 | 季度数据截断点使用最近4个季度TTM | 财务数据时效性大幅提升 |
| 等权→FCF加权 | 从等权改为FCF绝对值加权（10%封顶） | B版年化+1.24pp, D版+1.19pp |
| `hs_s`变量冲突 | 局部变量重命名 | 修复阶段收益计算错误 |
| 中文引号嵌套 | 双引号内含中文引号→改单引号 | 修复报告生成语法错误 |
| 数据误上传git | `git rm --cached` + .gitignore整目录排除 | 4712数据文件移出git追踪 |

---

## 七、输出产物索引

### 7.1 策略对比报告（docs/）

| 文件 | 内容 | 版本数 |
|------|------|--------|
| `zz800_bdefx_strategy_comparison.md` | **主报告**：B/D/E/F/X五版+932368+沪深300，含X版7项专项分析 | 5+2 |
| `zz800_bdefgxh_strategy_comparison.md` | 扩展报告：七版+基准对比 | 7+2 |
| `zz800_bdef_strategy_comparison.md` | 早期报告：B/D/E/F四版对比 | 4+2 |
| `zz800_bde_strategy_comparison.md` | 早期报告：B/D/E三版对比 | 3+2 |
| `zz800_fcf_full_comparison.md` | 全面对比：D/B版 vs 多基准 | 2+多基准 |
| `hs300_fcf_vs_932366_comparison_framework.md` | HS300 FCF vs 932366对比框架 | 1+1 |
| `hs300_fcf_d_vs_b_report.md` | HS300 D/B版对比 | 2+基准 |

### 7.2 专题分析报告

| 文件 | 内容 |
|------|------|
| `zz800_b_version_review.md` | B版复盘：53.5%胜率、1.54盈亏比 |
| `zz800_e_holdings_return_analysis.md` | E版持仓分析（含中海油案例） |
| `zz800_e40_top10_detail.md` | E版Top10持仓详情 |
| `zz800_fcf_data_diagnostic.md` | FCF数据覆盖率/质量诊断 |
| `zz800_20260316_holding_analysis.md` | 2026-03持仓快照分析 |
| `zz800_ttm_vs_annual_comparison.md` | TTM vs 年报口径对比 |
| `zz800_time_aligned_comparison.md` | 时间对齐对比 |
| `932366_validation_report.md` | 932366指数验证 |
| `932368_validation_report.md` | 932368指数验证 |
| `cn_us_20y_comparison.md` | 中美20年市场对比 |
| `data_coverage_report.md` | 数据覆盖率报告 |
| `full_universe_fcf_stats.md` | 全成分股FCF统计 |
| `fcf_932365_2026Q1_comparison.md` | 932365 2026Q1对比 |

---

## 八、待办事项 & 已知问题

### 8.1 待完成

| 优先级 | 事项 | 状态 | 说明 |
|--------|------|------|------|
| 🔴 高 | 恢复2024年income年报 | 待执行 | `restore_income_2024.py`已准备，需运行 |
| 🔴 高 | 重跑最新B/D/E/F/X回测 | 待执行 | income恢复后需重跑 |
| 🟡 中 | HS300 BDEFX五版完整回测 | 部分完成 | B/D/X已有，E/F缺 |
| 🟡 中 | G/H版正式纳入主流程 | 未整合 | 目前独立脚本，未合入run_bdefx_full.py |
| 🟢 低 | git历史清理 | 可选 | 数据文件仍占历史空间，可用BFG清除 |
| 🟢 低 | README更新 | 待更新 | 当前README仍描述红利周期策略，未反映FCF |

### 8.2 已知限制

| 限制 | 描述 | 影响 |
|------|------|------|
| 季度数据不完整 | 2023年之前季度数据缺失，部分股票TTM回退到年报 | TTM时效性在早期年份降低 |
| 932366/932368权重延迟 | 官方指数权重仅有期末快照 | 成分股重合度分析精度受限 |
| 单一数据源 | 仅Tushare，无备用源 | API限频时影响下载 |
| 无实时运行 | 回测为离线批处理模式 | 无法实时跟踪当日表现 |
| 滑点未建模 | 未考虑交易冲击成本 | 实际收益可能低于回测 |

---

## 九、运行指南

### 9.1 环境准备

```bash
# 1. 克隆仓库
git clone https://github.com/DragonZiLu/weekly_harness.git

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置Tushare token
cp .env.example .env
# 编辑 .env，填入 TUSHARE_TOKEN=xxx

# 4. 下载数据（首次，约30分钟）
python download_fcf_financials.py
python download_fcf_parallel.py
python download_daily_basic_cache.py
python cache_adj_close.py
```

### 9.2 运行回测

```bash
# 完整五版回测（选股+NAV+报告，约5-10分钟）
python run_bdefx_full.py

# 仅X版选股（保存排名池，约2分钟）
python run_bdefx_full.py --x-only

# 跳过选股，用已有basket算NAV+报告
python run_bdefx_full.py --nav-only

# G版（自适应TopN）回测
python run_g_full.py

# H版（FCF绝对值排序）回测
python run_h_full.py
```

### 9.3 基金跟踪

```bash
# 快照（所有标的，多区间）
python analysis/fund_tracker.py

# 周报
python analysis/fund_tracker.py --mode weekly

# 月报
python analysis/fund_tracker.py --mode monthly

# 季报（含ETF持仓Top10）
python analysis/fund_tracker.py --mode quarter
```

### 9.4 数据诊断

```bash
# 数据覆盖率诊断
python diagnose_data_coverage.py

# ZZ800 FCF数据质量诊断
python diagnose_zz800_fcf_data.py

# 一致性验证
python verify_fast_vs_original.py
```

---

## 十、Git管理规范

| 规则 | 描述 |
|------|------|
| **数据不上传** | `data/`、`cache/`、`output/` 已在.gitignore排除 |
| docs/ 保留 | 报告文档保留在git中，便于版本追踪 |
| 提交规范 | `feat:` / `fix:` / `chore:` / `docs:` 前缀 |
| 当前追踪文件 | ~199个（代码+docs+config），0个数据文件 |

---

## 十一、关键决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-06-03 | EV用total_mv替代circ_mv | 总市值更准确反映企业价值，流通市值低估大企业EV |
| 2026-06-04 | 采用TTM口径 | 季度数据时效性更好，年报滞后太久 |
| 2026-06-04 | FCF加权替代等权 | 大FCF企业（格力、中远海控）应获更高配置，B版+1.24pp |
| 2026-06-05 | 5年OCF用宽松模式 | 缺失年份跳过，避免因早期数据缺失误筛优质股 |
| 2026-06-05 | 新增E/F版缓冲区 | 降低换手率，减少交易成本，E版年化最优(15.80%) |
| 2026-06-06 | G版自适应TopN | 合格股多时扩大持仓，少时收缩，年化18.38%但需进一步验证 |
| 2026-06-07 | FCF率三口径展示 | 单一算术均值有误导，组合FCF率（ΣFCF/ΣEV）更能反映估值 |
| 2026-06-07 | 数据文件移出git | 4712个数据文件不应版本控制，本地保留+gitignore |

---

*本文档由项目进展整理生成，用于后续追踪和交接。*
*最后更新：2026-06-08*
