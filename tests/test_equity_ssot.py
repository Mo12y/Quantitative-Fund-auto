"""「权益类」口径 SSOT 回归哨兵 —— 数据源扩展计划书 §8.4（2026-09-27）。

背景：全项目曾有 **6 处、4 种口径**的"权益类"定义。其中两处算的是**同一件事**
（"当前权益占比"）：`reporter._calc_current_equity_pct` 的集合**漏 QDII**，
`rebalance_advisor` 的却含 QDII → 同一持仓在两个页面可能显示两个数字。
（实测：用户 2026-09-27 的 27 笔持仓恰好 0 差异 —— 其 QDII 类型是
"指数型-海外股票"含"指数"子串被旧口径兜住；但"QDII-普通股票"这类就会漏。）

修复：统一到 `fund_scorer.type_bucket`（SSOT）；`vol_predictor` 的白名单
改名 `VOL_MODEL_FUND_TYPES` 并显式声明"这不是权益类"。

本文件锁三件事：
  1. SSOT 判定表（含 QDII 各变体，即旧口径的漏点）；
  2. **同一件事一个数字**：reporter 的权益占比必须与 SSOT 一致；
  3. 反向哨兵：`VOL_MODEL_FUND_TYPES` **故意**比权益类窄（防"顺手统一"把 QDII 混进 vol 模型）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.fund_scorer import type_bucket          # noqa: E402
from src.output.reporter import WeeklyReporter                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. SSOT 判定表 ────────────────────────────────────────────────

@pytest.mark.parametrize("ftype,expected", [
    ("股票型", "equity"),
    ("股票型-普通", "equity"),
    ("股票型-标准指数", "equity"),
    ("股票型-增强指数", "equity"),
    ("混合型-偏股", "equity"),
    ("混合型-灵活配置", "equity"),
    ("指数型-股票", "equity"),
    ("指数型-海外股票", "equity"),     # QDII 联接（用户持仓的实际形态）
    ("QDII", "equity"),
    ("QDII-普通股票", "equity"),       # ← 旧 reporter 口径会漏
    ("QDII-混合", "equity"),           # ← 旧 reporter 口径会漏
    ("债券型-长债", "bond"),
    ("债券型-中短债", "bond"),
    ("货币型", "bond"),
    ("Reits", "bond"),
    ("", "bond"),
    (None, "bond"),
])
def test_type_bucket_table(ftype, expected):
    assert type_bucket(ftype) == expected


# ── 2. 同一件事一个数字 ───────────────────────────────────────────

def _portfolio(*pairs):
    return {"has_holdings": True,
            "total_market_value": float(sum(v for _, v in pairs)),
            "holdings_detail": [{"fund_type": t, "current_value": v} for t, v in pairs]}


def test_reporter_equity_pct_counts_qdii():
    """QDII 必须计入权益 —— 旧口径（`{"股票型","混合型","指数型",
    "混合型-偏股","混合型-灵活"}`）对 "QDII-普通股票" 会返回 0%。"""
    pf = _portfolio(("QDII-普通股票", 30.0), ("指数型-海外股票", 20.0), ("债券型-长债", 50.0))
    got = WeeklyReporter.__new__(WeeklyReporter)._calc_current_equity_pct(pf)
    assert got == pytest.approx(50.0), "QDII 两笔共 50/100 应算 50%（旧口径会算成 0%）"


def test_reporter_equity_pct_equals_ssot_definition():
    """reporter 的占比必须与 SSOT 手算**逐位一致**（防再次内联别的集合）。"""
    pf = _portfolio(("混合型-灵活配置", 10.0), ("QDII-混合", 15.0),
                    ("货币型", 25.0), ("股票型-增强指数", 50.0))
    got = WeeklyReporter.__new__(WeeklyReporter)._calc_current_equity_pct(pf)
    exp = (sum(d["current_value"] for d in pf["holdings_detail"]
               if type_bucket(d["fund_type"]) == "equity")
           / pf["total_market_value"] * 100)
    assert got == pytest.approx(exp)


def test_reporter_equity_pct_empty_portfolio():
    assert WeeklyReporter.__new__(WeeklyReporter)._calc_current_equity_pct({"has_holdings": False}) == 0
    assert WeeklyReporter.__new__(WeeklyReporter)._calc_current_equity_pct(
        {"has_holdings": True, "total_market_value": 0, "holdings_detail": []}) == 0


# ── 3. 反向哨兵：波动白名单 ≠ 权益类 ──────────────────────────────

def test_vol_model_whitelist_is_deliberately_narrower():
    """`VOL_MODEL_FUND_TYPES` 是**可预测性白名单**，刻意排除 QDII/混合型-灵活。

    它**不是**权益类（≠ type_bucket）。它比权益类窄，且用"精确类型列举"
    而非"子串关键词" —— 两者语义不同，不可互换。
    """
    from src.analysis.vol_predictor import VOL_MODEL_FUND_TYPES

    for t in ("QDII", "QDII-普通股票", "QDII-混合", "指数型-海外股票"):
        assert type_bucket(t) == "equity", f"{t} 属权益类"
        assert t not in VOL_MODEL_FUND_TYPES, f"{t} 不应在波动白名单（vol 特性不同）"
    # 混合型-灵活 同属权益类但不在白名单
    assert type_bucket("混合型-灵活配置") == "equity"
    assert "混合型-灵活配置" not in VOL_MODEL_FUND_TYPES
    # 白名单是精确列举，绝不等于 SSOT 的关键词表
    assert set(VOL_MODEL_FUND_TYPES) != {"股票型", "混合型", "指数型", "QDII"}


# ── 4. 调用方走 SSOT（正向静态哨兵） ──────────────────────────────

@pytest.mark.parametrize("rel", [
    "src/analysis/rebalance_advisor.py",
    "src/analysis/strategy_engine.py",
    "src/output/reporter.py",
])
def test_callers_go_through_ssot(rel):
    """三处"同一件事"的调用方必须引用 SSOT，且不得自行定义权益类口径。"""
    text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    assert "type_bucket" in text, f"{rel} 应走 SSOT type_bucket（§8.4）"
    assert "def type_bucket" not in text, f"{rel} 不得自行定义权益类口径"


def test_strategy_engine_is_equity_matches_ssot(tmp_path):
    """`StrategyEngine._is_equity` 的判定必须与 SSOT 一致（QDII 计入权益）。"""
    from src.analysis.strategy_engine import StrategyEngine
    from src.data.database import Database

    db = Database(str(tmp_path / "ssot.db"))
    try:
        db.upsert_fund_info({"fund_code": "Q1", "fund_name": "qdii", "fund_type": "QDII-普通股票"})
        db.upsert_fund_info({"fund_code": "B1", "fund_name": "bond", "fund_type": "债券型-长债"})

        class _Stub:
            """只提供 `.db`（`_is_equity` 只用到它），避免拉起温度计/筛选池。"""
            def __init__(self, db):
                self.db = db

        s = _Stub(db)
        assert StrategyEngine._is_equity(s, "Q1") is True
        assert StrategyEngine._is_equity(s, "B1") is False
        assert StrategyEngine._is_equity(s, "NO_SUCH_CODE") is False
    finally:
        db.close()
