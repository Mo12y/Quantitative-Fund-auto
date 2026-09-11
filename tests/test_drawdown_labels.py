"""
回撤标签口径的单测（C3）。

旧版直接在 `unit_nav` 上算 (cummax − nav)/cummax，且没有任何异常过滤 ——
**除息日**单位净值下挫会被当成一次回撤，把正例率推高。
修法：在**复权净值**上算（优先用累计净值 acc_nav，除息日累计净值不动）。
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.vol_predictor import compute_adjusted_nav, compute_daily_returns
from src.analysis.drawdown_warning import monthly_max_drawdown

DAYS = pd.date_range("2023-01-03", "2023-01-31", freq="B")
EX_DIV = pd.Timestamp("2023-01-16")


def _frames():
    """在 EX_DIV 除息：单位净值 1.00 → 0.95，累计净值保持 1.00（分红 0.05 计入累计）。"""
    unit = pd.Series(1.00, index=DAYS)
    unit.loc[unit.index >= EX_DIV] = 0.95
    acc = pd.Series(1.00, index=DAYS)          # 累计净值不变 → 分红不是亏损
    return pd.DataFrame({"F1": unit}), pd.DataFrame({"F1": acc})


class TestExDividendNotADrawdown(unittest.TestCase):
    def setUp(self):
        self.unit, self.acc = _frames()
        self.me = [DAYS[-1]]

    def test_adjusted_nav_is_flat_on_ex_dividend(self):
        adj = compute_adjusted_nav(self.unit, self.acc)
        self.assertAlmostEqual(float(adj["F1"].max() - adj["F1"].min()), 0.0, places=6)

    def test_raw_unit_nav_would_show_drawdown(self):
        """反证：只看单位净值，这个"回撤"是分红造出来的"""
        adj = compute_adjusted_nav(self.unit, None)
        dd = float(((adj["F1"].cummax() - adj["F1"]) / adj["F1"].cummax()).max())
        self.assertAlmostEqual(dd, 0.05, places=4)

    def test_label_uses_adjusted_nav(self):
        df = monthly_max_drawdown(self.unit, self.me, acc_wide=self.acc)
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(float(df["max_drawdown"].iloc[0]), 0.0, places=6)

    def test_real_drawdown_still_detected(self):
        """真回撤必须照旧被抓到（修分红不能把真信号一起修没）"""
        unit = self.unit.copy()
        unit.loc[unit.index >= EX_DIV, "F1"] = 0.80      # 真跌 20%
        acc = self.acc.copy()
        acc.loc[acc.index >= EX_DIV, "F1"] = 0.80
        df = monthly_max_drawdown(unit, self.me, acc_wide=acc)
        self.assertAlmostEqual(float(df["max_drawdown"].iloc[0]), 0.20, places=4)


class TestCleanReturnsShared(unittest.TestCase):
    def test_outlier_filter_applies_to_both_paths(self):
        nav = pd.DataFrame({"F1": [1.0, 1.0, 1.0, 3.0, 3.0]},
                           index=pd.date_range("2023-01-02", periods=5, freq="B"))
        # compute_daily_returns 与复权净值都基于同一套异常过滤
        ret = compute_daily_returns(nav)
        self.assertTrue(np.isnan(ret["F1"].iloc[3]))      # +200% 被剔除
        self.assertAlmostEqual(float(ret["F1"].iloc[4]), 0.0, places=9)
        # 复权净值把被剔除的那天当 0 收益 → 序列不因异常跳变而崩
        adj = compute_adjusted_nav(nav, None)
        self.assertAlmostEqual(float(adj["F1"].iloc[-1]), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
