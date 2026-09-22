"""全市场当日快照解析（parse_daily_snapshot）—— Step 1 cmd_snapshot 的纯函数核心。

快照接口（fund_open_fund_daily_em）返回宽表：带日期的净值列 + 申购状态 + 手续费。
这里用合成 DataFrame 离线验证解析规则，不触网：
- 两个交易日都入库，日增长率只挂在最新一天；
- 货币基金（无单位净值）跳过；
- 累计净值越界归 0；费率缺失保持 None（交给 upsert 保留旧值）；
- 'nan' 名称/状态不覆盖库内已有好值。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.collector import parse_daily_snapshot
from src.data.database import Database


def _snapshot_df():
    """模拟 fund_open_fund_daily_em 的宽表结构（2 只普通基金 + 1 只货币）。"""
    return pd.DataFrame([
        # 普通基金：两天净值齐全
        ["000001", "成长混合A", 1.500, 1.800, 1.480, 1.780, 0.02, 1.35, "开放申购", "开放赎回", "0.15%"],
        # 累计净值越界（应为脏数据 → 归 0）
        ["000002", "稳健债券C", 1.050, 250.0, 1.048, 1.200, 0.002, 0.19, "限大额", "开放赎回", "--"],
        # 货币基金：无单位净值口径 → 跳过 nav，但 info 仍更新
        ["000003", "现金货币B", None, None, None, None, 0.0, 0.0, "开放申购", "开放赎回", "0.00%"],
    ], columns=[
        "基金代码", "基金简称",
        "2026-09-18-单位净值", "2026-09-18-累计净值",
        "2026-09-17-单位净值", "2026-09-17-累计净值",
        "日增长值", "日增长率", "申购状态", "赎回状态", "手续费",
    ])


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "snap.db"))
    yield d
    d.close()


class TestParseDailySnapshot:
    def test_two_dates_and_latest_return(self):
        records, info, dates = parse_daily_snapshot(_snapshot_df())
        assert dates == ["2026-09-17", "2026-09-18"]
        by_key = {(r[0], r[1]): r for r in records}
        # 000001 两天都在；日增长率只挂最新一天
        assert by_key[("000001", "2026-09-18")] == ("000001", "2026-09-18", 1.5, 1.8, 1.35)
        assert by_key[("000001", "2026-09-17")][4] == 0.0

    def test_bad_acc_nav_zeroed(self):
        records, _, _ = parse_daily_snapshot(_snapshot_df())
        by_key = {(r[0], r[1]): r for r in records}
        assert by_key[("000002", "2026-09-18")][3] == 0.0   # 250 越界 → 0
        assert by_key[("000002", "2026-09-17")][3] == 1.2   # 合法值保留

    def test_money_fund_skipped_in_nav_but_info_kept(self):
        records, info, _ = parse_daily_snapshot(_snapshot_df())
        assert all(r[0] != "000003" for r in records)        # 货币基金无单位净值 → 跳过
        row = next(i for i in info if i["fund_code"] == "000003")
        assert row["purchase_status"] == "开放申购"          # 但申购状态仍可用
        # ⚠️ 2026-09-22 正名：该列是**手续费（申购费）**，不是管理费 → 键名改为 purchase_fee
        assert row["purchase_fee"] == 0.0  # '0.00%' 解析为 0；upsert 侧 0 不覆盖（_has_new_value）

    def test_fee_missing_stays_none(self):
        _, info, _ = parse_daily_snapshot(_snapshot_df())
        row = next(i for i in info if i["fund_code"] == "000002")
        assert row["purchase_fee"] is None                   # '--' → None，不清旧值

    def test_info_does_not_clear_existing_values(self, db):
        """upsert 集成：快照行里 fee=None 时，库内已有费率必须保留（批次 4.2 语义）。"""
        db.upsert_fund_info({"fund_code": "000002", "fund_name": "稳健债券C",
                             "mgt_fee": 0.30, "purchase_status": "开放申购"})
        _, info, _ = parse_daily_snapshot(_snapshot_df())
        with db.immediate():
            for f in info:
                db.upsert_fund_info(f, commit=False)  # 快照路径：单事务批量
        row = dict(db.conn.execute(
            "SELECT * FROM fund_info WHERE fund_code='000002'").fetchone())
        assert row["mgt_fee"] == 0.30                        # 旧费率未被 None 清掉
        assert row["purchase_status"] == "限大额"            # 新状态正常覆盖

    def test_upsert_default_commit_unchanged(self, db):
        """commit 参数默认 True，既有调用方行为不变。"""
        db.upsert_fund_info({"fund_code": "000009", "fund_name": "某基金", "mgt_fee": 1.0})
        row = dict(db.conn.execute(
            "SELECT * FROM fund_info WHERE fund_code='000009'").fetchone())
        assert row["mgt_fee"] == 1.0
