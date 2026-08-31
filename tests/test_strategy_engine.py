"""
策略引擎单元测试：覆盖 should_rebalance 的温度阈值触发逻辑（注入假温度计）。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.strategy_engine import StrategyEngine


class FakeThermometer:
    """假温度计：按调用顺序返回预设温度"""

    def __init__(self, temps):
        self.temps = list(temps)
        self._i = 0

    def get_temperature(self):
        t = self.temps[min(self._i, len(self.temps) - 1)]
        self._i += 1
        return {"temperature": t}


class TestShouldRebalance(unittest.TestCase):
    def _engine(self, temps):
        # db=None：should_rebalance 不访问数据库，仅依赖 thermometer
        engine = StrategyEngine(None)
        engine.thermometer = FakeThermometer(temps)
        return engine

    def test_first_call_always_rebalances(self):
        """首次评估 → 总是建立基准仓位"""
        engine = self._engine([50])
        ok, reason = engine.should_rebalance()
        self.assertTrue(ok)
        self.assertIn("首次", reason)

    def test_small_temp_change_holds(self):
        """温度变化 < 阈值(15°) → 不调仓"""
        engine = self._engine([50, 52])
        engine.should_rebalance()          # 首次: 建立基准 50°
        ok, reason = engine.should_rebalance()  # 变化 2° < 15°
        self.assertFalse(ok)
        self.assertIn("保持不动", reason)

    def test_large_temp_change_rebalances(self):
        """温度变化 >= 阈值(15°) → 触发调仓"""
        engine = self._engine([50, 70])
        engine.should_rebalance()          # 基准 50°
        ok, reason = engine.should_rebalance()  # 变化 20° >= 15°
        self.assertTrue(ok)
        self.assertIn("触发调仓", reason)

    def test_direction_in_reason(self):
        """升温/降温方向应体现在原因里"""
        engine = self._engine([50, 75])
        engine.should_rebalance()
        ok, reason = engine.should_rebalance()
        self.assertIn("升温", reason)


if __name__ == "__main__":
    unittest.main()
