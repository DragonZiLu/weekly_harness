# 沪深300自由现金流策略 (HS300 FCF)

> 对标中证沪深300自由现金流指数 (932366.CSI)，经验证 Recall=92%，权重Spearman=0.9955

## 策略概述

从沪深300成分股中选取 **50 只** 自由现金流率最高的非金融地产股，FCF加权（10%封顶），季度调仓。

## 编制规则

| 项目 | 内容 |
|------|------|
| 样本空间 | 沪深300指数成分股 (000300.SH) |
| 行业剔除 | 中证一级行业: 金融、房地产 (实现: 申万分类+关键词兜底) |
| 选股数量 | 50只 |
| FCF定义 | OCF(TTM) − 购建固定资产支付现金(TTM) |
| EV定义 | 总市值 + 总负债 − 货币资金 |
| 5年OCF | 连续5年经营活动现金流净额 > 0 (TTM口径优先, 年报回退) |
| 盈利质量 | PQ = (OCF − 营业利润) / 总资产, 全样本空间前80% |
| 加权方式 | FCF加权, 单只上限10% (迭代封顶重分配) |
| 调仓频率 | 3/6/9/12月第二个星期五下一交易日 |
| TTM参考期 | 3月→上年Q3, 6月→当年Q1, 9月→当年Q2, 12月→当年Q3 |

### TTM计算方法

```
FCF_TTM = OCF_TTM − CAPEX_TTM
OCF_TTM = OCF_年报 − OCF_上年Q3 + OCF_当年Q3  (以12月调仓为例)
CAPEX_TTM = CAPEX_年报 − CAPEX_上年Q3 + CAPEX_当年Q3
```

TTM数据不可用时自动回退到年报口径。

### 5年OCF检查 (TTM口径)

```python
# 每年检查: TTM口径优先, 回退到年报
for year in range(base_year-4, base_year+1):
    ocf = get_ttm_ocf(code, f"{year}{q_suffix}")  # e.g. "20230930"
    if ocf is None:
        ocf = get_annual_ocf(code, year)  # 年报回退
    if ocf is not None and ocf <= 0:
        return False
return True
```

### 10%权重封顶 (迭代重分配)

```python
def apply_weight_cap(raw_weights, cap=0.10):
    """超限标的封顶到cap, 溢出按比例分配给未封顶标的"""
    for _ in range(n_iterations):
        for code in weights:
            if weights[code] > cap:
                overflow += weights[code] - cap
                weights[code] = cap
                capped_set.add(code)
        # 溢出按比例分配
        for code in uncapped:
            weights[code] += overflow * weights[code] / uncapped_total
```

## 验证结果 (2024-12-16 调仓)

| 指标 | 值 |
|------|-----|
| Recall | **92%** (46/50) |
| 权重Spearman(封顶后) | **0.9955** |
| 缺失标的 | 4只 (排名51-62, 差距0.03-0.38pp) |
| 封顶标的 | 中国石油 14.95%→10%, 中国海油 10.14%→10% |

### 未达100%的根因

| 根因 | 数量 | 可修复性 |
|------|------|---------|
| 市值/EV数据精度(tushare vs Wind) | 4 | ❌ 数据源天花板 |
| 行业分类(申万vs中证) | 0 | - |

## 使用方法

### 1. 命令行运行

```bash
# 生成最新一期选股
python run_fcf_strategy.py --strategy hs300_fcf

# 指定调仓日期
python run_fcf_strategy.py --strategy hs300_fcf --date 2025-06-16

# 下载缺失数据后运行
python run_fcf_strategy.py --strategy hs300_fcf --download
```

### 2. Python API

```python
from weekly_harness.fcf_universe import FcfUniverse

uni = FcfUniverse(index_code="000300.SH")
uni.preload_all(download=True)  # 首次需下载

basket = uni.get_fcf_basket("2025-06-16", top_n=50, use_ttm=True, verbose=True)
# 返回: {ts_code: {name, fcf, ev, fcf_yield, weight, ...}}
```

### 3. 策略配置文件

见 `strategy.yaml` — 可直接被 `run_fcf_strategy.py` 加载。

## 与其他策略对比

| 维度 | HS300 FCF | ZZ800 FCF | CSI全指FCF |
|------|-----------|-----------|------------|
| 样本空间 | 沪深300(300只) | 中证800(800只) | 中证全指(3000+只) |
| 选股数量 | 50 | 50 | 100 |
| CAPEX口径 | 购建固定资产 | 购建固定资产+无形资产+其他长期资产 | 购建固定资产+无形资产+其他长期资产 |
| 验证Recall | 92% | 84% | 待验证 |
| 标的来源 | 全HS300 | 19只HS300+31只ZZ500 | 全市场 |

## 风险提示

1. tushare数据精度是Recall天花板(92%)，排名边界标的(51-62)无法精确复现
2. 申万行业分类 ≠ 中证行业分类，可能误剔/漏剔金融地产
3. TTM季度数据缺失时回退到年报，可能遗漏季节性OCF波动
