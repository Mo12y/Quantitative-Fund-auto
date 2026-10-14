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


# =====================================================================
# 2026-09-28：`is_temp_applicable` —— 把"声明"变成**可执行的判定**
#
# 背景：光有 §8.6 的 scope 声明还不够。调仓顾问当时用 `type_bucket`（资产类别 SSOT）
# 算"你当前权益"，把 QDII-海外与黄金都算进去，再与温度给的目标相比 ——
# 实测同一账户 99.9%（含 QDII/黄金）vs 45.0%（仅 A 股权益），
# 减仓金额 ¥555 vs ¥85（差 6.5 倍），且等于"拿 A 股估值信号去卖纳指和黄金"。
# =====================================================================

class TestIsTempApplicable:
    """判定表：类型字符串全部取自本库 27,864 只基金的**真实取值**。"""

    APPLICABLE = [
        "指数型-股票",       # A 股指数（中证500/科创芯片/创业板AI 联接）
        "混合型-偏股",
        "混合型-平衡",
        "混合型-灵活",
        "混合型-灵活配置",
        "股票型",
    ]
    NOT_APPLICABLE = [
        "指数型-海外股票",   # QDII 联接（用户持仓里的纳指/亚太）★ 含"指数"但必须排除
        "指数型-其他",       # 上海金 ETF 联接（商品）★ 含"指数"但必须排除
        "指数型-固收",       # 债券指数
        "混合型-偏债",       # 以债为主
        "QDII-普通股票", "QDII-混合偏股", "QDII-FOF", "QDII-REITs", "QDII-商品",
        "FOF-均衡型", "FOF-稳健型",
        "债券型-长债", "债券型-可转债", "债券型-混合二级",
        "货币型", "商品", "REITs", "Reits", "其他", "",
    ]

    def test_applicable_types(self):
        from src.analysis.thermometer import is_temp_applicable
        for t in self.APPLICABLE:
            assert is_temp_applicable(t) is True, "%s 应属温度适用范围" % t

    def test_not_applicable_types(self):
        from src.analysis.thermometer import is_temp_applicable
        for t in self.NOT_APPLICABLE:
            assert is_temp_applicable(t) is False, "%s 不应属温度适用范围" % t

    def test_exclude_check_runs_before_equity_keywords(self):
        """顺序纪律：`指数型-海外股票` 同时含「指数」（权益词）与「海外」（排除词）——
        必须先判排除，顺序反了就把 QDII 当成 A 股权益（这正是当初的事故）。"""
        from src.analysis.thermometer import is_temp_applicable
        assert is_temp_applicable("指数型-海外股票") is False
        assert is_temp_applicable("指数型-其他") is False

    def test_not_the_same_as_type_bucket(self):
        """反向哨兵：`type_bucket` 把 QDII/黄金算 equity（用于同类对比是对的），
        但**不能**拿它当温度适用范围 —— 两者必须给出不同结论，否则等于没修。"""
        from src.analysis.fund_scorer import type_bucket
        from src.analysis.thermometer import is_temp_applicable
        for t in ("指数型-海外股票", "指数型-其他"):
            assert type_bucket(t) == "equity", "%s 在资产类别上确实是权益类" % t
            assert is_temp_applicable(t) is False, "%s 但温度不覆盖它" % t

    def test_unknown_type_is_not_applicable(self):
        """类型未知 → 不猜成适用（本项目"算不了 ≠ 通过"的一贯做法）。"""
        from src.analysis.thermometer import is_temp_applicable
        assert is_temp_applicable(None) is False
        assert is_temp_applicable("") is False
        assert is_temp_applicable("某新类型") is False
