"""
策略引擎单元测试：覆盖 should_rebalance 的温度阈值触发逻辑（注入假温度计）。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import time
import unittest
from datetime import date, timedelta

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


class _StateDB:
    """最小状态库：实现 analysis_snapshot 的读写 + 过期语义。"""

    def __init__(self):
        self._s = {}

    def get_analysis_snapshot(self, key, max_age_sec=None):
        item = self._s.get(key)
        if not item:
            return None
        ts, payload = item
        if max_age_sec and (time.time() - ts) > max_age_sec:
            return None
        return payload

    def set_analysis_snapshot(self, key, payload):
        self._s[key] = (time.time(), payload)


class _CostDB:
    """假库：给成本估算提供持仓/净值/基金信息。"""

    def __init__(self, holdings, navs, infos):
        self._h = holdings
        self._n = navs
        self._i = infos

    def get_current_holdings(self):
        return self._h

    def get_latest_fund_nav(self, code):
        return {"unit_nav": self._n[code]}

    def get_fund_info(self, code):
        return self._i.get(code, {})


class _FixedTemp:
    def __init__(self, target_equity_pct):
        self._t = target_equity_pct

    def get_temperature(self):
        return {"target_equity_pct": self._t}


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


class TestStatePersistence(unittest.TestCase):
    """A5: 上次温度持久化，跨实例/跨次运行阈值判断才成立"""

    def test_second_instance_is_not_first_eval(self):
        db = _StateDB()
        e1 = StrategyEngine(db)
        e1.thermometer = FakeThermometer([50])
        ok1, r1 = e1.should_rebalance()
        self.assertTrue(ok1)
        self.assertIn("首次", r1)

        # 新实例（模拟再次运行 CLI）：不得再报"首次评估"
        e2 = StrategyEngine(db)
        e2.thermometer = FakeThermometer([50])
        ok2, r2 = e2.should_rebalance()
        self.assertFalse(ok2)
        self.assertIn("保持不动", r2)

    def test_large_change_across_instances_triggers(self):
        db = _StateDB()
        e1 = StrategyEngine(db)
        e1.thermometer = FakeThermometer([50])
        e1.should_rebalance()

        e2 = StrategyEngine(db)
        e2.thermometer = FakeThermometer([70])  # 变化 20° ≥ 15
        ok, reason = e2.should_rebalance()
        self.assertTrue(ok)
        self.assertIn("触发调仓", reason)

    def test_expired_state_is_treated_as_first(self):
        db = _StateDB()
        # 手工塞一条 10 天前的状态（超过 7 天窗口）
        db._s[StrategyEngine.STATE_KEY] = (time.time() - 10 * 86400, {"temp": 50.0, "date": "2026-09-01"})
        e = StrategyEngine(db)
        e.thermometer = FakeThermometer([50])
        ok, reason = e.should_rebalance()
        self.assertTrue(ok)
        self.assertIn("首次", reason)

    def test_db_none_still_works_in_memory(self):
        """db=None 的纯逻辑用法：仍按内存基准判断（不崩溃）"""
        e = StrategyEngine(None)
        e.thermometer = FakeThermometer([50, 52])
        e.should_rebalance()
        ok, reason = e.should_rebalance()
        self.assertFalse(ok)


class TestRebalanceCostEstimate(unittest.TestCase):
    """A6: 费率取单一真源，且只对实际调仓成交额计费"""

    def _engine(self, holdings, navs, infos, target_eq):
        e = StrategyEngine(_CostDB(holdings, navs, infos))
        e.thermometer = _FixedTemp(target_eq)
        return e

    def test_only_short_hold_charged_and_only_traded_portion(self):
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=60)).isoformat()
        holdings = [
            {"fund_code": "A", "fund_name": "新买的", "shares": 500, "buy_amount": 500,
             "buy_date": today},
            {"fund_code": "B", "fund_name": "拿久了", "shares": 500, "buy_amount": 500,
             "buy_date": old},
        ]
        navs = {"A": 1.0, "B": 1.0}
        infos = {"A": {"fund_type": "混合型"}, "B": {"fund_type": "混合型"}}
        # 当前权益 100%（1000 元）→ 目标 50% → 实际要卖 500 元，按市值等比例各卖 250
        e = self._engine(holdings, navs, infos, target_eq=50.0)
        r = e._estimate_rebalance_cost()

        self.assertAlmostEqual(r["traded_amount"], 500.0, places=2)
        # A 持有 0 天 → 1.5%；B 持有 60 天 → 0%
        self.assertAlmostEqual(r["total_cost"], 250 * 0.015, places=2)
        rates = {b["fund_name"]: b["fee_rate"] for b in r["breakdown"]}
        self.assertEqual(rates["新买的"], "1.50%")
        self.assertEqual(rates["拿久了"], "0.00%")

    def test_buy_side_uses_purchase_fee(self):
        holdings = [{"fund_code": "A", "fund_name": "A", "shares": 500, "buy_amount": 500,
                     "buy_date": date.today().isoformat()}]
        e = self._engine(holdings, {"A": 1.0}, {"A": {"fund_type": "混合型"}}, target_eq=100.0)
        r = e._estimate_rebalance_cost()
        # 当前权益 100% → 目标 100% → 无交易
        self.assertEqual(r["total_cost"], 0.0)


if __name__ == "__main__":
    unittest.main()
