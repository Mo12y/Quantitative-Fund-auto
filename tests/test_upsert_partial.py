"""fund_info upsert 局部更新保护 —— 批次 4.2（根因）。

两条采集路径各采一半字段（批量列表有费率/申购状态，enrich 有规模/公司/经理），
旧版全字段覆盖导致**谁后跑谁清空对方的数据**。修复后：
- 未提供的字段（NULL）不覆盖旧值；
- 数字字段的 0（费率/规模/年限的缺列默认值）也不覆盖旧值；
- `redeem_fee` 原有的 COALESCE 行为被包含。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.collector import DataCollector
from src.data.database import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "upsert.db"))
    yield d
    d.close()


def _info(db, code="000001"):
    return dict(db.conn.execute("SELECT * FROM fund_info WHERE fund_code=?", (code,)).fetchone())


class TestPartialUpsert:
    def test_enrich_does_not_clear_batch_fields(self, db):
        """批量路径先落费率，enrich 再补规模 → 费率必须还在（4.2 根因）。"""
        db.upsert_fund_info({"fund_code": "000001", "fund_name": "某混合A",
                             "fund_type": "混合型", "mgt_fee": 1.5,
                             "purchase_status": "开放申购"})
        db.upsert_fund_info({"fund_code": "000001", "fund_size": 49.59,
                             "company_name": "某基金公司", "manager_name": "张三",
                             "establish_date": "2015-01-01"})
        row = _info(db)
        assert row["mgt_fee"] == 1.5                    # 旧版这里被清成 NULL
        assert row["purchase_status"] == "开放申购"     # 旧版这里被清成 NULL
        assert row["fund_size"] == 49.59 and row["manager_name"] == "张三"

    def test_batch_does_not_clear_enrich_fields(self, db):
        """反方向：enrich 先落规模，批量再跑 → 规模必须还在。"""
        db.upsert_fund_info({"fund_code": "000002", "fund_size": 30.0,
                             "establish_date": "2018-06-01"})
        db.upsert_fund_info({"fund_code": "000002", "fund_name": "某债券C",
                             "fund_type": "债券型-长债", "mgt_fee": 0.3,
                             "purchase_status": "限大额"})
        row = _info(db, "000002")
        assert row["fund_size"] == 30.0 and row["establish_date"] == "2018-06-01"
        assert row["mgt_fee"] == 0.3 and row["purchase_status"] == "限大额"

    def test_null_never_overwrites(self, db):
        db.upsert_fund_info({"fund_code": "000003", "mgt_fee": 1.2, "redeem_fee": "0.10%"})
        db.upsert_fund_info({"fund_code": "000003"})          # 什么都不带
        row = _info(db, "000003")
        assert row["mgt_fee"] == 1.2 and row["redeem_fee"] == "0.10%"

    def test_zero_fee_does_not_overwrite(self, db):
        """费率类字段的 0 = 缺列默认值（"0%" 被解析成 0），不得覆盖已有真值。"""
        db.upsert_fund_info({"fund_code": "000004", "mgt_fee": 1.5})
        db.upsert_fund_info({"fund_code": "000004", "mgt_fee": 0})
        db.upsert_fund_info({"fund_code": "000004", "mgt_fee": 0.0})
        assert _info(db, "000004")["mgt_fee"] == 1.5

    def test_zero_size_does_not_overwrite(self, db):
        db.upsert_fund_info({"fund_code": "000005", "fund_size": 88.0})
        db.upsert_fund_info({"fund_code": "000005", "fund_size": 0})
        assert _info(db, "000005")["fund_size"] == 88.0

    def test_empty_string_does_not_overwrite(self, db):
        db.upsert_fund_info({"fund_code": "000006", "fund_name": "原名", "fund_type": "混合型"})
        db.upsert_fund_info({"fund_code": "000006", "fund_name": "", "fund_type": None})
        row = _info(db, "000006")
        assert row["fund_name"] == "原名" and row["fund_type"] == "混合型"

    def test_new_nonzero_value_overwrites(self, db):
        db.upsert_fund_info({"fund_code": "000007", "mgt_fee": 1.5})
        db.upsert_fund_info({"fund_code": "000007", "mgt_fee": 0.8})
        assert _info(db, "000007")["mgt_fee"] == 0.8

    def test_insert_still_writes_given_values(self, db):
        """新基金：INSERT 不受保护影响，给什么写什么（0 仍是 0）。"""
        db.upsert_fund_info({"fund_code": "000008", "fund_name": "新基金",
                             "mgt_fee": 0})
        assert _info(db, "000008")["mgt_fee"] == 0


class TestParseFee:
    def test_normal(self):
        assert DataCollector._parse_fee("0.15%") == 0.15
        assert DataCollector._parse_fee("1.50%") == 1.50
        assert DataCollector._parse_fee("0%") == 0.0

    def test_failure_returns_none_not_zero(self):
        """采集失败必须返回 None —— 0 会被下游当真零费率（4.2.3）。"""
        assert DataCollector._parse_fee("") is None
        assert DataCollector._parse_fee(None) is None
        assert DataCollector._parse_fee("--") is None
        assert DataCollector._parse_fee("费率未知") is None
