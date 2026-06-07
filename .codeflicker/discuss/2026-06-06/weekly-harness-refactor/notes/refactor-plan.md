# weekly_harness 重构追踪文档

> 创建：2026-06-06 | 最后更新：2026-06-06

---

## 一、系统现状快照

### 代码规模

| 文件 | 行数 | 职责 |
|------|------|------|
| `weekly_harness/backtest.py` | 2435 | 回测引擎（NAV计算+绩效+报告） |
| `weekly_harness/fcf_universe.py` | 1592 | FCF选股引擎（最核心） |
| `weekly_harness/reporter.py` | 1144 | 周报生成器（红利策略专用） |
| `weekly_harness/scanner.py` | 864 | 选股扫描器（红利/ROE类） |
| `weekly_harness/dca_planner.py` | 858 | 定投计划 |
| `weekly_harness/portfolio.py` | 562 | 持仓管理 |
| `weekly_harness/index_universe.py` | 555 | 指数成分股宇宙 |
| `weekly_harness/etf_universe.py` | 518 | ETF宇宙 |
| `weekly_harness/strategy.py` | 456 | 策略配置加载 |
| `weekly_harness/validator.py` | 403 | 验证工具 |
| 根目录散落脚本 | 50+ 文件 | 数据下载/篮子生成/验证报告 |

### 策略版本现状

| 版本 | 样本 | EV口径 | TTM | OCF规则 | 缓冲区 | 输出目录 |
|------|------|--------|-----|---------|--------|---------|
| ZZ800-A（原始宽松）| 中证800 | circ_mv | 无 | 宽松 | 无 | `output/zz800_fcf/` |
| ZZ800-B（fixed+宽松）| 中证800 | total_mv | 有 | 宽松 | 无 | `output/zz800_fcf_fixed_lenient/` |
| ZZ800-D（B+缓冲区）| 中证800 | total_mv | 有 | 宽松 | ±20% rank | `output/zz800_fcf_lenient_buffer/` |
| HS300-B（fixed+宽松）| 沪深300 | total_mv | 有 | 宽松 | 无 | `output/hs300_fcf_fixed_lenient/` |
| HS300-D（B+缓冲区）| 沪深300 | total_mv | 有 | 宽松 | ±20% rank | `output/hs300_fcf_lenient_buffer/` |

### 数据目录现状

```
data/
├── fcf_financials/          ← 财务缓存（cashflow/income/balance）
├── index_weights/           ← 指数权重快照（月度）
├── adj_close_cache/         ← 复权价格缓存
├── index_daily/             ← 指数日行情
├── stock_daily/             ← 个股日行情
└── backtest_*/              ← ⚠️ 旧版回测输出混在数据目录里（应归output）
```

---

## 二、已确认的已知 Bugs / 设计缺陷

### Bug-1：PQ排名基数包含金融地产（高优先级）

- **位置**：`weekly_harness/fcf_universe.py` L1320-1326
- **问题**：PQ(盈利质量)排名的20th percentile门槛是在**含金融地产**的全样本上计算，金融股PQ普遍偏高（银行ROA>4%），拉高整体门槛，导致非金融股PQ筛选更严格
- **正确做法**（对齐932368官方）：先剔除金融地产，再在非金融股中算PQ排名
- **修复代码**：
  ```python
  # 当前（错误）
  pq_all = [c["profit_quality"] for c in all_candidates if c["profit_quality"] is not None]
  pq_cutoff = np.percentile(pq_all, 20)

  # 修复后（正确）
  pq_non_fin = [c["profit_quality"] for c in all_candidates
                if c["profit_quality"] is not None and not c["is_financial"]]
  pq_cutoff = np.percentile(pq_non_fin, 20)
  ```
- **预期影响**：ZZ800-B vs 932368 的 Recall 从 52% 提升至约 60%+
- **状态**：❌ 已验证不合入（回测表现更差）
- **实测结果**：
  - 2024-12: Recall **下降** 48%→32%（-16pp），移出10只官方股（中远海控/中国动力等PQ本就不高的被PQ门槛排除）
  - 2025-03: Recall **下降** 46%→32%（-14pp），同上
  - 2025-06+: Recall **不变**（0pp），因2025年报下金融股PQ分布与非金融重叠大
  - **结论**：PQ基数修复对后期期次无影响，对早期期次反而降低Recall。PQ不是主要差异源
- **回测对比**（2016~2026Q1，前期复用B版收益，后期PQ修复生效）：

  | 指标 | B版(pq_base=all) | PQ修复(pq_base=non_fin) | D版(buffer±20%) | 932368官方 |
  |------|:---:|:---:|:---:|:---:|
  | 终值NAV | 4.528 | 4.111 | 4.675 | 3.634 |
  | CAGR | 15.3% | 14.2% | 15.7% | 12.8% |
  | 最大回撤 | -12.6% | -12.6% | -11.6% | -16.1% |
  | 夏普 | 0.81 | 0.76 | 0.85 | 0.65 |

  PQ修复版CAGR从15.3%降至14.2%（-1.1pp），夏普从0.81降至0.76。主要拖累来自2024-12和2025-03两期（Recall大幅下降）

### Bug-2：行业分类差异（中等优先级）

- **位置**：`weekly_harness/fcf_universe.py` L70-93
- **问题**：我们用**申万一级**行业过滤，932368用**中证一级**行业过滤，导致相同股票被不同系统分类到不同行业
- **典型案例**：中国移动/中国电信在申万分类为"电信运营"(非金融地产，不被排除)；但在中证分类中可能归属不同
- **状态**：⬜ 待调研

### Bug-3：FCF/EV口径与官方差异（低优先级，难修）

- **问题**：我们用年报数据 + `c_pay_acq_const_fiolta` 作 capex，官方可能用不同口径
- **典型案例**：中国海油用2026年3月市值算FCF率=4.54%，但官方2025年6月快照时FCF率=7.11%（时间点不同）
- **根因**：市值随股价波动，EV是动态的，FCF是历史的，时间点不一致导致排名差异
- **状态**：⬜ 分析中，暂无明确修复方案

---

## 三、重构方向

### 路径A：微重构（推荐先做，2-3天）

**目标**：解决最痛的问题，不改核心逻辑

| 任务 | 优先级 | 预计时间 | 说明 |
|------|--------|---------|------|
| 修复 Bug-1 PQ基数 | P0 | 30分钟 | 改一行代码，高ROI |
| 根目录脚本归档 | P1 | 2小时 | 50+ py文件归入子目录 |
| 添加统一 CLI 入口 | P2 | 1天 | `python -m weekly_harness.cli` |
| data/backtest_* 迁移 | P3 | 30分钟 | 旧数据移到output/ |

**脚本归档建议**：
```
scripts/
├── data/            ← download_*.py、cache_*.py
├── basket/          ← regenerate_*.py、generate_*baskets*.py
├── report/          ← generate_*report*.py、generate_*validation*.py
├── backtest/        ← backtest_*.py、compute_*nav*.py
└── analysis/        ← analyze_*.py、validate_*.py、eval_*.py
```

### 路径B：FCF口径参数化（中期，1周）

**目标**：让不同口径的FCF策略可以用配置文件切换，无需改源码

```python
# 目标接口
@dataclass
class FcfCalcConfig:
    ev_mv_field: str = "total_mv"        # "total_mv" | "circ_mv"
    use_ttm: bool = True
    capex_field: str = "c_pay_acq_const_fiolta"  # vs "c_pay_acq_const_fiolta+intang"
    pq_base: str = "non_financial"       # "all" | "non_financial"  ← Bug-1修复
    ocf_mode: str = "lenient"            # "lenient" | "strict"
    buffer_zone: Optional[int] = None    # None | 20 (±20% rank buffer)
    top_n: int = 50
    weight_cap: float = 0.10
```

**优点**：A/B/D版可以通过config统一管理，无需维护多份代码

### 路径C：数据层统一（长期，3-4周）

**目标**：把散落的数据访问逻辑集中到 `weekly_harness/data/` 模块

```
weekly_harness/data/
├── __init__.py
├── client.py          ← 统一入口 DataClient
├── financial.py       ← 财务数据（年报/TTM）
├── market.py          ← 市值/行情数据
├── index.py           ← 指数成分/权重
└── cache.py           ← 通用缓存基类
```

**关键接口**：
```python
client = DataClient()
# 财务数据
fin = client.get_annual_financials("600938.SH", year=2024)
ttm = client.get_ttm_financials("600938.SH", as_of="2026-03-16")
# 市值
mv = client.get_market_cap("600938.SH", date="2026-03-16")
# 指数成分
members = client.get_index_members("000906.SH", date="2026-03-16")
```

---

## 四、验证体系现状

### 现有验证报告

| 报告 | 路径 | 内容 |
|------|------|------|
| ZZ800 B/D vs 932368 | `docs/zz800_fcf_932368_validation.md` | 9章节，Recall/Spearman/TE/IR |
| HS300 B vs 932366 | `docs/hs300_fcf_932366_validation.md` | 同格式 |
| ZZ800全面对比 | `docs/zz800_fcf_full_comparison.md` | B/D/932368/ZZ800指数逐年 |
| 2026-03-16权重明细 | `docs/zz800_20260316_weight_detail.md` | 75只标的三版权重 |

### 当前关键指标（2026-03-16 快照）

| 指标 | B版 | D版 | 932368 |
|------|-----|-----|--------|
| 只数 Recall | 52% | 58% | 100% |
| 权重 Recall（B端）| 48.3% | 53.0% | — |
| Spearman（重叠标的）| ~0.98 | ~0.98 | — |
| 年化收益（历史）| 16.30% | ~16.5% | 13.77% |
| 最大回撤 | -12.4% | -11.6% | -46% |
| 夏普 | 0.89 | 0.91 | — |

---

## 五、待确认的优化方向

- [ ] 是否引入 **指数权重加权**（而非纯FCF加权）？参考932368的FCF加权逻辑
- [ ] 是否支持 **多因子混合**（FCF+低估+PQ综合评分）？
- [ ] **更新频率**：季度调仓是否改为月度？
- [ ] **样本扩展**：ZZ800(800只) → 全A股(5000+只)?
- [ ] **实盘对接**：如何从回测结果输出实际交易指令？

---

## 六、技术债务清单

| 编号 | 描述 | 文件 | 优先级 |
|------|------|------|--------|
| TD-1 | fcf_universe.py 职责过多（数据+逻辑混在一起）| fcf_universe.py | 中 |
| TD-2 | backtest.py 2435行，单文件太大 | backtest.py | 低 |
| TD-3 | 50+ 根目录脚本无组织 | 根目录 | 高 |
| TD-4 | data/backtest_* 目录混入数据层 | data/ | 低 |
| TD-5 | 多个 download_*.py 接口不一致 | 根目录 | 中 |
| TD-6 | output/ 下多个版本命名不一致（fcf_fixed_lenient vs fcf_lenient_buffer）| output/ | 低 |
| TD-7 | PQ排名基数 bug（Bug-1）| fcf_universe.py L1320 | P0 |
