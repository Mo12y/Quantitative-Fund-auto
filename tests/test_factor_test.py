"""
因子检验（factor_test）口径修正的单测。

覆盖：
  - 首月建仓只收**单边**成本（旧版一律 ×2，凭空多收 0.5pp）
  - 年度收益按**收益实现月**归年（信号在 t、收益在 t+1）
  - 基准类型显式判定：`date,close` 这种表头**不得**被判成"全收益"
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.factor_test import (
    backtest_factor, yearly_returns, detect_benchmark_type,
)


def _months(start="2023-01-31", n=8):
    return [m.strftime("%Y-%m-%d") for m in pd.date_range(start, periods=n, freq="ME")]


class TestFirstMonthSingleSideCost(unittest.TestCase):
    """首月只买不卖 → 成本 = turnover × one_way（而不是 ×2）"""

    def _setup(self):
        months = _months()
        idx = pd.DatetimeIndex(months)
        # A 每月 +10%，B 不动
        close = pd.DataFrame({
            "A": [100 * (1.10 ** i) for i in range(len(months))],
            "B": [100.0] * len(months),
        }, index=idx)
        # 因子永远选 A
        factor = pd.DataFrame({"A": 1.0, "B": 0.0}, index=idx[:-1])
        bench = pd.DataFrame({"ret": 0.0}, index=idx[:-1])
        return factor, close, bench

    def test_first_month_charged_one_side_only(self):
        factor, close, bench = self._setup()
        m = backtest_factor(factor, close, bench, top_n=1, one_way_cost=0.005)
        mr = m["monthly_returns"]
        # 首月毛收益 10%，单边成本 0.5% → 净 9.5%
        self.assertAlmostEqual(mr.iloc[0], 0.095, places=6)
        # 旧版按双边收 1.0% → 9.0%，明确断言不是它
        self.assertNotAlmostEqual(mr.iloc[0], 0.090, places=6)
        self.assertEqual(m["turnover_list"][0], 1.0)

    def test_subsequent_month_no_turnover_no_cost(self):
        factor, close, bench = self._setup()
        m = backtest_factor(factor, close, bench, top_n=1, one_way_cost=0.005)
        self.assertAlmostEqual(m["monthly_returns"].iloc[1], 0.10, places=6)
        self.assertEqual(m["turnover_list"][1], 0.0)


class TestYearlyReturnsRealizationYear(unittest.TestCase):
    """12 月信号赚到的次年 1 月收益，必须算进次年"""

    def test_december_signal_goes_to_next_year(self):
        s = pd.Series([0.10, 0.20],
                      index=pd.to_datetime(["2023-11-30", "2023-12-31"]))
        y = yearly_returns(s)
        self.assertAlmostEqual(y.loc[2023], 0.10, places=6)
        self.assertAlmostEqual(y.loc[2024], 0.20, places=6)

    def test_benchmark_not_shifted(self):
        s = pd.Series([0.10, 0.20],
                      index=pd.to_datetime(["2023-11-30", "2023-12-31"]))
        y = yearly_returns(s, shift_to_realization=False)
        self.assertEqual(list(y.index), [2023])
        self.assertAlmostEqual(y.loc[2023], 1.10 * 1.20 - 1, places=6)

    def test_full_year_stays_in_one_year(self):
        s = pd.Series([0.01] * 11,
                      index=pd.date_range("2023-01-31", periods=11, freq="ME"))
        y = yearly_returns(s)
        self.assertEqual(list(y.index), [2023])   # 1~11 月信号 → 2~12 月实现，同属 2023


class TestBenchmarkTypeDetection(unittest.TestCase):
    """显式判定基准类型；`close` 表头不得被当成全收益"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, text):
        p = Path(self.tmp.name) / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_plain_close_header_is_unknown(self):
        p = self._write("bench_plain.csv", "date,close\n2023-01-31,4000\n")
        self.assertEqual(detect_benchmark_type(p), "unknown")

    def test_total_return_code_detected(self):
        p = self._write("bench_a.csv",
                        "date,close,index_code\n2023-01-31,4000,H00300\n")
        self.assertEqual(detect_benchmark_type(p), "total_return")

    def test_price_code_detected(self):
        p = self._write("bench_b.csv",
                        "date,close,index_code\n2023-01-31,4000,000300\n")
        self.assertEqual(detect_benchmark_type(p), "price_only")

    def test_filename_hint_is_fallback_only(self):
        p = self._write("csi300_tr_daily.csv", "date,close\n2023-01-31,4000\n")
        self.assertEqual(detect_benchmark_type(p), "total_return")

    def test_missing_file_is_unknown(self):
        self.assertEqual(detect_benchmark_type(Path(self.tmp.name) / "nope.csv"),
                         "unknown")


if __name__ == "__main__":
    unittest.main()
