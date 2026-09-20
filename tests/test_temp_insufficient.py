"""F-02 回归：全维度缺失时不得给出兜底温度读数。

黑箱验收审计发现：PE/PB/ERP/量能/情绪五个维度全缺时，旧实现返回
`temperature = 50.0`，并顺着档位分类给出"适中 / 保持定投 / 建议权益 35%"，
以及基于该温度的调仓指令 —— 相当于把"没有数据"伪装成一个中性真实读数。

修复后：`temperature = None` + `insufficient_data = True`，
`target_equity_pct = None`；连带 rebalance / strategy 都不再给建议。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis.rebalance_advisor import RebalanceAdvisor
from src.analysis.strategy_engine import StrategyEngine
from src.analysis.thermometer import MarketThermometer
from src.data.database import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "empty.db"))
    yield d
    d.close()


class TestTemperatureInsufficient:
    def test_all_dimensions_missing_gives_no_reading(self, db):
        t = MarketThermometer(db).get_temperature()
        assert t["insufficient_data"] is True
        assert t["temperature"] is None                    # 旧实现这里是 50.0
        assert t["target_equity_pct"] is None              # 旧实现这里是 35.0
        assert t["level"] == "unknown"
        assert t["level_desc"] == "数据不足"
        # 五个维度都要如实列为缺失
        assert set(t["degraded_dimensions"]) == {
            "pe_percentile", "pb_percentile", "erp", "volume", "sentiment"}
        # 组件全为 None（不能悄悄填 0 或 50）
        assert all(v is None for v in t["components"].values())

    def test_rebalance_gives_no_instruction(self, db):
        rb = RebalanceAdvisor(db).analyze()
        assert rb["instructions"] == []                    # 旧实现会给出大额买卖指令
        assert rb["target_equity_pct"] is None
        assert rb["need_rebalance"] is False
        assert "temperature" in rb["degraded"]
        assert "数据不足" in rb["summary"]["verdict"]

    def test_strategy_does_not_rebalance(self, db):
        should, reason = StrategyEngine(db).should_rebalance()
        assert should is False
        assert "数据不足" in reason

    def test_cost_estimate_guarded(self, db):
        """无数据时不因 target_equity_pct=None 抛异常（空仓会先走"空仓"分支）。"""
        est = StrategyEngine(db)._estimate_rebalance_cost()
        assert est["total_cost"] == 0
        assert "None" not in str(est)

    def test_plain_report_text_handles_none(self, db):
        """报告文本层遇到 temperature=None 不能出现 'None°C'。"""
        from src.output.reporter import WeeklyReporter
        t = MarketThermometer(db).get_temperature()
        txt = WeeklyReporter._generate_minimal_plain(t)
        assert "None" not in txt
        assert "数据不足" in txt
