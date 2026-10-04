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


class TestDebtNotStolenByEquityKeywords(unittest.TestCase):
    """A2（2026-10-04）：债务/货币类**不得**被权益关键词抢走。

    实测背景：`fund_info` 27,864 只里，名称含「债/固收/货币/利率/存单…」的有 9,082 只，
    其中 **199 只**被纯子串匹配误分进权益主题 —— **177 只落「金融地产」**
    （「中债0-3年**政策性金融**债」「**银行**间…信用债」「中银**证券**安进债券」），
    另有 22 只落「科技综合」。后果：板块分榜与 `overview.stats.board_alloc` 都被污染。
    """

    def test_name_fallback_when_no_type(self):
        """拿不到 `fund_type` 时（holdings 表 SELECT * 没类型列）名称兜底必须生效。"""
        for name in ("景顺长城政策性金融债A", "大成安汇金融债E",
                     "国联银行间1-3年中高等级信用债指数A",
                     "中银证券安进债券A", "兴业中债1-3政策性金融债A"):
            self.assertEqual(fund_boards.classify(name), fund_boards.BOND_BOARD, name)

    def test_money_market_goes_to_cash(self):
        for name in ("中银证券现金管家货币A", "某某货币B"):
            self.assertEqual(fund_boards.classify(name), fund_boards.CASH_BOARD, name)

    def test_structured_type_wins(self):
        """有 `fund_type` 就以它为准（名称可以完全不像债券）。"""
        for ftype in ("债券型-长债", "债券型-中短债", "指数型-固收", "混合型-偏债"):
            self.assertEqual(fund_boards.classify("某某主题", ftype),
                             fund_boards.BOND_BOARD, ftype)
        self.assertEqual(fund_boards.classify("某某", "货币型-普通货币"),
                         fund_boards.CASH_BOARD)

    def test_equity_financials_are_not_swallowed(self):
        """⭐ **不能误伤**：银行/证券类的**权益**基金必须仍留在「金融地产」。

        这是本修复最容易翻车的地方 —— 判据是"类型优先"，不是"见'金融'就当债"。
        """
        self.assertEqual(
            fund_boards.classify("某某银行ETF联接C", "指数型-股票"), "金融地产")
        self.assertEqual(
            fund_boards.classify("某某证券公司指数", "指数型-股票"), "金融地产")

    def test_board_of_funds_reads_type_from_either_key(self):
        """类型字段自动识别 `fund_type` 与 `type`（Web 池子用的是 `type`）。"""
        rows = [
            {"fund_name": "某某主题", "fund_type": "债券型-长债", "amount": 50.0},
            {"fund_name": "某某主题", "type": "货币型-普通货币", "amount": 50.0},
        ]
        amts = fund_boards.board_of_funds(rows)
        self.assertEqual(amts.get(fund_boards.BOND_BOARD), 50.0)
        self.assertEqual(amts.get(fund_boards.CASH_BOARD), 50.0)
        self.assertNotIn(fund_boards._OTHER, amts)


if __name__ == "__main__":
    unittest.main()
