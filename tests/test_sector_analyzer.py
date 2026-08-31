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


if __name__ == "__main__":
    unittest.main()
