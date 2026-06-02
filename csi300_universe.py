"""
沪深300高股息股票池
对齐基础策略的 sector / category 体系，用于回测对比

数据基准：2026-05-29 收盘，筛选条件：
  - 沪深300成分股（000300.SH，截至2025-12-31）
  - dv_ttm >= 3.0%
  - pe_ttm > 0 且 < 100（剔除亏损/极端高PE）
"""

# ──────────────────────────────────────────────────────────────────────────────
# 沪深300高股息股票池（97只，dv_ttm >= 3%，pe 0-100）
# ──────────────────────────────────────────────────────────────────────────────
CSI300_COMPANIES = {
    # ── 弱周期红利 ──
    # [保险]
    "601318.SH": {"name": "中国平安", "industry": "保险", "sector": "保险", "category": "弱周期红利", "certainty": "B+"},
    "601336.SH": {"name": "新华保险", "industry": "保险", "sector": "保险", "category": "弱周期红利", "certainty": "B+"},
    "601601.SH": {"name": "中国太保", "industry": "保险", "sector": "保险", "category": "弱周期红利", "certainty": "B+"},
    # [水电]
    "600900.SH": {"name": "长江电力", "industry": "水力发电", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},
    "600886.SH": {"name": "国投电力", "industry": "水力发电", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},
    # [交通]
    "601006.SH": {"name": "大秦铁路", "industry": "铁路", "sector": "交通", "category": "弱周期红利", "certainty": "B+"},
    "001965.SZ": {"name": "招商公路", "industry": "路桥", "sector": "交通", "category": "弱周期红利", "certainty": "B+"},
    "600377.SH": {"name": "宁沪高速", "industry": "路桥", "sector": "交通", "category": "弱周期红利", "certainty": "B+"},
    "601816.SH": {"name": "京沪高铁", "industry": "铁路", "sector": "交通", "category": "弱周期红利", "certainty": "B+"},
    # [火电]
    "600023.SH": {"name": "浙能电力", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},
    "600795.SH": {"name": "国电电力", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},
    "600027.SH": {"name": "华电国际", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},
    "600011.SH": {"name": "华能国际", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},
    # [燃气]
    "600803.SH": {"name": "新奥股份", "industry": "供气供热", "sector": "燃气", "category": "弱周期红利", "certainty": "B+"},
    # [运营商]
    "300628.SZ": {"name": "亿联网络", "industry": "通信设备", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},
    "600941.SH": {"name": "中国移动", "industry": "电信运营", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},
    "601728.SH": {"name": "中国电信", "industry": "电信运营", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},
    "600050.SH": {"name": "中国联通", "industry": "电信运营", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},
    # [银行]
    "601166.SH": {"name": "兴业银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "600036.SH": {"name": "招商银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "601818.SH": {"name": "光大银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "600015.SH": {"name": "华夏银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "601229.SH": {"name": "上海银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "600016.SH": {"name": "民生银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "000001.SZ": {"name": "平安银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "601916.SH": {"name": "浙商银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "601825.SH": {"name": "沪农商行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},
    "601998.SH": {"name": "中信银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "600919.SH": {"name": "江苏银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "601838.SH": {"name": "成都银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "601077.SH": {"name": "渝农商行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "601009.SH": {"name": "南京银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "601398.SH": {"name": "工商银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "600926.SH": {"name": "杭州银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "600000.SH": {"name": "浦发银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "601288.SH": {"name": "农业银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "601169.SH": {"name": "北京银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    "002142.SZ": {"name": "宁波银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},
    # [建筑]
    "601668.SH": {"name": "中国建筑", "industry": "建筑工程", "sector": "建筑", "category": "弱周期红利", "certainty": "A"},
    "601390.SH": {"name": "中国中铁", "industry": "建筑工程", "sector": "建筑", "category": "弱周期红利", "certainty": "A"},
    "601186.SH": {"name": "中国铁建", "industry": "建筑工程", "sector": "建筑", "category": "弱周期红利", "certainty": "B+"},
    "600039.SH": {"name": "四川路桥", "industry": "建筑工程", "sector": "建筑", "category": "弱周期红利", "certainty": "B+"},
    "601800.SH": {"name": "中国交建", "industry": "建筑工程", "sector": "建筑", "category": "弱周期红利", "certainty": "B+"},
    "601117.SH": {"name": "中国化学", "industry": "建筑工程", "sector": "建筑", "category": "弱周期红利", "certainty": "B+"},
    # [工业]
    "000157.SZ": {"name": "中联重科", "industry": "工程机械", "sector": "工业", "category": "弱周期红利", "certainty": "A"},
    "601766.SH": {"name": "中国中车", "industry": "运输设备", "sector": "工业", "category": "弱周期红利", "certainty": "A"},
    "600031.SH": {"name": "三一重工", "industry": "工程机械", "sector": "工业", "category": "弱周期红利", "certainty": "B+"},
    "688009.SH": {"name": "中国通号", "industry": "运输设备", "sector": "工业", "category": "弱周期红利", "certainty": "B+"},
    # [非银金融]
    "002736.SZ": {"name": "国信证券", "industry": "证券", "sector": "非银金融", "category": "弱周期红利", "certainty": "B+"},
    "601236.SH": {"name": "红塔证券", "industry": "证券", "sector": "非银金融", "category": "弱周期红利", "certainty": "B+"},

    # ── 消费成长红利 ──
    # [医药]
    "000538.SZ": {"name": "云南白药", "industry": "中成药", "sector": "医药", "category": "消费成长红利", "certainty": "B+"},
    "000999.SZ": {"name": "华润三九", "industry": "中成药", "sector": "医药", "category": "消费成长红利", "certainty": "B+"},
    "002001.SZ": {"name": "新和成", "industry": "化学制药", "sector": "医药", "category": "消费成长红利", "certainty": "B+"},
    "000963.SZ": {"name": "华东医药", "industry": "化学制药", "sector": "医药", "category": "消费成长红利", "certainty": "B+"},
    # [农业]
    "002714.SZ": {"name": "牧原股份", "industry": "农业综合", "sector": "农业", "category": "消费成长红利", "certainty": "B+"},
    # [家电]
    "000651.SZ": {"name": "格力电器", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},
    "600690.SH": {"name": "海尔智家", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},
    "000333.SZ": {"name": "美的集团", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},
    "603195.SH": {"name": "公牛集团", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},
    # [汽车]
    "600066.SH": {"name": "宇通客车", "industry": "汽车整车", "sector": "汽车", "category": "消费成长红利", "certainty": "B+"},
    "600741.SH": {"name": "华域汽车", "industry": "汽车配件", "sector": "汽车", "category": "消费成长红利", "certainty": "B+"},
    "000625.SZ": {"name": "长安汽车", "industry": "汽车整车", "sector": "汽车", "category": "消费成长红利", "certainty": "B+"},
    "600660.SH": {"name": "福耀玻璃", "industry": "汽车配件", "sector": "汽车", "category": "消费成长红利", "certainty": "B+"},
    "601058.SH": {"name": "赛轮轮胎", "industry": "汽车配件", "sector": "汽车", "category": "消费成长红利", "certainty": "B+"},
    # [传媒]
    "002027.SZ": {"name": "分众传媒", "industry": "广告包装", "sector": "传媒", "category": "消费成长红利", "certainty": "B+"},
    # [服饰]
    "300979.SZ": {"name": "华利集团", "industry": "服饰", "sector": "服饰", "category": "消费成长红利", "certainty": "B+"},
    # [IT设备]
    "002415.SZ": {"name": "海康威视", "industry": "IT设备", "sector": "IT设备", "category": "消费成长红利", "certainty": "B+"},
    "002236.SZ": {"name": "大华股份", "industry": "IT设备", "sector": "IT设备", "category": "消费成长红利", "certainty": "B+"},
    # [白酒]
    "000858.SZ": {"name": "五粮液", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},
    "000568.SZ": {"name": "泸州老窖", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},
    "000596.SZ": {"name": "古井贡酒", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},
    "002304.SZ": {"name": "洋河股份", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},
    "603369.SH": {"name": "今世缘", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},
    "600519.SH": {"name": "贵州茅台", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},
    # [食品饮料]
    "600887.SH": {"name": "伊利股份", "industry": "乳制品", "sector": "食品饮料", "category": "消费成长红利", "certainty": "B+"},
    "000895.SZ": {"name": "双汇发展", "industry": "食品", "sector": "食品饮料", "category": "消费成长红利", "certainty": "B+"},
    "603288.SH": {"name": "海天味业", "industry": "食品", "sector": "食品饮料", "category": "消费成长红利", "certainty": "B+"},
    "600600.SH": {"name": "青岛啤酒", "industry": "啤酒", "sector": "食品饮料", "category": "消费成长红利", "certainty": "B+"},

    # ── 周期资源红利 ──
    # [海运]
    "601919.SH": {"name": "中远海控", "industry": "水运", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},
    "601298.SH": {"name": "青岛港", "industry": "港口", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},
    "600018.SH": {"name": "上港集团", "industry": "港口", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},
    "601018.SH": {"name": "宁波港", "industry": "港口", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},
    # [煤炭]
    "601088.SH": {"name": "中国神华", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},
    "601225.SH": {"name": "陕西煤业", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},
    "000983.SZ": {"name": "山西焦煤", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},
    "600188.SH": {"name": "兖矿能源", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},
    # [石油]
    "600028.SH": {"name": "中国石化", "industry": "石油加工", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},
    "601857.SH": {"name": "中国石油", "industry": "石油开采", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},
    "600938.SH": {"name": "中国海油", "industry": "石油开采", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},
    "600346.SH": {"name": "恒力石化", "industry": "石油加工", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},
    # [钢铁]
    "000708.SZ": {"name": "中信特钢", "industry": "特种钢", "sector": "钢铁", "category": "周期资源红利", "certainty": "B+"},
    "600019.SH": {"name": "宝钢股份", "industry": "普钢", "sector": "钢铁", "category": "周期资源红利", "certainty": "B+"},
    # [建材]
    "600585.SH": {"name": "海螺水泥", "industry": "水泥", "sector": "建材", "category": "周期资源红利", "certainty": "B+"},
    "000786.SZ": {"name": "北新建材", "industry": "其他建材", "sector": "建材", "category": "周期资源红利", "certainty": "B+"},
    # [化工]
    "002601.SZ": {"name": "龙佰集团", "industry": "化工原料", "sector": "化工", "category": "周期资源红利", "certainty": "B+"},
    "000408.SZ": {"name": "藏格矿业", "industry": "农药化肥", "sector": "化工", "category": "周期资源红利", "certainty": "B+"},
    # [有色]
    "600219.SH": {"name": "南山铝业", "industry": "铝", "sector": "有色", "category": "周期资源红利", "certainty": "B+"},

}


def get_csi300_stock_meta() -> dict:
    """返回与 BacktestEngine._stock_meta 格式兼容的字典"""
    return {
        ts_code: {
            "name":      info["name"],
            "category":  info["category"],
            "certainty": info.get("certainty", "B+"),
            "sector":    info["sector"],
            "is_etf":    False,
        }
        for ts_code, info in CSI300_COMPANIES.items()
    }


if __name__ == "__main__":
    meta = get_csi300_stock_meta()
    from collections import Counter
    cats = Counter(v["category"] for v in meta.values())
    secs = Counter(v["sector"] for v in meta.values())
    print(f"沪深300高股息股票池: {len(meta)} 只")
    print("\ncategory分布:")
    for k, v in sorted(cats.items()): print(f"  {k}: {v}只")
    print("\nsector分布:")
    for k, v in sorted(secs.items()): print(f"  {k}: {v}只")