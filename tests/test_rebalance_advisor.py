"""
调仓顾问单元测试。

重点覆盖 A4 修复：总资金 = 持仓**市值** + 计划现金弹药（cash_reserve），
权益占比用市值计算 —— 不再用 total_invested × 1.1 拍脑袋估算、也不再用成本价。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.rebalance_advisor import RebalanceAdvisor


class _FakeDB:
    def __init__(self, holdings, infos, navs):
        self._h = holdings
        self._i = infos
        self._n = navs

    def get_current_holdings(self):
        return self._h

    def get_fund_info(self, code):
        return self._i.get(code, {})

    def get_latest_fund_nav(self, code):
        return {"unit_nav": self._n[code]}


class _StubScreener:
    def _screen_single_fund(self, code, info):
        return {"risk_label": "🟢 稳健", "risk_reasons": [], "metrics": {}}


class _FixedTemp:
    def __init__(self, target_equity_pct):
        self._t = target_equity_pct

    def get_temperature(self):
        return {"temperature": 50.0, "target_equity_pct": self._t,
                "level_desc": "🌤️ 适中", "action": "保持定投"}


class TestTotalCapitalAndEquity(unittest.TestCase):
    def _advisor(self, holdings, infos, navs, target_eq):
        a = RebalanceAdvisor(_FakeDB(holdings, infos, navs))
        a.thermometer = _FixedTemp(target_eq)
        a.screener = _StubScreener()
        return a

    def test_total_capital_is_market_value_plus_cash(self):
        holdings = [{"id": 1, "fund_code": "A", "fund_name": "A", "shares": 1000,
                     "buy_amount": 1000, "buy_date": "2026-01-01", "status": "holding"}]
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}}, {"A": 1.2}, target_eq=60.0)
        r = a.analyze(cash_reserve=800.0)

        # 市值 1000×1.2 = 1200；总资金 = 1200 + 800 = 2000；权益占比 = 60%
        self.assertAlmostEqual(r["total_capital"], 2000.0, places=2)
        self.assertAlmostEqual(r["current_equity_pct"], 60.0, places=1)
        # 旧口径会得 1000/(1000×1.1) = 90.9% —— 明确断言不是它
        self.assertNotAlmostEqual(r["current_equity_pct"], 90.9, places=1)

    def test_equity_uses_market_value_not_cost(self):
        holdings = [{"id": 1, "fund_code": "A", "fund_name": "A", "shares": 1000,
                     "buy_amount": 1000, "buy_date": "2026-01-01", "status": "holding"}]
        # 浮盈 50%：市值 1500，成本仍是 1000
        a = self._advisor(holdings, {"A": {"fund_type": "股票型"}}, {"A": 1.5}, target_eq=65.0)
        r = a.analyze(cash_reserve=500.0)
        self.assertAlmostEqual(r["total_capital"], 2000.0, places=2)
        self.assertAlmostEqual(r["current_equity_pct"], 75.0, places=1)


if __name__ == "__main__":
    unittest.main()
