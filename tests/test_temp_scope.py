"""市场温度适用范围测试（数据源扩展计划书 §8.6）。

问题：温度由 **A 股权益估值**（沪深300/中证500/上证50 的 PE/PB 分位 + ERP）构成，
却曾被用来给出"降权益 → 买债券"的仓位建议 —— 逻辑上不成立（债券应有自己的口径）。
修复：温度返回里显式携带 `scope`，声明适用/不适用标的，供上层与前端如实标注。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.thermometer import TEMPERATURE_SCOPE  # noqa: E402


def test_scope_declares_a_share_equity_only():
    assert any("A 股权益" in s for s in TEMPERATURE_SCOPE["applies_to"])
    for t in ("债券型", "货币型", "QDII-海外", "商品/黄金", "Reits"):
        assert t in TEMPERATURE_SCOPE["not_applicable_to"], f"{t} 必须被声明为不适用"


def test_scope_note_gives_per_class_alternatives():
    """不适用还不够 —— 必须指出那几类该用什么口径（否则只剩一句空话）。"""
    note = TEMPERATURE_SCOPE["note"]
    assert "国债" in note and "汇率" in note and "实际利率" in note


def test_get_temperature_exposes_scope(tmp_path):
    """温度返回必须携带 scope（数据不足时也要有 —— 否则上层无从标注）。"""
    from src.analysis.thermometer import MarketThermometer
    from src.data.database import Database

    db = Database(str(tmp_path / "temp.db"))
    try:
        t = MarketThermometer(db).get_temperature()
    finally:
        db.close()

    assert "scope" in t, "温度返回必须携带适用范围"
    assert t["scope"]["applies_to"] == TEMPERATURE_SCOPE["applies_to"]
    assert "债券型" in t["scope"]["not_applicable_to"]
