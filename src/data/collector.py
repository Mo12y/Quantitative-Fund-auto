"""
数据采集模块: 通过 akshare 获取基金和指数数据。

这是整个系统的地基——数据采集不通过，一切都是空谈。
Phase 0 的首要任务就是验证这些接口的可用性。

使用的 akshare API (v1.18+):
- fund_name_em()               → 全市场基金名称+类型列表
- fund_open_fund_daily_em()    → 全市场基金每日净值+费率
- fund_open_fund_info_em()     → 单只基金净值历史
- fund_individual_basic_info_xq() → 单只基金详细信息(规模/经理/成立日)
- stock_index_pe_lg()          → 指数PE+PE分位数历史
- stock_index_pb_lg()          → 指数PB+PB分位数历史
- stock_zh_index_daily()       → 指数日线行情
"""

import pandas as pd
import numpy as np
import time
import re
from datetime import datetime, timedelta
from typing import Optional

try:
    import akshare as ak
except ImportError:
    raise ImportError(
        "请先安装 akshare: pip install akshare\n"
        "如果安装失败，参考: https://akshare.akfamily.xyz/"
    )

# 备选数据源: efinance（纯Python，更稳定，有累计净值）
try:
    import efinance as ef
    EFINANCE_AVAILABLE = True
except ImportError:
    EFINANCE_AVAILABLE = False

from .database import Database


class DataCollector:
    """数据采集器: 封装 akshare 调用，写入 SQLite"""

    # 支持的指数列表
    SUPPORTED_INDICES = {
        "000300": {"name": "沪深300", "pe_symbol": "沪深300", "pb_symbol": "沪深300", "daily_symbol": "sh000300"},
        "000905": {"name": "中证500", "pe_symbol": "中证500", "pb_symbol": "中证500", "daily_symbol": "sh000905"},
        "000016": {"name": "上证50",  "pe_symbol": "上证50",  "pb_symbol": "上证50",  "daily_symbol": "sh000016"},
    }

    def __init__(self, db: Database):
        self.db = db

    # =================================================================
    # Phase 0 验证: 测试数据获取是否正常
    # =================================================================

    def test_connection(self) -> dict:
        """
        测试所有关键数据接口的可用性。

        Returns:
            dict: {接口名: (是否成功, 详情)}
        """
        results = {}

        # 测试1: 基金名称列表
        try:
            df = ak.fund_name_em()
            results["基金名称列表"] = (True, f"获取 {len(df)} 条, 列: {list(df.columns)}")
        except Exception as e:
            results["基金名称列表"] = (False, str(e)[:120])

        # 测试2: 全市场基金每日数据
        try:
            df = ak.fund_open_fund_daily_em()
            results["基金每日数据"] = (True, f"获取 {len(df)} 条, 列: {list(df.columns)}")
        except Exception as e:
            results["基金每日数据"] = (False, str(e)[:120])

        # 测试3: 单只基金净值历史
        try:
            df = ak.fund_open_fund_info_em(symbol="000011", indicator="单位净值走势")
            results["基金净值历史"] = (True, f"获取 {len(df)} 条, 列: {list(df.columns)}")
        except Exception as e:
            results["基金净值历史"] = (False, str(e)[:120])

        # 测试4: 指数PE估值
        try:
            df = ak.stock_index_pe_lg(symbol="沪深300")
            last = df.iloc[-1]
            results["指数PE估值"] = (True, f"获取 {len(df)} 条, PE分位列: {df.columns[-1]}")
        except Exception as e:
            results["指数PE估值"] = (False, str(e)[:120])

        # 测试5: 指数PB估值
        try:
            df = ak.stock_index_pb_lg(symbol="沪深300")
            last = df.iloc[-1]
            results["指数PB估值"] = (True, f"获取 {len(df)} 条, PB分位列: {df.columns[-1]}")
        except Exception as e:
            results["指数PB估值"] = (False, str(e)[:120])

        # 测试6: 指数日线行情
        try:
            df = ak.stock_zh_index_daily(symbol="sh000300")
            results["指数日线行情"] = (True, f"获取 {len(df)} 条")
        except Exception as e:
            results["指数日线行情"] = (False, str(e)[:120])

        # 测试7: 基金详细信息（抽样）
        try:
            df = ak.fund_individual_basic_info_xq(symbol="000011")
            info_dict = dict(zip(df["item"], df["value"]))
            results["基金详细信息"] = (True, f"获取到 {len(df)} 个字段")
        except Exception as e:
            results["基金详细信息"] = (False, str(e)[:120])

        # 测试8: efinance备选数据源
        if EFINANCE_AVAILABLE:
            try:
                df = ef.fund.get_quote_history("000011")
                results["efinance(备选)基金净值"] = (True, f"获取 {len(df)} 条, 含累计净值")
            except Exception as e:
                results["efinance(备选)基金净值"] = (False, str(e)[:120])
        else:
            results["efinance(备选)基金净值"] = (False, "未安装 (pip install efinance)")

        return results

    # =================================================================
    # 基金数据采集
    # =================================================================

    def collect_fund_name_list(self) -> pd.DataFrame:
        """
        获取全市场基金名称和类型。

        Returns:
            DataFrame: 列: 基金代码, 拼音缩写, 基金简称, 基金类型, 拼音全名
        """
        df = ak.fund_name_em()
        self.db.log_data_collection("fund_name_list", "success", len(df))
        return df

    def collect_fund_daily_all(self) -> pd.DataFrame:
        """
        获取全市场开放式基金每日净值、费率、申赎状态。

        Returns:
            DataFrame: 每个基金最新交易日的净值和费率
        """
        df = ak.fund_open_fund_daily_em()
        self.db.log_data_collection("fund_daily", "success", len(df))
        return df

    def collect_fund_nav(self, fund_code: str) -> pd.DataFrame:
        """
        获取单只基金的净值历史（efinance为主，akshare为备选）。

        efinance 更稳定且返回累计净值，akshare新版不再返回累计净值。

        Args:
            fund_code: 基金代码，如 '000011'

        Returns:
            DataFrame: 列: 净值日期, 单位净值, 累计净值, 日增长率
        """
        # 优先用 efinance: 更稳定、有累计净值
        if EFINANCE_AVAILABLE:
            try:
                df = ef.fund.get_quote_history(fund_code)
                df = df.rename(columns={"日期": "净值日期", "涨跌幅": "日增长率"})
                time.sleep(0.2)
                return df
            except Exception:
                pass

        # 回退到 akshare
        df = ak.fund_open_fund_info_em(
            symbol=fund_code,
            indicator="单位净值走势"
        )
        time.sleep(0.3)
        return df

    def collect_fund_detail(self, fund_code: str) -> dict:
        """
        获取单只基金的详细信息。

        Args:
            fund_code: 基金代码

        Returns:
            dict: {字段名: 值}，包含基金类型、规模、经理、成立日期等
        """
        try:
            df = ak.fund_individual_basic_info_xq(symbol=fund_code)
            info = dict(zip(df["item"], df["value"]))
            time.sleep(0.2)
            return info
        except Exception:
            return {}

    def collect_fund_navs_batch(
        self,
        fund_codes: list,
        progress_callback=None,
        max_funds: int = 100
    ) -> dict:
        """
        批量获取多只基金的净值历史。

        Args:
            fund_codes: 基金代码列表
            progress_callback: 进度回调 (current, total)
            max_funds: 最多获取多少只（避免请求过多）

        Returns:
            dict: {fund_code: DataFrame 或 "ERROR: xxx"}
        """
        results = {}
        codes = fund_codes[:max_funds]
        total = len(codes)

        for i, code in enumerate(codes):
            try:
                results[code] = self.collect_fund_nav(code)
            except Exception as e:
                results[code] = f"ERROR: {e}"

            if progress_callback:
                progress_callback(i + 1, total)

        return results

    # =================================================================
    # 指数数据采集
    # =================================================================

    def collect_index_pe(self, symbol: str = "沪深300") -> pd.DataFrame:
        """
        获取指数PE估值历史（含历史分位数）。

        Args:
            symbol: 指数名称，如 '沪深300', '中证500', '上证50'

        Returns:
            DataFrame: 含 日期/PE/PE分位数
        """
        df = ak.stock_index_pe_lg(symbol=symbol)
        self.db.log_data_collection(f"pe_{symbol}", "success", len(df))
        return df

    def collect_index_pb(self, symbol: str = "沪深300") -> pd.DataFrame:
        """
        获取指数PB估值历史（含历史分位数）。

        Args:
            symbol: 指数名称

        Returns:
            DataFrame: 含 日期/PB/PB分位数
        """
        df = ak.stock_index_pb_lg(symbol=symbol)
        self.db.log_data_collection(f"pb_{symbol}", "success", len(df))
        return df

    def collect_index_daily(self, symbol: str = "sh000300") -> pd.DataFrame:
        """
        获取指数日线行情。

        Args:
            symbol: 指数代码，如 'sh000300'（沪深300）

        Returns:
            DataFrame: OHLCV 日线数据
        """
        df = ak.stock_zh_index_daily(symbol=symbol)
        self.db.log_data_collection(f"index_daily_{symbol}", "success", len(df))
        return df

    def collect_all_index_valuations(self) -> dict:
        """
        采集所有支持指数的PE/PB估值数据。

        Returns:
            dict: {index_code: {"pe": DataFrame, "pb": DataFrame}}
        """
        results = {}
        for code, info in self.SUPPORTED_INDICES.items():
            pe_data = None
            pb_data = None
            try:
                pe_data = self.collect_index_pe(info["pe_symbol"])
                results[code] = results.get(code, {})
                results[code]["pe"] = pe_data
            except Exception as e:
                results[code] = results.get(code, {})
                results[code]["pe_error"] = str(e)

            try:
                pb_data = self.collect_index_pb(info["pb_symbol"])
                results[code]["pb"] = pb_data
            except Exception as e:
                results[code]["pb_error"] = str(e)

            time.sleep(0.5)

        return results

    # =================================================================
    # 数据清洗与入库
    # =================================================================

    def save_fund_list_to_db(self, name_df: pd.DataFrame, daily_df: pd.DataFrame):
        """
        将基金名称列表和每日数据合并后存入 fund_info 表。

        name_df (fund_name_em):  基金代码, 拼音缩写, 基金简称, 基金类型, 拼音全称
        daily_df (fund_open_fund_daily_em): 基金代码, 基金简称, today-单位净值,
            today-累计净值, yesterday-单位净值, yesterday-累计净值,
            日增长值, 日增长率, 申购状态, 赎回状态, 手续费
        """
        daily_cols = list(daily_df.columns)
        # 列: [0]基金代码 [1]基金简称 ... [-3]申购状态 [-2]赎回状态 [-1]手续费

        # 建立基金代码 → 每日数据映射
        daily_info = {}
        for _, row in daily_df.iterrows():
            code = str(row.iloc[0])
            daily_info[code] = {
                "fund_name": str(row.iloc[1]),
                "purchase_status": str(row.iloc[-3]) if len(daily_cols) >= 9 else "",
                "redeem_status": str(row.iloc[-2]) if len(daily_cols) >= 10 else "",
                "fee_str": str(row.iloc[-1]) if len(daily_cols) >= 11 else "",
            }

        count = 0
        for _, row in name_df.iterrows():
            code = str(row.iloc[0])
            fund_type = str(row.iloc[3]) if len(name_df.columns) > 3 else ""
            fund_name = str(row.iloc[2]) if len(name_df.columns) > 2 else ""

            # 从 daily_info 补充数据
            extra = daily_info.get(code, {})
            if extra.get("fund_name"):
                fund_name = extra["fund_name"]

            # 解析费率
            mgt_fee = self._parse_fee(extra.get("fee_str", "0%"))

            fund = {
                "fund_code": code,
                "fund_name": fund_name,
                "fund_type": fund_type,
                "purchase_status": extra.get("purchase_status", ""),
                "mgt_fee": mgt_fee,
            }
            self.db.upsert_fund_info(fund)
            count += 1

        self.db.log_data_collection("fund_info_save", "success", count)

    def save_fund_detail_to_db(self, fund_code: str, detail: dict):
        """将基金详细信息更新到数据库"""
        if not detail:
            return

        # 从 item/value 对中提取关键字段
        field_map = {
            "基金全称": "fund_full_name",
            "成立时间": "establish_date",
            "最新规模": "fund_size",
            "基金公司": "company_name",
            "基金经理": "manager_name",
            "托管银行": "custodian",
            "基金类型": "fund_type_detailed",
            "跟踪标的": "benchmark",
            "运作方式": "operation_mode",
        }

        fund = {"fund_code": fund_code}
        for item_key, db_key in field_map.items():
            val = detail.get(item_key, "")
            if val:
                fund[db_key] = str(val)

        # 解析规模: "49.59亿" → 49.59
        if fund.get("fund_size"):
            fund["fund_size"] = self._parse_size(fund["fund_size"])

        # 更新到数据库
        if fund.get("establish_date"):
            update_data = {
                "fund_code": fund_code,
                "fund_name": detail.get("基金名称", detail.get("基金简称", "")),
                "establish_date": fund.get("establish_date", ""),
                "fund_size": fund.get("fund_size", 0),
                "company_name": fund.get("company_name", ""),
                "manager_name": fund.get("manager_name", ""),
                "benchmark": fund.get("benchmark", ""),
                "fund_type": fund.get("fund_type_detailed", ""),
            }
            self.db.upsert_fund_info(update_data)

    def save_fund_nav_batch(self, fund_code: str, df: pd.DataFrame):
        """
        将净值历史数据清洗后入库。

        支持两种数据源格式:
        - efinance: 净值日期, 单位净值, 累计净值, 日增长率
        - akshare:  净值日期, 单位净值, 日增长率 (无累计净值)
        """
        records = []
        cols = list(df.columns)

        for _, row in df.iterrows():
            nav_date = str(row.iloc[0])
            unit_nav = self._to_float(row.iloc[1])

            # 累计净值: 如果列数>=3且第3列看起来像净值（值>0且不是百分比）
            acc_nav = 0.0
            if len(cols) >= 3:
                v3 = self._to_float(row.iloc[2])
                if v3 > 0.5:  # 合理净值范围
                    acc_nav = v3

            # 日增长率: 最后一列
            daily_return = 0.0
            if len(cols) >= 3:
                v_last = self._to_float(row.iloc[-1])
                if -20 < v_last < 20:  # 日涨跌幅合理范围(%)
                    daily_return = v_last

            if nav_date and unit_nav:
                records.append((str(fund_code), nav_date, unit_nav, acc_nav, daily_return))

        if records:
            self.db.insert_nav_batch(records)

    def save_index_val_to_db(self, index_code: str, pe_df: pd.DataFrame, pb_df: pd.DataFrame):
        """
        将指数PE/PB数据清洗后存入 index_valuation 和 index_daily 表。

        PE列 (v1.18+): 日期, 指数, 等权静态市盈率, 静态市盈率, 静态市盈率中位数,
                       等权滚动市盈率, 滚动市盈率, 滚动市盈率中位数
        PB列 (v1.18+): 日期, 指数, 市净率, 等权市净率, 市净率中位数

        注意: akshare 不直接返回分位数，我们基于全部历史数据自己计算。
        """
        if pe_df is None or pb_df is None:
            return

        pe_cols = list(pe_df.columns)
        pb_cols = list(pb_df.columns)

        # PE数据: 使用"滚动市盈率"(TTM PE, col index 6)
        pe_values = pd.to_numeric(pe_df.iloc[:, 6], errors='coerce').dropna()
        current_pe = pe_values.iloc[-1] if len(pe_values) > 0 else 0
        # 计算PE分位数: 当前PE在历史中的位置（越低越好=估值越低）
        pe_pct = (pe_values < current_pe).sum() / len(pe_values) * 100

        # PB数据: 使用"市净率"(col index 2)
        pb_values = pd.to_numeric(pb_df.iloc[:, 2], errors='coerce').dropna()
        current_pb = pb_values.iloc[-1] if len(pb_values) > 0 else 0
        pb_pct = (pb_values < current_pb).sum() / len(pb_values) * 100

        # 存入 index_valuation 快照表
        val = {
            "index_code": index_code,
            "index_name": self.SUPPORTED_INDICES.get(index_code, {}).get("name", index_code),
            "pe": round(current_pe, 2),
            "pe_percentile": round(pe_pct, 1),
            "pb": round(current_pb, 2),
            "pb_percentile": round(pb_pct, 1),
            "dividend_yield": 0,
        }
        self.db.upsert_index_valuation(val)

        # 存入 index_daily 历史表
        pe_date_map = {}
        for _, row in pe_df.iterrows():
            date_str = str(row.iloc[0])
            pe_date_map[date_str] = {
                "pe": self._to_float(row.iloc[6]),
                "pe_pct": 0,  # 逐日分位计算太慢，先存0，后续优化
            }

        records = []
        for _, row in pb_df.iterrows():
            date_str = str(row.iloc[0])
            pb_val = self._to_float(row.iloc[2])
            close_val = self._to_float(row.iloc[1])
            pe_data = pe_date_map.get(date_str, {})

            records.append({
                "index_code": index_code,
                "trade_date": date_str,
                "close": close_val,
                "pe": pe_data.get("pe", 0) or 0,
                "pb": pb_val,
                "pe_percentile": pe_data.get("pe_pct", 0),
                "pb_percentile": 0,
            })

        if records:
            self._insert_index_daily_batch(records)

    def _insert_index_daily_batch(self, records: list):
        """批量插入指数日线数据"""
        import sqlite3
        cursor = self.db.conn.cursor()
        cursor.executemany("""
            INSERT OR IGNORE INTO index_daily
            (index_code, trade_date, close, pe, pb, pe_percentile, pb_percentile)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (r["index_code"], r["trade_date"], r["close"],
             r["pe"], r["pb"], r["pe_percentile"], r["pb_percentile"])
            for r in records
        ])
        self.db.conn.commit()

    # =================================================================
    # 工具方法
    # =================================================================

    @staticmethod
    def _to_float(val) -> float:
        """安全转换为浮点数"""
        try:
            if pd.isna(val):
                return 0.0
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _parse_fee(fee_str: str) -> float:
        """解析费率字符串: '0.15%' → 0.15, '1.50%' → 1.50"""
        try:
            return float(fee_str.replace("%", ""))
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _parse_size(size_str: str) -> float:
        """解析规模字符串: '49.59亿' → 49.59"""
        try:
            return float(size_str.replace("亿", "").replace("万", ""))
        except (ValueError, TypeError):
            return 0.0


def quick_test():
    """
    Phase 0 快速测试: 验证数据接口可用性。
    运行: python src/main.py test
    """
    print("=" * 60)
    print("🔍 Phase 0: 数据接口验证")
    print(f"   akshare 版本: {ak.__version__}")
    print("=" * 60)

    db = Database("data/fund_quant.db")
    collector = DataCollector(db)

    results = collector.test_connection()

    all_pass = True
    for name, (success, detail) in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"\n{status} | {name}")
        print(f"       {detail}")
        if not success:
            all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("✅ 所有接口测试通过！可以开始 Phase 1 开发。")
        print("   下一步: python src/main.py collect")
    else:
        print("❌ 部分接口测试失败。")
        print("   常见解决方案:")
        print("   1. pip install akshare --upgrade")
        print("   2. 检查网络连接")
        print("   3. 查看 akshare 文档: https://akshare.akfamily.xyz/")
    print("=" * 60)

    db.close()
    return all_pass


if __name__ == "__main__":
    quick_test()
