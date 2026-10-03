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


class TestHonestSummary(unittest.TestCase):
    """摘要必须说实话（2026-10-01）：缺口大但无指令 ≠ "偏差在合理范围内"。"""

    def _advisor(self, holdings, infos, navs, target_eq):
        a = RebalanceAdvisor(_FakeDB(holdings, infos, navs))
        a.thermometer = _FixedTemp(target_eq)
        a.screener = _StubScreener()
        return a

    def test_needs_buy_but_no_candidates_tells_truth(self):
        """加仓缺口 >5pp 但候选池为空 → 必须如实说"需要加仓、暂无候选"。

        2026-10-01 实测暴露：枢轴模型把目标从 36.9% 抬到 ~58.8% 后缺口 24.9pp，
        但摘要仍显示"无需调仓 / 偏差在合理范围内"（因为 _summarize 只看"有没有指令"）。
        """
        holdings = [{"id": 1, "fund_code": "A", "fund_name": "A", "shares": 1000,
                     "buy_amount": 1000, "buy_date": "2026-01-01", "status": "holding"}]
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}}, {"A": 1.2}, target_eq=80.0)
        a._pool = []                               # 池子为空（或前列全是非温度适用标的）
        r = a.analyze(cash_reserve=800.0)
        self.assertTrue(r["need_rebalance"])       # 当前 60% vs 目标 80% → 缺口 20pp
        self.assertEqual(r["instructions"], [])
        self.assertIn("暂无候选", r["summary"]["verdict"])
        self.assertIn("20.0pp", r["summary"]["detail"])
        self.assertNotIn("合理范围内", r["summary"]["detail"], "缺口 20pp 时不得写'偏差在合理范围内'")

    def test_model_tag_when_absent_in_stub(self):
        """桩温度没有 target_model → 不编造来源注（旧载荷兼容）。"""
        holdings = [{"id": 1, "fund_code": "A", "fund_name": "A", "shares": 1000,
                     "buy_amount": 1000, "buy_date": "2026-01-01", "status": "holding"}]
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}}, {"A": 1.2}, target_eq=60.0)
        a._pool = []
        r = a.analyze(cash_reserve=800.0)          # 当前 60% vs 目标 60% → 无操作
        self.assertIn("无需调仓", r["summary"]["verdict"])
        self.assertNotIn("长期中性", r["summary"]["detail"])

    def test_model_tag_declared_when_present(self):
        """载荷带 target_model 时，摘要要**注明来源**（目标不是凭空来的）。"""
        holdings = [{"id": 1, "fund_code": "A", "fund_name": "A", "shares": 1000,
                     "buy_amount": 1000, "buy_date": "2026-01-01", "status": "holding"}]
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}}, {"A": 1.2}, target_eq=60.0)
        a.thermometer = _FixedTemp(60.0)
        a.thermometer.get_temperature = lambda: {
            "temperature": 50.0, "target_equity_pct": 60.0, "level_desc": "适中", "action": "保持",
            "target_model": {"kind": "equity_pivot", "neutral_pct": 60.0, "tilt_pp": 25.0}}
        a._pool = []
        r = a.analyze(cash_reserve=800.0)
        self.assertIn("长期中性 60% ±25pp", r["summary"]["detail"])


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
        a._pool = [{"code": "000001", "name": "甲稳健", "risk": "🟢 稳健", "type": "混合型-偏股"},
                   {"code": "000002", "name": "乙高风险", "risk": "🔴 高风险", "type": "股票型"},
                   {"code": "000003", "name": "丙稳健", "risk": "🟢 稳健", "type": "指数型-股票"}]
        out = a._pick_steady_candidates(10)
        assert [x["fund_code"] for x in out] == ["000001", "000003"], out
        assert out[0]["fund_name"] == "甲稳健"
        assert calls == [], "注入池后**不得**再调 screen_funds（那是全市场重算）"

    def test_injected_pool_respects_the_same_cut_as_before(self):
        """取前 n 条再过滤 —— 与旧代码 `screen_funds(max_results=n)` 后过滤等价。"""
        a = self._advisor_without_init()
        a.screener = None
        a._pool = [{"code": "C%02d" % i, "name": "x", "risk": "🔴 高风险", "type": "股票型"}
                   for i in range(5)] \
                  + [{"code": "GOOD", "name": "y", "risk": "🟢 稳健", "type": "股票型"}]
        assert a._pick_steady_candidates(5) == []          # 前 5 条里没有稳健 → 空
        assert [x["fund_code"] for x in a._pick_steady_candidates(10)] == ["GOOD"]

    def test_pool_candidate_outside_temp_scope_is_skipped(self):
        """温度不覆盖的标的（QDII-海外/黄金）**不得**成为加仓候选 ——
        否则就是"用 A 股估值信号提示你加仓纳指/黄金"，与减仓侧过滤自相矛盾。"""
        a = self._advisor_without_init()
        a.screener = None
        a._pool = [{"code": "QDII1", "name": "纳指联接", "risk": "🟢 稳健", "type": "指数型-海外股票"},
                   {"code": "GOLD1", "name": "上海金联接", "risk": "🟢 稳健", "type": "指数型-其他"},
                   {"code": "A500", "name": "境内指数", "risk": "🟢 稳健", "type": "指数型-股票"}]
        assert [x["fund_code"] for x in a._pick_steady_candidates(10)] == ["A500"]

    def test_fallback_without_pool_still_calls_screener(self):
        """pool=None（CLI / 单测）→ 保留旧路径，不得回归。"""
        import pandas as pd
        a = self._advisor_without_init()
        calls = []

        class _S:
            def screen_funds(self, max_results=10):
                calls.append(max_results)
                return pd.DataFrame([{"fund_code": "000009", "fund_name": "丙",
                                      "risk_label": "🟢 稳健", "fund_type": "股票型"}])

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
        a._pool = [{"code": "016371", "name": "信澳业绩驱动混合C", "risk": "🟢 稳健",
                    "type": "混合型-偏股"}]
        ins = a._build_increase_instructions(gap_amount=200.0, total_cap=1000.0,
                                            temp={"temperature": 56.3},
                                            current_eq=28.8, target_eq=40.0)
        assert len(ins) == 1 and ins[0].fund_code == "016371"
        assert calls == []


# =====================================================================
# 2026-09-28：多批次聚合（真实账户实测事故）
#
# `get_current_holdings()` 返回的是**批次**行，不是基金行 —— 真实账户 7 只基金
# 有 29 个批次（016453 一只就有 21 个小额定投批次）。旧实现把每个批次当独立持仓：
#   ① 同一只基金输出 21 条重复「卖出 ¥10」；
#   ② 叠加起卖下限 `max(sell_amount, 10)` → 单批次被要求卖出**超过自身市值**
#      （实测批次 9.99 / 9.92 → 指令 ¥10.00）；
#   ③ 30 条指令合计 ¥1108 **> 账户总市值 ¥854.51**（卖出额超过整个账户）。
# =====================================================================

class TestMultiLotAggregation:
    def _advisor(self, holdings, infos, navs, target_eq):
        a = RebalanceAdvisor(_FakeDB(holdings, infos, navs))
        a.thermometer = _FixedTemp(target_eq)
        a.screener = _StubScreener()
        return a

    @staticmethod
    def _lots(code, n, shares, amount, buy_date="2026-01-01"):
        return [{"id": i, "fund_code": code, "fund_name": code, "shares": shares,
                 "buy_amount": amount, "buy_date": buy_date, "status": "holding"}
                for i in range(1, n + 1)]

    def test_multi_lot_same_fund_yields_one_sell_instruction(self):
        """21 个批次 → 1 条卖出指令（复现真实账户 016453 的场景）。"""
        holdings = self._lots("A", 21, shares=10, amount=10.0)      # 市值 21×10×1.0 = 210
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}}, {"A": 1.0}, target_eq=10.0)
        r = a.analyze()
        sells = [i for i in r["instructions"] if i["action"] == "卖出" and i["fund_code"] == "A"]
        assert len(sells) == 1, "21 个批次应合成 1 条指令，实际 %d 条" % len(sells)
        assert sells[0]["amount"] <= 210.0

    def test_sell_amount_never_exceeds_fund_value(self):
        """基金市值 ¥9.99 → 卖出额不得超过 ¥9.99（旧实现给 ¥10.00）。

        ⚠️ 必须让 `sell_needed` **大于** 起卖下限 ¥10 才能复现 —— 真实事故就是
        "还需卖出几百元"时，每个小额批次都被强推 ¥10，超过批次自身市值。
        （若 sell_needed < 10，旧代码也会因 `min(…, sell_needed)` 而恰好不超卖，
        这样的用例复现不出 bug。）"""
        holdings = (self._lots("SMALL", 1, shares=9.99, amount=9.99)
                    + self._lots("BIG", 1, shares=1000, amount=1000.0))
        a = self._advisor(holdings, {"SMALL": {"fund_type": "混合型"},
                                     "BIG": {"fund_type": "股票型"}},
                          {"SMALL": 1.0, "BIG": 1.0}, target_eq=10.0)   # 需卖出约 90% ≈ ¥909
        r = a.analyze()
        small = [i for i in r["instructions"] if i["fund_code"] == "SMALL"]
        for i in small:
            assert i["amount"] <= 9.99 + 1e-6, "卖超了：%s" % i
        assert [i for i in r["instructions"] if i["fund_code"] == "BIG"], "大额那只应承担主要减仓"

    def test_total_sell_stays_within_portfolio_value(self):
        """端到端：卖出合计不得超过账户总市值（旧实现 ¥1108 > ¥854.51）。"""
        holdings = (self._lots("A", 21, shares=10, amount=10.0)
                    + self._lots("B", 2, shares=10, amount=10.0))
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}, "B": {"fund_type": "股票型"}},
                          {"A": 1.0, "B": 1.0}, target_eq=15.0)      # 市值 AA 210 + B 20 = 230
        r = a.analyze()
        total_sell = sum(i["amount"] for i in r["instructions"] if i["action"] == "卖出")
        assert total_sell <= r["portfolio_value"] + 1e-6, \
            "卖出合计 ¥%.0f 超过总市值 ¥%.2f" % (total_sell, r["portfolio_value"])

    def test_pnl_pct_is_fund_level_not_lot_level(self):
        """盈亏率按基金（合计市值 vs 合计成本）—— 批次的成本/市值不可比。"""
        holdings = self._lots("A", 2, shares=10, amount=10.0)         # 两批：成本各 10，市值各 10×1.5
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}}, {"A": 1.5}, target_eq=10.0)
        fr = a._assess_holdings(holdings)
        assert len(fr) == 1
        assert abs(fr[0]["current_value"] - 30.0) < 1e-6              # 2×10×1.5
        assert abs(fr[0]["pnl_pct"] - 50.0) < 1e-6                    # (30-20)/20
        assert len(fr[0]["lots"]) == 2, "聚合掉批次但**不丢**批次信息（赎回费判定要用）"

    def test_hold_instructions_are_per_fund(self):
        """持有分支同样按基金（旧实现会给 21 条「持有」）。"""
        holdings = self._lots("A", 21, shares=10, amount=10.0)
        a = self._advisor(holdings, {"A": {"fund_type": "混合型"}}, {"A": 1.0}, target_eq=100.0)
        r = a.analyze()
        assert [i["action"] for i in r["instructions"]] == ["持有"]
        assert len(r["instructions"]) == 1


class TestFifoRedemptionFeeCheck:
    """赎回费提示按**先进先出**判定（FIFO 是行业惯例，非合同条款 → 措辞用「将触及」）。"""

    def test_covered_by_old_lots_no_warning(self):
        lots = [{"days_held": 30, "value": 100.0}, {"days_held": 2, "value": 10.0}]
        assert RebalanceAdvisor._sell_reaches_young_lots(lots, 50.0) is False

    def test_reaches_young_lot_warns(self):
        lots = [{"days_held": 30, "value": 30.0}, {"days_held": 2, "value": 100.0}]
        assert RebalanceAdvisor._sell_reaches_young_lots(lots, 50.0) is True

    def test_exactly_consumes_old_lots_is_free(self):
        lots = [{"days_held": 30, "value": 50.0}, {"days_held": 1, "value": 90.0}]
        assert RebalanceAdvisor._sell_reaches_young_lots(lots, 50.0) is False

    def test_missing_lots_is_conservative(self):
        """批次信息缺失 → 无法保证免费 → 提示（宁可多提醒一次）"""
        assert RebalanceAdvisor._sell_reaches_young_lots([], 50.0) is True

    def test_reason_mentions_fifo_when_needed(self):
        """端到端：卖出触及年轻批次时，理由里出现赎回费提示。"""
        a = RebalanceAdvisor.__new__(RebalanceAdvisor)
        a.screener = _StubScreener()
        a._get_latest_nav = lambda code: 1.0
        a._days_held = lambda d: 1                       # 全部批次都是「昨天买的」
        a._get_fund_info = lambda code: {"fund_type": "混合型"}
        a._holding_value = lambda h: float(h["buy_amount"])
        fr = a._assess_holdings([{"id": 1, "fund_code": "A", "fund_name": "A", "shares": 100,
                                  "buy_amount": 100.0, "buy_date": "2026-09-27"}])
        ins = a._build_reduce_instructions(fr, gap_amount=200.0, total_cap=1000.0,
                                           current_eq=90.0, target_eq=20.0)
        assert ins and "赎回费" in ins[0].reason, ins[0].reason if ins else "无指令"
