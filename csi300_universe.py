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
    "601318.SH": {"name": "中国平安", "industry": "保险", "sector": "保险", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.8% pe=7.3
    "601336.SH": {"name": "新华保险", "industry": "保险", "sector": "保险", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.5% pe=5.0
    "601601.SH": {"name": "中国太保", "industry": "保险", "sector": "保险", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.4% pe=5.7
    # [水电]
    "601006.SH": {"name": "大秦铁路", "industry": "铁路", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.2% pe=18.3
    "001965.SZ": {"name": "招商公路", "industry": "路桥", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.2% pe=14.7
    "600377.SH": {"name": "宁沪高速", "industry": "路桥", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.8% pe=13.7
    "600900.SH": {"name": "长江电力", "industry": "水力发电", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.4% pe=18.8
    "600886.SH": {"name": "国投电力", "industry": "水力发电", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.2% pe=15.4
    "601816.SH": {"name": "京沪高铁", "industry": "铁路", "sector": "水电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.1% pe=18.4
    # [火电]
    "600803.SH": {"name": "新奥股份", "industry": "供气供热", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=6.2% pe=13.9
    "600023.SH": {"name": "浙能电力", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=5.3% pe=11.7
    "600795.SH": {"name": "国电电力", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.1% pe=13.6
    "600027.SH": {"name": "华电国际", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.8% pe=10.9
    "600011.SH": {"name": "华能国际", "industry": "火力发电", "sector": "火电", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.0% pe=10.0
    # [运营商]
    "300628.SZ": {"name": "亿联网络", "industry": "通信设备", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=5.0% pe=17.0
    "600941.SH": {"name": "中国移动", "industry": "电信运营", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.8% pe=15.8
    "601728.SH": {"name": "中国电信", "industry": "电信运营", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.5% pe=17.5
    "600050.SH": {"name": "中国联通", "industry": "电信运营", "sector": "运营商", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.0% pe=15.7
    # [银行]
    "601166.SH": {"name": "兴业银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=8.8% pe=5.1
    "600036.SH": {"name": "招商银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=7.9% pe=6.4
    "000157.SZ": {"name": "中联重科", "industry": "工程机械", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=6.9% pe=14.4
    "601818.SH": {"name": "光大银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=6.1% pe=4.8
    "600015.SH": {"name": "华夏银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=6.0% pe=3.9
    "601229.SH": {"name": "上海银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.8% pe=5.3
    "601668.SH": {"name": "中国建筑", "industry": "建筑工程", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.6% pe=5.3
    "600016.SH": {"name": "民生银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.6% pe=5.3
    "601766.SH": {"name": "中国中车", "industry": "运输设备", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.5% pe=12.3
    "000001.SZ": {"name": "平安银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.5% pe=4.9
    "601390.SH": {"name": "中国中铁", "industry": "建筑工程", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.5% pe=5.5
    "601916.SH": {"name": "浙商银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.2% pe=6.4
    "601825.SH": {"name": "沪农商行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "A"},  # dv_ttm=5.1% pe=6.6
    "601998.SH": {"name": "中信银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.9% pe=5.8
    "600919.SH": {"name": "江苏银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.8% pe=5.9
    "601838.SH": {"name": "成都银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.8% pe=5.9
    "601077.SH": {"name": "渝农商行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.7% pe=6.1
    "601186.SH": {"name": "中国铁建", "industry": "建筑工程", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.7% pe=4.9
    "600039.SH": {"name": "四川路桥", "industry": "建筑工程", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.7% pe=10.9
    "601009.SH": {"name": "南京银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.6% pe=6.0
    "601800.SH": {"name": "中国交建", "industry": "建筑工程", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.3% pe=7.8
    "601398.SH": {"name": "工商银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.3% pe=6.9
    "002736.SZ": {"name": "国信证券", "industry": "证券", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.2% pe=9.6
    "600926.SH": {"name": "杭州银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.1% pe=5.9
    "600000.SH": {"name": "浦发银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=4.0% pe=6.2
    "601288.SH": {"name": "农业银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.9% pe=7.5
    "601169.SH": {"name": "北京银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.9% pe=5.2
    "002142.SZ": {"name": "宁波银行", "industry": "银行", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.9% pe=6.8
    "600031.SH": {"name": "三一重工", "industry": "工程机械", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.7% pe=19.6
    "601117.SH": {"name": "中国化学", "industry": "建筑工程", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.7% pe=7.2
    "688009.SH": {"name": "中国通号", "industry": "运输设备", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.5% pe=13.9
    "601236.SH": {"name": "红塔证券", "industry": "证券", "sector": "银行", "category": "弱周期红利", "certainty": "B+"},  # dv_ttm=3.0% pe=29.0

    # ── 消费成长红利 ──
    # [中药]
    "000538.SZ": {"name": "云南白药", "industry": "中成药", "sector": "中药", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=5.2% pe=17.0
    "000999.SZ": {"name": "华润三九", "industry": "中成药", "sector": "中药", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=4.3% pe=12.6
    "002714.SZ": {"name": "牧原股份", "industry": "农业综合", "sector": "中药", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.5% pe=22.5
    "002001.SZ": {"name": "新和成", "industry": "化学制药", "sector": "中药", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.4% pe=13.4
    "000963.SZ": {"name": "华东医药", "industry": "化学制药", "sector": "中药", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.2% pe=14.5
    # [家电]
    "600066.SH": {"name": "宇通客车", "industry": "汽车整车", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=7.7% pe=13.1
    "000651.SZ": {"name": "格力电器", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=7.6% pe=7.5
    "002027.SZ": {"name": "分众传媒", "industry": "广告包装", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=6.8% pe=22.4
    "300979.SZ": {"name": "华利集团", "industry": "服饰", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=6.1% pe=14.1
    "600690.SH": {"name": "海尔智家", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=5.9% pe=10.5
    "000333.SZ": {"name": "美的集团", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=5.0% pe=13.9
    "600741.SH": {"name": "华域汽车", "industry": "汽车配件", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=4.8% pe=7.3
    "000625.SZ": {"name": "长安汽车", "industry": "汽车整车", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=4.4% pe=25.5
    "603195.SH": {"name": "公牛集团", "industry": "家用电器", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=4.1% pe=18.1
    "600660.SH": {"name": "福耀玻璃", "industry": "汽车配件", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=4.0% pe=15.3
    "002415.SZ": {"name": "海康威视", "industry": "IT设备", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.8% pe=18.7
    "002236.SZ": {"name": "大华股份", "industry": "IT设备", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.3% pe=14.0
    "601058.SH": {"name": "赛轮轮胎", "industry": "汽车配件", "sector": "家电", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.0% pe=11.7
    # [白酒]
    "000858.SZ": {"name": "五粮液", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=6.8% pe=26.2
    "000568.SZ": {"name": "泸州老窖", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=6.5% pe=13.5
    "000596.SZ": {"name": "古井贡酒", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=6.4% pe=17.4
    "600887.SH": {"name": "伊利股份", "industry": "乳制品", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=6.3% pe=14.1
    "000895.SZ": {"name": "双汇发展", "industry": "食品", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=5.8% pe=16.5
    "002304.SZ": {"name": "洋河股份", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=5.1% pe=67.3
    "603369.SH": {"name": "今世缘", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=4.0% pe=16.1
    "603288.SH": {"name": "海天味业", "industry": "食品", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.9% pe=28.9
    "600519.SH": {"name": "贵州茅台", "industry": "白酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.9% pe=20.0
    "600600.SH": {"name": "青岛啤酒", "industry": "啤酒", "sector": "白酒", "category": "消费成长红利", "certainty": "B+"},  # dv_ttm=3.6% pe=17.8

    # ── 周期资源红利 ──
    # [海运]
    "601919.SH": {"name": "中远海控", "industry": "水运", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=11.3% pe=8.6
    "601298.SH": {"name": "青岛港", "industry": "港口", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.8% pe=11.3
    "600018.SH": {"name": "上港集团", "industry": "港口", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.8% pe=8.7
    "601018.SH": {"name": "宁波港", "industry": "港口", "sector": "海运", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.4% pe=13.1
    # [煤炭]
    "601088.SH": {"name": "中国神华", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=6.2% pe=19.7
    "601225.SH": {"name": "陕西煤业", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=4.6% pe=15.4
    "000983.SZ": {"name": "山西焦煤", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.8% pe=29.0
    "600188.SH": {"name": "兖矿能源", "industry": "煤炭开采", "sector": "煤炭", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.3% pe=22.7
    # [石油]
    "600028.SH": {"name": "中国石化", "industry": "石油加工", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=4.7% pe=16.5
    "601857.SH": {"name": "中国石油", "industry": "石油开采", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=4.3% pe=12.5
    "600938.SH": {"name": "中国海油", "industry": "石油开采", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.7% pe=13.1
    "600346.SH": {"name": "恒力石化", "industry": "石油加工", "sector": "石油", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.1% pe=13.5
    # [矿业]
    "600219.SH": {"name": "南山铝业", "industry": "铝", "sector": "矿业", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=8.4% pe=14.4
    "600585.SH": {"name": "海螺水泥", "industry": "水泥", "sector": "矿业", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=4.7% pe=13.7
    "000708.SZ": {"name": "中信特钢", "industry": "特种钢", "sector": "矿业", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=4.6% pe=11.8
    "002601.SZ": {"name": "龙佰集团", "industry": "化工原料", "sector": "矿业", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=4.2% pe=45.7
    "000786.SZ": {"name": "北新建材", "industry": "其他建材", "sector": "矿业", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.7% pe=13.6
    "600019.SH": {"name": "宝钢股份", "industry": "普钢", "sector": "矿业", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.6% pe=13.0
    "000408.SZ": {"name": "藏格矿业", "industry": "农药化肥", "sector": "矿业", "category": "周期资源红利", "certainty": "B+"},  # dv_ttm=3.2% pe=25.9

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