"""
定投管理单元测试：频率计算、到期判断、执行推进（临时库）。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.dca import DcaManager
from src.data.database import Database


class TestNextRunDate(unittest.TestCase):
    """频率 → 下一期日期计算（纯逻辑）"""

    def test_weekly(self):
        self.assertEqual(DcaManager.next_run_date("weekly", "2026-08-24"), "2026-08-31")

    def test_biweekly(self):
        self.assertEqual(DcaManager.next_run_date("biweekly", "2026-08-24"), "2026-09-07")

    def test_monthly(self):
        self.assertEqual(DcaManager.next_run_date("monthly", "2026-08-24"), "2026-09-23")

    def test_unknown_frequency_defaults_weekly(self):
        self.assertEqual(DcaManager.next_run_date("daily", "2026-08-24"), "2026-08-31")


class TestDcaFlow(unittest.TestCase):
    """定投计划全流程（临时库）"""

    def _make_db(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = Database(path)
        db.add_dca_plan({
            "fund_code": "016453",
            "fund_name": "测试定投基金",
            "amount_per_period": 10,
            "frequency": "weekly",
            "start_date": "2026-08-24",
            "next_run_date": "2026-08-24",
        })
        return db, path

    def test_due_detection(self):
        db, path = self._make_db()
        try:
            mgr = DcaManager(db)
            # 08-31 已到 08-24 之后 → 到期
            self.assertTrue(mgr.get_status("2026-08-31")[0]["due"])
            # 08-20 早于开始 → 未到期
            self.assertFalse(mgr.get_status("2026-08-20")[0]["due"])
        finally:
            db.close()
            os.unlink(path)

    def test_execute_advances_plan(self):
        db, path = self._make_db()
        try:
            mgr = DcaManager(db)
            r = mgr.execute_installment(1, buy_date="2026-08-31")
            self.assertTrue(r["ok"])
            self.assertEqual(r["period"], 1)

            plan = db.get_dca_plans()[0]
            self.assertEqual(plan["next_run_date"], "2026-09-07")  # weekly +7
            self.assertEqual(plan["total_periods"], 1)
            self.assertEqual(plan["total_amount"], 10.0)

            # 同时应生成一笔买入记录
            holdings = db.get_current_holdings()
            self.assertEqual(len(holdings), 1)
            self.assertEqual(holdings[0]["buy_amount"], 10.0)
        finally:
            db.close()
            os.unlink(path)

    def test_execute_invalid_plan(self):
        db, path = self._make_db()
        try:
            mgr = DcaManager(db)
            r = mgr.execute_installment(999)
            self.assertIn("error", r)
        finally:
            db.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
