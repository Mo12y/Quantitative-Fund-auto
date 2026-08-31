"""
严谨回测引擎单元测试。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.backtest import (
    CostModel,
    RigorousBacktest,
    alpha_t_test,
    compute_max_drawdown,
)


class TestCostModel(unittest.TestCase):
    """交易成本模型"""

    def setUp(self):
        self.cost = CostModel()

    def test_redemption_tiered(self):
        """赎回费按持有天数分层"""
        self.assertEqual(self.cost.redemption_rate(3), 0.015)     # <7 天 1.5%
        self.assertEqual(self.cost.redemption_rate(7), 0.005)     # 7-30 天 0.5%
        self.assertEqual(self.cost.redemption_rate(30), 0.0)      # >30 天免费
        self.assertEqual(self.cost.redemption_rate(365), 0.0)

    def test_round_trip_includes_management_fee(self):
        """往返成本 = 申购费 + 赎回费 + 持有期管理费摊销"""
        # 持有 365 天：0.15% 申购 + 0 赎回 + 1.5% 管理费
        self.assertAlmostEqual(self.cost.round_trip_cost(365), 0.0015 + 0.015, places=6)
        # 持有 3 天：0.15% 申购 + 1.5% 赎回 + 少量管理费
        c = self.cost.round_trip_cost(3)
        self.assertAlmostEqual(c, 0.0015 + 0.015 + 0.015 * 3 / 365, places=6)


class TestMaxDrawdown(unittest.TestCase):
    def test_known_sequence(self):
        """已知序列：120 → 80 回撤 33.3%"""
        nav = np.array([100, 120, 90, 110, 80])
        self.assertAlmostEqual(compute_max_drawdown(nav), 33.33, places=1)

    def test_monotonic_up_no_drawdown(self):
        nav = np.array([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(compute_max_drawdown(nav), 0.0)


class TestAlphaTTest(unittest.TestCase):
    def test_consistent_outperformance_significant(self):
        """策略每月稳定跑赢基准 → alpha 显著为正"""
        strategy = np.full(36, 2.0)
        benchmark = np.full(36, 0.5)
        result = alpha_t_test(strategy, benchmark)
        self.assertTrue(result["significant"])
        self.assertGreater(result["t_stat"], 0)
        self.assertAlmostEqual(result["mean_alpha"], 1.5, places=6)

    def test_identical_series_not_significant(self):
        """策略与基准完全一致 → 不显著"""
        np.random.seed(42)
        strategy = np.random.normal(1.0, 1.0, 36)
        result = alpha_t_test(strategy, strategy.copy())
        self.assertFalse(result["significant"])


class TestNearestTempLookAheadFree(unittest.TestCase):
    """_nearest_temp 必须无未来函数：只允许返回 <= 目标日期的温度"""

    def test_never_uses_future_temp(self):
        temp_map = {
            "2024-01-01": 10.0,   # 目标日期之前
            "2024-06-01": 80.0,   # 目标日期之后（未来！）
            "2024-12-31": 90.0,
        }
        t = RigorousBacktest._nearest_temp(temp_map, "2024-03-01")
        # 只能命中 1月1日的 10.0，绝不能用 6月/12月的未来数据
        self.assertEqual(t, 10.0)

    def test_exact_date_match(self):
        temp_map = {"2024-03-01": 42.0}
        t = RigorousBacktest._nearest_temp(temp_map, "2024-03-01")
        self.assertEqual(t, 42.0)

    def test_empty_map_fallback(self):
        self.assertEqual(RigorousBacktest._nearest_temp({}, "2024-03-01"), 50.0)


if __name__ == "__main__":
    unittest.main()
