"""
基金质量筛选器单元测试：纯逻辑 + 临时库合成数据验证风险标签。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.fund_scorer import FundScreener
from src.data.database import Database

CODE = "999999"


def make_db_with_nav(
    daily_ret: float = 0.001,
    days: int = 250,
    drop: tuple = None,
    surge_from: int = None,
    surge_daily: float = 0.0065,
    fee: float = 0.6,
    size: float = 10.0,
):
    """
    创建临时库并写入一只合成基金。

    Args:
        daily_ret: 每日收益率（平稳段）
        drop: (起始idx, 总跌幅%) —— 模拟大跌
        surge_from: 从该 idx 起进入暴涨段（用于追涨测试）
        surge_daily: 暴涨段每日收益率
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    db.upsert_fund_info({
        "fund_code": CODE,
        "fund_name": "测试基金",
        "fund_type": "混合型",
        "establish_date": "2020-01-01",
        "fund_size": size,
        "mgt_fee": fee,
        "custodian_fee": 0.2,
    })

    nav = 1.0
    records = []
    start = pd.Timestamp("2024-01-01")
    for i in range(days):
        if drop and drop[0] <= i < drop[0] + 10:
            nav *= (1 - drop[1] / 10)          # 10 天跌掉 drop[1]
        elif surge_from is not None and i >= surge_from:
            nav *= (1 + surge_daily)           # 暴涨段
        else:
            nav *= (1 + daily_ret)
        d = (start + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        records.append((CODE, d, round(nav, 4), round(nav, 4), round(daily_ret, 6)))
    db.insert_nav_batch(records)
    return db, path


class TestRiskLabeling(unittest.TestCase):
    """风险标签判定（核心逻辑，临时库合成数据）"""

    def _screen(self, db, fee=None, size=None, fund_type="混合型-偏股"):
        """注意 fund_type 用**可映射到参照系组的真实类型**（批次 A 起动量阈值
        取自组内 P90，类型未映射时检查会正确地声明「类型未映射」而不是给结论）。"""
        screener = FundScreener(db)
        info = {
            "fund_code": CODE,
            "fund_name": "测试基金",
            "fund_type": fund_type,
            "establish_date": "2020-01-01",
            "fund_size": size if size is not None else 10.0,
            "mgt_fee": fee if fee is not None else 0.6,
            "custodian_fee": 0.2,
        }
        return screener._screen_single_fund(CODE, info)

    def test_healthy_fund_is_green(self):
        """平稳上涨 + 低费率 + 规模适中 → 🟢 稳健

        注意（批次 A）：追涨阈值改为**组内 P90** 后，"平稳上涨"的合成基金
        （3 月约 +6.3%）会超过 A 组实测 P90(3.93%) 而被判追涨 —— 这是新语义的
        正确行为。本测试只针对费率/规模，故把参照系换成"P90 很高"的桩，
        隔离动量维度，避免把两件事混在一个断言里。
        """
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from unittest.mock import patch
        from src.analysis import peer_percentile as pp
        neutral = {"version": pp.CACHE_VERSION,
                   "groups": {"A 偏股混合": {"n": 500, "quality_level": "full",
                                             "grid": {"momentum_3m": {"p50": 0.0, "p75": 5.0,
                                                                      "p90": 99.0}}}}}
        db, path = make_db_with_nav()
        try:
            with patch.object(FundScreener, "_peer_dist", lambda self: neutral):
                r = self._screen(db)
            self.assertIsNotNone(r)
            self.assertEqual(r["risk_label"], "🟢 稳健")
        finally:
            db.close()
            os.unlink(path)

    def test_fee_no_longer_uses_absolute_threshold(self):
        """A2 改口径（2026-09-22）：费率**不再**用绝对阈值判失败。

        原因：旧 `max_total_fee=2.0`（管理费+托管费）实测近似失效（全库 >2.0 仅 10 只），
        且当时 `mgt_fee` 里装的其实是**申购手续费**（已正名）。新口径 = **TER 的组内分位**
        （文献：晨星 2016/2025 显示费率预测力最强，但必须**同类内比较**）。
        无参照系时如实声明，而不是凭绝对值把基金打成 🔴。
        """
        db, path = make_db_with_nav(fee=2.5)  # 管理费 2.5 + 托管 0.2 = TER 2.7%（属极贵）
        try:
            r = self._screen(db, fee=2.5)
            self.assertIsNotNone(r)
            # 不得仅因"TER 数值大"就给费率警告 —— 必须有同类参照系才判
            self.assertFalse(any("费率" in w for w in r["risk_reasons"]),
                             "不应基于绝对阈值给费率警告（A2 已改为组内分位）")
        finally:
            db.close()
            os.unlink(path)

    def test_high_drawdown_is_high_risk(self):
        """第100天起跌50% → 回撤超35%阈值 → 不合格/高风险"""
        db, path = make_db_with_nav(drop=(100, 0.5))  # 10天跌50% → 回撤约40%
        try:
            r = self._screen(db)
            self.assertIsNotNone(r)
            self.assertIn(r["risk_label"], ("不合格", "🔴 高风险"))
            self.assertTrue(any("回撤" in w for w in r["risk_reasons"]))
        finally:
            db.close()
            os.unlink(path)

    def test_momentum_surge_warns(self):
        """最后63天暴涨约50% → 超同组 P90 参考线 → 追涨警告 → 🟡 注意

        批次 A 起"追涨"阈值 = **组内 P90**（A1：相对概念）。因此本测试必须
        给定参照系缓存；否则会（正确地）声明"参照系未构建"而非给结论。
        """
        from unittest.mock import patch
        from src.analysis import peer_percentile as pp
        fake = {"version": pp.CACHE_VERSION,
                "groups": {"A 偏股混合": {"n": 500, "quality_level": "full",
                                          "grid": {"momentum_3m": {"p50": -10.0, "p75": -1.8,
                                                                   "p90": 3.93}}}}}
        db, path = make_db_with_nav(daily_ret=0.0001, surge_from=187)
        try:
            with patch.object(FundScreener, "_peer_dist", lambda self: fake):
                r = self._screen(db)
            self.assertIsNotNone(r)
            self.assertEqual(r["risk_label"], "🟡 注意")
            self.assertTrue(any("追涨" in w for w in r["risk_reasons"]))
        finally:
            db.close()
            os.unlink(path)

    def test_insufficient_nav_returns_none(self):
        """净值不足60条 → 返回 None（不评价）"""
        db, path = make_db_with_nav(days=30)
        try:
            r = self._screen(db)
            self.assertIsNone(r)
        finally:
            db.close()
            os.unlink(path)


class TestThresholds(unittest.TestCase):
    """质量门槛配置合理性（纯逻辑）"""

    def test_thresholds_are_sane(self):
        t = FundScreener.THRESHOLDS
        self.assertGreater(t["min_age_months"], 0)
        self.assertLess(t["min_size_yi"], t["max_size_yi"])
        self.assertLess(t["warn_total_fee"], t["max_total_fee"])
        self.assertGreater(t["momentum_warning"], 20)


class TestPoolSummary(unittest.TestCase):
    """筛选池统计摘要（纯逻辑）"""

    def test_empty_df(self):
        screener = FundScreener(None)
        s = screener.get_pool_summary(pd.DataFrame())
        self.assertEqual(s["total"], 0)

    def test_with_data(self):
        screener = FundScreener(None)
        df = pd.DataFrame([
            {"risk_label": "🟢 稳健", "mgt_fee": 0.6},
            {"risk_label": "🟢 稳健", "mgt_fee": 1.0},
            {"risk_label": "🟡 注意", "mgt_fee": 1.8},
        ])
        s = screener.get_pool_summary(df)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_risk"]["🟢 稳健"], 2)
        self.assertAlmostEqual(s["avg_fee"], 1.13, places=2)


if __name__ == "__main__":
    unittest.main()


# =====================================================================
# 2026-09-25：筛选池排序必须**确定性**（可复现）
# =====================================================================

class TestPoolOrderIsDeterministic:
    """`screen_funds` 的输出顺序不能随进程/输入顺序变。

    实测（修复前）：同一份数据、不同 `PYTHONHASHSEED` 给出的前 10 名**尾部完全不同**
      seed=1     → …020215, 006961, 006962
      seed=12345 → …006961, 020215, 009324
    根因两层：① 上游 `_get_funds_with_nav()` 是 **set**（迭代顺序随 hash 变）；
              ② `sort_values` 默认 quicksort **不稳定**，平局顺序无保证。
    后果：调仓顾问"建议买哪只"随运行变 —— 违反本项目「结论可复现」。
    """

    def _src(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return open(os.path.join(root, "src", "analysis", "fund_scorer.py"), encoding="utf-8").read()

    def test_sort_has_deterministic_tiebreak(self):
        s = self._src()
        i = s.find('df["_risk_order"] = df["risk_label"].map(risk_order)')
        assert i > 0, "排序段未找到（代码结构变了？）"
        seg = s[i:i + 1200]
        assert '"fund_code"' in seg, \
            "排序键末尾必须带 fund_code 作为确定性 tiebreak（否则平局顺序随 hash 变）"
        assert 'kind="mergesort"' in seg, \
            "必须用稳定排序 mergesort（默认 quicksort 不稳定）"

    def test_no_unstable_sort_without_tiebreak(self):
        """回归哨兵：排序键里一旦去掉 fund_code，本测试就失败。"""
        s = self._src()
        i = s.find('df.sort_values(["_risk_order", "quality_score", "mgt_fee"')
        # 允许出现，但必须是「…mgt_fee", "fund_code"]」这种带 tiebreak 的写法
        assert i < 0 or '"fund_code"]' in s[i:i + 160], \
            "发现不带 tiebreak 的排序键（会把不确定性带回来）"
