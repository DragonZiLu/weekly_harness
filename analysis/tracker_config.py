"""
基金 & 指数跟踪配置
==================
在这里集中管理所有想跟踪的标的，fund_tracker.py 会读取此配置。

字段说明:
    code      Tushare 代码（ETF 用 .SZ/.SH，指数用 .CSI/.CNI 等）
    name      自定义名称
    category  分类标签，用于分组展示
    benchmark 是否作为基准对比（不算超额，仅展示）
    type      "etf" | "index_price" | "index_tr" | "strategy" | "stock"
              - etf:          直接用 ETF 净值/价格（复权）
              - index_price:  价格指数 + 估算股息再投
              - index_tr:     全收益指数，直接用
              - strategy:     本地自建策略 nav（从 nav_path 读取）
              - stock:        个股日线（复权价）
    nav_path  仅 type=="strategy" 时需要，相对项目根的路径
    div_add   仅 index_price 时用，额外补偿股息率（pp），默认 0
"""

WATCHLIST = [
    # ── 自建 FCF 策略（ZZ800）──────────────────────────────────
    {
        "code": "MY_B",
        "name": "自建B版（±0%缓冲）",
        "category": "自建FCF策略",
        "type": "strategy",
        "nav_path": "output/zz800_fcf_fixed_lenient/backtest_nav_tr.csv",
        "benchmark": False,
    },
    {
        "code": "MY_D",
        "name": "自建D版（±20%缓冲）",
        "category": "自建FCF策略",
        "type": "strategy",
        "nav_path": "output/zz800_fcf_lenient_buffer/backtest_nav_tr.csv",
        "benchmark": False,
    },
    {
        "code": "MY_E",
        "name": "自建E版（±40%缓冲）",
        "category": "自建FCF策略",
        "type": "strategy",
        "nav_path": "output/zz800_fcf_lenient_buffer_e40/backtest_nav_tr.csv",
        "benchmark": False,
    },
    {
        "code": "MY_F",
        "name": "自建F版（±50%缓冲）",
        "category": "自建FCF策略",
        "type": "strategy",
        "nav_path": "output/zz800_fcf_lenient_buffer_f50/backtest_nav_tr.csv",
        "benchmark": False,
    },

    # ── 现金流相关指数 & ETF ────────────────────────────────────
    {
        "code": "980092.CNI",
        "name": "国证自由现金流(价格)",
        "category": "现金流指数",
        "type": "index_price",
        "div_add": 1.2,
        "benchmark": False,
    },
    {
        "code": "480092.CNI",
        "name": "国证FCF全收益",
        "category": "现金流指数",
        "type": "index_tr",
        "benchmark": False,
    },
    {
        "code": "932368.CSI",
        "name": "中证800现金流(价格)",
        "category": "现金流指数",
        "type": "index_price",
        "div_add": 0.7,
        "benchmark": False,
    },
    {
        "code": "932365.CSI",
        "name": "中证全指现金流(价格)",
        "category": "现金流指数",
        "type": "index_price",
        "div_add": 0.8,
        "benchmark": False,
    },
    {
        "code": "159229.SZ",
        "name": "中证800现金流ETF(159229)",
        "category": "现金流ETF",
        "type": "etf",
        "benchmark": False,
    },
    {
        "code": "159201.SZ",
        "name": "自由现金流ETF华夏(159201)",
        "category": "现金流ETF",
        "type": "etf",
        "benchmark": False,
    },

    # ── 自建 800红利策略 ────────────────────────────────────────
    {
        "code": "MY_800DIV",
        "name": "自建800红利Top100（复现931644）",
        "category": "自建红利策略",
        "type": "strategy",
        "nav_path": "output/800div/backtest_nav_tr.csv",
        "benchmark": False,
    },
    {
        "code": "MY_800DIVX",
        "name": "自建800红利X版（全合规加权）",
        "category": "自建红利策略",
        "type": "strategy",
        "nav_path": "output/800div_x/backtest_nav_tr.csv",
        "benchmark": False,
    },

    # ── 红利相关指数 & ETF ──────────────────────────────────────
    {
        "code": "H00922.CSI",
        "name": "中证红利全收益",
        "category": "红利指数",
        "type": "index_tr",
        "benchmark": False,
    },
    {
        "code": "931644.CSI",
        "name": "中证800红利(价格)",
        "category": "红利指数",
        "type": "index_price",
        "div_add": 0.5,
        "benchmark": False,
    },
    {
        "code": "515180.SH",
        "name": "红利ETF易方达(515180)",
        "category": "红利ETF",
        "type": "etf",
        "benchmark": False,
    },
    {
        "code": "510880.SH",
        "name": "红利ETF华泰(510880)",
        "category": "红利ETF",
        "type": "etf",
        "benchmark": False,
    },
    {
        "code": "563020.SH",
        "name": "红利低波ETF易方达(563020)",
        "category": "红利ETF",
        "type": "etf",
        "benchmark": False,
    },

    # ── 宽基基准 ────────────────────────────────────────────────
    {
        "code": "H00300.CSI",
        "name": "沪深300全收益",
        "category": "宽基基准",
        "type": "index_tr",
        "benchmark": True,
    },
    {
        "code": "H00905.CSI",
        "name": "中证500全收益",
        "category": "宽基基准",
        "type": "index_tr",
        "benchmark": True,
    },
    {
        "code": "H00852.CSI",
        "name": "中证1000全收益",
        "category": "宽基基准",
        "type": "index_tr",
        "benchmark": True,
    },

    # ── 精选个股跟踪 ────────────────────────────────────────────
    {
        "code": "000423.SZ",
        "name": "东阿阿胶",
        "category": "精选个股",
        "type": "stock",
        "benchmark": False,
    },
]
