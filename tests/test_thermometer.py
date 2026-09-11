"""
市场温度计单元测试 —— 覆盖不依赖数据库的纯逻辑。

运行方式:
    python -m unittest discover tests
    # 或
    python tests/test_thermometer.py
"""

import os
import sys
import unittest

# 确保项目根目录在 sys.path 中，以便 import src 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.thermometer import MarketThermometer


class TestTemperatureClassification(unittest.TestCase):
    """温度数值 → 等级/建议/目标仓位 的映射逻辑"""

    def setUp(self):
        # db 传 None：_classify_temperature 不访问数据库
        self.t = MarketThermometer(db=None)

    def test_cold_range(self):
        """0-20° 极冷，目标权益仓位 70%"""
        level, desc, action, equity = self.t._classify_temperature(10)
        self.assertEqual(level, "cold")
        self.assertEqual(desc, "🧊 极冷")
        self.assertEqual(equity, 0.70)

    def test_normal_range(self):
        """40-60° 适中，目标权益仓位 35%"""
        level, desc, action, equity = self.t._classify_temperature(50)
        self.assertEqual(level, "normal")
        self.assertEqual(equity, 0.35)

    def test_hot_range(self):
        """80-100° 过热，目标权益仓位 5%"""
        level, desc, action, equity = self.t._classify_temperature(90)
        self.assertEqual(level, "hot")
        self.assertEqual(equity, 0.05)

    def test_lower_boundary_inclusive(self):
        """边界值：20° 应落入 cool（20 <= t < 40）"""
        level, _, _, _ = self.t._classify_temperature(20)
        self.assertEqual(level, "cool")

    def test_upper_boundary_inclusive(self):
        """边界值：80° 应落入 hot（80 <= t < 101）"""
        level, _, _, _ = self.t._classify_temperature(80)
        self.assertEqual(level, "hot")


class _FakeDB:
    """最小假库：只提供温度计离线路径需要的方法。"""

    def __init__(self, index_val=None, daily_rows=None):
        self._iv = index_val or []
        self._rows = daily_rows or []

    def get_index_valuation(self):
        return self._iv

    def get_index_daily(self, code):
        return self._rows

    def get_trade_date_set(self):
        return set()


def _daily(close_series, volume=None):
    """构造指数日线行（close 必填；volume 传 None 表示量能全缺失）。"""
    rows = []
    for i, c in enumerate(close_series):
        rows.append({
            "trade_date": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "close": c,
            "volume": None if volume is None else volume[i],
        })
    return rows


_IV3 = [
    {"index_code": "000016", "index_name": "上证50", "pe": 12.0, "pe_percentile": 49.8,
     "pb_percentile": 28.4},
    {"index_code": "000300", "index_name": "沪深300", "pe": 13.0, "pe_percentile": 56.6,
     "pb_percentile": 28.7},
    {"index_code": "000905", "index_name": "中证500", "pe": 30.0, "pe_percentile": 58.6,
     "pb_percentile": 53.2},
]


class TestVolumeDegradation(unittest.TestCase):
    """A1: 量能缺失时剔除该维度并重新归一化，不注入 50.0"""

    def _series(self):
        # 一段确定性的价格序列（>100 点），保证 close 路径有效
        return [100 + (i % 7) * 0.5 + i * 0.01 for i in range(150)]

    def test_volume_missing_is_degraded_and_renormalized(self):
        db = _FakeDB(index_val=_IV3, daily_rows=_daily(self._series(), volume=None))
        t = MarketThermometer(db, live=False).get_temperature()

        self.assertEqual(t["degraded_dimensions"], ["volume"])
        self.assertIsNone(t["components"]["volume_score"])

        c = t["components"]
        w = {"pe": 0.25, "pb": 0.15, "erp": 0.25, "sent": 0.15}
        norm = (w["pe"] * c["pe_score"] + w["pb"] * c["pb_score"]
                + w["erp"] * c["erp_score"] + w["sent"] * c["sentiment_score"]) / sum(w.values())
        injected = (w["pe"] * c["pe_score"] + w["pb"] * c["pb_score"]
                    + w["erp"] * c["erp_score"] + 0.20 * 50.0
                    + w["sent"] * c["sentiment_score"])
        self.assertAlmostEqual(t["temperature"], round(norm, 1), places=1)
        self.assertNotAlmostEqual(t["temperature"], round(injected, 1), places=1)

    def test_volume_present_is_used(self):
        series = self._series()
        vol = [1e8 + i * 1e6 for i in range(len(series))]
        db = _FakeDB(index_val=_IV3, daily_rows=_daily(series, volume=vol))
        t = MarketThermometer(db, live=False).get_temperature()
        self.assertEqual(t["degraded_dimensions"], [])
        self.assertIsNotNone(t["components"]["volume_score"])


class TestValuationRenormalization(unittest.TestCase):
    """A3: PE/PB 按实际存在的指数权重归一化，缺指数不再整体减半"""

    def test_single_index_not_halved(self):
        iv = [_IV3[1]]  # 只有沪深300
        db = _FakeDB(index_val=iv, daily_rows=_daily([100 + i for i in range(150)], volume=None))
        t = MarketThermometer(db, live=False).get_temperature()
        # 只有沪深300 → pe_score 就是它自己的分位（旧实现会减半成 28.3）
        self.assertAlmostEqual(t["components"]["pe_score"], 56.6, places=1)
        self.assertAlmostEqual(t["components"]["pb_score"], 28.7, places=1)

    def test_three_indices_weighted_average(self):
        db = _FakeDB(index_val=_IV3, daily_rows=_daily([100 + i for i in range(150)], volume=None))
        t = MarketThermometer(db, live=False).get_temperature()
        pe = 0.5 * 56.6 + 0.3 * 58.6 + 0.2 * 49.8
        self.assertAlmostEqual(t["components"]["pe_score"], round(pe, 1), places=1)

    def test_find_index_exact_code(self):
        """A9.3: "50" 不得再命中 "中证500"（000905）"""
        t = MarketThermometer(db=None)
        self.assertEqual(t._find_index(_IV3, "000016")["index_code"], "000016")
        self.assertEqual(t._find_index(_IV3, "000905")["index_code"], "000905")
        self.assertIsNone(t._find_index(_IV3, "500"))  # 非精确代码 → 不匹配


class TestEquityInterpolation(unittest.TestCase):
    """A2: 温度→仓位连续（线性插值），单调不增"""

    def setUp(self):
        self.t = MarketThermometer(db=None)

    def test_continuous_at_band_boundary(self):
        """跨档处 ±0.1° 的仓位变化 < 2 个百分点（旧实现跳 20pp）"""
        for boundary in (20, 40, 60, 80):
            a = self.t._calc_target_equity(boundary - 0.05)
            b = self.t._calc_target_equity(boundary + 0.05)
            self.assertLess(abs(b - a), 2.0, f"boundary={boundary} 跳变 {a}→{b}")

    def test_monotonic_non_increasing(self):
        prev = 200.0
        x = 0.0
        while x <= 100.0:
            cur = self.t._calc_target_equity(x)
            self.assertLessEqual(cur, prev + 1e-9, f"温度 {x} 处仓位回升")
            prev = cur
            x += 0.5

    def test_band_centres_match_levels(self):
        """锚点仍落在原档位语义值上（10°/30°/50°/70°/90°）"""
        self.assertAlmostEqual(self.t._calc_target_equity(10), 70.0, places=1)
        self.assertAlmostEqual(self.t._calc_target_equity(50), 35.0, places=1)
        self.assertAlmostEqual(self.t._calc_target_equity(90), 5.0, places=1)


class TestDivergenceDetection(unittest.TestCase):
    """PE/PB/ERP 三维度分歧检测逻辑"""

    def setUp(self):
        self.t = MarketThermometer(db=None)

    def test_none_dimensions_ignored(self):
        """缺失维度不应让分歧检测崩溃，可用维度<2 时返回未知"""
        r = self.t._detect_divergence(None, 30, None)
        self.assertEqual(r["level"], "未知")

    def test_consistent_signals(self):
        """三维度接近 → 一致"""
        r = self.t._detect_divergence(50, 48, 52)
        self.assertEqual(r["level"], "一致")

    def test_mild_divergence(self):
        """最大差值 15-30 → 轻微分歧"""
        r = self.t._detect_divergence(70, 50, 55)
        self.assertEqual(r["level"], "轻微分歧")

    def test_strong_divergence(self):
        """最大差值 >= 30 → 显著分歧"""
        r = self.t._detect_divergence(80, 30, 50)
        self.assertEqual(r["level"], "显著分歧")

    def test_pe_high_pb_low(self):
        """PE 偏高 PB 偏低 → 显著分歧，且给出盈利下行期提示"""
        r = self.t._detect_divergence(85, 30, 50)
        self.assertEqual(r["level"], "显著分歧")
        self.assertIn("盈利下行", r["message"])


if __name__ == "__main__":
    unittest.main()
