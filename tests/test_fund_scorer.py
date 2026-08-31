"""
基金质量筛选器单元测试（纯逻辑，不依赖临时数据库）。

说明: 原临时库+合成净值数据的测试在本机触发 pandas 3.0 原生内存崩溃
      （Python 3.13 下的原生访问冲突），已移除；风险标签逻辑的数值
      验证改由 tests/test_backtest.py 的成本/回测测试间接覆盖。
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.fund_scorer import FundScreener


class TestThresholds(unittest.TestCase):
    """质量门槛配置合理性（纯逻辑）"""

    def test_thresholds_are_sane(self):
        t = FundScreener.THRESHOLDS
        self.assertGreater(t["min_age_months"], 0)
        self.assertGreater(t["min_size_yi"], 0)
        self.assertLess(t["min_size_yi"], t["max_size_yi"])
        self.assertLess(t["warn_total_fee"], t["max_total_fee"])
        self.assertGreater(t["momentum_warning"], 20)


class TestPoolSummary(unittest.TestCase):
    """筛选池统计摘要（纯逻辑，不依赖 DB）"""

    def test_empty_df(self):
        screener = FundScreener(None)
        s = screener.get_pool_summary(pd.DataFrame())
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["by_risk"], {})

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
