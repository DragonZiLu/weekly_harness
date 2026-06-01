"""
中证500高股息股票池
对齐基础策略的 sector / category 体系，用于回测对比

数据基准：2026-05-29 收盘，筛选条件：
  - 中证500成分股（000905.SH，截至2025-12-31）
  - dv_ttm >= 3.0%
  - pe_ttm > 0 且 < 100（剔除亏损/极端高PE）

sector → SECTOR_THRESHOLDS 映射（与 dividend_evaluator.py 一致）
category → 策略类别上限（与 strategy.py 一致）
certainty → S4确定性评分（中证500标的信息少，默认 B+）
"""

# ──────────────────────────────────────────────────────────────────────────────
# industry(tushare) → (sector, category) 映射表
# ──────────────────────────────────────────────────────────────────────────────
INDUSTRY_TO_SECTOR_CATEGORY = {
    # 弱周期红利
    "银行":      ("银行",   "弱周期红利"),
    "多元金融":  ("银行",   "弱周期红利"),
    "证券":      ("银行",   "弱周期红利"),
    "保险":      ("保险",   "弱周期红利"),
    "火力发电":  ("火电",   "弱周期红利"),
    "新型电力":  ("火电",   "弱周期红利"),
    "水力发电":  ("水电",   "弱周期红利"),
    "水务":      ("水电",   "弱周期红利"),   # 公用事业-类水电
    "路桥":      ("水电",   "弱周期红利"),   # 公用基础设施
    "环境保护":  ("水电",   "弱周期红利"),
    "电信运营":  ("运营商", "弱周期红利"),
    "建筑工程":  ("银行",   "弱周期红利"),
    "专用机械":  ("银行",   "弱周期红利"),
    "出版业":    ("银行",   "弱周期红利"),   # 稳定现金流
    "互联网":    ("银行",   "弱周期红利"),
    "园区开发":  ("银行",   "弱周期红利"),
    # 消费成长红利
    "白酒":      ("白酒",   "消费成长红利"),
    "啤酒":      ("白酒",   "消费成长红利"),
    "软饮料":    ("白酒",   "消费成长红利"),
    "食品":      ("白酒",   "消费成长红利"),
    "中成药":    ("中药",   "消费成长红利"),
    "化学制药":  ("中药",   "消费成长红利"),
    "医药商业":  ("中药",   "消费成长红利"),
    "医疗保健":  ("中药",   "消费成长红利"),
    "生物制药":  ("中药",   "消费成长红利"),
    "种植业":    ("中药",   "消费成长红利"),
    "家用电器":  ("家电",   "消费成长红利"),
    "家居用品":  ("家电",   "消费成长红利"),
    "服饰":      ("家电",   "消费成长红利"),
    "摩托车":    ("家电",   "消费成长红利"),
    "汽车整车":  ("家电",   "消费成长红利"),
    "汽车配件":  ("家电",   "消费成长红利"),
    "文教休闲":  ("家电",   "消费成长红利"),
    "日用化工":  ("家电",   "消费成长红利"),
    "百货":      ("家电",   "消费成长红利"),
    "IT设备":    ("家电",   "消费成长红利"),
    # 周期资源红利
    "煤炭开采":  ("煤炭",   "周期资源红利"),
    "石油开采":  ("石油",   "周期资源红利"),
    "铝":        ("矿业",   "周期资源红利"),
    "铜":        ("矿业",   "周期资源红利"),
    "特种钢":    ("矿业",   "周期资源红利"),
    "普钢":      ("矿业",   "周期资源红利"),
    "其他建材":  ("矿业",   "周期资源红利"),
    "农药化肥":  ("矿业",   "周期资源红利"),
    "化工原料":  ("矿业",   "周期资源红利"),
    "染料涂料":  ("矿业",   "周期资源红利"),
    "电气设备":  ("矿业",   "周期资源红利"),
    "水泥":      ("矿业",   "周期资源红利"),
    "港口":      ("海运",   "周期资源红利"),
    "水运":      ("海运",   "周期资源红利"),
    "仓储物流":  ("海运",   "周期资源红利"),
}

# ──────────────────────────────────────────────────────────────────────────────
# 中证500高股息股票池（84只，dv_ttm >= 3%，pe 0-100）
# 格式：ts_code → {name, industry, sector, category, certainty}
# certainty 默认 B+（中证500中型企业，无手工评级，统一给 B+）
# ──────────────────────────────────────────────────────────────────────────────
CSI500_COMPANIES = {
    # ── 弱周期红利 / 银行 ──
    "601577.SH": {"name": "长沙银行",   "industry": "银行",    "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "601128.SH": {"name": "常熟银行",   "industry": "银行",    "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "002966.SZ": {"name": "苏州银行",   "industry": "银行",    "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "601997.SH": {"name": "贵阳银行",   "industry": "银行",    "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "002958.SZ": {"name": "青农商行",   "industry": "银行",    "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "601665.SH": {"name": "齐鲁银行",   "industry": "银行",    "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "600901.SH": {"name": "江苏金租",   "industry": "多元金融", "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "000987.SZ": {"name": "越秀资本",   "industry": "多元金融", "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "000563.SZ": {"name": "陕国投A",    "industry": "多元金融", "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "601555.SH": {"name": "东吴证券",   "industry": "证券",    "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    # 出版/传媒（稳定现金流，归银行类）
    "601928.SH": {"name": "凤凰传媒",   "industry": "出版业",  "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "601921.SH": {"name": "浙版传媒",   "industry": "出版业",  "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "601098.SH": {"name": "中南传媒",   "industry": "出版业",  "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    "603444.SH": {"name": "吉比特",     "industry": "互联网",  "sector": "银行",  "category": "弱周期红利",  "certainty": "B+"},
    # 建工/机械（稳定分红，归银行类）
    "600820.SH": {"name": "隧道股份",   "industry": "建筑工程", "sector": "银行", "category": "弱周期红利",  "certainty": "B+"},
    "600970.SH": {"name": "中材国际",   "industry": "建筑工程", "sector": "银行", "category": "弱周期红利",  "certainty": "B+"},
    "600720.SH": {"name": "中交设计",   "industry": "建筑工程", "sector": "银行", "category": "弱周期红利",  "certainty": "B+"},
    "601717.SH": {"name": "中创智领",   "industry": "专用机械", "sector": "银行", "category": "弱周期红利",  "certainty": "B+"},
    "600582.SH": {"name": "天地科技",   "industry": "专用机械", "sector": "银行", "category": "弱周期红利",  "certainty": "B+"},
    "600007.SH": {"name": "中国国贸",   "industry": "园区开发", "sector": "银行", "category": "弱周期红利",  "certainty": "B+"},
    # ── 弱周期红利 / 水电（公用事业） ──
    "600008.SH": {"name": "首创环保",   "industry": "水务",    "sector": "水电",  "category": "弱周期红利",  "certainty": "B+"},
    "000429.SZ": {"name": "粤高速A",    "industry": "路桥",    "sector": "水电",  "category": "弱周期红利",  "certainty": "B+"},
    "603568.SH": {"name": "伟明环保",   "industry": "环境保护", "sector": "水电",  "category": "弱周期红利",  "certainty": "B+"},
    # ── 弱周期红利 / 火电 ──
    "600098.SH": {"name": "广州发展",   "industry": "火力发电", "sector": "火电",  "category": "弱周期红利",  "certainty": "B+"},
    "600642.SH": {"name": "申能股份",   "industry": "火力发电", "sector": "火电",  "category": "弱周期红利",  "certainty": "B+"},
    "600483.SH": {"name": "福能股份",   "industry": "新型电力", "sector": "火电",  "category": "弱周期红利",  "certainty": "B+"},
    # ── 消费成长红利 / 家电 ──
    "603833.SH": {"name": "欧派家居",   "industry": "家居用品", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "603816.SH": {"name": "顾家家居",   "industry": "家居用品", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "600398.SH": {"name": "海澜之家",   "industry": "服饰",    "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "600177.SH": {"name": "雅戈尔",     "industry": "服饰",    "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "002032.SZ": {"name": "苏泊尔",     "industry": "家用电器", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "002508.SZ": {"name": "老板电器",   "industry": "家用电器", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "000921.SZ": {"name": "海信家电",   "industry": "家用电器", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "600060.SH": {"name": "海信视像",   "industry": "家用电器", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "603529.SH": {"name": "爱玛科技",   "industry": "摩托车",  "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "603766.SH": {"name": "隆鑫通用",   "industry": "摩托车",  "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "689009.SH": {"name": "九号公司",   "industry": "摩托车",  "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "000951.SZ": {"name": "中国重汽",   "industry": "汽车整车", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "002984.SZ": {"name": "森麒麟",     "industry": "汽车配件", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "603899.SH": {"name": "晨光股份",   "industry": "文教休闲", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "603605.SH": {"name": "珀莱雅",     "industry": "日用化工", "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    "002152.SZ": {"name": "广电运通",   "industry": "IT设备",  "sector": "家电",  "category": "消费成长红利", "certainty": "B+"},
    # ── 消费成长红利 / 白酒 ──
    "603589.SH": {"name": "口子窖",     "industry": "白酒",    "sector": "白酒",  "category": "消费成长红利", "certainty": "B+"},
    "300146.SZ": {"name": "汤臣倍健",   "industry": "食品",    "sector": "白酒",  "category": "消费成长红利", "certainty": "B+"},
    "603156.SH": {"name": "养元饮品",   "industry": "软饮料",  "sector": "白酒",  "category": "消费成长红利", "certainty": "B+"},
    "600132.SH": {"name": "重庆啤酒",   "industry": "啤酒",    "sector": "白酒",  "category": "消费成长红利", "certainty": "B+"},
    "600737.SH": {"name": "中粮糖业",   "industry": "食品",    "sector": "白酒",  "category": "消费成长红利", "certainty": "B+"},
    # ── 消费成长红利 / 中药 ──
    "600329.SH": {"name": "达仁堂",     "industry": "中成药",  "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "600566.SH": {"name": "济川药业",   "industry": "中成药",  "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "000423.SZ": {"name": "东阿阿胶",   "industry": "中成药",  "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "600332.SH": {"name": "白云山",     "industry": "中成药",  "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "002007.SZ": {"name": "华兰生物",   "industry": "生物制药", "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "600998.SH": {"name": "九州通",     "industry": "医药商业", "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "603233.SH": {"name": "大参林",     "industry": "医药商业", "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "603939.SH": {"name": "益丰药房",   "industry": "医药商业", "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "600598.SH": {"name": "北大荒",     "industry": "种植业",  "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "002223.SZ": {"name": "鱼跃医疗",   "industry": "医疗保健", "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "000513.SZ": {"name": "丽珠集团",   "industry": "化学制药", "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    "000739.SZ": {"name": "普洛药业",   "industry": "化学制药", "sector": "中药",  "category": "消费成长红利", "certainty": "B+"},
    # ── 周期资源红利 / 煤炭 ──
    "000937.SZ": {"name": "冀中能源",   "industry": "煤炭开采", "sector": "煤炭",  "category": "周期资源红利", "certainty": "B"},
    "601666.SH": {"name": "平煤股份",   "industry": "煤炭开采", "sector": "煤炭",  "category": "周期资源红利", "certainty": "B"},
    "600985.SH": {"name": "淮北矿业",   "industry": "煤炭开采", "sector": "煤炭",  "category": "周期资源红利", "certainty": "B"},
    "600546.SH": {"name": "山煤国际",   "industry": "煤炭开采", "sector": "煤炭",  "category": "周期资源红利", "certainty": "B"},
    "002128.SZ": {"name": "电投能源",   "industry": "煤炭开采", "sector": "煤炭",  "category": "周期资源红利", "certainty": "B"},
    "601001.SH": {"name": "晋控煤业",   "industry": "煤炭开采", "sector": "煤炭",  "category": "周期资源红利", "certainty": "B"},
    "600348.SH": {"name": "华阳股份",   "industry": "煤炭开采", "sector": "煤炭",  "category": "周期资源红利", "certainty": "B"},
    # ── 周期资源红利 / 矿业 ──
    "601567.SH": {"name": "三星电气",   "industry": "电气设备", "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "600282.SH": {"name": "南钢股份",   "industry": "普钢",    "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "600096.SH": {"name": "云天化",     "industry": "农药化肥", "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "600295.SH": {"name": "鄂尔多斯",   "industry": "特种钢",  "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "002372.SZ": {"name": "伟星新材",   "industry": "其他建材", "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "600801.SH": {"name": "华新建材",   "industry": "水泥",    "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "002318.SZ": {"name": "久立特材",   "industry": "特种钢",  "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "000683.SZ": {"name": "博源化工",   "industry": "化工原料", "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "600352.SH": {"name": "浙江龙盛",   "industry": "染料涂料", "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "002532.SZ": {"name": "天山铝业",   "industry": "铝",      "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "601168.SH": {"name": "西部矿业",   "industry": "铜",      "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    "056.SZ":    {"name": "横店东磁",   "industry": "电气设备", "sector": "矿业",  "category": "周期资源红利", "certainty": "B"},
    # ── 周期资源红利 / 石油 ──
    "600968.SH": {"name": "海油发展",   "industry": "石油开采", "sector": "石油",  "category": "周期资源红利", "certainty": "B"},
    # ── 周期资源红利 / 海运 ──
    "603565.SH": {"name": "中谷物流",   "industry": "水运",    "sector": "海运",  "category": "周期资源红利", "certainty": "B"},
    "600704.SH": {"name": "物产中大",   "industry": "仓储物流", "sector": "海运",  "category": "周期资源红利", "certainty": "B"},
    "000088.SZ": {"name": "盐田港",     "industry": "港口",    "sector": "海运",  "category": "周期资源红利", "certainty": "B"},
    "601598.SH": {"name": "中国外运",   "industry": "仓储物流", "sector": "海运",  "category": "周期资源红利", "certainty": "B"},
    "601000.SH": {"name": "唐山港",     "industry": "港口",    "sector": "海运",  "category": "周期资源红利", "certainty": "B"},
}

# 修正笔误
CSI500_COMPANIES["002056.SZ"] = {"name": "横店东磁", "industry": "电气设备", "sector": "矿业", "category": "周期资源红利", "certainty": "B"}
# 删除错误键
CSI500_COMPANIES.pop("056.SZ", None)


def get_csi500_stock_meta() -> dict:
    """
    返回与 BacktestEngine._stock_meta 格式兼容的字典
    key: ts_code
    value: {name, category, certainty, sector, is_etf}
    """
    meta = {}
    for ts_code, info in CSI500_COMPANIES.items():
        meta[ts_code] = {
            "name":      info["name"],
            "category":  info["category"],
            "certainty": info.get("certainty", "B+"),
            "sector":    info["sector"],
            "is_etf":    False,
        }
    return meta


if __name__ == "__main__":
    meta = get_csi500_stock_meta()
    from collections import Counter
    cats = Counter(v["category"] for v in meta.values())
    secs = Counter(v["sector"] for v in meta.values())
    print(f"中证500高股息股票池: {len(meta)} 只")
    print()
    print("category分布:")
    for k, v in sorted(cats.items()):
        print(f"  {k}: {v}只")
    print()
    print("sector分布:")
    for k, v in sorted(secs.items()):
        print(f"  {k}: {v}只")
