"""
同花顺官方 API 数据采集器 (HiThink Finance)

API文档: https://fuyao.aicubes.cn/docs/
GitHub:  https://github.com/HiThink-Tech/Financial-API

用于替代 akshare+efinance 的以下场景:
- 基金净值历史 + 收益率
- 指数行情 + 指数K线
- 基金基本信息(管理人/经理/成立日)

优势:
- 官方稳定API，有SLA
- 返回数据格式统一、质量高
- RESTful，不依赖C扩展
- 累计净值(adj_nav)、收益率、持仓数据一应俱全
"""

import os
import time
import requests
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np

from .database import Database


class HiThinkCollector:
    """同花顺官方API采集器"""

    BASE_URL = "https://fuyao.aicubes.cn"

    # 我们的指数代码 → HiThink thscode 映射
    INDEX_MAP = {
        "000300": "000300.SH",
        "000905": "000905.SH",
        "000016": "000016.SH",
        "000918": "000918.SH",  # 沪深300成长
        "000919": "000919.SH",  # 沪深300价值
        "000922": "000922.SH",  # 中证红利
    }

    def __init__(self, db: Database, api_key: str = None):
        self.db = db
        self.api_key = api_key or os.environ.get("HITHINK_API_KEY", "")
        if not self.api_key:
            raise ValueError("请设置 HITHINK_API_KEY 环境变量或传入 api_key 参数")
        self.session = requests.Session()
        self.session.headers.update({
            "X-api-key": self.api_key,
            "Content-Type": "application/json",
        })

    # =================================================================
    # 基金数据
    # =================================================================

    def get_fund_profile(self, fund_code: str) -> Optional[dict]:
        """获取基金基本信息（管理人、经理、成立日）"""
        thscode = self._to_thscode(fund_code)
        try:
            r = self.session.get(
                f"{self.BASE_URL}/api/fund/profile/detail",
                params={"fund_type": "otc", "thscode": thscode},
                timeout=10,
            )
            d = r.json()
            if d["code"] == 0 and d["data"]["item"]:
                return d["data"]["item"][0]
            return None
        except Exception:
            return None

    def get_fund_nav(self, fund_code: str, range_str: str = "tyear") -> Optional[list]:
        """
        获取基金净值历史。

        Args:
            fund_code: 基金代码, 如 '004371'
            range_str: week/month/tmonth/hyear/year/twoyear/tyear/fyear

        Returns:
            list of {nav_date(ms), unit_nav, adj_nav}
        """
        thscode = self._to_thscode(fund_code)
        try:
            r = self.session.get(
                f"{self.BASE_URL}/api/fund/performance/nav",
                params={
                    "fund_type": "otc",
                    "thscode": thscode,
                    "range": range_str,
                },
                timeout=10,
            )
            d = r.json()
            if d["code"] == 0 and d["data"]["item"]:
                return d["data"]["item"]
            return None
        except Exception:
            return None

    def get_fund_nav_all(self, fund_code: str) -> Optional[list]:
        """
        获取基金全部净值历史（通过多次调用拼合）

        策略: 先取 fyear(5年)，再取 tyear(3年)补充
        """
        all_navs = []
        seen_dates = set()

        for rng in ["fyear", "tyear", "twoyear", "year"]:
            items = self.get_fund_nav(fund_code, rng)
            if items:
                for item in items:
                    if item["nav_date"] not in seen_dates:
                        seen_dates.add(item["nav_date"])
                        all_navs.append(item)
            time.sleep(0.1)

        if not all_navs:
            return None

        all_navs.sort(key=lambda x: x["nav_date"])
        return all_navs

    def get_fund_returns(self, fund_code: str) -> Optional[dict]:
        """获取基金区间收益率"""
        thscode = self._to_thscode(fund_code)
        try:
            r = self.session.get(
                f"{self.BASE_URL}/api/fund/performance/returns",
                params={"fund_type": "otc", "thscode": thscode},
                timeout=10,
            )
            d = r.json()
            if d["code"] == 0 and d["data"]["item"]:
                return d["data"]["item"][0]
            return None
        except Exception:
            return None

    def get_fund_holdings(self, fund_code: str) -> Optional[list]:
        """获取基金前十大重仓股（定期披露）"""
        thscode = self._to_thscode(fund_code)
        try:
            r = self.session.get(
                f"{self.BASE_URL}/api/fund/portfolio/holdings",
                params={"fund_type": "otc", "thscode": thscode},
                timeout=10,
            )
            d = r.json()
            if d["code"] == 0 and d["data"]["item"]:
                return d["data"]["item"]
            return None
        except Exception:
            return None

    # =================================================================
    # 指数数据
    # =================================================================

    def get_index_historical(
        self,
        index_code: str,
        start_ms: int = None,
        end_ms: int = None,
    ) -> Optional[list]:
        """
        获取指数历史日K线。

        Args:
            index_code: 指数代码, 如 '000300'
            start_ms: 起始毫秒时间戳
            end_ms: 结束毫秒时间戳

        Returns:
            list of {date_ms, open_price, high_price, low_price, close_price, volume, turnover}
        """
        thscode = self.INDEX_MAP.get(index_code, f"{index_code}.SH")

        if end_ms is None:
            end_ms = int(datetime.now().timestamp() * 1000)
        if start_ms is None:
            start_ms = int((datetime.now() - timedelta(days=365 * 10)).timestamp() * 1000)

        # 单次最多10年
        try:
            r = self.session.get(
                f"{self.BASE_URL}/api/a-share-index/prices/historical",
                params={
                    "thscode": thscode,
                    "interval": "1d",
                    "start": start_ms,
                    "end": end_ms,
                },
                timeout=15,
            )
            d = r.json()
            if d["code"] == 0 and d["data"]["item"]:
                return d["data"]["item"]
            return None
        except Exception:
            return None

    def get_index_snapshot(self, index_codes: list = None) -> Optional[list]:
        """获取指数最新行情快照（批量）"""
        if index_codes is None:
            index_codes = list(self.INDEX_MAP.keys())

        thscodes = ",".join(
            self.INDEX_MAP.get(c, f"{c}.SH") for c in index_codes
        )
        try:
            r = self.session.get(
                f"{self.BASE_URL}/api/a-share-index/prices/snapshot",
                params={"thscodes": thscodes},
                timeout=10,
            )
            d = r.json()
            if d["code"] == 0 and d["data"]["item"]:
                return d["data"]["item"]
            return None
        except Exception:
            return None

    # =================================================================
    # 估值数据
    # =================================================================

    def get_valuation_snapshot(self, stock_codes: list) -> Optional[list]:
        """批量获取A股估值快照 (PE/PB/PS/PCF)"""
        thscodes = ",".join(c for c in stock_codes)
        try:
            r = self.session.get(
                f"{self.BASE_URL}/api/a-share/valuations/snapshot",
                params={"thscodes": thscodes},
                timeout=10,
            )
            d = r.json()
            if d["code"] == 0 and d["data"]["item"]:
                return d["data"]["item"]
            return None
        except Exception:
            return None

    # =================================================================
    # 批量采集 + 入库
    # =================================================================

    def collect_fund_batch(
        self,
        fund_codes: list,
        progress_callback=None,
    ) -> dict:
        """
        批量采集基金数据（NAV+基础信息+收益率），写入数据库。

        Returns:
            dict: {success: N, fail: N, skipped: N}
        """
        stats = {"success": 0, "fail": 0, "skipped": 0}
        total = len(fund_codes)

        for i, code in enumerate(fund_codes):
            # 检查是否已经有净值数据
            existing = self.db.get_fund_nav(code)
            if existing and len(existing) > 100:
                stats["skipped"] += 1
                if progress_callback:
                    progress_callback(i + 1, total, stats)
                continue

            try:
                # 1. 采集基金基本信息
                profile = self.get_fund_profile(code)
                if profile:
                    self._save_profile_to_db(code, profile)

                # 2. 采集净值历史
                nav_items = self.get_fund_nav_all(code)
                if nav_items:
                    self._save_nav_to_db(code, nav_items)

                # 3. 采集收益率
                returns = self.get_fund_returns(code)
                if returns:
                    self._save_returns_to_db(code, returns)

                stats["success"] += 1
            except Exception:
                stats["fail"] += 1

            if progress_callback:
                progress_callback(i + 1, total, stats)
            time.sleep(0.15)  # 礼貌间隔

        return stats

    def collect_index_batch(self) -> dict:
        """采集所有支持指数的历史K线"""
        stats = {"success": 0, "fail": 0}

        for code in self.INDEX_MAP:
            try:
                items = self.get_index_historical(code)
                if items:
                    self._save_index_to_db(code, items)
                    stats["success"] += 1
                else:
                    stats["fail"] += 1
            except Exception:
                stats["fail"] += 1
            time.sleep(0.2)

        return stats

    # =================================================================
    # 入库辅助
    # =================================================================

    def _save_profile_to_db(self, fund_code: str, profile: dict):
        """将基金基本信息写入 fund_info 表"""
        estab_ms = profile.get("estab_date")
        estab_str = ""
        if estab_ms:
            try:
                estab_str = datetime.fromtimestamp(estab_ms / 1000).strftime("%Y-%m-%d")
            except Exception:
                pass

        self.db.upsert_fund_info({
            "fund_code": fund_code,
            "fund_name": profile.get("fund_name", ""),
            "company_name": profile.get("mgmt_name", ""),
            "manager_name": profile.get("manager_name", ""),
            "establish_date": estab_str,
        })

    def _save_nav_to_db(self, fund_code: str, items: list):
        """将净值数据批量写入 fund_nav 表"""
        records = []
        for item in items:
            nav_date_ms = item.get("nav_date")
            if nav_date_ms:
                try:
                    nav_date = datetime.fromtimestamp(nav_date_ms / 1000).strftime("%Y-%m-%d")
                except Exception:
                    continue
            else:
                continue

            unit_nav = item.get("unit_nav")
            adj_nav = item.get("adj_nav", 0) or 0
            # 计算日涨跌幅
            daily_return = 0.0

            if unit_nav is not None:
                records.append((str(fund_code), nav_date, float(unit_nav), float(adj_nav), daily_return))

        if records:
            self.db.insert_nav_batch(records)

    def _save_returns_to_db(self, fund_code: str, returns: dict):
        """保存基金收益率到 fund_info 表（更新字段暂略，后续扩展）"""
        # MVP: 收益率暂不单独存储，在评分时从净值计算
        pass

    def _save_index_to_db(self, index_code: str, items: list):
        """将指数K线数据写入 index_daily 表"""
        records = []
        for item in items:
            ms = item.get("date_ms")
            if not ms:
                continue
            try:
                trade_date = datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
            except Exception:
                continue

            records.append({
                "index_code": index_code,
                "trade_date": trade_date,
                "close": item.get("close_price", 0) or 0,
                "volume": item.get("volume", 0) or 0,
                "pe": 0, "pb": 0, "pe_percentile": 0, "pb_percentile": 0,
            })

        if records:
            self._insert_index_batch(records)

    def _insert_index_batch(self, records: list):
        import sqlite3
        cursor = self.db.conn.cursor()
        cursor.executemany("""
            INSERT OR IGNORE INTO index_daily
            (index_code, trade_date, close, volume, pe, pb, pe_percentile, pb_percentile)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (r["index_code"], r["trade_date"], r["close"], r["volume"],
             r["pe"], r["pb"], r["pe_percentile"], r["pb_percentile"])
            for r in records
        ])
        self.db.conn.commit()

    # =================================================================
    # 工具方法
    # =================================================================

    @staticmethod
    def _to_thscode(fund_code: str) -> str:
        """将纯6位代码转换为 HiThink thscode 格式"""
        code = str(fund_code).strip()
        if "." in code:
            return code
        return f"{code}.OF"

    @staticmethod
    def ms_to_date(ms: int) -> str:
        """毫秒时间戳 → 日期字符串"""
        try:
            return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        except Exception:
            return ""


def quick_test(api_key: str):
    """测试 HiThink API 连接"""
    import requests

    HEADERS = {"X-api-key": api_key}
    print("=" * 60)
    print("🧪 HiThink API 连接测试")
    print("=" * 60)

    tests = [
        ("基金净值", f"https://fuyao.aicubes.cn/api/fund/performance/nav?fund_type=otc&thscode=004371.OF&range=month"),
        ("基金资料", f"https://fuyao.aicubes.cn/api/fund/profile/detail?fund_type=otc&thscode=004371.OF"),
        ("指数行情", f"https://fuyao.aicubes.cn/api/a-share-index/prices/snapshot?thscodes=000300.SH,000905.SH"),
        ("基金收益", f"https://fuyao.aicubes.cn/api/fund/performance/returns?fund_type=otc&thscode=004371.OF"),
    ]

    all_pass = True
    for name, url in tests:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            d = r.json()
            if d["code"] == 0:
                n = len(d["data"]["item"]) if d["data"].get("item") else "OK"
                print(f"  ✅ {name}: {n} 条")
            else:
                print(f"  ❌ {name}: code={d['code']} {d.get('message','')}")
                all_pass = False
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            all_pass = False

    print("=" * 60)
    if all_pass:
        print("✅ HiThink API 全部通过！可以作为主数据源。")
    else:
        print("⚠️ 部分测试失败")
    print("=" * 60)
    return all_pass


if __name__ == "__main__":
    key = os.environ.get("HITHINK_API_KEY", "")
    if not key:
        key = input("请输入 HiThink API Key: ").strip()
    quick_test(key)
