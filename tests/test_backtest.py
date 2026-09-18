"""
严谨回测引擎单元测试。

运行方式:
    python -m unittest discover tests
"""

import math
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.backtest import (
    CostModel,
    RigorousBacktest,
    alpha_t_test,
    compute_beta_and_alpha,
    compute_max_drawdown,
    compute_metrics,
    _t_cdf,
    _t_cdf_numerical,
)


# ---------------------------------------------------------------------
# 假数据层：绕开真实 DB 与联网，让回测可被精确手算校验
# ---------------------------------------------------------------------

class _FakeConn:
    def __init__(self, dates):
        self._d = dates
        self._row = None

    def cursor(self):
        return self

    def execute(self, sql, *args):
        self._row = (max(self._d), min(self._d))
        return self

    def fetchone(self):
        return self._row


class _FakeNavDB:
    """只提供净值的最小假库。navs: {code: {date: nav}}；names: {code: fund_name}"""

    def __init__(self, navs, names=None):
        self._navs = navs
        self._names = names or {}
        all_d = sorted({d for m in navs.values() for d in m})
        self.conn = _FakeConn(all_d)

    def get_all_fund_codes(self):
        return list(self._navs)

    def get_fund_nav(self, code, end_date=None):
        return [{"nav_date": d, "unit_nav": v}
                for d, v in sorted(self._navs.get(code, {}).items())
                if end_date is None or d <= end_date]

    def get_fund_info(self, code):
        return {"fund_name": self._names.get(code, "")}


ZERO_COST = CostModel(fee_scale=0.0)


def _fake_market(names=None):
    """造一段可控行情：A 每日稳定上涨，B 完全不涨；返回 (db, navs, 月末网格)。

    网格按 run() 的同款定义生成（末端 = 最新净值日，起点回溯 1 年，取自然月末），
    这样手算与引擎走的是同一串月份。

    `names` 用于测份额类别对申购费的影响；不传则基金名为空（走保守默认费率）。
    """
    dates = pd.date_range("2022-01-03", "2023-08-31", freq="B").strftime("%Y-%m-%d").tolist()
    navs = {
        "A": {d: round(1.001 ** i, 8) for i, d in enumerate(dates)},
        "B": {d: 1.0 for d in dates},
    }
    end = max(dates)
    start = (pd.to_datetime(end) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    grid = [d.strftime("%Y-%m-%d")
            for d in pd.date_range(start=start, end=end, freq="ME")]
    return _FakeNavDB(navs, names=names), navs, grid


def _engine(db, cost=None):
    e = RigorousBacktest(db, cost_model=cost)
    # 恒定温度 → 只在首月建仓，之后不再调仓（阈值 15° 不会触发）
    e._temp_map = {"2022-09-30": 50.0}
    e._benchmarks = {}
    return e


class TestTCdf(unittest.TestCase):
    """B5: 真 t 分布 CDF（旧实现其实是标准正态）"""

    def test_numerical_fallback_matches_scipy(self):
        from scipy import stats
        for df in (1, 2, 3, 5, 10, 30, 60, 120):
            for x in (-3.0, -1.5, -0.5, 0.0, 0.5, 1.96, 2.5, 3.0):
                self.assertAlmostEqual(_t_cdf_numerical(x, df),
                                       float(stats.t.cdf(x, df)), places=6,
                                       msg=f"df={df} x={x}")

    def test_dispatcher_uses_scipy_when_available(self):
        from scipy import stats
        self.assertAlmostEqual(_t_cdf(1.96, 10), float(stats.t.cdf(1.96, 10)), places=9)

    def test_fatter_tails_than_normal(self):
        """小 df 下 t 分布尾部更厚 → P(T<=2) 明显小于正态的 P(Z<=2)"""
        normal = 0.5 * (1.0 + math.erf(2.0 / math.sqrt(2)))
        self.assertLess(_t_cdf(2.0, 5), normal - 0.01)

    def test_large_df_approaches_normal(self):
        normal = 0.5 * (1.0 + math.erf(1.0 / math.sqrt(2)))
        self.assertAlmostEqual(_t_cdf(1.0, 5000), normal, places=3)


class TestBetaAlphaConsistency(unittest.TestCase):
    """B5: beta 的分子分母统一用样本统计量（ddof=1）"""

    def test_beta_is_ols_slope(self):
        s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        b = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        r = compute_beta_and_alpha(s, b)
        self.assertAlmostEqual(r["beta"], 0.5, places=6)

    def test_alpha_annual_is_twelve_times_monthly(self):
        s = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        b = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        r = compute_beta_and_alpha(s, b)
        self.assertAlmostEqual(r["alpha_annual"], 3.5 * 12, places=3)


class TestMonthGrid(unittest.TestCase):
    """B4: 自然月末网格，不再 30 天步进漂移"""

    def setUp(self):
        self.e = RigorousBacktest.__new__(RigorousBacktest)

    def test_all_month_ends(self):
        grid = self.e._month_grid("2021-09-11", "2026-08-07")
        for d in grid:
            dt = pd.to_datetime(d)
            self.assertEqual(dt, dt + pd.offsets.MonthEnd(0), f"{d} 不是月末")

    def test_exactly_one_month_apart(self):
        grid = self.e._month_grid("2021-09-11", "2026-08-07")
        per = pd.PeriodIndex(pd.to_datetime(grid), freq="M")
        for a, b in zip(per, per[1:]):
            self.assertEqual((b - a).n, 1, f"{a} → {b} 不是相邻月份")

    def test_no_drift_within_month(self):
        grid = self.e._month_grid("2021-09-11", "2026-08-07")
        days = {pd.to_datetime(d).day for d in grid}
        self.assertTrue(all(d >= 28 for d in days), f"出现非月末日: {sorted(days)}")

    def test_empty_when_end_before_start(self):
        self.assertEqual(self.e._month_grid("2024-01-01", "2023-01-01"), [])


class TestCostsFlowIntoMetrics(unittest.TestCase):
    """B1: 申购费/赎回费/管理费必须流进指标；费率归零时应复现毛收益"""

    def _expected(self, navs, grid, cost, buy_fee=None):
        """独立手算：等权买 A/B，A 按净值增长，B 不动，再按期扣管理费。

        净值按"不晚于该日期的最近一条"取值（与 get_fund_nav(end_date=...) 同口径）。
        `buy_fee` 指定首月建仓的申购费率；不传则用 `cost.purchase_rate()`（默认费率）。
        """
        def asof(code, d):
            keys = [k for k in navs[code] if k <= d]
            return navs[code][max(keys)]

        pv = 100.0
        pv -= pv * (cost.purchase_rate() if buy_fee is None else buy_fee)   # 首月建仓：申购费
        vA = vB = pv / 2.0
        for m, nm in zip(grid[:-1], grid[1:]):
            rA = asof("A", nm) / asof("A", m) - 1
            decay = (1 - cost.management_rate_annual() / 12)
            vA *= (1 + rA) * decay
            vB *= 1.0 * decay
        return ((vA + vB) / 100.0 - 1) * 100

    def test_zero_cost_matches_hand_calc(self):
        db, navs, grid = _fake_market()
        r = _engine(db, ZERO_COST).run(lookback_years=1)
        self.assertNotIn("error", r)
        exp = self._expected(navs, grid, ZERO_COST)
        self.assertAlmostEqual(r["strategy"]["total_return"], round(exp, 2), places=2)
        # 零费率下每月净收益就是毛收益
        self.assertEqual(r["strategy"]["months"], len(grid) - 1)

    def test_real_cost_matches_hand_calc(self):
        db, navs, grid = _fake_market()
        r = _engine(db, CostModel()).run(lookback_years=1)
        exp = self._expected(navs, grid, CostModel())
        self.assertAlmostEqual(r["strategy"]["total_return"], round(exp, 2), places=2)

    def test_cost_strictly_lowers_return(self):
        db0, _, _ = _fake_market()
        db1, _, _ = _fake_market()
        r0 = _engine(db0, ZERO_COST).run(lookback_years=1)
        r1 = _engine(db1, CostModel()).run(lookback_years=1)
        self.assertGreater(r0["strategy"]["annual_return"], r1["strategy"]["annual_return"])
        self.assertGreater(r0["strategy"]["total_return"], r1["strategy"]["total_return"])

    def test_cost_model_is_disclosed(self):
        db, _, _ = _fake_market()
        r = _engine(db, CostModel()).run(lookback_years=1)
        self.assertIn("cost_model", r)
        # 管理费默认 0 → 1 年往返成本 = 申购费 0.15% + 赎回 0
        self.assertAlmostEqual(r["cost_model"]["round_trip_cost_1y"], 0.0015, places=6)
        self.assertEqual(r["cost_model"]["management_fee_annual"], 0.0)

    def test_management_fee_not_charged_separately(self):
        """1.2 回归：净值已含管理费，不得再按月扣一次。

        默认 CostModel 的管理费率必须是 0；且在这个"零申购费(C类)+零管理费"的场景下，
        组合总收益必须与**净值本身的复利**完全一致 —— 多扣任何一笔费用都会让它偏低。
        """
        self.assertEqual(CostModel().management_fee_annual, 0.0)
        db, navs, grid = _fake_market(names={"A": "某某混合C", "B": "某某混合C"})
        r = _engine(db, CostModel()).run(lookback_years=1)
        # C 类份额：申购费 0；管理费 0 → 组合收益 = 净值复利，一分钱都没多扣
        exp = self._expected(navs, grid, CostModel(), buy_fee=0.0)
        self.assertAlmostEqual(r["strategy"]["total_return"], round(exp, 2), places=2)

    def test_charging_management_fee_would_lower_the_result(self):
        """证明这一项仍然"接线"着 —— 只是默认关掉了（回测标的是指数时才会用到）"""
        db0, _, _ = _fake_market(names={"A": "某某混合C", "B": "某某混合C"})
        r0 = _engine(db0, CostModel()).run(lookback_years=1)
        db1, _, _ = _fake_market(names={"A": "某某混合C", "B": "某某混合C"})
        r1 = _engine(db1, CostModel(management_fee_annual=0.015)).run(lookback_years=1)
        self.assertLess(r1["strategy"]["total_return"], r0["strategy"]["total_return"])


class TestSharpeRiskFreeRate(unittest.TestCase):
    """1.1: `ann_ret` 是百分数、`rf_annual` 是小数 —— 相减前必须归一。

    旧实现 `(ann_ret - rf_annual)` 只扣了 0.02 个百分点而不是 2 个百分点（差 100 倍）。
    """

    def setUp(self):
        rng = np.random.default_rng(7)
        self.rets = rng.normal(1.0, 3.0, 60)

    def test_rf_is_actually_subtracted(self):
        m0 = compute_metrics(self.rets, rf_annual=0.0)
        m2 = compute_metrics(self.rets, rf_annual=0.02)
        av = m0["annual_volatility"]
        # 回归本 bug：旧实现这里恒为 0.0000
        self.assertAlmostEqual(m2["sharpe"] - m0["sharpe"], -2.0 / av, delta=0.02)

    def test_rf0_and_default_are_not_identical(self):
        a = compute_metrics(self.rets, rf_annual=0.0)["sharpe"]
        b = compute_metrics(self.rets)["sharpe"]      # 默认 0.02
        self.assertNotAlmostEqual(a, b, places=2)

    def test_sharpe_matches_manual(self):
        m = compute_metrics(self.rets, rf_annual=0.02)
        manual = (m["annual_return"] - 2.0) / m["annual_volatility"]
        self.assertAlmostEqual(m["sharpe"], manual, places=2)

    def test_consistent_with_portfolio_metrics(self):
        """同一组收益，两个模块的 Sharpe 必须一致（旧版 1.59 vs 1.83）"""
        from src.analysis.vol_predictor import _portfolio_metrics
        import pandas as pd
        a = compute_metrics(self.rets, rf_annual=0.02)
        b = _portfolio_metrics(pd.Series(self.rets / 100.0), rf_annual=0.02)
        self.assertAlmostEqual(a["sharpe"], b["sharpe"], places=2)

    def test_unit_conventions_differ_and_are_documented(self):
        """compute_metrics 返回百分数、_portfolio_metrics 返回小数（各自 docstring 已写明）"""
        from src.analysis.vol_predictor import _portfolio_metrics
        import pandas as pd
        a = compute_metrics(self.rets, rf_annual=0.02)
        b = _portfolio_metrics(pd.Series(self.rets / 100.0), rf_annual=0.02)
        self.assertAlmostEqual(a["annual_return"], b["annual_return"] * 100, delta=0.05)
        self.assertIn("小数", _portfolio_metrics.__doc__)
        self.assertIn("百分数", compute_metrics.__doc__)


class TestPurchaseRateByShareClass(unittest.TestCase):
    """1.3: 前端申购费按份额类别 —— C/E/I 类不收（销售服务费已含在净值里）"""

    def test_c_and_e_class_free(self):
        cm = CostModel()
        self.assertEqual(cm.purchase_rate("某某混合C"), 0.0)
        self.assertEqual(cm.purchase_rate("某某混合E"), 0.0)

    def test_a_class_uses_configured_default(self):
        cm = CostModel()
        self.assertEqual(cm.purchase_rate("某某混合A"), cm.purchase_fee)

    def test_no_name_falls_back_to_default(self):
        self.assertEqual(CostModel().purchase_rate(), 0.0015)

    def test_fee_scale_zeroes_everything(self):
        cm = CostModel(fee_scale=0.0)
        self.assertEqual(cm.purchase_rate("某某混合A"), 0.0)
        self.assertEqual(cm.purchase_rate("某某混合C"), 0.0)

    def test_c_class_buy_pays_no_purchase_fee(self):
        """端到端：全 C 类组合的买入成本应为 0"""
        db, _, _ = _fake_market(names={"A": "某某混合C", "B": "某某混合C"})
        r = _engine(db, CostModel()).run(lookback_years=1)
        self.assertNotIn("error", r)
        self.assertEqual(sum(t["buy_cost"] for t in r["trades"]), 0.0)

    def test_a_class_buy_pays_purchase_fee(self):
        db, _, _ = _fake_market(names={"A": "某某混合A", "B": "某某混合A"})
        r = _engine(db, CostModel()).run(lookback_years=1)
        self.assertGreater(sum(t["buy_cost"] for t in r["trades"]), 0.0)


class TestDateAlignment(unittest.TestCase):
    """B3: 策略与基准按日期对齐，不再按位置截断"""

    def setUp(self):
        self.e = RigorousBacktest(_FakeNavDB({}), cost_model=CostModel())

    def test_aligns_on_intersection_not_prefix(self):
        # 策略只有中间三个月；基准多出前后各两个月
        strat = {"2024-03-31": 1.0, "2024-04-30": 2.0, "2024-05-31": 3.0}
        bench = {"2024-01-31": 9.0, "2024-02-29": 9.0, "2024-03-31": 0.0,
                 "2024-04-30": 1.0, "2024-05-31": 1.0, "2024-06-30": 9.0}
        r = self.e._build_report(dict(strat), {"sh000300": dict(bench)}, [], {})
        b = r["benchmark_sh000300"]
        self.assertEqual(b["aligned_months"], 3)
        # 交集应是 03/04/05（均值 0.667），而不是旧实现的 01/02/03（均值 6.0）
        self.assertAlmostEqual(b["annual_return"] is not None, True)
        a = r["alpha_vs_sh000300"]
        self.assertEqual(a["n"], 3)
        self.assertAlmostEqual(a["mean_alpha"], round(np.mean([1.0, 1.0, 2.0]), 3), places=3)

    def test_no_benchmark_when_no_overlap(self):
        r = self.e._build_report({"2024-03-31": 1.0, "2024-04-30": 2.0, "2024-05-31": 3.0},
                                 {"sh000300": {"2019-01-31": 1.0}}, [], {})
        self.assertNotIn("benchmark_sh000300", r)


class TestYearlyByNaturalYear(unittest.TestCase):
    """B4: 逐年表按自然年（收益实现月）分组，不是每 12 期切一块"""

    def setUp(self):
        self.e = RigorousBacktest(_FakeNavDB({}), cost_model=CostModel())

    def test_groups_by_calendar_year(self):
        sr = {}
        for m in range(1, 13):
            sr[f"2023-{m:02d}-28"] = 1.0
        for m in range(1, 7):
            sr[f"2024-{m:02d}-28"] = 2.0
        r = self.e._yearly_breakdown(pd.Series(sr).sort_index(), {})
        self.assertEqual([y["period"] for y in r], ["2023", "2024"])
        self.assertEqual([y["months"] for y in r], [12, 6])
        self.assertAlmostEqual(r[0]["strategy_return"], round((1.01 ** 12 - 1) * 100, 2), places=2)
        self.assertAlmostEqual(r[1]["strategy_return"], round((1.02 ** 6 - 1) * 100, 2), places=2)

    def test_benchmark_aligned_within_year(self):
        sr = {"2023-01-31": 1.0, "2023-02-28": 1.0}
        bench = {"sh000300": {"2023-01-31": 2.0, "2023-02-28": 2.0, "2023-03-31": 99.0}}
        r = self.e._yearly_breakdown(pd.Series(sr).sort_index(), bench)
        self.assertEqual(len(r), 1)
        # 3 月的基准不得混进来
        self.assertAlmostEqual(r[0]["sh000300"], round((1.02 ** 2 - 1) * 100, 2), places=2)


class TestCostModel(unittest.TestCase):
    """交易成本模型"""

    def setUp(self):
        self.cost = CostModel()

    def test_redemption_matches_accounting_source(self):
        """赎回费率取 portfolio.redeem_fee_rate：<7 天 1.5%，≥7 天一律 0"""
        self.assertEqual(self.cost.redemption_rate(3), 0.015)     # <7 天监管下限
        self.assertEqual(self.cost.redemption_rate(6), 0.015)
        self.assertEqual(self.cost.redemption_rate(7), 0.0)       # ≥7 天免费（不再有 0.5% 档）
        self.assertEqual(self.cost.redemption_rate(30), 0.0)
        self.assertEqual(self.cost.redemption_rate(365), 0.0)

    def test_redemption_uses_fund_own_rate_when_higher(self):
        """基金自身费率高于监管下限时取孰高"""
        self.assertEqual(self.cost.redemption_rate(3, "1.50%"), 0.015)
        self.assertAlmostEqual(self.cost.redemption_rate(3, "0.10%"), 0.015, places=6)
        # 极端：某基金写了 0.5% 的短期费率，仍取 1.5% 下限
        self.assertAlmostEqual(self.cost.redemption_rate(3, "0.50%"), 0.015, places=6)

    def test_zero_scale_disables_all_fees(self):
        zero = CostModel(fee_scale=0.0)
        self.assertEqual(zero.redemption_rate(3), 0.0)
        self.assertEqual(zero.purchase_rate(), 0.0)
        self.assertEqual(zero.management_rate_annual(), 0.0)

    def test_round_trip_cost_defaults_without_management_fee(self):
        """往返成本 = 申购费 + 赎回费（管理费默认为 0：净值已含，不再重复扣）"""
        # 持有 365 天：0.15% 申购 + 0 赎回 + 0 管理费
        self.assertAlmostEqual(self.cost.round_trip_cost(365), 0.0015, places=6)
        # 持有 3 天：0.15% 申购 + 1.5% 赎回
        self.assertAlmostEqual(self.cost.round_trip_cost(3), 0.0015 + 0.015, places=6)

    def test_round_trip_cost_still_models_management_fee_when_asked(self):
        """显式给出管理费率时仍能算（用于"回测标的换成指数"的假设场景）"""
        cm = CostModel(management_fee_annual=0.015)
        self.assertAlmostEqual(cm.round_trip_cost(365), 0.0015 + 0.015, places=6)
        self.assertAlmostEqual(cm.round_trip_cost(3), 0.0015 + 0.015 + 0.015 * 3 / 365, places=6)


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
        """策略持续显著跑赢基准 → alpha 显著为正"""
        np.random.seed(7)
        benchmark = np.random.normal(0.5, 1.0, 36)
        strategy = benchmark + np.random.normal(1.0, 0.3, 36)  # 月均 alpha ≈ +1.0，波动小
        result = alpha_t_test(strategy, benchmark)
        self.assertTrue(result["significant"])
        self.assertGreater(result["t_stat"], 0)

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
