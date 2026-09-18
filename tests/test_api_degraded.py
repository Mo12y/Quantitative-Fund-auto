"""
API 层不得静默填默认值 —— 批次 3.4。

只覆盖**影响数字正确性**的那一处：`_all_rebalance` 读不到计划现金弹药时，
旧代码静默 `cash_reserve = 0.0`，于是"总资金"被低估、**权益占比被高估**，
而前端完全看不出来。现在必须显式声明（`degraded: ["cash_reserve"]`）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.web.app as app_module


@pytest.fixture()
def rebalance_env(monkeypatch):
    """把 _all_rebalance 的外部依赖全部替换掉，只留被测的那段逻辑。"""

    class _FakeDB:
        def close(self):
            pass

        def get_current_holdings(self):
            return [{"fund_code": "X", "fund_name": "X", "shares": 1.0}]

    class _FakeAdvisor:
        def __init__(self, db):
            pass

        def analyze(self, cash_reserve=0.0):
            return {"need_rebalance": False, "current_equity_pct": 50.0,
                    "target_equity_pct": 40.0, "gap_pct": 10.0,
                    "summary": {}, "instructions": [],
                    "_seen_cash": cash_reserve}

    monkeypatch.setattr(app_module, "get_db", lambda: _FakeDB())
    monkeypatch.setattr(app_module, "RebalanceAdvisor", _FakeAdvisor)
    monkeypatch.setattr(app_module, "ensure_seed", lambda db: None)
    return monkeypatch


def _run(rebalance_env, plan):
    rebalance_env.setattr(app_module, "get_plan", lambda db: plan)
    return app_module._all_rebalance()


class TestCashReserveIsDeclaredNotFaked:
    def test_normal_plan_has_no_degradation(self, rebalance_env):
        out = _run(rebalance_env, {"cash_reserve": 280.0})
        assert out["cash_reserve"] == 280.0
        assert out["degraded"] == []

    def test_genuine_zero_is_not_degradation(self, rebalance_env):
        """计划里**真的**写了 0 → 这是有效值，不是缺失。"""
        out = _run(rebalance_env, {"cash_reserve": 0})
        assert out["cash_reserve"] == 0.0
        assert out["degraded"] == []

    def test_missing_key_is_declared(self, rebalance_env):
        out = _run(rebalance_env, {})
        assert out["cash_reserve"] == 0.0
        assert "cash_reserve" in out["degraded"]

    def test_none_value_is_declared(self, rebalance_env):
        out = _run(rebalance_env, {"cash_reserve": None})
        assert "cash_reserve" in out["degraded"]

    def test_blank_string_is_declared(self, rebalance_env):
        out = _run(rebalance_env, {"cash_reserve": "  "})
        assert "cash_reserve" in out["degraded"]

    def test_garbage_plan_is_declared(self, rebalance_env):
        out = _run(rebalance_env, None)
        assert "cash_reserve" in out["degraded"]

    def test_response_shape_is_preserved(self, rebalance_env):
        """新增字段不能挤掉既有字段（前端仍按老字段渲染）。"""
        out = _run(rebalance_env, {"cash_reserve": 100.0})
        for k in ("need_rebalance", "current_equity_pct", "target_equity_pct",
                  "gap_pct", "summary", "instructions"):
            assert k in out
