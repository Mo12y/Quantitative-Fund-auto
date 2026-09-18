"""
半开区间（时点隔离）约定测试 —— 批次 2.1。

窗口是**按天**给定的，但被比较的是**带时分秒**的时点。约定：
**下界闭、上界开**（`start <= t < end + 1天`）。

并验证它能把现有的 `trade_rules` 语义"装进去"（15:00 切点、非交易日顺延、
生效日定价）—— **不改动 trade_rules 的任何算术**，只做一致性断言。
"""
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.time_window import (
    LIVE_GRACE_DAYS,
    day_window,
    include_dateless,
    in_window,
    pricing_day_window,
)
from src.analysis.trade_rules import effective_apply_date, resolve_apply

# 2026-08-21 是周五，08-22/23 是周末，08-24 是周一
CAL = {"2026-08-19", "2026-08-20", "2026-08-21",
       "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"}


class TestHalfOpenBoundaries(unittest.TestCase):
    def test_2359_on_end_day_is_inside(self):
        """边界日：end 当天 23:59 在窗口内"""
        self.assertTrue(in_window(datetime(2026, 8, 24, 23, 59), "2026-08-20", "2026-08-24"))

    def test_0000_next_day_is_outside(self):
        """边界日：end 之后那天 00:00 不在窗口内（上界开）"""
        self.assertFalse(in_window(datetime(2026, 8, 25, 0, 0), "2026-08-20", "2026-08-24"))

    def test_0000_on_start_day_is_inside(self):
        """下界闭：start 当天 00:00 算在窗口内"""
        self.assertTrue(in_window(datetime(2026, 8, 20, 0, 0), "2026-08-20", "2026-08-24"))

    def test_last_instant_before_start_is_outside(self):
        self.assertFalse(in_window(datetime(2026, 8, 19, 23, 59, 59), "2026-08-20", "2026-08-24"))

    def test_end_boundary_is_the_midnight_after_end(self):
        lo, hi = day_window("2026-08-20", "2026-08-24")
        self.assertEqual(lo, datetime(2026, 8, 20, 0, 0, tzinfo=hi.tzinfo))
        self.assertEqual(hi, datetime(2026, 8, 25, 0, 0, tzinfo=hi.tzinfo))

    def test_single_day_window(self):
        self.assertTrue(in_window(datetime(2026, 8, 24, 15, 0), "2026-08-24", "2026-08-24"))
        self.assertFalse(in_window(datetime(2026, 8, 25, 0, 0), "2026-08-24", "2026-08-24"))

    def test_empty_window_rejected(self):
        with self.assertRaises(ValueError):
            day_window("2026-08-24", "2026-08-20")


class TestConformsToTradeRules(unittest.TestCase):
    """把 trade_rules 的既有语义用同一套窗口约定表述出来（不改算术）"""

    def test_non_trading_day_deferral_lands_in_the_right_window(self):
        """非交易日顺延后，生效日落在正确的单日定价窗口里"""
        eff = effective_apply_date("2026-08-22", CAL)     # 周六 → 顺延
        self.assertEqual(eff, "2026-08-24")
        lo, hi = pricing_day_window(eff)
        self.assertEqual(lo.date(), date(2026, 8, 24))
        self.assertEqual(hi.date(), date(2026, 8, 25))
        # 用公开 API 判断（会做 naive/aware 归一）
        self.assertTrue(in_window(datetime(2026, 8, 24, 23, 59), eff, eff))
        self.assertFalse(in_window(datetime(2026, 8, 25, 0, 0), eff, eff))

    def test_trading_day_pricing_window_is_that_day(self):
        self.assertEqual(effective_apply_date("2026-08-21", CAL), "2026-08-21")

    def test_cutoff_changes_confirm_date_but_not_the_pricing_day(self):
        """15:00 切点改的是**确认日/起算日**；定价日（生效日）与切点无关。

        这是 `effective_apply_date` 比 `confirm_date` 更适合做成交价的原因 ——
        成交价只取生效日那天的净值。
        """
        before = resolve_apply("2026-08-21", after_cutoff=False, calendar=CAL)
        after = resolve_apply("2026-08-21", after_cutoff=True, calendar=CAL)
        self.assertNotEqual(before[0], after[0], "15:00 前后确认日必须不同")
        self.assertNotEqual(before[1], after[1], "起算日跟随确认日，也应不同")
        # 定价日不受切点影响（effective_apply_date 没有 after_cutoff 参数，这是设计）
        self.assertEqual(effective_apply_date("2026-08-21", CAL), "2026-08-21")

    def test_cutoff_boundary_1440_vs_1500(self):
        """15:00 整算"之后"（`(h, m) >= (15, 0)`），14:59 不算"""
        from src.analysis.trade_rules import is_after_cutoff
        self.assertFalse(is_after_cutoff(datetime(2026, 8, 21, 14, 59)))
        self.assertTrue(is_after_cutoff(datetime(2026, 8, 21, 15, 0)))
        self.assertTrue(is_after_cutoff(datetime(2026, 8, 21, 15, 1)))


class TestDatelessEntries(unittest.TestCase):
    def test_dateless_excluded_in_backtest_window(self):
        """回测窗口：无日期条目必须排除（无法证明它不是未来）"""
        self.assertFalse(include_dateless("2022-06-30", now=date(2026, 9, 14)))

    def test_dateless_kept_in_live_window(self):
        """实时窗口：end 就是最近，保留"""
        self.assertTrue(include_dateless("2026-09-14", now=date(2026, 9, 14)))
        self.assertTrue(include_dateless("2026-09-13", now=date(2026, 9, 14)))

    def test_grace_days_constant(self):
        self.assertEqual(LIVE_GRACE_DAYS, 1)


if __name__ == "__main__":
    unittest.main()
