"""
红利周期投资 — 企业量化评估程序
======================================
数据优先级：
  1. tushare pro（实时行情 + 财务数据）
  2. 网络搜集（tushare 缺失时补充）

评估框架（基于红利周期三层估值体系）：
  Layer 1: 股息率历史分位评分
  Layer 2: 股债息差（核心锚）评分
  Layer 3: 等效分红率（含回购）评分
  Bonus:   确定性评级 / 护城河评估

输出：
  - 控制台彩色报告
  - data/dividend_report.md（Markdown）
  - data/dividend_chart.png（可视化图表）
"""

import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import font_manager
import warnings
warnings.filterwarnings("ignore")

# ─── 项目路径 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import tushare_cfg
import tushare as ts

# ─── 中文字体（macOS 苹方 / SimHei）────────────────────────────
def _setup_font():
    fonts = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ]
    for fp in fonts:
        if Path(fp).exists():
            font_manager.fontManager.addfont(fp)
            prop = font_manager.FontProperties(fname=fp)
            plt.rcParams["font.family"] = prop.get_name()
            plt.rcParams["axes.unicode_minus"] = False
            return
    # 回退：使用 sans-serif
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

_setup_font()

# ──────────────────────────────────────────────────────────────
# 目标企业定义（红利周期投资体系）
# ──────────────────────────────────────────────────────────────
COMPANIES = {
    # ── 第一类：弱周期红利 ─────────────────────────────────────
    "水电": {
        "长江电力": {"ts_code": "600900.SH", "category": "弱周期红利", "certainty": "A",
                   "moat": "垄断水电资产·类债券", "comment": "红利标杆，终极养老标的"},
        "国投电力": {"ts_code": "600886.SH", "category": "弱周期红利", "certainty": "A-",
                   "moat": "水电+火电混合", "comment": "水电占比持续提升，红利成色增强"},
    },
    "运营商": {
        "中国移动": {"ts_code": "600941.SH", "category": "弱周期红利", "certainty": "A",
                   "moat": "央企垄断·5G+算力新曲线", "comment": "Token新增长曲线，分红持续提升"},
        "中国电信": {"ts_code": "601728.SH", "category": "弱周期红利", "certainty": "A-",
                   "moat": "央企垄断·云业务", "comment": "云网融合转型，分红稳定"},
    },
    "交通": {
        "宁沪高速": {"ts_code": "600377.SH", "category": "弱周期红利", "certainty": "A",
                   "moat": "垄断长三角核心路桥资产·黄金路段", "comment": "长三角最赚钱高速公路，连接沪宁核心经济带，车流量稳增"},
        "山东高速": {"ts_code": "600350.SH", "category": "弱周期红利", "certainty": "A-",
                   "moat": "垄断山东全省高速资产·特许经营权", "comment": "山东高速集团旗下路桥龙头，弱周期稳分红，全省路网垄断"},
    },
    "银行": {
        "招商银行": {"ts_code": "600036.SH", "category": "弱周期红利", "certainty": "A",
                   "moat": "庞大优质客户资源·零售之王", "comment": "仅次于四大行，保守预估2%增速"},
        "工商银行": {"ts_code": "601398.SH", "category": "弱周期红利", "certainty": "AA",
                   "moat": "系统重要性银行·国家信用", "comment": "四大行最高确定性，倒了买金条也没用"},
        "农业银行": {"ts_code": "601288.SH", "category": "弱周期红利", "certainty": "AA",
                   "moat": "系统重要性银行·三农优势", "comment": "四大行之一，高股息稳定"},
        "建设银行": {"ts_code": "601939.SH", "category": "弱周期红利", "certainty": "AA",
                   "moat": "系统重要性银行·基建优势", "comment": "四大行之一，高股息稳定"},
        "中国银行": {"ts_code": "601988.SH", "category": "弱周期红利", "certainty": "AA",
                   "moat": "系统重要性银行·国际化", "comment": "四大行之一，高股息稳定"},
        "宁波银行": {"ts_code": "002142.SZ", "category": "弱周期红利", "certainty": "B+",
                   "moat": "城商行龙头·零售+对公均衡", "comment": "ROE 12%+，成长性优于国有行，股息率偏低但增速快"},
    },
    "保险": {
        "中国平安": {"ts_code": "601318.SH", "category": "弱周期红利", "certainty": "A-",
                   "moat": "综合金融帝国·保险+银行+资管", "comment": "保险龙头，54以下攒股，预期26年分红≥2.8元"},
    },
    # ── 第二类：消费/成长红利 ─────────────────────────────────
    "家电": {
        "美的集团": {"ts_code": "000333.SZ", "category": "消费成长红利", "certainty": "B+",
                   "moat": "品牌+技术全球领先·出海逻辑", "comment": "大手笔回购注销，等效分红8%+"},
        "海尔智家": {"ts_code": "600690.SH", "category": "消费成长红利", "certainty": "B+",
                   "moat": "全球家电品牌矩阵", "comment": "出海+回购，等效分红提升"},
        "格力电器": {"ts_code": "000651.SZ", "category": "消费成长红利", "certainty": "B+",
                   "moat": "空调技术领先·高分红传统", "comment": "分红率高，回购力度增加"},
    },
    "白酒": {
        "贵州茅台": {"ts_code": "600519.SH", "category": "消费成长红利", "certainty": "A",
                   "moat": "国酒品牌·定价权·超强护城河", "comment": "A股股王，品牌壁垒极高，分红率持续提升"},
        "五粮液": {"ts_code": "000858.SZ", "category": "消费成长红利", "certainty": "A-",
                   "moat": "浓香龙头·品牌溢价·千元价格带", "comment": "浓香型白酒龙头，品牌力仅次于茅台"},
        "泸州老窖": {"ts_code": "000568.SZ", "category": "消费成长红利", "certainty": "B+",
                   "moat": "国窖1573品牌·高端白酒第二梯队", "comment": "国窖1573放量，高端化趋势明确"},
    },
    "中药": {
        "云南白药": {"ts_code": "000538.SZ", "category": "消费成长红利", "certainty": "A-",
                   "moat": "百年国药品牌·白药系列垄断·大健康延伸", "comment": "中药第一品牌，混改后分红率大幅提升，分红+回购双轮驱动"},
        "达仁堂": {"ts_code": "600329.SH", "category": "消费成长红利", "certainty": "B+",
                   "moat": "速效救心丸独家品种·百年老字号", "comment": "中药老字号，股息率9.2%高分红，ROE 28.5%"},
        "东阿阿胶": {"ts_code": "000423.SZ", "category": "消费成长红利", "certainty": "B+",
                   "moat": "阿胶品类垄断·品牌壁垒极深", "comment": "阿胶龙头，分红29年稳健，股息率5.4%"},
        "济川药业": {"ts_code": "600566.SH", "category": "消费成长红利", "certainty": "B",
                   "moat": "蒲地蓝消炎口服液·儿科药强势", "comment": "中药细分龙头，股息率7.6%，负债率仅17%"},
    },
    # ── 第四类：ETF红利 ────────────────────────────────
    "ETF": {
        "易方达红利ETF": {"ts_code": "515180.SH", "category": "ETF红利", "certainty": "AA",
                   "moat": "跟踪中证红利指数·分散投资低费率", "comment": "中证红利低换手策略，适合长期攒股"},
    },
    # ── 第三类：周期资源红利 ─────────────────────────────────
    "矿业": {
        "紫金矿业": {"ts_code": "601899.SH", "category": "周期资源红利", "certainty": "B+",
                   "moat": "全球黄金/铜矿储量增长", "comment": "黄金4000/铜11000中枢，网格30/28/26，周期+成长"},
        "藏格矿业": {"ts_code": "000408.SZ", "category": "周期资源红利", "certainty": "B+",
                   "moat": "钾锂铜三主业·巨龙铜矿30.78%权益", "comment": "钾肥+碳酸锂+铜矿投资三轮驱动，2025年净利+49%，分红率61%"},
        "云铝股份": {"ts_code": "000807.SZ", "category": "周期资源红利", "certainty": "B+",
                   "moat": "绿电铝一体化·云南水电优势", "comment": "水电铝龙头，碳关税背景下绿电溢价，2025年净利+37%，ROE 19.7%"},
        "赤峰黄金": {"ts_code": "600988.SH", "category": "周期资源红利", "certainty": "B",
                   "moat": "国内高成长黄金矿企·海外矿山并购", "comment": "金价高位驱动利润暴增+75%，但分红率仅20%，强周期金矿"},

    },
    "石油": {
        "中国海油": {"ts_code": "600938.SH", "category": "周期资源红利", "certainty": "B+",
                   "moat": "低成本海上油田·央企", "comment": "油价50美元仍可高分红，安全边际高"},
    },
    "煤炭": {
        "陕西煤业": {"ts_code": "601225.SH", "category": "周期资源红利", "certainty": "B",
                   "moat": "优质煤矿资源·低成本", "comment": "熊市表现佳，高股息防御性强"},
        "中国神华": {"ts_code": "601088.SH", "category": "周期资源红利", "certainty": "B+",
                   "moat": "煤电运一体化·全产业链", "comment": "煤炭龙头，一体化壁垒，分红率超70%"},
    },
    "火电": {
        "华能国际": {"ts_code": "600011.SH", "category": "周期资源红利", "certainty": "B",
                   "moat": "火电龙头·煤价反向标的", "comment": "煤价跌利润弹，与煤炭天然对冲"},
        "华电国际": {"ts_code": "600027.SH", "category": "周期资源红利", "certainty": "B",
                   "moat": "火电央企·煤电联营", "comment": "煤电联营平滑周期，分红提升中"},
    },
    "海运": {
        "中远海控": {"ts_code": "601919.SH", "category": "周期资源红利", "certainty": "B",
                   "moat": "全球第四大集装箱航运公司·海洋联盟成员", "comment": "全球集运龙头，运价周期底部高分红，2025全年分红1.0元/股"},
    },
}

# 红利周期投资评估阈值
THRESHOLDS = {
    # 弱周期红利买入股息率阈值
    "弱周期_买入": 4.0,
    "弱周期_极佳": 5.5,
    # 消费成长红利阈值（含回购）
    "消费_买入": 5.0,
    "消费_等效分红_买入": 8.0,
    # 周期资源股息率阈值
    "周期_买入": 5.0,
    "周期_极佳": 7.0,
    # 10年国债利率（实时，近似值）
    "bond_yield_10y": 1.65,  # 2026年5月约1.65%
}

# ── 细分行业股息率锚 ──────────────────────────────────────────
# 参考「分红养老之路」作者逻辑：不同行业的历史股息率锚点不同
# 每个行业定义四档：观察（开始关注）、买入（建仓）、加仓（加大）、满仓（极佳买点）
# 以及网格交易的减仓线（股息率低于此值减仓）
SECTOR_THRESHOLDS = {
    "水电": {
        "watch": 3.5, "buy": 4.0, "add": 4.5, "full": 5.0, "reduce": 3.0,
        "comment": "弱周期标杆，4%+即可攒股，5%+黄金坑",
    },
    "运营商": {
        "watch": 4.0, "buy": 4.5, "add": 5.0, "full": 5.5, "reduce": 3.5,
        "comment": "央企垄断，4.5%+布局，5%+加仓",
    },
    "银行": {
        "watch": 4.0, "buy": 5.0, "add": 6.0, "full": 7.0, "reduce": 3.5,
        "comment": "四大行7%+历史极值，5%+可建仓",
    },
    "保险": {
        "watch": 3.5, "buy": 4.5, "add": 5.0, "full": 5.5, "reduce": 3.5,
        "comment": "综合金融，4.5%+布局",
    },
    "家电": {
        "watch": 4.0, "buy": 5.0, "add": 6.0, "full": 7.0, "reduce": 3.5,
        "comment": "消费龙头6%股息率很香，7%+极佳",
    },
    "白酒": {
        "watch": 3.5, "buy": 5.0, "add": 6.0, "full": 7.0, "reduce": 3.0,
        "comment": "高端白酒5%+可布局，6%+积极",
    },
    "医药": {
        "watch": 3.5, "buy": 5.0, "add": 5.5, "full": 6.0, "reduce": 3.5,
        "comment": "百年品牌5%+可布局，6%+极佳",
    },
    "农业": {
        "watch": 3.0, "buy": 4.5, "add": 5.5, "full": 6.5, "reduce": 2.5,
        "comment": "猪周期底部高股息信号，4.5%+布局",
    },
    "食品饮料": {
        "watch": 3.0, "buy": 4.0, "add": 5.0, "full": 6.0, "reduce": 2.5,
        "comment": "消费龙头4%+可攒股，6%+黄金坑",
    },
    "汽车": {
        "watch": 3.0, "buy": 4.0, "add": 5.0, "full": 6.0, "reduce": 2.5,
        "comment": "汽车产业链4%+关注，6%+极佳",
    },
    "传媒": {
        "watch": 3.0, "buy": 4.5, "add": 5.5, "full": 6.5, "reduce": 2.5,
        "comment": "梯媒垄断，4.5%+建仓",
    },
    "服饰": {
        "watch": 3.0, "buy": 4.0, "add": 5.0, "full": 6.0, "reduce": 2.5,
        "comment": "代工龙头，高股息稀缺",
    },
    "IT设备": {
        "watch": 2.0, "buy": 3.0, "add": 3.5, "full": 4.0, "reduce": 1.5,
        "comment": "科技硬件3%安全线，3.5%+低估",
    },
    "钢铁": {
        "watch": 2.0, "buy": 3.0, "add": 3.5, "full": 4.0, "reduce": 1.5,
        "comment": "周期钢铁3%安全边际线，4%+极佳买点",
    },
    "建材": {
        "watch": 2.0, "buy": 3.0, "add": 3.5, "full": 4.0, "reduce": 1.5,
        "comment": "周期建材3%安全边际线，4%+极佳买点",
    },
    "化工": {
        "watch": 2.0, "buy": 3.0, "add": 3.5, "full": 4.0, "reduce": 1.5,
        "comment": "周期化工3%安全边际线，4%+极佳买点",
    },
    "有色": {
        "watch": 2.0, "buy": 3.0, "add": 3.5, "full": 4.0, "reduce": 1.5,
        "comment": "周期有色3%安全边际线，4%+极佳买点",
    },
    "交通": {
        "watch": 3.5, "buy": 4.0, "add": 4.5, "full": 5.0, "reduce": 3.0,
        "comment": "铁路/公路垄断资产，4%+攒股",
    },
    "建筑": {
        "watch": 3.0, "buy": 4.0, "add": 5.0, "full": 6.0, "reduce": 2.5,
        "comment": "基建龙头，4%+布局",
    },
    "工业": {
        "watch": 2.5, "buy": 3.5, "add": 4.5, "full": 5.5, "reduce": 2.0,
        "comment": "工业制造，3.5%+关注",
    },
    "非银金融": {
        "watch": 3.0, "buy": 4.0, "add": 4.5, "full": 5.0, "reduce": 2.5,
        "comment": "券商红利稀缺，4%+关注",
    },
    "燃气": {
        "watch": 4.0, "buy": 5.0, "add": 6.0, "full": 7.0, "reduce": 3.5,
        "comment": "城燃稳定分红，5%+布局",
    },
    "石油": {
        "watch": 4.0, "buy": 5.0, "add": 6.0, "full": 7.0, "reduce": 3.5,
        "comment": "周期资源5%+建仓，7%+极佳",
    },
    "煤炭": {
        "watch": 5.0, "buy": 7.0, "add": 8.0, "full": 10.0, "reduce": 4.5,
        "comment": "煤炭10%+历史极值，7%+可建仓",
    },
    "火电": {
        "watch": 3.5, "buy": 4.5, "add": 5.5, "full": 6.5, "reduce": 3.0,
        "comment": "煤价跌利好火电，5%+可加仓，与煤炭天然对冲",
    },
    "海运": {
        "watch": 5.0, "buy": 6.0, "add": 7.0, "full": 8.0, "reduce": 4.0,
        "comment": "强周期海运，6%+可建仓，8%+极佳买点",
    },

    "ETF": {
        "watch": 3.5, "buy": 4.0, "add": 4.5, "full": 5.0, "reduce": 3.0,
        "comment": "ETF分散投资，4%+即可攒股",
    },
}

# ── 行业对冲组合 ──────────────────────────────────────────────
# 天然对冲：配煤炭则配火电，煤价跌利好火电、煤价涨利好煤炭
HEDGE_PAIRS = {
    "煤炭": "火电",   # 煤炭↔火电天然对冲
    "银行": "保险",   # 金融板块内均衡
    "石油": "化工",   # 上下游对冲

}

# ── 行业生命周期 ──────────────────────────────────────────────
# 成熟/夕阳行业的资本开支结束 → 自由现金流充裕 → 分红率提升 → 分红奶牛
# 关键信号：增速为负/零 + 分红率提升 = 分红奶牛信号
SECTOR_LIFECYCLE = {
    # 行业: { "stage": 阶段, "capex_trend": 资本开支趋势, "dividend_potential": 分红潜力 }
    "水电":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "高", "comment": "大坝建成，资本开支低，自由现金流充沛"},
    "运营商": {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "高", "comment": "5G建设高峰已过，进入收获期"},
    "银行":   {"stage": "成熟",   "capex_trend": "无",   "dividend_potential": "高", "comment": "无资本开支，利润直接分红"},
    "保险":   {"stage": "成熟",   "capex_trend": "无",   "dividend_potential": "中", "comment": "综合金融，分红稳定"},
    "家电":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "中高", "comment": "竞争格局稳定，龙头回购+分红"},
    "白酒":   {"stage": "成熟",   "capex_trend": "低",   "dividend_potential": "高", "comment": "品牌护城河，极低资本开支"},
    "医药":   {"stage": "成熟",   "capex_trend": "低",   "dividend_potential": "中", "comment": "品牌溢价，分红率可提升"},
    "农业":   {"stage": "周期",   "capex_trend": "波动", "dividend_potential": "中", "comment": "猪周期底部高分红信号"},
    "汽车":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "中高", "comment": "汽车产业链龙头，分红稳定"},
    "传媒":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "高", "comment": "梯媒垄断+轻资产，自由现金流好"},
    "服饰":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "中", "comment": "代工龙头高分红"},
    "IT设备":{"stage": "成长",   "capex_trend": "高",   "dividend_potential": "低", "comment": "科技硬件资本开支大，分红率较低"},
    "食品饮料":{"stage": "成熟", "capex_trend": "低",   "dividend_potential": "高", "comment": "消费品龙头，极低资本开支"},
    "石油":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "中高", "comment": "能源转型期，维持产量为主"},
    "煤炭":   {"stage": "夕阳转奶牛", "capex_trend": "下降", "dividend_potential": "极高", "comment": "资本开支结束→分红率70%+，典型夕阳→奶牛"},
    "火电":   {"stage": "转型",   "capex_trend": "稳定", "dividend_potential": "中高", "comment": "煤电转型期，电价市场化提升利润弹性"},
    "海运":   {"stage": "周期",   "capex_trend": "波动", "dividend_potential": "中", "comment": "全球集运周期，运价波动大，底部高分红"},
    "钢铁":   {"stage": "周期",   "capex_trend": "下降", "dividend_potential": "中", "comment": "钢铁周期，资本开支下降→分红率提升"},
    "建材":   {"stage": "周期",   "capex_trend": "稳定", "dividend_potential": "中高", "comment": "水泥建材龙头，分红率高"},
    "化工":   {"stage": "成长",   "capex_trend": "高",   "dividend_potential": "低", "comment": "扩产周期，资本开支大；待高峰后分红率可升至50%"},
    "有色":   {"stage": "周期",   "capex_trend": "稳定", "dividend_potential": "中", "comment": "铝加工，周期波动"},
    "交通":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "中高", "comment": "铁路/公路垄断，收费稳定，分红可靠"},
    "建筑":   {"stage": "成熟",   "capex_trend": "下降", "dividend_potential": "中高", "comment": "基建央企，去杠杆后分红率提升"},
    "工业":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "中", "comment": "工业制造龙头，分红稳定"},
    "非银金融":{"stage": "周期",  "capex_trend": "无",   "dividend_potential": "中", "comment": "券商，经营周期性但轻资产"},
    "燃气":   {"stage": "成熟",   "capex_trend": "稳定", "dividend_potential": "中高", "comment": "城燃特许经营，现金流稳定"},

    "ETF":   {"stage": "N/A",    "capex_trend": "N/A",  "dividend_potential": "N/A", "comment": "被动投资"},
}

# 网络补充数据（tushare 缺失时使用，2026-05-22 数据）
FALLBACK_DATA = {
    "600900.SH": {  # 长江电力
        "close": 26.85, "pe_ttm": 18.5, "pb": 3.2,
        "div_yield": 3.74,  # 2025年DPS=1.0，华泰证券数据
        "roe": 14.2, "dps_latest": 1.0,
        "buyback_yield": 0.0, "revenue_growth": 2.07, "net_profit_growth": 6.17,
        "payout_ratio": 70.9, "total_mv": 6550.0,
        "note": "2025年报：营收862亿+2.07%，净利润345亿+6.17%，DPS 1.0元"
    },
    "600886.SH": {  # 国投电力
        "close": 12.50, "pe_ttm": 16.0, "pb": 2.1,
        "div_yield": 4.2, "roe": 13.0, "dps_latest": 0.52,
        "buyback_yield": 0.0, "revenue_growth": 5.0, "net_profit_growth": 8.0,
        "payout_ratio": 55.0, "total_mv": 900.0,
        "note": "水电占比持续提升，分红稳定增长"
    },
    "600941.SH": {  # 中国移动
        "close": 95.98, "pe_ttm": 15.31, "pb": 1.5,
        # 2025全年分红=中期+年报共4.7037元/股，股价95.98元，自算4.90%
        "div_yield": 4.90, "roe": 9.97, "dps_latest": 4.7037,
        "buyback_yield": 0.5, "revenue_growth": 3.0, "net_profit_growth": 5.0,
        "payout_ratio": 70.0, "total_mv": 20000.0,
        "note": "2025全年分红4.7037元/股，股价95.98元，股息率4.90%；Token新增长曲线"
    },
    "601728.SH": {  # 中国电信
        "close": 6.15, "pe_ttm": 17.77, "pb": 1.2,
        # 2025全年分红=中期+年报到0.272元/股，股价6.15元，自算4.42%
        "div_yield": 4.42, "roe": 7.27, "dps_latest": 0.272,
        "buyback_yield": 0.3, "revenue_growth": 4.0, "net_profit_growth": 6.0,
        "payout_ratio": 65.0, "total_mv": 5800.0,
        "note": "2025全年分红0.272元/股，股价6.15元，股息率4.42%"
    },
    "600036.SH": {  # 招商银行
        "close": 38.50, "pe_ttm": 7.8, "pb": 1.1,
        # 2025年全年分红 = 中期1.013 + 年报预案1.003 = 2.016元/股
        # 正常股息率 ≈ 2.016 / 37.15 = 5.43%（tushare dv_ttm=8.11%含2024年特别大额分红2.0元）
        "div_yield": 5.4,
        "roe": 12.0, "dps_latest": 2.02,
        "buyback_yield": 0.0, "revenue_growth": -2.0, "net_profit_growth": 1.2,
        "payout_ratio": 33.0, "total_mv": 9700.0,
        "note": "2025年全年分红约2.02元/股（中期+年报预案），正常股息率约5.4%；tushare dv_ttm含2024特别高分红偏高"
    },
    "601398.SH": {  # 工商银行
        "close": 7.18, "pe_ttm": 6.89, "pb": 0.77,
        # 2025全年分红=中期0.1414+年报0.1689=0.3103元/股，股价7.18元，股息率4.32%
        # tushare自算4.32%已准确（年报已于2026-05-13除权）
        "div_yield": 4.32, "roe": 12.5, "dps_latest": 0.31,
        "buyback_yield": 0.0, "revenue_growth": -1.0, "net_profit_growth": 0.5,
        "payout_ratio": 30.0, "total_mv": 26000.0,
        "note": "2025全年分红0.3103元/股(中期0.1414+年报0.1689)，股价7.18元，股息率4.32%；年报已于2026-05-13除权"
    },
    "601288.SH": {  # 农业银行
        "close": 6.46, "pe_ttm": 7.68, "pb": 0.79,
        # 2025全年分红=中期0.1195+年报0.1300=0.2495元/股，股价6.46元，股息率3.86%
        # 注：年报0.13已于2026-05-13除权，自算3.86%准确
        "div_yield": 3.86, "roe": 9.2, "dps_latest": 0.25,
        "buyback_yield": 0.0, "revenue_growth": -0.5, "net_profit_growth": 0.8,
        "payout_ratio": 30.0, "total_mv": 17500.0,
        "note": "2025全年分红0.2495元/股(中期0.1195+年报0.1300)，股价6.46元，股息率3.86%；年报已除权"
    },
    "601939.SH": {  # 建设银行
        "close": 10.05, "pe_ttm": 7.7, "pb": 0.73,
        # 2025年全年分红 = 中期0.1858 + 年报预案0.2029 = 0.3887元/股
        # 正确股息率 = 0.3887 / 10.05 = 3.87%（tushare dv_ttm=1.85%因年报分红未除权严重偏低）
        # fallback数据6.3%偏高，网络验证华创证券给出约4.1%（含目标价溢价），按实际股价修正为3.87%
        "div_yield": 3.87,
        "roe": 9.7, "dps_latest": 0.39,
        "buyback_yield": 0.0, "revenue_growth": -1.2, "net_profit_growth": 0.9,
        "payout_ratio": 30.0, "total_mv": 24400.0,
        "note": "2025全年分红0.389元/股(中期0.186+年报0.203)，股价10.05元，股息率3.87%；tushare原始1.85%因未除权严重偏低，网络验证修正"
    },
    "601988.SH": {  # 中国银行
        "close": 5.86, "pe_ttm": 7.7, "pb": 0.68,
        # 2025年全年分红: 中期0.1094(已实施2025-12-11) + 年报预案0.1169 = 0.2263元/股
        # 自算股息率: 0.2263 / 5.86 = 3.86%（准确值）
        # tushare dv_ttm=1.87%是因TTM窗口滚动：2024年报除权日2025-04-25已滚出TTM窗口，仅含中期0.1094/5.86=1.87%
        # 此fallback设为与自算一致，避免触发兜底
        "div_yield": 3.86,
        "roe": 8.3, "dps_latest": 0.23,
        "buyback_yield": 0.0, "revenue_growth": -0.8, "net_profit_growth": 0.3,
        "payout_ratio": 30.0, "total_mv": 21400.0,
        "note": "2025全年分红0.2263元/股(中期0.1094+年报预案0.1169)，股价5.86元，股息率3.86%；dv_ttm=1.87%因TTM窗口滚动（年报除权日滚出），自算结果准确"
    },
    "002142.SZ": {  # 宁波银行
        "close": 31.09, "pe_ttm": 6.82, "pb": 0.88,
        # 2025年全年分红=中期0.3+年报0.9=1.2元/股，÷31.09元=3.86%
        # 自算可以直接获取，fallback供兜底
        "div_yield": 3.86,
        "roe": 12.21, "dps_latest": 1.2,
        "buyback_yield": 0.0, "revenue_growth": 3.0, "net_profit_growth": 8.13,
        "payout_ratio": 22.0, "total_mv": 20530.0,
        "note": "2025全年分红1.2元/股(中期0.3+年报0.9)，股价31.09元，股息率3.86%；ROE 12%城商行龙头"
    },
    "000333.SZ": {  # 美的集团
        # 网络验证：多机构PE≈13x，EPS约6.19元，股价≈81元
        # 2025年全年分红=中期0.5+年报3.8=4.3元/股，÷81元≈5.3%
        # 国联民生说"接近7%"可能基于更低时点股价（约62元时的数字）
        "close": 81.0, "pe_ttm": 13.0, "pb": 4.8,
        "div_yield": 5.3, "roe": 20.0, "dps_latest": 4.30,
        "buyback_yield": 4.5, "revenue_growth": 10.0, "net_profit_growth": 14.0,
        "payout_ratio": 70.0, "total_mv": 5600.0,
        "note": "网络验证：PE≈13x(国联民生)，股价≈81元，2025全年分红4.3元/股，股息率5.3%；等效分红=5.3%+4.5%=9.8%"
    },
    "600690.SH": {  # 海尔智家
        # tushare实测(2026-05-30)：close≈20.88, 自算股息率=5.54%
        # 2025年全年分红: 中期0.2692+年报预案0.8867=1.1559元/股
        # fallback旧值 4.2%基于21.4元已过时，更新为与自算一致
        "close": 20.88, "pe_ttm": 9.5, "pb": 2.8,
        "div_yield": 5.5, "roe": 17.0, "dps_latest": 1.16,
        "buyback_yield": 2.5, "revenue_growth": 8.0, "net_profit_growth": 9.0,
        "payout_ratio": 42.0, "total_mv": 1900.0,
        "note": "2025全年分红1.1559元/股(中期0.2692+年报预案0.8867)，自算股息率5.54%；等效分红=5.5%+2.5%=8.0%"
    },
    "000651.SZ": {  # 格力电器
        # tushare实测(2026-05-30)：close≈39.17, 自算股息率=7.66%
        # 2025全年分红: 三季报0.98+1.0(实施2026-01-23)+年报预案2.0=3.0元/股
        # 3.0/39.17=7.66%，与dv_ttm=7.64%高度吻合
        "close": 39.17, "pe_ttm": 9.8, "pb": 2.8,
        "div_yield": 7.6, "roe": 28.0, "dps_latest": 3.0,
        "buyback_yield": 1.2, "revenue_growth": 2.0, "net_profit_growth": 3.0,
        "payout_ratio": 60.0, "total_mv": 2350.0,
        "note": "2025全年分红3.0元/股(三季报1.0+年报预案2.0)，股息率7.66%；等效分红≈8.8%；空调龙头高分红"
    },
    "601899.SH": {  # 紫金矿业
        # 网络验证：国联民生2026-04-24 PE=11x（2026E），财信证券10.37x，中邮10.27x
        # 2025年全年分红=中期0.22+年报0.38=0.60元，EPS约2.7元×PE11≈29.7元
        # 股息率=0.60/29.7≈2.0%；tushare dv_ttm=1.7%基本吻合
        # tushare实测：close=29.99元，dv_ttm=1.66%，pe=12.9x（2026-05-21）市值7975亿
        # 网络验证：国联民生PE=11x（2026-04-24，更早时点），财信10.37x，当前PE因股价上涨约12.9x
        # 全年分红0.60元/29.99元=2.0%；tushare dv_ttm=1.66%略低可能因部分分红尚未除权
        "close": 29.99, "pe_ttm": 12.9, "pb": 4.2,
        "div_yield": 2.0, "roe": 31.8, "dps_latest": 0.60,
        "buyback_yield": 0.3, "revenue_growth": 15.0, "net_profit_growth": 61.5,
        "payout_ratio": 22.0, "total_mv": 7975.0,
        "note": "tushare实测(2026-05-21)：股价29.99元，PE=12.9x，市值7975亿；全年分红0.60元，股息率2.0%"
    },
    "600938.SH": {  # 中国海油
        # 网络验证：信达证券2026-04-29明确"A股收盘价对应PE=12.02x"，EPS=3.31元，推算股价≈39.8元
        # 2025全年分红约1.46元/股（含中期），÷40元≈3.65%
        # tushare实测：close=36.22元，dv_ttm=3.51%，pe=13.8x（2026-05-21）
        # 网络：信达证券PE=12.02x（2026-04-29，价格较低时点）；当前市值约1.72万亿
        "close": 36.22, "pe_ttm": 13.8, "pb": 1.9,
        "div_yield": 3.51, "roe": 18.0, "dps_latest": 1.27,
        "buyback_yield": 0.0, "revenue_growth": -5.0, "net_profit_growth": -8.0,
        "payout_ratio": 45.0, "total_mv": 17215.0,
        "note": "tushare实测(2026-05-21)：股价36.22元，PE=13.8x，dv_ttm=3.51%；网络验证信达证券12.02x（4月底较低价时点）"
    },
    "601318.SH": {  # 中国平安
        # 中国平安2025年报：归母净利润1266亿+18.9%，营运利润1219亿+9.5%
        # 2025年全年分红=中期0.93+年报1.62=2.55元/股，股价约52元，股息率约4.9%
        # ROE约13.5%，PE(TTM)约8.5x，PB约1.05x
        "close": 52.0, "pe_ttm": 8.5, "pb": 1.05,
        "div_yield": 4.9, "roe": 13.5, "dps_latest": 2.55,
        "buyback_yield": 0.5, "revenue_growth": 5.0, "net_profit_growth": 18.9,
        "payout_ratio": 42.0, "total_mv": 9500.0,
        "note": "2025年报：归母净利润1266亿+18.9%，全年分红2.55元/股(中0.93+年报1.62)，股息率约4.9%；综合金融帝国，分红持续提升"
    },
    "601088.SH": {  # 中国神华
        # 中国神华2025年报：营收3448亿+0.6%，归母净利润586亿-0.4%
        # 2025年全年分红=中期0.98+年报预案1.03=2.01元/股，当前股价46.92元，股息率约4.28%
        # tushare dv_ttm=6.22%含: 2024年报2.26(2025-07-07除权)+2025中期0.98 = 跨年TTM含两年数据
        # 自算4.28%是正确的2025年度预期股息率
        "close": 46.92, "pe_ttm": 10.3, "pb": 1.60,
        "div_yield": 4.28, "roe": 14.0, "dps_latest": 2.01,
        "buyback_yield": 0.0, "revenue_growth": 0.6, "net_profit_growth": -0.4,
        "payout_ratio": 72.0, "total_mv": 9320.0,
        "note": "2025全年分红2.01元/股(中期0.98+年报预案1.03)，股息率4.28%；dv_ttm=6.22%为跨年TTM含2024年报+2025中期，自算更准确"
    },
    "515180.SH": {  # 易方达中证红利ETF
        "close": 1.396, "pe_ttm": 27.6, "pb": 0.0,
        "div_yield": 4.37,
        "roe": 9.9, "dps_latest": 0.0,
        "buyback_yield": 0.0, "revenue_growth": 0.0, "net_profit_growth": 0.0,
        "payout_ratio": 0.0, "total_mv": 125.0,
        "note": "易方达中证红利ETF(515180)，跟踪中证红利指数，规模约125亿，管理费0.15%/年，持仓加权PE=27.6x，ROE=9.9%"
    },
    "601225.SH": {  # 陕西煤业
        # 网络验证：中国银河2026-05-11明确"当前收盘价对应股息率为4.0%，2025年每股分红0.948元"
        # 推算股价 = 0.948 / 4.0% = 23.7元；PE=12.8x（2026E预测）
        # tushare实测：close=23.42元，dv_ttm=5.02%，pe=14.0x（2026-05-21）
        # dv_ttm=5.02%含近12个月TTM分红（包含中期+年报两次分红）
        # 中国银河2026-05-11说"4.0%"是仅按2025年报单次分红0.948元/23.7元计算
        # tushare的5.02%更准确（反映全年度真实股东回报）
        "close": 23.42, "pe_ttm": 14.0, "pb": 2.6,
        "div_yield": 5.02, "roe": 25.0, "dps_latest": 0.95,
        "buyback_yield": 0.0, "revenue_growth": -10.0, "net_profit_growth": -12.0,
        "payout_ratio": 58.0, "total_mv": 2271.0,
        "note": "tushare实测(2026-05-21)：股价23.42元，PE=14x，dv_ttm=5.02%(TTM含中期+年报)；中国银河4.0%仅算单次年报分红"
    },
    "000408.SZ": {  # 藏格矿业
        # 2025年报：营收35.77亿+10.0%，归母净利润38.52亿+49.3%，ROE 25.59%
        # 全年分红=每10股15元=1.5元/股，分红率61.1%
        # 股价约77.18元(2026-05-30)，PE=19.24x，PB=6.86x
        # 72%利润来自巨龙铜矿30.78%权益（投资收益），主业为钾肥+碳酸锂
        "close": 77.18, "pe_ttm": 19.24, "pb": 6.86,
        "div_yield": 1.94, "roe": 25.59, "dps_latest": 1.50,
        "buyback_yield": 0.0, "revenue_growth": 10.0, "net_profit_growth": 49.3,
        "payout_ratio": 61.0, "total_mv": 1211.0,
        "note": "2025年报：钾锂铜三轮驱动，净利38.52亿+49.3%，全年分红1.5元/股，股息率1.94%；巨龙铜矿贡献72%利润"
    },
    "000807.SZ": {  # 云铝股份
        # 2025年报：营收600.43亿+10.3%，归母净利润60.55亿+37.2%，ROE 19.70%
        # 全年累计分红24.24亿(含中期)，每股约0.70元，分红率约40%
        # 股价31.69元(2026-05-26)，PE≈11.6x，PB≈2.8x，市值~1099亿
        "close": 31.69, "pe_ttm": 11.6, "pb": 2.8,
        "div_yield": 2.21, "roe": 19.70, "dps_latest": 0.70,
        "buyback_yield": 0.0, "revenue_growth": 10.3, "net_profit_growth": 37.2,
        "payout_ratio": 40.0, "total_mv": 1099.0,
        "note": "2025年报：水电铝龙头，净利60.55亿+37.2%，全年分红0.70元/股，股息率2.21%；绿电溢价+碳关税受益"
    },
    "600988.SH": {  # 赤峰黄金
        # 2025年报：营收126.39亿+40.0%，归母净利润30.82亿+74.7%，ROE 27.14%
        # 全年分红0.32元/股，分红率仅19.7%
        # 股价33.37元(2026-05-29)，PE=17.67x，PB=4.64x，市值~634亿
        "close": 33.37, "pe_ttm": 17.67, "pb": 4.64,
        "div_yield": 0.96, "roe": 27.14, "dps_latest": 0.32,
        "buyback_yield": 0.0, "revenue_growth": 40.0, "net_profit_growth": 74.7,
        "payout_ratio": 19.7, "total_mv": 634.0,
        "note": "2025年报：高成长金矿，净利30.82亿+74.7%，但分红仅0.32元/股(率19.7%)，股息率0.96%；强周期高波动"
    },
    "600519.SH": {  # 贵州茅台
        # tushare实测(2026-05-26)：股价1285.88元，PE=19.5x，dv_ttm=4.02%
        # 2025年全年分红=中期23.957+年报预案27.993=51.95元/股，股息率=51.95/1285.88=4.04%
        # 2025年报：营收-1.2%，净利润-4.5%，ROE=34.46%
        "close": 1285.88, "pe_ttm": 19.5, "pb": 5.94,
        "div_yield": 4.04, "roe": 34.46, "dps_latest": 51.95,
        "buyback_yield": 0.0, "revenue_growth": -1.2, "net_profit_growth": -4.5,
        "payout_ratio": 79.0, "total_mv": 16103.0,
        "note": "2025年报：ROE=34.46%，全年分红51.95元/股(中期23.957+年报预案27.993)，股息率4.04%；A股股王，品牌壁垒极高"
    },
    "000858.SZ": {  # 五粮液
        # tushare实测(2026-05-26)：股价84.14元，PE=25.9x，dv_ttm=6.83%
        # 2025年全年分红=中期2.578(已实施)+年报预案2.578=5.156元/股，股息率=5.156/84.14=6.13%
        # 注：tushare dv_ttm=6.83%偏高，含TTM重叠；自算6.13%更准确
        "close": 84.14, "pe_ttm": 25.9, "pb": 2.55,
        "div_yield": 6.13, "roe": 22.0, "dps_latest": 5.16,
        "buyback_yield": 0.5, "revenue_growth": -5.0, "net_profit_growth": -5.0,
        "payout_ratio": 70.0, "total_mv": 3266.0,
        "note": "2025全年分红5.156元/股(中期2.578+年报2.578)，股息率6.13%；浓香龙头，品牌力仅次于茅台；tushare dv_ttm=6.83%偏高含TTM重叠"
    },
    "000538.SZ": {  # 云南白药
        # tushare实测(2026-05-28)：股价49.73元，PE=16.9x，自算股息率=5.23%
        # 2025年报全年分红=中期0.86+年报预案1.74=2.60元/股，股息率=2.60/49.73=5.23%
        # ROE=13.1%，净利润增速+8.5%，混改后分红率大幅提升
        # 2024-2025年有多次回购注销，等效分红更高
        "close": 49.73, "pe_ttm": 16.9, "pb": 2.26,
        "div_yield": 5.23, "roe": 13.1, "dps_latest": 2.60,
        "buyback_yield": 2.0, "revenue_growth": 5.0, "net_profit_growth": 8.5,
        "payout_ratio": 75.0, "total_mv": 887.0,
        "note": "2025全年分红2.60元/股(中期0.86+年报1.74)，股息率5.23%；百年国药品牌，混改后分红率超75%，等效分红≈7.2%"
    },
    "600329.SH": {  # 达仁堂
        "close": 40.72, "pe_ttm": 14.4, "pb": 4.17,
        "div_yield": 9.16, "roe": 28.5, "dps_latest": 2.45,
        "buyback_yield": 0.0, "revenue_growth": -32.7, "net_profit_growth": -4.4,
        "payout_ratio": 85.0, "total_mv": 314.0,
        "note": "2025年报：速效救心丸独家品种，股息率9.16%，ROE 28.5%，分红21年；营收下滑因渠道调整"
    },
    "000423.SZ": {  # 东阿阿胶
        "close": 49.99, "pe_ttm": 18.2, "pb": 3.26,
        "div_yield": 5.40, "roe": 16.8, "dps_latest": 1.78,
        "buyback_yield": 0.0, "revenue_growth": 8.8, "net_profit_growth": 11.7,
        "payout_ratio": 60.0, "total_mv": 322.0,
        "note": "2025年报：阿胶品类垄断，分红29年连续，股息率5.40%，业绩稳步回升"
    },
    "600566.SH": {  # 济川药业
        "close": 27.53, "pe_ttm": 14.6, "pb": 1.69,
        "div_yield": 7.57, "roe": 12.1, "dps_latest": 2.09,
        "buyback_yield": 0.0, "revenue_growth": -22.4, "net_profit_growth": -29.8,
        "payout_ratio": 80.0, "total_mv": 254.0,
        "note": "2025年报：儿科中药细分龙头，股息率7.57%，负债率仅17.3%，轻资产高分红；利润下滑因集采影响"
    },
    "000568.SZ": {  # 泸州老窖
        # tushare实测(2026-05-26)：股价91.85元，PE=13.6x，dv_ttm=6.48%
        # 2025年全年分红=中期1.358(已实施)+年报预案4.417=5.775元/股，股息率=5.775/91.85=6.29%
        # 2025年报：营收-17.5%，净利润-19.6%，ROE=22.29%
        "close": 91.85, "pe_ttm": 13.6, "pb": 2.62,
        "div_yield": 6.29, "roe": 22.29, "dps_latest": 5.78,
        "buyback_yield": 0.0, "revenue_growth": -17.5, "net_profit_growth": -19.6,
        "payout_ratio": 65.0, "total_mv": 1352.0,
        "note": "2025全年分红5.775元/股(中期1.358+年报预案4.417)，股息率6.29%；国窖1573放量，高端化趋势明确"
    },
    "601919.SH": {  # 中远海控
        "close": 14.14, "pe_ttm": 7.1, "pb": 1.0,
        "div_yield": 7.07, "roe": 13.17, "dps_latest": 1.0,
        "buyback_yield": 0.0, "revenue_growth": -6.14, "net_profit_growth": -37.13,
        "payout_ratio": 50.0, "total_mv": 2165.0,
        "note": "2025全年分红1.0元/股(中报0.56+年报预案0.44)，股息率7.07%；全球第四大集运公司，运价周期底部高分红"
    },
    "600011.SH": {  # 华能国际
        # 2025年报：净利润144.1亿+42.2%，营收2292.9亿，ROE=10.30%
        # 2025年分红：仅年报预案0.4元/股（无中期），总股本≈157亿股，分红率≈44%
        # 股价8.87元(2026-05-29)，PE=10.0x，PB=1.99，市值1392亿
        # 股息率 = 0.4 / 8.87 = 4.51%
        # tushare dv_ttm=3.04%含2024年报0.27元已除权(2025-07-10)，未含2025年报预案 → 偏低
        "close": 8.87, "pe_ttm": 10.0, "pb": 1.99,
        "div_yield": 4.51, "roe": 10.3, "dps_latest": 0.40,
        "buyback_yield": 0.0, "revenue_growth": 5.0, "net_profit_growth": 42.2,
        "payout_ratio": 44.0, "total_mv": 1392.0,
        "note": "2025年报：净利144.1亿+42.2%，年报分红0.4元/股(无中期)，股息率4.51%；火电龙头，煤价下行利好利润弹性；tushare dv_ttm=3.04%含2024年报未含2025预案，自算更准"
    },
    "600350.SH": {  # 山东高速
        # tushare实测(2026-06-02)：股价11.62元，PE=17.4x，PB=1.83，自算股息率=3.61%
        # 2025年报：ROE=7.3%，净利润增速+0.3%
        # road/toll highway stock, stable dividend
        "close": 11.62, "pe_ttm": 17.4, "pb": 1.83,
        "div_yield": 3.61, "roe": 7.3, "dps_latest": 0.42,
        "buyback_yield": 0.0, "revenue_growth": 2.0, "net_profit_growth": 0.3,
        "payout_ratio": 55.0, "total_mv": 562.0,
        "note": "2026-06-02 tushare实测: 股价11.62元，PE=17.4x，自算股息率3.61%；山东路桥龙头，ROE偏低因重资产高折旧"
    },
    "600377.SH": {  # 宁沪高速
        # tushare实测(2026-06-02)：股价12.93元，PE=13.7x，PB=1.50，自算股息率=3.79%
        # 2025年报：ROE=11.5%，净利润增速-7.1%
        # 长三角最赚钱的高速公路资产
        "close": 12.93, "pe_ttm": 13.7, "pb": 1.50,
        "div_yield": 3.79, "roe": 11.5, "dps_latest": 0.49,
        "buyback_yield": 0.0, "revenue_growth": 2.0, "net_profit_growth": -7.1,
        "payout_ratio": 60.0, "total_mv": 651.0,
        "note": "2026-06-02 tushare实测: 股价12.93元，PE=13.7x，自算股息率3.79%；长三角黄金路段，ROE 11.5%为路桥中优质"
    },
    "600027.SH": {  # 华电国际
        # 2025年报：净利润60.7亿+1.4%，营收1260.1亿，ROE=8.89%
        # 2025年分红：中期0.09(已实施2025-11-12)+年报预案0.14 = 0.23元/股，分红率≈44%
        # 股价5.56元(2026-05-29)，PE=10.89x，PB=1.29，市值646亿
        # 股息率 = 0.23 / 5.56 = 4.14%
        # tushare dv_ttm=3.79%含2024年报0.13(2025-07-14除权)+2025中期0.09(2025-11-12除权)，
        # 未含2025年报预案0.14 → 略偏低
        "close": 5.56, "pe_ttm": 10.89, "pb": 1.29,
        "div_yield": 4.14, "roe": 8.89, "dps_latest": 0.23,
        "buyback_yield": 0.0, "revenue_growth": 3.0, "net_profit_growth": 1.4,
        "payout_ratio": 44.0, "total_mv": 646.0,
        "note": "2025年报：净利60.7亿+1.4%，全年分红0.23元/股(中期0.09+年报预案0.14)，股息率4.14%；煤电联营平滑周期，与煤炭形成天然对冲；tushare dv_ttm=3.79%未含2025年报预案"
    },

}


# ──────────────────────────────────────────────────────────────
# tushare 数据获取
# ──────────────────────────────────────────────────────────────
class TushareDataFetcher:
    def __init__(self):
        try:
            ts.set_token(tushare_cfg.token)
            self.pro = ts.pro_api()
            print("✅ tushare pro 初始化成功")
        except Exception as e:
            print(f"⚠️  tushare 初始化失败: {e}")
            self.pro = None

    def _safe_call(self, func_name: str, **kwargs) -> Optional[pd.DataFrame]:
        """带重试的 API 调用"""
        if self.pro is None:
            return None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(0.3)  # 重试前等待（仅重试时）
                result = getattr(self.pro, func_name)(**kwargs)
                if result is not None and not result.empty:
                    return result
                return pd.DataFrame()
            except Exception as e:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))  # 重试退避：1.5s/3s
                else:
                    print(f"    ⚠️  {func_name} 失败: {e}")
                    return None

    def get_daily_basic(self, ts_code: str) -> Optional[Dict]:
        """获取最新交易日行情+估值指标"""
        # 获取最近20个交易日的数据
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        df = self._safe_call(
            "daily_basic",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv,turnover_rate"
        )
        if df is None or df.empty:
            return None

        df = df.sort_values("trade_date", ascending=False)
        row = df.iloc[0]

        # dv_ttm = TTM股息率(%)
        return {
            "close": float(row.get("close", 0) or 0),
            "pe_ttm": float(row.get("pe_ttm", 0) or 0),
            "pb": float(row.get("pb", 0) or 0),
            "div_yield": float(row.get("dv_ttm", 0) or 0),  # tushare dv_ttm即股息率(%)
            "total_mv": float(row.get("total_mv", 0) or 0) / 10000,  # 转换为亿元
            "trade_date": str(row.get("trade_date", "")),
        }

    def get_financial_indicator(self, ts_code: str) -> Optional[Dict]:
        """获取最新财务指标（ROE, 分红率等）
        
        注意：
          - tushare fina_indicator 的 roe 是单期ROE（季度值），需识别是否年报
          - 优先取最新年报数据（end_date 末尾为1231）
          - netprofit_yoy/or_yoy 是同比增速（%）
        """
        df = self._safe_call(
            "fina_indicator",
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,roe,netprofit_yoy,or_yoy,profit_dedt,basic_eps,dps"
        )
        if df is None or df.empty:
            return None

        df = df.sort_values("ann_date", ascending=False).reset_index(drop=True)
        
        # 优先取最新年报（end_date 以 1231 结尾）
        annual = df[df["end_date"].astype(str).str.endswith("1231")]
        if not annual.empty:
            row = annual.iloc[0]
            # 年报 ROE 是全年值，直接使用
            roe = float(row.get("roe", 0) or 0)
        else:
            # 无年报时取最新期，季度ROE × 4 近似年化
            row = df.iloc[0]
            end_date = str(row.get("end_date", ""))
            roe_raw = float(row.get("roe", 0) or 0)
            # 若是季报（0331/0630/0930），做简单年化
            if end_date.endswith("0331"):
                roe = roe_raw * 4
            elif end_date.endswith("0630"):
                roe = roe_raw * 2
            elif end_date.endswith("0930"):
                roe = roe_raw * 4 / 3
            else:
                roe = roe_raw

        return {
            "roe": round(roe, 2),
            "net_profit_growth": float(row.get("netprofit_yoy", 0) or 0),
            "revenue_growth": float(row.get("or_yoy", 0) or 0),
            "dps_latest": float(row.get("dps", 0) or 0),
            "ann_date": str(row.get("ann_date", "")),
        }

    def get_real_div_yield(self, ts_code: str, close: float) -> Optional[float]:
        """
        从 dividend 接口自算真实年化股息率。

        逻辑：
          取最新会计年度（end_year）的所有分红批次，
          状态为「实施」或「股东大会通过」均计入（不等待除权）。
          对同一个 end_date + div_proc 对，只保留一条（去重），
          因为 tushare 有时会对同一笔分红同时存"股东大会通过"和"实施"两条。

          优先取「实施」记录，若无「实施」则取「股东大会通过」，
          同一 end_date 下每个状态只取一条，合计为当年度每股分红。

          目标年度选取：取近两年中数据更完整的一年（按批次数判断）。

        Returns
        -------
        float | None : 股息率(%)，失败时返回 None
        """
        if close <= 0:
            return None

        df = self._safe_call(
            "dividend",
            ts_code=ts_code,
            fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,ex_date,pay_date"
        )
        if df is None or df.empty:
            return None

        # 纳入「预案」「股东大会通过」「实施」三种状态
        # 预案 = 董事会公告，极少被否决，纳入后可得到更完整的当年预期股息率
        valid_procs = {"预案", "股东大会通过", "实施"}
        df = df[df["div_proc"].isin(valid_procs)].copy()
        if df.empty:
            return None

        df["end_date"] = df["end_date"].astype(str)
        df["cash_div_tax"] = pd.to_numeric(df["cash_div_tax"], errors="coerce").fillna(0)
        df["end_year"] = df["end_date"].str[:4]

        # 去重：同一 end_date，优先保留状态更进阶的记录
        # 实施(0) > 股东大会通过(1) > 预案(2)，取优先级最高的那条
        proc_priority = {"实施": 0, "股东大会通过": 1, "预案": 2}
        df["proc_rank"] = df["div_proc"].map(proc_priority)
        df_dedup = (
            df.sort_values("proc_rank")
            .drop_duplicates(subset=["end_date"], keep="first")
        )

        # 按年度汇总
        year_sums = df_dedup.groupby("end_year")["cash_div_tax"].sum()

        # 选取目标年度：取近两年（当前年和上一年）中，分红批次最多的一年
        current_year = str(datetime.now().year)
        prev_year = str(datetime.now().year - 1)

        # 各年的批次数（未去重前，反映分红次数）
        year_counts = df_dedup.groupby("end_year")["end_date"].count()

        target_year = None
        for year in [current_year, prev_year]:
            if year in year_sums and year_sums[year] > 0:
                target_year = year
                break

        if target_year is None:
            return None

        total_dps = year_sums[target_year]
        if total_dps <= 0:
            return None

        div_yield = round(total_dps / close * 100, 4)
        return div_yield

    def get_dividend_history(self, ts_code: str) -> Optional[pd.DataFrame]:
        """获取分红历史（计算历史股息率区间）"""
        df = self._safe_call(
            "dividend",
            ts_code=ts_code,
            fields="ts_code,end_date,ann_date,div_proc,stk_div,cash_div,cash_div_tax,record_date,ex_date,pay_date"
        )
        return df

    # ── ETF 专用方法 ──────────────────────────────────────────

    def get_etf_daily(self, ts_code: str) -> Optional[Dict]:
        """获取 ETF 最新净值/行情"""
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        df = self._safe_call(
            "fund_daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close,pre_close,change,pct_chg"
        )
        if df is None or df.empty:
            return None

        df = df.sort_values("trade_date", ascending=False)
        row = df.iloc[0]
        return {
            "close": float(row.get("close", 0) or 0),
            "trade_date": str(row.get("trade_date", "")),
        }

    def get_etf_dividend(self, ts_code: str, close: float) -> Optional[float]:
        """
        从 fund_div 接口获取 ETF 分红数据，计算股息率

        逻辑：取最近一次年度分红的每份分红金额，除以当前净值
        fund_div 的关键字段：ex_date(除息日), div_cash(每份分红金额)
        """
        if close <= 0:
            return None

        df = self._safe_call(
            "fund_div",
            ts_code=ts_code,
        )
        if df is None or df.empty:
            return None

        df["div_cash"] = pd.to_numeric(df["div_cash"], errors="coerce").fillna(0)
        df = df[df["div_cash"] > 0]
        if df.empty:
            return None

        # 用 ex_date 提取年份（除息日最能代表分红归属年度）
        df["ex_date"] = df["ex_date"].astype(str)
        df = df[df["ex_date"].str.len() >= 4]  # 过滤空值

        # 去重：同一 ex_date 只保留一条（fund_div 可能对同一笔分红存多条记录）
        df = df.drop_duplicates(subset=["ex_date"], keep="first")

        df["year"] = df["ex_date"].str[:4]

        # 按年度汇总
        year_sums = df.groupby("year")["div_cash"].sum()

        # 取最近两年中有分红的一年
        current_year = str(datetime.now().year)
        prev_year = str(datetime.now().year - 1)

        target_year = None
        for year in [current_year, prev_year]:
            if year in year_sums and year_sums[year] > 0:
                target_year = year
                break

        if target_year is None:
            return None

        total_div = year_sums[target_year]
        if total_div <= 0:
            return None

        # ETF 的 close 是净值（元），div_cash 也是每份金额（元）
        div_yield = round(total_div / close * 100, 4)
        return div_yield

    def get_etf_weighted_metrics(self, ts_code: str) -> Optional[Dict]:
        """
        通过 ETF 持仓加权计算 PE/ROE

        取最新一期前 10 大持仓，按持仓占比加权 PE/ROE
        """
        # 1. 获取持仓
        df = self._safe_call(
            "fund_portfolio",
            ts_code=ts_code,
        )
        if df is None or df.empty:
            return None

        # 取最新一期
        latest = df["end_date"].max()
        df_latest = df[df["end_date"] == latest].copy()

        # 按持仓占比排序，取前 10
        df_latest = df_latest.sort_values("stk_mkv_ratio", ascending=False).head(10)
        if df_latest.empty:
            return None

        # 2. 逐个获取持仓 PE/ROE
        weighted_pe = 0.0
        weighted_roe = 0.0
        total_weight_pe = 0.0
        total_weight_roe = 0.0
        holdings_info = []

        for _, row in df_latest.iterrows():
            symbol = row["symbol"]
            weight = float(row.get("stk_mkv_ratio", 0) or 0)
            if weight <= 0:
                continue

            # 获取 PE
            try:
                time.sleep(0.15)
                bdf = self._safe_call(
                    "daily_basic",
                    ts_code=symbol,
                    fields="ts_code,trade_date,pe_ttm"
                )
                pe = 0.0
                if bdf is not None and not bdf.empty:
                    pe = float(bdf.iloc[0].get("pe_ttm", 0) or 0)
                if pe and pe > 0:
                    weighted_pe += pe * weight
                    total_weight_pe += weight
            except Exception:
                pass

            # 获取 ROE
            try:
                time.sleep(0.15)
                fdf = self._safe_call(
                    "fina_indicator",
                    ts_code=symbol,
                    fields="ts_code,ann_date,end_date,roe"
                )
                roe = 0.0
                if fdf is not None and not fdf.empty:
                    fdf = fdf.sort_values("ann_date", ascending=False)
                    # 优先年报
                    annual = fdf[fdf["end_date"].astype(str).str.endswith("1231")]
                    if not annual.empty:
                        roe = float(annual.iloc[0].get("roe", 0) or 0)
                    else:
                        roe_raw = float(fdf.iloc[0].get("roe", 0) or 0)
                        end_d = str(fdf.iloc[0].get("end_date", ""))
                        if end_d.endswith("0331"):
                            roe = roe_raw * 4
                        elif end_d.endswith("0630"):
                            roe = roe_raw * 2
                        elif end_d.endswith("0930"):
                            roe = roe_raw * 4 / 3
                        else:
                            roe = roe_raw
                if roe and roe > 0:
                    weighted_roe += roe * weight
                    total_weight_roe += weight
            except Exception:
                pass

            holdings_info.append(f"{symbol}({weight:.1f}%)")

        result = {}
        result["holdings_desc"] = f"前{len(holdings_info)}大持仓: " + ", ".join(holdings_info[:5])

        if total_weight_pe > 0:
            result["pe_ttm"] = round(weighted_pe / total_weight_pe, 1)
        else:
            result["pe_ttm"] = 0.0

        if total_weight_roe > 0:
            result["roe"] = round(weighted_roe / total_weight_roe, 1)
        else:
            result["roe"] = 0.0

        return result

    def get_repurchase(self, ts_code: str) -> Optional[pd.DataFrame]:
        """获取回购记录"""
        df = self._safe_call(
            "repurchase",
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit"
        )
        return df


# ──────────────────────────────────────────────────────────────
# 红利周期投资评估引擎
# ──────────────────────────────────────────────────────────────
class DividendCycleEvaluator:
    """
    基于红利周期投资体系的企业量化评估引擎
    
    评分维度：
    S1: 股息率评分 (0-30分) - 核心
    S2: 股债息差评分 (0-25分) - 核心锚
    S3: 等效分红率评分 (0-20分)
    S4: 护城河/确定性评分 (0-15分)
    S5: 成长性评分 (0-10分)
    总分: 0-100分
    """

    BOND_YIELD = THRESHOLDS["bond_yield_10y"]  # 10年国债收益率

    def __init__(self):
        self.fetcher = TushareDataFetcher()
        self.results: List[Dict] = []

    def _get_company_data(self, name: str, ts_code: str, category: str = "") -> Dict:
        """获取单只股票数据（tushare优先，fallback补充）"""
        print(f"  📊 获取 {name} ({ts_code}) 数据...", end=" ")

        # ── ETF 专用数据获取 ──
        if category == "ETF红利":
            return self._get_etf_data(name, ts_code)

        # Step 1: tushare 实时数据
        basic = self.fetcher.get_daily_basic(ts_code)
        fina = self.fetcher.get_financial_indicator(ts_code)

        # Step 2: 整合数据
        data = {}
        fallback = FALLBACK_DATA.get(ts_code, {})

        # 行情数据（股价/PE/PB/市值）
        if basic:
            data["close"] = basic["close"]
            data["pe_ttm"] = basic["pe_ttm"]
            data["pb"] = basic["pb"]
            data["total_mv"] = basic["total_mv"]
            data["trade_date"] = basic["trade_date"]
            data["source_basic"] = "tushare"
            print(f"✅ 行情", end=" ")
        else:
            data.update({k: fallback.get(k, 0) for k in ["close", "pe_ttm", "pb", "total_mv"]})
            data["trade_date"] = "fallback"
            data["source_basic"] = "fallback"
            print(f"⚠️fallback行情", end=" ")

        # 股息率：优先用 dividend 接口自算（含「股东大会通过」），保证当年完整分红
        close_price = data.get("close", 0)
        fallback_div = fallback.get("div_yield", 0)
        dv_ttm_raw = basic["div_yield"] if basic else 0.0
        real_div = self.fetcher.get_real_div_yield(ts_code, close_price) if close_price > 0 else None

        if real_div and real_div >= 0.3:
            # 如果自算值比 fallback 低 30% 以上，说明年报分红尚未公告，
            # 用 fallback 兜底（fallback 是手工核验的完整全年值）
            if fallback_div > 0 and real_div < fallback_div * 0.7:
                data["div_yield"] = fallback_div
                data["source_basic"] += f"(div=fallback兜底,自算{real_div:.2f}%<全年预期)"
                print(f"⚠️ 股息率自算{real_div:.2f}%不完整→用fallback{fallback_div:.2f}%", end=" ")
            else:
                data["div_yield"] = real_div
                data["source_basic"] += "(div=自算年化)"
                print(f"✅ 股息率={real_div:.2f}%", end=" ")
        else:
            if fallback_div > 0:
                data["div_yield"] = fallback_div
                data["source_basic"] += "(div=fallback)"
            elif dv_ttm_raw >= 0.3:
                data["div_yield"] = dv_ttm_raw
                data["source_basic"] += "(div=dv_ttm)"
            else:
                data["div_yield"] = 0.0
            print(f"⚠️ 股息率fallback={data['div_yield']:.2f}%", end=" ")

        # ── 自验证：透传三源原始值供 Validator 交叉核对 ──────────────
        data["_div_self_calc"] = round(real_div, 4) if real_div else None
        data["_div_dv_ttm"]    = round(dv_ttm_raw, 4) if dv_ttm_raw else None
        data["_div_fallback"]  = round(fallback_div, 4) if fallback_div else None
        data["_close_fallback"] = fallback.get("close", None)

        # 财务数据（ROE/增速）
        if fina:
            data["roe"] = fina["roe"]
            data["net_profit_growth"] = fina["net_profit_growth"]
            data["revenue_growth"] = fina["revenue_growth"]
            data["dps_latest"] = fina["dps_latest"]
            data["source_fina"] = "tushare"
            print(f"✅ 财务")
        else:
            data.update({k: fallback.get(k, 0) for k in ["roe", "net_profit_growth", "revenue_growth", "dps_latest"]})
            data["source_fina"] = "fallback"
            print(f"⚠️fallback财务")

        # 从 fallback 补充 tushare 没有的字段
        data["buyback_yield"] = fallback.get("buyback_yield", 0.0)
        data["payout_ratio"] = fallback.get("payout_ratio", 30.0)
        data["note"] = fallback.get("note", "")

        return data

    def _get_etf_data(self, name: str, ts_code: str) -> Dict:
        """ETF 专用数据获取（fund_daily + fund_div + fund_portfolio加权PE/ROE）"""
        fallback = FALLBACK_DATA.get(ts_code, {})
        data = {}

        # 1. 行情数据：fund_daily 获取净值
        etf_daily = self.fetcher.get_etf_daily(ts_code)
        if etf_daily:
            data["close"] = etf_daily["close"]
            data["trade_date"] = etf_daily["trade_date"]
            data["source_basic"] = "tushare(fund_daily)"
            print(f"✅ 净值", end=" ")
        else:
            data["close"] = fallback.get("close", 0)
            data["trade_date"] = "fallback"
            data["source_basic"] = "fallback"
            print(f"⚠️fallback净值", end=" ")

        # 2. 股息率：fund_div 接口
        close_price = data.get("close", 0)
        fallback_div = fallback.get("div_yield", 0)
        etf_div = self.fetcher.get_etf_dividend(ts_code, close_price) if close_price > 0 else None

        if etf_div and etf_div >= 0.3:
            data["div_yield"] = etf_div
            data["source_basic"] += "(div=fund_div)"
            print(f"✅ 股息率={etf_div:.2f}%", end=" ")
        else:
            if fallback_div > 0:
                data["div_yield"] = fallback_div
                data["source_basic"] += "(div=fallback)"
            else:
                data["div_yield"] = 0.0
            print(f"⚠️ 股息率fallback={data['div_yield']:.2f}%", end=" ")

        # ETF 透传字段
        data["_div_self_calc"] = round(etf_div, 4) if etf_div else None
        data["_div_dv_ttm"] = None  # ETF 无 dv_ttm
        data["_div_fallback"] = round(fallback_div, 4) if fallback_div else None
        data["_close_fallback"] = fallback.get("close", None)

        # 3. PE/ROE：持仓加权计算
        etf_metrics = self.fetcher.get_etf_weighted_metrics(ts_code)
        if etf_metrics:
            data["pe_ttm"] = etf_metrics.get("pe_ttm", 0)
            data["roe"] = etf_metrics.get("roe", 0)
            data["source_fina"] = "tushare(持仓加权)"
            data["note"] = etf_metrics.get("holdings_desc", "")
            print(f"✅ 持仓加权PE={data['pe_ttm']:.1f} ROE={data['roe']:.1f}%", end=" ")
        else:
            data["pe_ttm"] = fallback.get("pe_ttm", 0)
            data["roe"] = fallback.get("roe", 0)
            data["source_fina"] = "fallback"
            data["note"] = fallback.get("note", "")
            print(f"⚠️fallback PE/ROE", end=" ")

        # ETF 无意义的字段填充 0
        data["pb"] = 0.0
        data["net_profit_growth"] = 0.0
        data["revenue_growth"] = 0.0
        data["dps_latest"] = 0.0
        data["total_mv"] = fallback.get("total_mv", 0)
        data["buyback_yield"] = 0.0
        data["payout_ratio"] = 0.0

        # 补充 note
        if not data.get("note"):
            data["note"] = fallback.get("note", "")
        elif fallback.get("note"):
            data["note"] = data["note"] + " | " + fallback["note"]

        print()  # 换行
        return data

    # ──────────────── 评分函数 ────────────────────────────────

    def score_dividend_yield(self, div_yield: float, category: str) -> Tuple[float, str]:
        """S1: 股息率评分 (0-30分)"""
        if category == "弱周期红利":
            # 弱周期：5%+ 满分，4%良好，3.5%及格，<3%不及格
            if div_yield >= 5.5:
                return 30, f"★★★★★ {div_yield:.1f}% 历史极值区间"
            elif div_yield >= 5.0:
                return 27, f"★★★★☆ {div_yield:.1f}% 历史高位"
            elif div_yield >= 4.5:
                return 23, f"★★★★ {div_yield:.1f}% 较高区间"
            elif div_yield >= 4.0:
                return 18, f"★★★ {div_yield:.1f}% 合理买点"
            elif div_yield >= 3.5:
                return 12, f"★★☆ {div_yield:.1f}% 偏低，可观察"
            else:
                return 5, f"★ {div_yield:.1f}% 偏低，暂不买"
        elif category == "消费成长红利":
            # 消费类：现金分红3%+即可，主要看等效分红率
            if div_yield >= 5.0:
                return 28, f"★★★★★ {div_yield:.1f}% 消费类高股息"
            elif div_yield >= 4.0:
                return 24, f"★★★★ {div_yield:.1f}% 较高"
            elif div_yield >= 3.0:
                return 18, f"★★★ {div_yield:.1f}% 合理"
            elif div_yield >= 2.0:
                return 10, f"★★ {div_yield:.1f}% 偏低，需回购补足"
            else:
                return 4, f"★ {div_yield:.1f}% 过低"
        elif category == "ETF红利":
            # ETF 红利：类似弱周期，但标准略低
            if div_yield >= 5.0:
                return 28, f"★★★★★ {div_yield:.1f}% ETF高股息"
            elif div_yield >= 4.0:
                return 24, f"★★★★ {div_yield:.1f}% 较高"
            elif div_yield >= 3.0:
                return 18, f"★★★ {div_yield:.1f}% 合理"
            elif div_yield >= 2.0:
                return 10, f"★★ {div_yield:.1f}% 偏低"
            else:
                return 4, f"★ {div_yield:.1f}% 过低"
        else:  # 周期资源红利
            if div_yield >= 7.0:
                return 30, f"★★★★★ {div_yield:.1f}% 周期高峰/价格支撑"
            elif div_yield >= 5.0:
                return 24, f"★★★★ {div_yield:.1f}% 较高"
            elif div_yield >= 3.5:
                return 15, f"★★★ {div_yield:.1f}% 合理"
            else:
                return 6, f"★★ {div_yield:.1f}% 偏低"

    def score_bond_spread(self, div_yield: float) -> Tuple[float, str]:
        """S2: 股债息差评分 (0-25分)"""
        spread = div_yield - self.BOND_YIELD
        spread_bp = spread * 100  # 转为基点

        if spread_bp >= 300:
            return 25, f"息差 {spread_bp:.0f}BP ≫ 历史极高，极佳买点 🔥"
        elif spread_bp >= 230:
            return 22, f"息差 {spread_bp:.0f}BP 历史最高区间，大胆攒股"
        elif spread_bp >= 180:
            return 18, f"息差 {spread_bp:.0f}BP 历史较高区间，积极布局"
        elif spread_bp >= 130:
            return 14, f"息差 {spread_bp:.0f}BP 高于历史中枢(~100BP)，合理"
        elif spread_bp >= 80:
            return 9, f"息差 {spread_bp:.0f}BP 接近历史中枢，谨慎"
        elif spread_bp >= 30:
            return 4, f"息差 {spread_bp:.0f}BP 低于历史中枢，偏贵"
        else:
            return 0, f"息差 {spread_bp:.0f}BP 过低，放缓节奏"

    def score_effective_yield(self, div_yield: float, buyback_yield: float, category: str) -> Tuple[float, str]:
        """S3: 等效分红率评分 (0-20分)"""
        eff_yield = div_yield + buyback_yield

        if category == "消费成长红利":
            # 消费类标准更高：红利周期投资要求8%+
            if eff_yield >= 9.0:
                return 20, f"等效分红 {eff_yield:.1f}% 超越目标(8%)，优秀"
            elif eff_yield >= 8.0:
                return 17, f"等效分红 {eff_yield:.1f}% 达到目标阈值"
            elif eff_yield >= 6.0:
                return 12, f"等效分红 {eff_yield:.1f}% 低于目标，需提升"
            else:
                return 5, f"等效分红 {eff_yield:.1f}% 显著低于目标"
        elif category == "ETF红利":
            # ETF 红利：类似弱周期
            if eff_yield >= 7.0:
                return 20, f"等效分红 {eff_yield:.1f}% 极高"
            elif eff_yield >= 5.5:
                return 16, f"等效分红 {eff_yield:.1f}% 高"
            elif eff_yield >= 4.0:
                return 11, f"等效分红 {eff_yield:.1f}% 合理"
            else:
                return 5, f"等效分红 {eff_yield:.1f}% 偏低"
        else:
            # 其他类：单纯现金股息
            if eff_yield >= 7.0:
                return 20, f"等效分红 {eff_yield:.1f}% 极高"
            elif eff_yield >= 5.5:
                return 16, f"等效分红 {eff_yield:.1f}% 高"
            elif eff_yield >= 4.0:
                return 11, f"等效分红 {eff_yield:.1f}% 合理"
            else:
                return 5, f"等效分红 {eff_yield:.1f}% 偏低"

    def score_certainty(self, certainty: str, moat: str) -> Tuple[float, str]:
        """S4: 确定性/护城河评分 (0-15分)"""
        mapping = {
            "AA": (15, "系统性银行·国家信用背书"),
            "A": (13, "垄断资产·核心护城河"),
            "A-": (11, "行业领先·护城河较强"),
            "B+": (8, "竞争优势明显·周期属性"),
            "B": (5, "行业竞争·需关注周期底部"),
            "B-": (3, "护城河一般"),
        }
        score, reason = mapping.get(certainty, (5, "待评估"))
        return score, f"[{certainty}级] {reason} | {moat}"

    def score_growth(self, roe: float, net_profit_growth: float, category: str) -> Tuple[float, str]:
        """S5: 成长性评分 (0-10分)"""
        if category == "周期资源红利":
            # 周期类：不用高增速，稳定ROE更重要
            if roe >= 20:
                return 9, f"ROE {roe:.1f}% 周期类优秀"
            elif roe >= 15:
                return 7, f"ROE {roe:.1f}% 较好"
            elif roe >= 10:
                return 5, f"ROE {roe:.1f}% 合理"
            else:
                return 2, f"ROE {roe:.1f}% 偏低"
        elif category == "弱周期红利":
            # 弱周期：保守增速2%即可，稳定性更重要
            if roe >= 15 and net_profit_growth >= 0:
                return 9, f"ROE {roe:.1f}% 稳定增长 {net_profit_growth:.1f}%"
            elif roe >= 10:
                return 7, f"ROE {roe:.1f}% 稳定"
            elif roe >= 8:
                return 5, f"ROE {roe:.1f}% 可接受"
            else:
                return 3, f"ROE {roe:.1f}% 偏低"
        elif category == "ETF红利":
            # ETF 没有 ROE，给一个基于指数成长性的默认分数
            return 7, f"ETF 跟踪中证红利指数，成分股平均ROE约10-12%"
        else:  # 消费成长
            if roe >= 25 and net_profit_growth >= 10:
                return 10, f"ROE {roe:.1f}% 强劲增长 {net_profit_growth:.1f}%"
            elif roe >= 20:
                return 8, f"ROE {roe:.1f}% 优质成长"
            elif roe >= 15:
                return 6, f"ROE {roe:.1f}% 较好"
            else:
                return 3, f"ROE {roe:.1f}% 一般"

    def _get_verdict(self, total_score: float, div_yield: float, category: str) -> Tuple[str, str]:
        """红利周期投资体系操作建议"""
        eff_buy_threshold = THRESHOLDS.get(
            f"{'弱周期' if '弱' in category else ('消费' if '消费' in category else '周期')}_买入", 4.0
        )

        if total_score >= 80:
            return "🔥 大胆攒股", "处于黄金坑，建议底仓+逢跌加仓至满仓"
        elif total_score >= 65:
            return "✅ 积极布局", "性价比高，建3-4成底仓，网格化买入"
        elif total_score >= 50:
            return "👀 观察等待", f"有一定吸引力但未达极佳买点，等待股息率>{eff_buy_threshold+0.5:.1f}%"
        elif total_score >= 35:
            return "⏸️  暂缓", "估值偏高或不确定性大，放缓节奏"
        else:
            return "🚫 回避", "当前不符合红利周期投资体系买入标准"

    def evaluate_company(self, name: str, ts_code: str, meta: Dict) -> Dict:
        """评估单家企业"""
        category = meta["category"]
        certainty = meta["certainty"]
        moat = meta["moat"]
        sector = meta.get("sector", "")

        # 获取数据
        data = self._get_company_data(name, ts_code, category=category)

        # 评分
        s1, r1 = self.score_dividend_yield(data["div_yield"], category)
        s2, r2 = self.score_bond_spread(data["div_yield"])
        s3, r3 = self.score_effective_yield(data["div_yield"], data["buyback_yield"], category)
        s4, r4 = self.score_certainty(certainty, moat)
        s5, r5 = self.score_growth(data["roe"], data["net_profit_growth"], category)

        total = s1 + s2 + s3 + s4 + s5

        # ── 新增：分红奶牛信号（夕阳产业→分红奶牛） ──
        dividend_cow = self._calc_dividend_cow_signal(
            sector=sector,
            payout_ratio=data.get("payout_ratio", 0),
            net_profit_growth=data.get("net_profit_growth", 0),
            div_yield=data["div_yield"],
        )

        # 分红奶牛加分（最多+5分，总封顶100）
        cow_bonus = dividend_cow.get("bonus", 0)
        total = min(100, total + cow_bonus)

        verdict, advice = self._get_verdict(total, data["div_yield"], category)

        # 分红复投10年预测
        drip_10y = self._drip_projection(data["div_yield"], 10)

        # ── 新增：阶梯攒股价格表 ──
        ladder = self._calc_ladder_prices(data["div_yield"], data["close"], sector)

        # ── 新增：预期股息率 ──
        forward_div = self._calc_forward_div_yield(
            data["div_yield"], data["close"], data["net_profit_growth"], data.get("dps_latest", 0)
        )

        # ── 新增：网格交易区间 ──
        grid = self._calc_grid_range(data["div_yield"], sector)

        return {
            "name": name,
            "ts_code": ts_code,
            "category": category,
            "sector": sector,
            "certainty": certainty,
            "moat": moat,
            "comment": meta.get("comment", ""),
            # 数据
            "close": data["close"],
            "pe_ttm": data["pe_ttm"],
            "pb": data["pb"],
            "div_yield": data["div_yield"],
            "eff_yield": data["div_yield"] + data["buyback_yield"],
            "buyback_yield": data["buyback_yield"],
            "roe": data["roe"],
            "net_profit_growth": data["net_profit_growth"],
            "bond_spread_bp": (data["div_yield"] - self.BOND_YIELD) * 100,
            "total_mv": data["total_mv"],
            "payout_ratio": data["payout_ratio"],
            "note": data["note"],
            "source": f"{data['source_basic']}/{data['source_fina']}",
            # 评分
            "s1_div": s1, "r1": r1,
            "s2_spread": s2, "r2": r2,
            "s3_eff": s3, "r3": r3,
            "s4_certainty": s4, "r4": r4,
            "s5_growth": s5, "r5": r5,
            "total_score": total,
            "verdict": verdict,
            "advice": advice,
            "drip_10y": drip_10y,
            # ── 新增字段 ──
            "ladder": ladder,
            "forward_div_yield": forward_div["forward_div_yield"],
            "forward_dps": forward_div["forward_dps"],
            "grid": grid,
            # ── 新增字段 ──
            "dividend_cow": dividend_cow,
            # ── 自验证透传字段（供 Validator 交叉核对，不用于评分）──
            "_div_self_calc":  data.get("_div_self_calc"),
            "_div_dv_ttm":     data.get("_div_dv_ttm"),
            "_div_fallback":   data.get("_div_fallback"),
            "_close_fallback": data.get("_close_fallback"),
        }

    def _calc_ladder_prices(self, div_yield: float, close: float, sector: str) -> Dict:
        """
        阶梯攒股价格表
        
        基于行业股息率锚，反推不同档位的目标价格：
          观察价 → 开始关注
          买入价 → 建仓底仓
          加仓价 → 加大仓位
          满仓价 → 极佳买点，全力攒股
        
        公式：目标价 = DPS / (目标股息率阈值/100)
        其中 DPS = 当前股价 × 当前股息率/100
        """
        thresholds = SECTOR_THRESHOLDS.get(sector, {})
        if not thresholds or close <= 0 or div_yield <= 0:
            return {"watch": 0, "buy": 0, "add": 0, "full": 0, "sector_anchor": ""}

        dps = close * div_yield / 100  # 每股分红

        watch_price = round(dps / (thresholds["watch"] / 100), 2) if thresholds["watch"] > 0 else 0
        buy_price   = round(dps / (thresholds["buy"] / 100), 2) if thresholds["buy"] > 0 else 0
        add_price   = round(dps / (thresholds["add"] / 100), 2) if thresholds["add"] > 0 else 0
        full_price  = round(dps / (thresholds["full"] / 100), 2) if thresholds["full"] > 0 else 0

        return {
            "watch": watch_price,
            "buy": buy_price,
            "add": add_price,
            "full": full_price,
            "sector_anchor": thresholds.get("comment", ""),
        }

    def _calc_forward_div_yield(
        self, div_yield: float, close: float, 
        net_profit_growth: float, dps_latest: float
    ) -> Dict:
        """
        预期股息率（前瞻性指标）
        
        基于净利润增速推算下一年 DPS，再除以当前股价：
          forward_dps = latest_dps × (1 + growth/100)
          forward_div_yield = forward_dps / close × 100
        
        当增速为负时，保守取 min(dps_latest, dps_latest*(1+growth/100))
        """
        if close <= 0:
            return {"forward_div_yield": 0.0, "forward_dps": 0.0}

        # 用 dps_latest 作为基准（若有），否则从股息率反推
        if dps_latest and dps_latest > 0:
            base_dps = dps_latest
        else:
            base_dps = close * div_yield / 100

        # 增速为正：用增速推算；为负：保守取值
        if net_profit_growth > 0:
            forward_dps = base_dps * (1 + net_profit_growth / 100)
        else:
            forward_dps = min(base_dps, base_dps * (1 + net_profit_growth / 100))

        forward_div_yield = round(forward_dps / close * 100, 2) if forward_dps > 0 else 0.0

        return {
            "forward_div_yield": forward_div_yield,
            "forward_dps": round(forward_dps, 4),
        }

    def _calc_grid_range(self, div_yield: float, sector: str) -> Dict:
        """
        网格交易区间（基于股息率）
        
        将当前股息率与行业锚对比，给出操作区间：
          低吸区 → 股息率 ≥ 买入阈值，适合加仓
          持有区 → 股息率在减仓线和买入阈值之间，持股收息
          减仓区 → 股息率 < 减仓线，估值偏高可减仓
        """
        thresholds = SECTOR_THRESHOLDS.get(sector, {})
        if not thresholds:
            return {"zone": "未知", "buy_line": 0, "reduce_line": 0, "desc": "无行业锚数据"}

        buy_line = thresholds["buy"]
        reduce_line = thresholds["reduce"]

        if div_yield >= buy_line:
            zone = "低吸"
            desc = f"股息率{div_yield:.1f}% ≥ 买入线{buy_line:.1f}%，适合加仓"
        elif div_yield >= reduce_line:
            zone = "持有"
            desc = f"股息率{reduce_line:.1f}%~{buy_line:.1f}%，持股收息"
        else:
            zone = "减仓"
            desc = f"股息率{div_yield:.1f}% < 减仓线{reduce_line:.1f}%，估值偏高"

        return {
            "zone": zone,
            "buy_line": buy_line,
            "reduce_line": reduce_line,
            "desc": desc,
        }

    def _calc_dividend_cow_signal(
        self, sector: str, payout_ratio: float,
        net_profit_growth: float, div_yield: float
    ) -> Dict:
        """
        分红奶牛信号（夕阳产业→分红奶牛）

        逻辑：行业进入成熟/衰退期后：
          1. 资本开支下降 → 自由现金流充裕
          2. 增长机会减少 → 分红率提升
          3. 典型案例：煤炭行业资本开支结束→分红率70%+

        信号强度：
          - 强信号：夕阳转奶牛 + 分红率≥60% + 增速≤0 + 高股息率
          - 中信号：成熟行业 + 分红率≥50% + 增速≤5%
          - 弱信号：成熟行业 + 分红率≥40%

        加分规则（最多+5分）：
          - 强信号：+5
          - 中信号：+3
          - 弱信号：+1
        """
        lifecycle = SECTOR_LIFECYCLE.get(sector, {})
        stage = lifecycle.get("stage", "")
        dividend_potential = lifecycle.get("dividend_potential", "低")

        signal = "无"
        bonus = 0
        reason = ""

        # 强信号：夕阳转奶牛 + 高分红率 + 低/负增速 + 高股息
        if stage == "夕阳转奶牛":
            if payout_ratio >= 60 and div_yield >= 5.0:
                signal = "强"
                bonus = 5
                reason = f"夕阳→奶牛：资本开支结束，分红率{payout_ratio:.0f}%，股息率{div_yield:.1f}%"
            elif payout_ratio >= 50:
                signal = "中"
                bonus = 3
                reason = f"夕阳→奶牛倾向：分红率{payout_ratio:.0f}%，增速{net_profit_growth:.1f}%"
            else:
                signal = "弱"
                bonus = 1
                reason = f"夕阳行业，分红率{payout_ratio:.0f}%，奶牛潜力待释放"

        # 成熟行业 + 分红率高 → 分红奶牛倾向
        elif stage == "成熟" and dividend_potential in ("高", "极高"):
            if payout_ratio >= 50 and net_profit_growth <= 5:
                signal = "中"
                bonus = 3
                reason = f"成熟行业奶牛：分红率{payout_ratio:.0f}%，增速{net_profit_growth:.1f}%"
            elif payout_ratio >= 40:
                signal = "弱"
                bonus = 1
                reason = f"成熟行业：分红率{payout_ratio:.0f}%，奶牛潜力中等"

        return {
            "signal": signal,
            "bonus": bonus,
            "reason": reason,
            "stage": stage,
            "dividend_potential": dividend_potential,
        }

    def _drip_projection(self, initial_yield: float, years: int) -> Dict:
        """
        分红复投复利预测（假设股价不涨）
        初始投入100万，计算持股数量和分红增长
        """
        invest = 100.0  # 万元
        # 简化：假设股息率固定，每年分红复投（买同等股息率的股权）
        # 成本股息率提升 = (1 + yield/100)^n * yield
        cost_yield = initial_yield
        annual_div = invest * cost_yield / 100
        results = {}
        cumulative_cost_yield = initial_yield

        for y in range(1, years + 1):
            cumulative_cost_yield = cumulative_cost_yield * (1 + initial_yield / 100)
            annual_div_y = invest * cumulative_cost_yield / 100
            results[f"第{y}年"] = {
                "成本股息率": round(cumulative_cost_yield, 2),
                "年分红(万)": round(annual_div_y, 2),
            }

        return {
            "第1年分红": round(invest * initial_yield / 100, 2),
            "第5年成本股息率": round(results.get("第5年", {}).get("成本股息率", initial_yield), 2),
            "第10年成本股息率": round(results.get("第10年", {}).get("成本股息率", initial_yield), 2),
            "第10年年分红": round(results.get("第10年", {}).get("年分红(万)", 0), 2),
        }

    def evaluate_all(self) -> List[Dict]:
        """评估所有企业"""
        print("\n" + "=" * 65)
        print("  🌊 红利周期投资 — 企业量化评估系统")
        print(f"  📅 评估日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  📡 10年国债收益率: {self.BOND_YIELD}%")
        print("=" * 65 + "\n")

        for sector, companies in COMPANIES.items():
            print(f"━━ 【{sector}】 ━━")
            for name, meta in companies.items():
                meta["sector"] = sector  # 注入行业细分
                result = self.evaluate_company(name, meta["ts_code"], meta)
                self.results.append(result)
                print(f"    → 总分: {result['total_score']:.0f}/100 | {result['verdict']}")

        return self.results

    def evaluate_all_to_json(self, bond_yield_override: Optional[float] = None) -> Dict:
        """
        评估所有企业并返回结构化 JSON（供 weekly_harness 使用）

        Parameters
        ----------
        bond_yield_override : float, optional
            覆盖默认的10年国债收益率（供 Planner 传入实时数据）

        Returns
        -------
        dict : {
            "week": str,          # ISO 周号，如 "2026-W21"
            "timestamp": str,     # ISO 时间戳
            "bond_yield_10y": float,
            "scores": { ts_code: {...} },
            "summary": { "strong_buy": [...], "buy": [...], ... }
        }
        """
        if bond_yield_override is not None:
            self.BOND_YIELD = bond_yield_override

        if not self.results:
            self.evaluate_all()

        now = datetime.now()
        iso_week = now.strftime("%G-W%V")  # e.g. "2026-W21"

        scores = {}
        for r in self.results:
            scores[r["ts_code"]] = {
                "name": r["name"],
                "sector": r.get("sector", next(
                    (s for s, cos in COMPANIES.items() if r["name"] in cos), "未知"
                )),
                "category": r["category"],
                "certainty": r["certainty"],
                "total_score": r["total_score"],
                "verdict": r["verdict"],
                "close": r["close"],
                "pe_ttm": r["pe_ttm"],
                "pb": r["pb"],
                "div_yield": r["div_yield"],
                "buyback_yield": r["buyback_yield"],
                "eff_yield": r["eff_yield"],
                "bond_spread_bp": r["bond_spread_bp"],
                "roe": r["roe"],
                "net_profit_growth": r["net_profit_growth"],
                "total_mv": r["total_mv"],
                "source": r["source"],
                # 分项评分
                "s1_div": r["s1_div"],
                "s2_spread": r["s2_spread"],
                "s3_eff": r["s3_eff"],
                "s4_certainty": r["s4_certainty"],
                "s5_growth": r["s5_growth"],
                # 评分理由
                "r1": r["r1"], "r2": r["r2"], "r3": r["r3"],
                "r4": r["r4"], "r5": r["r5"],
                "advice": r["advice"],
                "drip_10y": r["drip_10y"],
                "note": r["note"],
                # ── 新增：阶梯攒股 / 预期股息率 / 网格交易 ──
                "ladder": r.get("ladder", {}),
                "forward_div_yield": r.get("forward_div_yield", 0.0),
                "forward_dps": r.get("forward_dps", 0.0),
                "grid": r.get("grid", {}),
                # ── 自验证字段（透传给 Validator）──
                "_div_self_calc":  r.get("_div_self_calc"),
                "_div_dv_ttm":     r.get("_div_dv_ttm"),
                "_div_fallback":   r.get("_div_fallback"),
                "_close_fallback": r.get("_close_fallback"),
            }

        # 按操作建议分组
        summary: Dict[str, List] = {
            "strong_buy": [],   # 80+
            "buy": [],          # 65-79
            "watch": [],        # 50-64
            "hold": [],         # 35-49
            "avoid": [],        # <35
        }
        for ts_code, s in scores.items():
            sc = s["total_score"]
            if sc >= 80:
                summary["strong_buy"].append(ts_code)
            elif sc >= 65:
                summary["buy"].append(ts_code)
            elif sc >= 50:
                summary["watch"].append(ts_code)
            elif sc >= 35:
                summary["hold"].append(ts_code)
            else:
                summary["avoid"].append(ts_code)

        return {
            "week": iso_week,
            "timestamp": now.isoformat(timespec="seconds"),
            "bond_yield_10y": self.BOND_YIELD,
            "scores": scores,
            "summary": summary,
        }


# ──────────────────────────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────────────────────────
class ReportGenerator:
    def __init__(self, results: List[Dict]):
        self.results = sorted(results, key=lambda x: x["total_score"], reverse=True)
        self.output_dir = PROJECT_ROOT / "data"
        self.output_dir.mkdir(exist_ok=True)

    def _category_emoji(self, category: str) -> str:
        return {"弱周期红利": "💧", "消费成长红利": "📦", "周期资源红利": "⛏️", "ETF红利": "📈"}.get(category, "📊")

    def generate_markdown(self) -> str:
        """生成 Markdown 报告"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            "# 🌊 红利周期投资 — 企业量化评估报告",
            f"\n> **评估日期**: {now}  \n> **数据来源**: tushare pro + 网络补充  \n> **评估框架**: 三层股息估值 + 护城河 + 成长性",
            f"\n> **10年国债收益率**: {THRESHOLDS['bond_yield_10y']}% (2026年5月)\n",
            "\n---\n",
        ]

        # 排行榜
        lines.append("## 📊 综合排行榜（总分 0-100）\n")
        lines.append("| 排名 | 企业 | 类别 | 股息率 | 息差(BP) | 等效分红 | ROE | 总分 | 操作建议 |")
        lines.append("|------|------|------|--------|---------|---------|-----|------|---------|")
        for i, r in enumerate(self.results, 1):
            emoji = self._category_emoji(r["category"])
            lines.append(
                f"| {i} | **{r['name']}** | {emoji}{r['category'][:4]} | "
                f"{r['div_yield']:.1f}% | {r['bond_spread_bp']:.0f} | "
                f"{r['eff_yield']:.1f}% | {r['roe']:.1f}% | "
                f"**{r['total_score']:.0f}** | {r['verdict']} |"
            )

        # 分类详情
        categories_order = ["弱周期红利", "消费成长红利", "周期资源红利", "ETF红利"]
        cat_labels = {"弱周期红利": "第一类：弱周期红利（最优先）",
                      "消费成长红利": "第二类：消费/成长红利（中等难度）",
                      "周期资源红利": "第三类：周期资源红利（最高难度）",
                      "ETF红利": "第四类：ETF红利（分散投资）"}

        for cat in categories_order:
            cat_results = [r for r in self.results if r["category"] == cat]
            if not cat_results:
                continue

            emoji = self._category_emoji(cat)
            lines.append(f"\n---\n\n## {emoji} {cat_labels[cat]}\n")

            for r in cat_results:
                lines.append(f"### {r['name']} ({r['ts_code']})\n")
                lines.append(f"> 💬 **点评**: {r['comment']}\n")
                lines.append(f"**护城河**: {r['moat']}  **确定性等级**: {r['certainty']}\n")
                lines.append(f"> 📝 数据备注: {r['note']}\n")

                # 估值数据
                lines.append("**核心估值指标**\n")
                lines.append("| 指标 | 数值 | 说明 |")
                lines.append("|------|------|------|")
                lines.append(f"| 当前股价 | {r['close']:.2f}元 | 来源: {r['source']} |")
                lines.append(f"| TTM市盈率 | {r['pe_ttm']:.1f}x | |")
                lines.append(f"| 市净率 | {r['pb']:.2f}x | |")
                lines.append(f"| 现金股息率 | **{r['div_yield']:.2f}%** | 核心指标 |")
                lines.append(f"| 回购收益率 | {r['buyback_yield']:.1f}% | 等效分红组成 |")
                lines.append(f"| **等效分红率** | **{r['eff_yield']:.2f}%** | 含回购的真实股东回报 |")
                lines.append(f"| 股债息差 | **{r['bond_spread_bp']:.0f}BP** | 超10Y国债({THRESHOLDS['bond_yield_10y']}%) |")
                lines.append(f"| ROE | {r['roe']:.1f}% | |")
                lines.append(f"| 净利润增速 | {r['net_profit_growth']:.1f}% | TTM |")
                lines.append(f"| 总市值 | {r['total_mv']:.0f}亿元 | |")
                lines.append("")

                # 评分明细
                lines.append("**红利周期三层评估评分**\n")
                lines.append("| 评估维度 | 得分 | 满分 | 评价 |")
                lines.append("|----------|------|------|------|")
                lines.append(f"| S1 股息率 | {r['s1_div']} | 30 | {r['r1']} |")
                lines.append(f"| S2 股债息差 | {r['s2_spread']} | 25 | {r['r2']} |")
                lines.append(f"| S3 等效分红率 | {r['s3_eff']} | 20 | {r['r3']} |")
                lines.append(f"| S4 护城河确定性 | {r['s4_certainty']} | 15 | {r['r4']} |")
                lines.append(f"| S5 成长性ROE | {r['s5_growth']} | 10 | {r['r5']} |")
                lines.append(f"| **合计** | **{r['total_score']:.0f}** | **100** | {r['verdict']} |")
                lines.append("")

                # 分红复投预测
                drip = r["drip_10y"]
                lines.append("**分红复投测算（100万初始投入，假设股价不涨）**\n")
                lines.append(f"- 第1年分红: **{drip['第1年分红']:.1f}万**")
                lines.append(f"- 第5年成本股息率: **{drip['第5年成本股息率']:.1f}%**")
                lines.append(f"- 第10年成本股息率: **{drip['第10年成本股息率']:.1f}%**")
                lines.append(f"- 第10年年分红: **{drip['第10年年分红']:.1f}万**")
                lines.append("")

                # 操作建议
                lines.append(f"**操作建议**: {r['verdict']}  \n**策略**: {r['advice']}\n")

        # 总结
        top3 = self.results[:3]
        lines.append("\n---\n\n## 🏆 当前最优配置建议\n")
        lines.append("根据红利周期投资体系评分，当前最值得攒股的标的：\n")
        for i, r in enumerate(top3, 1):
            emoji = self._category_emoji(r["category"])
            lines.append(f"{i}. **{r['name']}** — {emoji} {r['verdict']}")
            lines.append(f"   - 股息率 {r['div_yield']:.1f}% | 息差 {r['bond_spread_bp']:.0f}BP | 总分 {r['total_score']:.0f}/100")
            lines.append(f"   - {r['advice']}\n")

        lines.append("\n> ⚠️ **免责声明**: 本报告仅供学习研究，不构成投资建议。投资有风险，决策需谨慎。\n")
        lines.append(f"\n---\n*生成时间: {now} | 框架: 红利周期投资量化实现*")

        report = "\n".join(lines)
        report_path = self.output_dir / "dividend_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n✅ Markdown 报告已保存: {report_path}")
        return report

    def generate_chart(self):
        """生成可视化图表"""
        df = pd.DataFrame(self.results)

        fig = plt.figure(figsize=(20, 22))
        fig.patch.set_facecolor("#0d1117")
        gs = fig.add_gridspec(3, 3, hspace=0.45, wspace=0.35)

        colors_cat = {
            "弱周期红利": "#4ecdc4",
            "消费成长红利": "#f9ca24",
            "周期资源红利": "#e17055",
            "ETF红利": "#a29bfe",  # 紫色
        }
        bar_colors = [colors_cat.get(c, "#gray") for c in df["category"]]

        # ── 图1: 综合总分排行 ──────────────────────────────────
        ax1 = fig.add_subplot(gs[0, :])
        ax1.set_facecolor("#161b22")
        names = df["name"].tolist()
        scores = df["total_score"].tolist()
        bars = ax1.barh(names, scores, color=bar_colors, alpha=0.85, height=0.6)
        ax1.set_xlim(0, 110)
        ax1.axvline(80, color="#ff6b6b", linestyle="--", alpha=0.6, label="极佳买点(80+)")
        ax1.axvline(65, color="#ffd93d", linestyle="--", alpha=0.6, label="积极布局(65+)")
        ax1.axvline(50, color="#6bcb77", linestyle="--", alpha=0.4, label="观察等待(50+)")

        for bar, score in zip(bars, scores):
            ax1.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                     f"{score:.0f}分", va="center", ha="left", color="white", fontsize=9)

        ax1.set_title("红利周期投资综合评分排行（满分100）", color="white", pad=12, fontsize=13, fontweight="bold")
        ax1.tick_params(colors="white", labelsize=9)
        ax1.spines[:].set_color("#30363d")
        legend = ax1.legend(loc="lower right", facecolor="#161b22", edgecolor="#30363d",
                            labelcolor="white", fontsize=8)
        # 添加图例区分颜色
        patches = [mpatches.Patch(color=v, label=k, alpha=0.85) for k, v in colors_cat.items()]
        ax1.legend(handles=patches, loc="upper right", facecolor="#161b22",
                   edgecolor="#30363d", labelcolor="white", fontsize=8)

        # ── 图2: 股息率 vs 市场（散点图）────────────────────────
        ax2 = fig.add_subplot(gs[1, 0])
        ax2.set_facecolor("#161b22")
        for cat, grp in df.groupby("category"):
            ax2.scatter(grp["pe_ttm"], grp["div_yield"],
                        c=colors_cat[cat], s=120, alpha=0.85, label=cat, zorder=3)
            for _, row in grp.iterrows():
                ax2.annotate(row["name"][:2], (row["pe_ttm"], row["div_yield"]),
                             fontsize=7, color="white", xytext=(3, 3),
                             textcoords="offset points")

        ax2.axhline(THRESHOLDS["bond_yield_10y"], color="#aaa", linestyle=":", alpha=0.7,
                    label=f"国债收益率 {THRESHOLDS['bond_yield_10y']}%")
        ax2.axhline(5.0, color="#ffd93d", linestyle="--", alpha=0.5, label="5% 优质红利线")
        ax2.set_xlabel("PE(TTM)", color="#8b949e", fontsize=9)
        ax2.set_ylabel("股息率 (%)", color="#8b949e", fontsize=9)
        ax2.set_title("估值 vs 股息率", color="white", fontsize=10, pad=8)
        ax2.tick_params(colors="white", labelsize=8)
        ax2.spines[:].set_color("#30363d")
        ax2.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图3: 股债息差对比 ─────────────────────────────────
        ax3 = fig.add_subplot(gs[1, 1])
        ax3.set_facecolor("#161b22")
        spread_colors = ["#ff6b6b" if x >= 230 else "#ffd93d" if x >= 130 else "#6bcb77"
                         for x in df["bond_spread_bp"]]
        ax3.barh(df["name"], df["bond_spread_bp"], color=spread_colors, alpha=0.85, height=0.6)
        ax3.axvline(100, color="#aaa", linestyle="--", alpha=0.6, label="历史中枢~100BP")
        ax3.axvline(230, color="#ff6b6b", linestyle="--", alpha=0.6, label="极佳买点>230BP")
        ax3.set_title("股债息差 (BP)", color="white", fontsize=10, pad=8)
        ax3.tick_params(colors="white", labelsize=8)
        ax3.spines[:].set_color("#30363d")
        ax3.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图4: 等效分红率（含回购） ─────────────────────────
        ax4 = fig.add_subplot(gs[1, 2])
        ax4.set_facecolor("#161b22")
        x = range(len(df))
        ax4.bar(x, df["div_yield"], color="#4ecdc4", alpha=0.85, label="现金股息率")
        ax4.bar(x, df["buyback_yield"], bottom=df["div_yield"], color="#f9ca24",
                alpha=0.85, label="回购收益率")
        ax4.axhline(5.0, color="#ff6b6b", linestyle="--", alpha=0.7, label="5%目标线")
        ax4.axhline(8.0, color="#e17055", linestyle="--", alpha=0.7, label="8%优秀线")
        ax4.set_xticks(list(x))
        ax4.set_xticklabels([n[:2] for n in df["name"]], color="white", fontsize=8)
        ax4.set_title("等效分红率 = 现金股息 + 回购", color="white", fontsize=10, pad=8)
        ax4.tick_params(colors="white", labelsize=8)
        ax4.spines[:].set_color("#30363d")
        ax4.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图5: 分红复投10年增长曲线（TOP5）────────────────────
        ax5 = fig.add_subplot(gs[2, :2])
        ax5.set_facecolor("#161b22")
        top5 = df.head(5)
        years = list(range(1, 11))

        for _, row in top5.iterrows():
            init_yield = row["div_yield"] / 100
            cost_yields = []
            cy = row["div_yield"]
            for y in years:
                cy = cy * (1 + row["div_yield"] / 100)
                cost_yields.append(cy)
            ax5.plot(years, cost_yields,
                     marker="o", markersize=4, label=row["name"],
                     color=colors_cat.get(row["category"], "#aaa"), linewidth=2, alpha=0.85)

        ax5.axhline(8.0, color="#ff6b6b", linestyle="--", alpha=0.6, label="8%复利目标")
        ax5.set_xlabel("持有年数", color="#8b949e", fontsize=9)
        ax5.set_ylabel("成本股息率 (%)", color="#8b949e", fontsize=9)
        ax5.set_title("分红复投复利曲线（假设股价不涨，100万投入）", color="white", fontsize=10, pad=8)
        ax5.tick_params(colors="white", labelsize=8)
        ax5.spines[:].set_color("#30363d")
        ax5.legend(fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

        # ── 图6: 雷达图（TOP1企业）─────────────────────────────
        ax6 = fig.add_subplot(gs[2, 2], projection="polar")
        ax6.set_facecolor("#161b22")
        top1 = self.results[0]
        categories_radar = ["股息率\n(×30)", "股债息差\n(×25)", "等效分红\n(×20)", "确定性\n(×15)", "成长性\n(×10)"]
        values = [
            top1["s1_div"] / 30 * 100,
            top1["s2_spread"] / 25 * 100,
            top1["s3_eff"] / 20 * 100,
            top1["s4_certainty"] / 15 * 100,
            top1["s5_growth"] / 10 * 100,
        ]
        values += values[:1]
        N = len(categories_radar)
        angles = [n / float(N) * 2 * np.pi for n in range(N)]
        angles += angles[:1]

        ax6.plot(angles, values, color=colors_cat.get(top1["category"], "#4ecdc4"),
                 linewidth=2, linestyle="solid")
        ax6.fill(angles, values, alpha=0.25, color=colors_cat.get(top1["category"], "#4ecdc4"))
        ax6.set_xticks(angles[:-1])
        ax6.set_xticklabels(categories_radar, size=8, color="white")
        ax6.set_ylim(0, 100)
        ax6.tick_params(colors="white", labelsize=7)
        ax6.set_title(f"TOP1: {top1['name']}\n总分{top1['total_score']:.0f}/100",
                      color="white", fontsize=10, pad=20)
        ax6.set_facecolor("#161b22")
        ax6.spines["polar"].set_color("#30363d")
        ax6.yaxis.set_tick_params(labelcolor="#8b949e")

        # 总标题
        fig.suptitle("🌊 红利周期投资 — 企业量化评估报告",
                     fontsize=15, color="white", y=0.98, fontweight="bold")

        chart_path = self.output_dir / "dividend_chart.png"
        plt.savefig(chart_path, dpi=150, bbox_inches="tight",
                    facecolor="#0d1117", edgecolor="none")
        plt.close()
        print(f"✅ 可视化图表已保存: {chart_path}")

    def print_console_report(self):
        """控制台彩色输出报告"""
        print("\n" + "=" * 65)
        print("  🏆 红利周期投资 — 企业评估综合排行榜")
        print("=" * 65)

        cat_order = ["弱周期红利", "消费成长红利", "周期资源红利", "ETF红利"]
        cat_labels = {
            "弱周期红利": "💧 第一类：弱周期红利",
            "消费成长红利": "📦 第二类：消费成长红利",
            "周期资源红利": "⛏️  第三类：周期资源红利",
            "ETF红利": "📈 第四类：ETF红利",
        }

        for cat in cat_order:
            cat_res = sorted(
                [r for r in self.results if r["category"] == cat],
                key=lambda x: x["total_score"], reverse=True
            )
            if not cat_res:
                continue
            print(f"\n{cat_labels[cat]}")
            print("-" * 60)
            print(f"{'企业':<8} {'股息率':>6} {'息差BP':>7} {'等效分红':>8} {'ROE':>6} {'总分':>6} {'建议'}")
            print("-" * 60)
            for r in cat_res:
                score_bar = "█" * int(r["total_score"] / 10) + "░" * (10 - int(r["total_score"] / 10))
                print(
                    f"{r['name']:<8} "
                    f"{r['div_yield']:>5.1f}% "
                    f"{r['bond_spread_bp']:>6.0f} "
                    f"{r['eff_yield']:>7.1f}% "
                    f"{r['roe']:>5.1f}% "
                    f"{r['total_score']:>5.0f} "
                    f"{r['verdict']}"
                )
                print(f"         [{score_bar}] {r['advice'][:45]}")

        print("\n" + "=" * 65)
        print("  🔝 当前最值得攒股的 TOP3 标的：")
        print("=" * 65)
        for i, r in enumerate(self.results[:3], 1):
            print(f"  {i}. {r['name']} | 总分 {r['total_score']:.0f} | {r['verdict']}")
            drip = r["drip_10y"]
            print(f"     💰 100万投入: 第1年分红{drip['第1年分红']}万 → 第10年{drip['第10年年分红']}万")
            print(f"     📋 {r['comment']}")
        print()


# ──────────────────────────────────────────────────────────────
# 主程序入口
# ──────────────────────────────────────────────────────────────
def main():
    evaluator = DividendCycleEvaluator()
    results = evaluator.evaluate_all()

    reporter = ReportGenerator(results)
    reporter.print_console_report()
    reporter.generate_markdown()
    reporter.generate_chart()

    print("\n✅ 评估完成！")
    print(f"   📄 报告: data/dividend_report.md")
    print(f"   📊 图表: data/dividend_chart.png")


if __name__ == "__main__":
    main()
