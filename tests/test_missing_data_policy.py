"""
「数据缺失不得静默填充」约定的回归测试（批次 2.2）。

背景：温度计 A1 已经把「缺失维度剔除并按剩余权重归一化 + 声明 degraded_dimensions」
做成了正确范例。这里把同一约定固化下来：

- 取信号的函数在**没有信号时返回 None**，不在取数处就填中性值；
- 需要兜底的地方用**具名常量**，并把命中次数**声明**到返回值里（不静默）。
"""
import inspect
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.analysis.portfolio_simulation as ps
import src.analysis.vol_predictor as vp
from src.analysis.vol_predictor import (
    FALLBACK_DD_FRAC,
    FALLBACK_PE_EQUITY,
    FALLBACK_PRED_VOL,
    _compute_ewma_vol_series,
    last_signal_before,
)


class TestSignalReturnsNoneInsteadOfNeutral(unittest.TestCase):
    """取信号处不得自造中性值 —— 没有就是 None，由上层决定怎么声明"""

    def test_no_prior_signal_returns_none(self):
        s = pd.Series([0.30], index=[pd.Timestamp("2024-03-31")])
        # 严格早于：同龄/更早都没有可用信号
        self.assertIsNone(last_signal_before(s, pd.Timestamp("2024-03-31")))
        self.assertIsNone(last_signal_before(s, pd.Timestamp("2024-01-31")))

    def test_prior_signal_is_returned(self):
        s = pd.Series([0.30], index=[pd.Timestamp("2024-03-31")])
        self.assertAlmostEqual(last_signal_before(s, pd.Timestamp("2024-04-30")), 0.30)

    def test_empty_series_returns_none(self):
        empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        self.assertIsNone(last_signal_before(empty, pd.Timestamp("2024-04-30")))


class TestFallbacksAreNamedAndDeclared(unittest.TestCase):
    """兜底值必须是具名常量，且命中次数写进返回值"""

    def test_fallback_constants_keep_their_values(self):
        """重构只是「具名化 + 声明」，数值不许变"""
        self.assertEqual(FALLBACK_PRED_VOL, 0.20)
        self.assertEqual(FALLBACK_PE_EQUITY, 0.35)
        self.assertEqual(FALLBACK_DD_FRAC, 0.0)

    def test_no_anonymous_neutral_assignments_left(self):
        """源码里不应再散落 `pred_v = 0.20` / `dd_frac = 0.0` 这类匿名兜底"""
        for mod in (vp, ps):
            src = inspect.getsource(mod)
            self.assertNotIn("pred_v = 0.20", src, mod.__name__)
            self.assertNotIn("dd_frac = 0.0", src, mod.__name__)
            self.assertNotIn("if pred_v > 0 else 0.35", src, mod.__name__)
        self.assertIn("FALLBACK_PRED_VOL", inspect.getsource(vp.portfolio_simulation))
        self.assertIn("fallback_counts", inspect.getsource(ps.combined_portfolio_simulation))

    def test_portfolio_simulation_declares_fallback_counts(self):
        """两个模拟函数都必须把兜底次数放进返回值"""
        self.assertIn("fallback_vol_signal_months", inspect.getsource(vp.portfolio_simulation))
        self.assertIn("fallback_pe_signal_months", inspect.getsource(vp.portfolio_simulation))
        self.assertIn("fallback_vol_signal_months", inspect.getsource(ps.combined_portfolio_simulation))
        self.assertIn("fallback_dd_signal_months", inspect.getsource(ps.combined_portfolio_simulation))


class TestEwmaMissingDay(unittest.TestCase):
    """重点检查项：缺失日不得被当成「零方差日」（旧代码 .fillna(0.0) 会低估波动率）"""

    def test_source_has_no_fillna_zero(self):
        """只看**代码行**：注释里会提到旧写法，注释不算"""
        code_lines = [ln.split("#")[0] for ln in inspect.getsource(_compute_ewma_vol_series).splitlines()]
        code = "\n".join(code_lines)
        self.assertNotIn(".fillna(0", code)
        self.assertIn("ret_wide ** 2", code)

    def test_missing_day_does_not_drag_ewma_down(self):
        ret = pd.DataFrame({"F": [0.01, 0.02, np.nan, 0.03, np.nan, 0.01]})
        got = float(_compute_ewma_vol_series(ret)["F"].iloc[-1])
        wrong = float(np.sqrt(
            (ret ** 2).fillna(0.0).ewm(alpha=1 - 0.94, adjust=False).mean()["F"].iloc[-1] * 252))
        # 填 0 的写法系统性低估；正确写法（跳过缺失）必须更高
        self.assertGreater(got, wrong)

    def test_all_present_matches_manual_recursion(self):
        """无缺失时结果应与手写递推一致：var_t = λ·var_{t-1} + (1−λ)·r²_t"""
        r = [0.01, 0.02, -0.01, 0.03]
        got = float(_compute_ewma_vol_series(pd.DataFrame({"F": r}))["F"].iloc[-1])
        lam, var = 0.94, None
        for x in r:
            var = x * x if var is None else lam * var + (1 - lam) * x * x
        self.assertAlmostEqual(got, np.sqrt(var * 252), places=10)


if __name__ == "__main__":
    unittest.main()
