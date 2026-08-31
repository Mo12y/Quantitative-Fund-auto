"""
基金质量筛选器单元测试：纯逻辑 + 临时库合成数据验证风险标签。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.fund_scorer import FundScreener
from src.data.database import Database

CODE = "999999"


def make_db_with_nav(
    daily_ret: float = 0.001,
    days: int = 250,
    drop: tuple = None,
    surge_from: int = None,
    surge_daily: float = 0.0065,
    fee: float = 0.6,
    size: float = 10.0,
):
    """
    创建临时库并写入一只合成基金。

    Args:
        daily_ret: 每日收益率（平稳段）
        drop: (起始idx, 总跌幅%) —— 模拟大跌
        surge_from: 从该 idx 起进入暴涨段（用于追涨测试）
        surge_daily: 暴涨段每日收益率
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    db.upsert_fund_info({
        "fund_code": CODE,
        "fund_name": "测试基金",
        "fund_type": "混合型",
        "establish_date": "2020-01-01",
        "fund_size": size,
        "mgt_fee": fee,
        "custodian_fee": 0.2,
    })

    nav = 1.0
    records = []
    start = pd.Timestamp("2024-01-01")
    for i in range(days):
        if drop and drop[0] <= i < drop[0] + 10:
            nav *= (1 - drop[1] / 10)          # 10 天跌掉 drop[1]
        elif surge_from is not None and i >= surge_from:
            nav *= (1 + surge_daily)           # 暴涨段
        else:
            nav *= (1 + daily_ret)
        d = (start + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        records.append((CODE, d, round(nav, 4), round(nav, 4), round(daily_ret, 6)))
    db.insert_nav_batch(records)
    return db, path


class TestRiskLabeling(unittest.TestCase):
    """风险标签判定（核心逻辑，临时库合成数据）"""

    def _screen(self, db, fee=None, size=None):
        screener = FundScreener(db)
        info = {
            "fund_code": CODE,
            "fund_name": "测试基金",
            "fund_type": "混合型",
            "establish_date": "2020-01-01",
            "fund_size": size if size is not None else 10.0,
            "mgt_fee": fee if fee is not None else 0.6,
            "custodian_fee": 0.2,
        }
        return screener._screen_single_fund(CODE, info)

    def test_healthy_fund_is_green(self):
        """平稳上涨 + 低费率 + 规模适中 → 🟢 稳健"""
        db, path = make_db_with_nav()
        try:
            r = self._screen(db)
            self.assertIsNotNone(r)
            self.assertEqual(r["risk_label"], "🟢 稳健")
        finally:
            db.close()
            os.unlink(path)

    def test_high_fee_is_high_risk(self):
        """总费率 2.7% > 2.0% → 费率检查失败 → 🔴 高风险"""
        db, path = make_db_with_nav(fee=2.5)  # 2.5 + 0.2 = 2.7%
        try:
            r = self._screen(db, fee=2.5)  # info dict 需显式传 fee
            self.assertEqual(r["risk_label"], "🔴 高风险")
            self.assertTrue(any("费率" in w for w in r["risk_reasons"]))
        finally:
            db.close()
            os.unlink(path)

    def test_high_drawdown_is_high_risk(self):
        """第100天起跌50% → 回撤超35%阈值 → 不合格/高风险"""
        db, path = make_db_with_nav(drop=(100, 0.5))  # 10天跌50% → 回撤约40%
        try:
            r = self._screen(db)
            self.assertIsNotNone(r)
            self.assertIn(r["risk_label"], ("不合格", "🔴 高风险"))
            self.assertTrue(any("回撤" in w for w in r["risk_reasons"]))
        finally:
            db.close()
            os.unlink(path)

    def test_momentum_surge_warns(self):
        """最后63天暴涨约50% → 追涨警告 → 🟡 注意"""
        db, path = make_db_with_nav(daily_ret=0.0001, surge_from=187)
        try:
            r = self._screen(db)
            self.assertIsNotNone(r)
            self.assertEqual(r["risk_label"], "🟡 注意")
            self.assertTrue(any("追涨" in w for w in r["risk_reasons"]))
        finally:
            db.close()
            os.unlink(path)

    def test_insufficient_nav_returns_none(self):
        """净值不足60条 → 返回 None（不评价）"""
        db, path = make_db_with_nav(days=30)
        try:
            r = self._screen(db)
            self.assertIsNone(r)
        finally:
            db.close()
            os.unlink(path)


class TestThresholds(unittest.TestCase):
    """质量门槛配置合理性（纯逻辑）"""

    def test_thresholds_are_sane(self):
        t = FundScreener.THRESHOLDS
        self.assertGreater(t["min_age_months"], 0)
        self.assertLess(t["min_size_yi"], t["max_size_yi"])
        self.assertLess(t["warn_total_fee"], t["max_total_fee"])
        self.assertGreater(t["momentum_warning"], 20)


class TestPoolSummary(unittest.TestCase):
    """筛选池统计摘要（纯逻辑）"""

    def test_empty_df(self):
        screener = FundScreener(None)
        s = screener.get_pool_summary(pd.DataFrame())
        self.assertEqual(s["total"], 0)

    def test_with_data(self):
        screener = FundScreener(None)
        df = pd.DataFrame([
            {"risk_label": "🟢 稳健", "mgt_fee": 0.6},
            {"risk_label": "🟢 稳健", "mgt_fee": 1.0},
            {"risk_label": "🟡 注意", "mgt_fee": 1.8},
        ])
        s = screener.get_pool_summary(df)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_risk"]["🟢 稳健"], 2)
        self.assertAlmostEqual(s["avg_fee"], 1.13, places=2)


if __name__ == "__main__":
    unittest.main()
