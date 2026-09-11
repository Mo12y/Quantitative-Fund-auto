"""
基金板块归类单元测试。

重点覆盖 A7 修复：黄金类基金必须归入「黄金对冲」，不能被「周期资源」的
"黄金"关键词遮蔽（旧顺序下黄金板块几乎不可达）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import fund_boards


class TestGoldBoard(unittest.TestCase):
    def test_gold_funds_go_to_gold_board(self):
        for name in ("国泰黄金ETF联接C", "黄金主题混合", "易方达黄金ETF", "博时黄金ETF联接A"):
            self.assertEqual(fund_boards.classify(name), "黄金对冲", name)

    def test_precious_metal_goes_to_gold_board(self):
        self.assertEqual(fund_boards.classify("华安贵金属主题"), "黄金对冲")

    def test_other_resource_not_stolen(self):
        """非黄金的资源类仍归周期资源 —— 修黄金不能把有色金属也带走"""
        self.assertEqual(fund_boards.classify("有色金属ETF联接"), "周期资源")
        self.assertEqual(fund_boards.classify("煤炭指数基金"), "周期资源")

    def test_unknown_goes_to_other(self):
        self.assertEqual(fund_boards.classify("某某灵活配置"), "其他")


if __name__ == "__main__":
    unittest.main()
