"""
行业板块分析器单元测试：覆盖纯逻辑方法 _score_all（zscore 标准化 + 综合评分）。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.sector_analyzer import SectorAnalyzer


class TestScoreAll(unittest.TestCase):
    def setUp(self):
        self.analyzer = SectorAnalyzer()

    def _make_data(self):
        """A=强势行业, B=中性, C=弱势行业"""
        return [
            {"name": "A", "ret_1m": 5, "ret_3m": 15, "ret_6m": 20,
             "ma_ratio": 1.1, "ma_slope": 1.0, "volatility": 20, "max_dd_6m": 10},
            {"name": "B", "ret_1m": 0, "ret_3m": 0, "ret_6m": 0,
             "ma_ratio": 1.0, "ma_slope": 0.0, "volatility": 30, "max_dd_6m": 20},
            {"name": "C", "ret_1m": -5, "ret_3m": -10, "ret_6m": -15,
             "ma_ratio": 0.9, "ma_slope": -1.0, "volatility": 40, "max_dd_6m": 30},
        ]

    def test_ranked_by_strength(self):
        """强势行业排前、弱势行业排后"""
        result = self.analyzer._score_all(self._make_data())
        names = [r["name"] for r in result]
        self.assertEqual(names[0], "A")
        self.assertEqual(names[-1], "C")

    def test_output_has_score_and_rank(self):
        result = self.analyzer._score_all(self._make_data())
        for r in result:
            self.assertIn("score", r)
            self.assertIn("rank", r)
        # rank 从1开始且递增
        ranks = [r["rank"] for r in result]
        self.assertEqual(ranks, [1, 2, 3])

    def test_empty_input(self):
        self.assertEqual(self.analyzer._score_all([]), [])

    def test_constant_series_no_crash(self):
        """所有行业指标相同 → zscore 除数为 0，应返回 0 分而非崩溃"""
        data = [
            {"name": "X", "ret_1m": 1, "ret_3m": 1, "ret_6m": 1,
             "ma_ratio": 1.0, "ma_slope": 0.0, "volatility": 30, "max_dd_6m": 10},
        ] * 3
        result = self.analyzer._score_all(data)
        self.assertEqual(len(result), 3)
        # 所有分数相同
        scores = {r["score"] for r in result}
        self.assertEqual(len(scores), 1)


class TestValueCandidates(unittest.TestCase):
    """A8: 超跌反弹候选必须同时满足 近1月跌超8% + 近6月>0 + 站上120日线"""

    def setUp(self):
        self.analyzer = SectorAnalyzer()

    def _row(self, name, ret_1m, ret_6m, ma120_ratio):
        return {"name": name, "ret_1m": ret_1m, "ret_3m": 0.0, "ret_6m": ret_6m,
                "ma_ratio": 1.0, "ma120_ratio": ma120_ratio, "ma_slope": 0.0,
                "volatility": 20, "max_dd_6m": 10}

    def test_persistent_downtrend_not_selected(self):
        """持续单边下跌（近6月<0）不得被选为超跌反弹候选 —— 这是"接飞刀"防护"""
        scored = [self._row("持续下跌", ret_1m=-15, ret_6m=-25, ma120_ratio=0.7)]
        self.assertEqual(self.analyzer._pick_value_candidates(scored), [])

    def test_below_120ma_not_selected(self):
        """跌破120日线（趋势已破）不得入选"""
        scored = [self._row("破位", ret_1m=-10, ret_6m=5, ma120_ratio=0.95)]
        self.assertEqual(self.analyzer._pick_value_candidates(scored), [])

    def test_valid_pullback_selected(self):
        """短期超跌但长期趋势未破 + 站上120日线 → 入选"""
        scored = [self._row("健康回调", ret_1m=-9, ret_6m=8, ma120_ratio=1.02)]
        got = self.analyzer._pick_value_candidates(scored)
        self.assertEqual([s["name"] for s in got], ["健康回调"])

    def test_sorted_by_worst_recent(self):
        scored = [
            self._row("小跌", ret_1m=-8.5, ret_6m=3, ma120_ratio=1.01),
            self._row("大跌", ret_1m=-20, ret_6m=6, ma120_ratio=1.05),
        ]
        got = self.analyzer._pick_value_candidates(scored)
        self.assertEqual([s["name"] for s in got], ["大跌", "小跌"])


if __name__ == "__main__":
    unittest.main()
