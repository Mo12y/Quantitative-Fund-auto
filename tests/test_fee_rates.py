"""
费率口径单元测试 —— `portfolio` 是费率的**单一真源**。

覆盖：
- `redeem_fee_rate`：<7 天取监管下限与基金自身费率孰高，≥7 天为 0
- `share_class` / `purchase_fee_rate`：C/E/I 类不收前端申购费（批次 1.3）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.portfolio import (
    DEFAULT_PURCHASE_FEE,
    redeem_fee_rate,
    purchase_fee_rate,
    share_class,
)


class TestShareClass(unittest.TestCase):
    def test_common_classes(self):
        self.assertEqual(share_class("南方中证500ETF联接发起式C"), "C")
        self.assertEqual(share_class("易方达瑞富混合E"), "E")
        self.assertEqual(share_class("某某灵活配置混合A"), "A")
        self.assertEqual(share_class("某某债券I"), "I")
        self.assertEqual(share_class("银河创新成长混合C"), "C")

    def test_product_type_not_mistaken_for_class(self):
        """LOF / ETF / QDII 是产品类型，不能被读成份额类别"""
        for name in ("某某成长LOF", "某某300ETF", "某某标普500QDII", "某某REITs"):
            self.assertIsNone(share_class(name), name)

    def test_no_suffix(self):
        self.assertIsNone(share_class("国联安鑫享混合"))
        self.assertIsNone(share_class(""))
        self.assertIsNone(share_class(None))

    def test_digits_before_class(self):
        self.assertEqual(share_class("南方中证500C"), "C")


class TestPurchaseFeeRate(unittest.TestCase):
    def test_c_class_is_free(self):
        self.assertEqual(purchase_fee_rate("南方中证500ETF联接发起式C"), 0.0)

    def test_e_and_i_class_free(self):
        self.assertEqual(purchase_fee_rate("易方达瑞富混合E"), 0.0)
        self.assertEqual(purchase_fee_rate("某某债券I"), 0.0)

    def test_a_class_uses_conservative_default(self):
        """A 类真实前端费率需数据源扩展阶段才有；此批次用保守默认"""
        self.assertEqual(purchase_fee_rate("某某灵活配置混合A"), DEFAULT_PURCHASE_FEE)
        self.assertEqual(purchase_fee_rate("某某灵活配置混合A", default=0.01), 0.01)

    def test_unknown_class_uses_default(self):
        self.assertIsNone(share_class("国联安鑫享混合"))
        self.assertEqual(purchase_fee_rate("国联安鑫享混合"), DEFAULT_PURCHASE_FEE)

    def test_lof_not_treated_as_free(self):
        """LOF 不能被误判成免费（否则会低估成本）"""
        self.assertEqual(purchase_fee_rate("某某成长LOF"), DEFAULT_PURCHASE_FEE)


class TestRedeemFeeRate(unittest.TestCase):
    def test_short_hold_uses_regulatory_floor(self):
        self.assertEqual(redeem_fee_rate(None, 3), 0.015)
        self.assertEqual(redeem_fee_rate("0.10%", 3), 0.015)

    def test_seven_days_or_more_is_free(self):
        for d in (7, 30, 365):
            self.assertEqual(redeem_fee_rate(None, d), 0.0)

    def test_fund_own_rate_wins_when_higher(self):
        self.assertAlmostEqual(redeem_fee_rate("2.00%", 3), 0.02, places=6)


if __name__ == "__main__":
    unittest.main()
