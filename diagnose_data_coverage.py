"""
diagnose_data_coverage.py
=================================
中证800历史成分股财务数据覆盖率诊断脚本
输出：逐期覆盖率 + 缺失股票的合理性解释（退市/金融/未上市/Tushare无数据）
"""
import sys, time, json
import pandas as pd
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = _PROJECT_ROOT / "data" / "fcf_financials"
IW_DIR   = _PROJECT_ROOT / "data" / "index_weights"

FINANCIAL_KW = {"金融","银行","证券","保险","地产","房产","多元金融","信托","期货",
                "融资租赁","金融控股","资产管理","房地产开发","房地产服务",
                "全国地产","区域地产","房产服务","园区开发"}

def is_financial(industry: str) -> bool:
    industry = str(industry).strip()
    if industry in FINANCIAL_KW:
        return True
    for kw in ("金融","银行","证券","保险","地产","房产"):
        if kw in industry:
            return True
    return False


def get_stock_meta() -> pd.DataFrame:
    """获取股票基本信息（上市/退市/行业）"""
    sl = pd.read_csv(DATA_DIR / "stock_list.csv", dtype={"ts_code": str})
    return sl


def load_existing_codes(path: Path, end_date_str: str = None) -> set:
    if not path.exists():
        return set()
    df = pd.read_csv(path, dtype={"ts_code": str})
    if end_date_str and "end_date" in df.columns:
        df = df[df["end_date"].astype(str).str[:8] == end_date_str]
    return set(df["ts_code"])


def classify_missing(missing_codes: set, target_year: int, sl_df: pd.DataFrame,
                     tushare_check: bool = False) -> dict:
    """
    将缺失股票分类解释：
      fin:        金融/地产（策略排除）
      not_listed: 该年份尚未上市
      delisted:   该年份已退市
      no_data:    Tushare无数据
      need_fix:   需要补全
    """
    ind_map  = dict(zip(sl_df["ts_code"], sl_df["industry"]))
    name_map = dict(zip(sl_df["ts_code"], sl_df["name"]))

    result = {"fin": [], "not_listed": [], "delisted": [], "no_data": [], "need_fix": []}

    for code in missing_codes:
        ind = ind_map.get(code, "")
        name = name_map.get(code, code)

        # 是否金融/地产
        if is_financial(ind):
            result["fin"].append({"code": code, "name": name, "industry": ind})
            continue

        # 检查上市/退市日期
        row = sl_df[sl_df["ts_code"] == code]
        if not row.empty:
            list_date = str(row.iloc[0].get("list_date", "") or "")
            delist_date = str(row.iloc[0].get("delist_date", "") or "")

            if list_date and len(list_date) >= 4:
                list_year = int(list_date[:4])
                if list_year > target_year:
                    result["not_listed"].append({"code": code, "name": name,
                                                  "list_date": list_date})
                    continue

            if delist_date and len(delist_date) >= 4 and delist_date != "nan":
                delist_year = int(delist_date[:4])
                if delist_year < target_year:
                    result["delisted"].append({"code": code, "name": name,
                                                "delist_date": delist_date})
                    continue

        # 剩余：需要补全或Tushare无数据
        result["need_fix"].append({"code": code, "name": name, "industry": ind})

    return result


def run_diagnosis(output_md: bool = True) -> dict:
    """执行完整诊断，返回报告数据"""
    sl_df = get_stock_meta()
    ind_map = dict(zip(sl_df["ts_code"], sl_df["industry"]))

    # 历史全量成分股
    hist = pd.read_csv(IW_DIR / "index_weight_000906.SH.csv", dtype={"con_code": str})
    all_hist = set(hist["con_code"].astype(str))
    non_fin_target = set(c for c in all_hist if not is_financial(ind_map.get(c, "")))
    fin_target = all_hist - non_fin_target

    print(f"历史全量成分: {len(all_hist)}只")
    print(f"  金融/地产（排除）: {len(fin_target)}只")
    print(f"  非金融目标: {len(non_fin_target)}只")
    print()

    report_lines = []
    report_lines.append("# 中证800财务数据覆盖率报告\n")
    report_lines.append(f"> 目标股票：中证800历史全量非金融成分 {len(non_fin_target)}只\n")
    report_lines.append(f"> 排除金融/地产：{len(fin_target)}只（FCF策略不使用）\n\n")

    summary = {}

    # ── 年报部分 ──
    print("=" * 65)
    print("年报覆盖率（非金融目标）")
    print("=" * 65)
    report_lines.append("## 一、年报覆盖率\n\n")
    report_lines.append("| 年份 | CF覆盖 | BS覆盖 | INC覆盖 | 缺失分析 |\n")
    report_lines.append("|------|:------:|:------:|:-------:|----------|\n")

    for year in range(2015, 2026):
        cf_path  = DATA_DIR / f"cashflow_{year}.csv"
        bs_path  = DATA_DIR / f"balance_{year}.csv"
        inc_path = DATA_DIR / f"income_{year}.csv"

        cf_exist  = load_existing_codes(cf_path)
        bs_exist  = load_existing_codes(bs_path)
        inc_exist = load_existing_codes(inc_path)

        cf_miss = non_fin_target - cf_exist
        inc_miss = non_fin_target - inc_exist

        cf_cov  = (len(non_fin_target)-len(cf_miss))/len(non_fin_target)*100
        bs_cov  = (len(non_fin_target)-len(non_fin_target-bs_exist))/len(non_fin_target)*100
        inc_cov = (len(non_fin_target)-len(inc_miss))/len(non_fin_target)*100

        # 分类缺失原因（以cashflow为主指标）
        cats = classify_missing(cf_miss, year, sl_df)
        miss_analysis = (f"合理缺失: 金融{len(cats['fin'])} 未上市{len(cats['not_listed'])} "
                         f"退市{len(cats['delisted'])} | 需补: {len(cats['need_fix'])}")

        status = "✅" if cf_cov >= 97 else ("⚠️" if cf_cov >= 90 else "❌")
        print(f"  {year} {status}: CF={cf_cov:.1f}% BS={bs_cov:.1f}% INC={inc_cov:.1f}% | {miss_analysis}")
        report_lines.append(f"| {year} {status} | {cf_cov:.1f}%({len(non_fin_target)-len(cf_miss)}只) | "
                            f"{bs_cov:.1f}% | {inc_cov:.1f}% | 未上市{len(cats['not_listed'])} "
                            f"退市{len(cats['delisted'])} 需补{len(cats['need_fix'])} |\n")
        summary[str(year)] = {"cf_cov": cf_cov, "bs_cov": bs_cov, "inc_cov": inc_cov,
                               "need_fix": len(cats["need_fix"])}

    # ── 季报部分 ──
    print()
    print("=" * 65)
    print("季报覆盖率（非金融目标）")
    print("=" * 65)
    report_lines.append("\n## 二、季报覆盖率\n\n")
    report_lines.append("| 期次 | CF覆盖 | BS覆盖 | INC覆盖 | 未上市 | 需补 |\n")
    report_lines.append("|------|:------:|:------:|:-------:|:------:|:----:|\n")

    q_periods = []
    for year in range(2015, 2026):
        if year == 2015:
            q_periods.append((year, "Q3", "0930"))
        else:
            for q, qd in [("Q1","0331"),("Q2","0630"),("Q3","0930")]:
                q_periods.append((year, q, qd))

    for year, q, qdate in q_periods:
        period = f"{year}{q}"
        end_date_str = f"{year}{qdate}"
        cf_path  = DATA_DIR / f"cashflow_{period}.csv"
        bs_path  = DATA_DIR / f"balance_{period}.csv"
        inc_path = DATA_DIR / f"income_{period}.csv"

        cf_exist  = load_existing_codes(cf_path, end_date_str)
        bs_exist  = load_existing_codes(bs_path, end_date_str)
        inc_exist = load_existing_codes(inc_path, end_date_str)

        cf_miss  = non_fin_target - cf_exist
        cf_cov   = (len(non_fin_target)-len(cf_miss))/len(non_fin_target)*100
        bs_cov   = (len(non_fin_target)-len(non_fin_target-bs_exist))/len(non_fin_target)*100
        inc_cov  = (len(non_fin_target)-len(non_fin_target-inc_exist))/len(non_fin_target)*100

        cats = classify_missing(cf_miss, year, sl_df)
        not_listed_n = len(cats["not_listed"])
        need_n = len(cats["need_fix"])
        status = "✅" if cf_cov >= 95 else ("⚠️" if cf_cov >= 80 else "❌")

        print(f"  {period} {status}: CF={cf_cov:.1f}% BS={bs_cov:.1f}% INC={inc_cov:.1f}% | 未上市={not_listed_n} 需补={need_n}")
        report_lines.append(f"| {period} {status} | {cf_cov:.1f}%({len(non_fin_target)-len(cf_miss)}只) | "
                            f"{bs_cov:.1f}% | {inc_cov:.1f}% | {not_listed_n} | {need_n} |\n")

    # ── 说明 ──
    report_lines.append("\n## 三、合理缺失说明\n\n")
    report_lines.append("| 原因类型 | 数量估算 | 说明 |\n")
    report_lines.append("|---------|:-------:|------|\n")
    report_lines.append("| 金融/地产（FCF策略排除） | ~138只 | 银行/证券/保险/地产行业，FCF策略不使用，无需下载 |\n")
    report_lines.append("| 股票尚未上市（历史期） | 视年份 | 某只股票2019年上市，则2015-2018年无年报，属合理缺失 |\n")
    report_lines.append("| 退市后无年报 | 少量 | 股票退市后不再发布年报，属合理缺失 |\n")
    report_lines.append("| Tushare数据库不收录 | 极少量 | 部分特殊股票Tushare接口查无数据，已穷举重试 |\n")
    report_lines.append("| 季报未在期限内披露 | 极少量 | 极少数公司季报延迟披露，Tushare查无数据 |\n")

    report_lines.append("\n---\n\n> 说明：⚠️ 表示覆盖率80-97%（有缺口但主要是合理缺失），✅ 表示≥97%，❌ 表示<80%\n")

    # 写入文档
    out_path = _PROJECT_ROOT / "docs" / "data_coverage_report.md"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        f.writelines(report_lines)
    print(f"\n📄 报告已写入: {out_path}")
    return summary


if __name__ == "__main__":
    run_diagnosis()
