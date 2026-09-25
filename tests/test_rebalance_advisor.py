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


# =====================================================================
# 2026-09-25：筛选池复用（避免为"挑 1 只基金"把全市场重算一遍）
# =====================================================================

class TestPoolInjection:
    """`/api/all` 的 funds worker 已算过筛选池，调仓顾问不该再独立跑一次
    `screen_funds()`（当前数据规模下那是 ~85 秒的全市场重算）。"""

    def _advisor_without_init(self):
        from src.analysis.rebalance_advisor import RebalanceAdvisor
        a = RebalanceAdvisor.__new__(RebalanceAdvisor)      # 跳过 __init__（不需要 DB）
        return a

    def test_injected_pool_is_used_and_screener_not_called(self):
        a = self._advisor_without_init()
        calls = []

        class _S:
            def screen_funds(self, max_results=10):
                calls.append(max_results)
                return None

        a.screener = _S()
        a._pool = [{"code": "000001", "name": "甲稳健", "risk": "🟢 稳健"},
                   {"code": "000002", "name": "乙高风险", "risk": "🔴 高风险"},
                   {"code": "000003", "name": "丙稳健", "risk": "🟢 稳健"}]
        out = a._pick_steady_candidates(10)
        assert [x["fund_code"] for x in out] == ["000001", "000003"], out
        assert out[0]["fund_name"] == "甲稳健"
        assert calls == [], "注入池后**不得**再调 screen_funds（那是全市场重算）"

    def test_injected_pool_respects_the_same_cut_as_before(self):
        """取前 n 条再过滤 —— 与旧代码 `screen_funds(max_results=n)` 后过滤等价。"""
        a = self._advisor_without_init()
        a.screener = None
        a._pool = [{"code": "C%02d" % i, "name": "x", "risk": "🔴 高风险"} for i in range(5)] \
                  + [{"code": "GOOD", "name": "y", "risk": "🟢 稳健"}]
        assert a._pick_steady_candidates(5) == []          # 前 5 条里没有稳健 → 空
        assert [x["fund_code"] for x in a._pick_steady_candidates(10)] == ["GOOD"]

    def test_fallback_without_pool_still_calls_screener(self):
        """pool=None（CLI / 单测）→ 保留旧路径，不得回归。"""
        import pandas as pd
        a = self._advisor_without_init()
        calls = []

        class _S:
            def screen_funds(self, max_results=10):
                calls.append(max_results)
                return pd.DataFrame([{"fund_code": "000009", "fund_name": "丙",
                                      "risk_label": "🟢 稳健"}])

        a.screener = _S()
        a._pool = None
        out = a._pick_steady_candidates(10)
        assert [x["fund_code"] for x in out] == ["000009"]
        assert calls == [10], "无池时必须回退到 screen_funds"

    def test_build_increase_instructions_uses_injected_pool(self):
        """端到端：注入池时加仓指令能从池里挑出基金（且不触发筛选器）。"""
        from src.analysis.rebalance_advisor import RebalanceAdvisor
        a = RebalanceAdvisor.__new__(RebalanceAdvisor)
        calls = []

        class _S:
            def screen_funds(self, max_results=10):
                calls.append(1)
                return None

        a.screener = _S()
        a._pool = [{"code": "016371", "name": "信澳业绩驱动混合C", "risk": "🟢 稳健"}]
        ins = a._build_increase_instructions(gap_amount=200.0, total_cap=1000.0,
                                            temp={"temperature": 56.3},
                                            current_eq=28.8, target_eq=40.0)
        assert len(ins) == 1 and ins[0].fund_code == "016371"
        assert calls == []
