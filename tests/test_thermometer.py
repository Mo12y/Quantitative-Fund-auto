"""
市场温度计单元测试 —— 覆盖不依赖数据库的纯逻辑。

运行方式:
    python -m unittest discover tests
    # 或
    python tests/test_thermometer.py
"""

import os
import sys
import unittest

# 确保项目根目录在 sys.path 中，以便 import src 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.thermometer import MarketThermometer


class TestTemperatureClassification(unittest.TestCase):
    """温度数值 → 等级/建议/目标仓位 的映射逻辑"""

    def setUp(self):
        # db 传 None：_classify_temperature 不访问数据库
        self.t = MarketThermometer(db=None)

    def test_cold_range(self):
        """0-20° 极冷，目标权益仓位 70%"""
        level, desc, action, equity = self.t._classify_temperature(10)
        self.assertEqual(level, "cold")
        self.assertEqual(desc, "🧊 极冷")
        self.assertEqual(equity, 0.70)

    def test_normal_range(self):
        """40-60° 适中，目标权益仓位 35%"""
        level, desc, action, equity = self.t._classify_temperature(50)
        self.assertEqual(level, "normal")
        self.assertEqual(equity, 0.35)

    def test_hot_range(self):
        """80-100° 过热，目标权益仓位 5%"""
        level, desc, action, equity = self.t._classify_temperature(90)
        self.assertEqual(level, "hot")
        self.assertEqual(equity, 0.05)

    def test_lower_boundary_inclusive(self):
        """边界值：20° 应落入 cool（20 <= t < 40）"""
        level, _, _, _ = self.t._classify_temperature(20)
        self.assertEqual(level, "cool")

    def test_upper_boundary_inclusive(self):
        """边界值：80° 应落入 hot（80 <= t < 101）"""
        level, _, _, _ = self.t._classify_temperature(80)
        self.assertEqual(level, "hot")


class TestDivergenceDetection(unittest.TestCase):
    """PE/PB/ERP 三维度分歧检测逻辑"""

    def setUp(self):
        self.t = MarketThermometer(db=None)

    def test_consistent_signals(self):
        """三维度接近 → 一致"""
        r = self.t._detect_divergence(50, 48, 52)
        self.assertEqual(r["level"], "一致")

    def test_mild_divergence(self):
        """最大差值 15-30 → 轻微分歧"""
        r = self.t._detect_divergence(70, 50, 55)
        self.assertEqual(r["level"], "轻微分歧")

    def test_strong_divergence(self):
        """最大差值 >= 30 → 显著分歧"""
        r = self.t._detect_divergence(80, 30, 50)
        self.assertEqual(r["level"], "显著分歧")

    def test_pe_high_pb_low(self):
        """PE 偏高 PB 偏低 → 显著分歧，且给出盈利下行期提示"""
        r = self.t._detect_divergence(85, 30, 50)
        self.assertEqual(r["level"], "显著分歧")
        self.assertIn("盈利下行", r["message"])


if __name__ == "__main__":
    unittest.main()
