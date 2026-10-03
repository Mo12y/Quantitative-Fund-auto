"""回本门槛（P2）测试。

三条地基：
1. **7 天惩罚**：持有 <7 天赎回费 1.5%（与监管下限孰高）；≥7 天为 0。
2. **份额类别**：C/E/I 类免前端申购费；A 类/未知取保守默认 0.15%。
3. **不重写费率**：本模块的数字必须与 `portfolio` 的单一真源**逐位一致**
   （若哪天有人在 breakeven 里自建费率表，这条会红）。
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.breakeven import analyze, lot_breakeven  # noqa: E402
from src.analysis.portfolio import (  # noqa: E402
    purchase_fee_rate, redeem_fee_rate,
)

TODAY = date(2026, 10, 3)


# ── 单批次 ────────────────────────────────────────────────────────

def test_penalty_period_charges_1_5pct():
    """持有 3 天（<7）→ 赎回费 1.5%，且**门槛等于它**。"""
    lot = {"fund_code": "T1", "shares": 100, "buy_amount": 100,
           "confirm_date": "2026-09-30", "current_nav": 1.0}
    r = lot_breakeven(lot, {"fund_name": "某C类基金", "redeem_fee": None}, TODAY)
    assert r["held_days"] == 3
    assert r["redeem_fee_pct"] == pytest.approx(1.5)
    assert r["in_penalty"] is True
    assert r["days_to_free"] == 4          # 满 7 天 → 10-07
    assert r["free_date"] == "2026-10-07"


def test_after_seven_days_is_free():
    lot = {"fund_code": "T1", "shares": 100, "buy_amount": 100,
           "confirm_date": "2026-09-26", "current_nav": 1.0}
    r = lot_breakeven(lot, {"fund_name": "某C类基金", "redeem_fee": None}, TODAY)
    assert r["held_days"] == 7
    assert r["redeem_fee_pct"] == 0.0
    assert r["in_penalty"] is False
    assert r["days_to_free"] == 0


def test_c_class_has_no_purchase_fee():
    lot = {"fund_code": "T1", "shares": 100, "buy_amount": 100,
           "confirm_date": "2026-06-01", "current_nav": 1.0}
    c = lot_breakeven(lot, {"fund_name": "南方纳斯达克100指数发起(QDII)C"}, TODAY)
    a = lot_breakeven(lot, {"fund_name": "某主动股票A"}, TODAY)
    assert c["buy_fee_pct"] == 0.0
    assert a["buy_fee_pct"] == pytest.approx(0.15)


def test_matches_single_source_of_truth():
    """⭐ 本模块的数字必须与 `portfolio` 的真源**逐位一致**（防止自建费率表）。"""
    for days in (0, 3, 6, 7, 30, 400):
        lot = {"fund_code": "T1", "shares": 1, "buy_amount": 1, "current_nav": 1.0,
               "confirm_date": (TODAY - timedelta(days=days)).isoformat()}
        r = lot_breakeven(lot, {"fund_name": "x", "redeem_fee": None}, TODAY)
        expect = redeem_fee_rate(None, days) * 100
        assert r["redeem_fee_pct"] == pytest.approx(round(expect, 3)), "持有 %d 天" % days
    assert purchase_fee_rate("x") * 100 == pytest.approx(0.15)


def test_redeem_cost_and_net():
    """净卖收益 = (市值 − 赎回费 − 成本) / 成本。"""
    lot = {"fund_code": "T1", "shares": 100, "buy_amount": 100,
           "confirm_date": "2026-09-30", "current_nav": 1.0}     # 持有 3 天，惩罚期
    r = lot_breakeven(lot, {"fund_name": "x", "redeem_fee": None}, TODAY)
    assert r["market_value"] == pytest.approx(100.0)
    assert r["redeem_cost"] == pytest.approx(1.5)
    assert r["net_if_sell"] == pytest.approx(98.5)
    assert r["net_if_sell_pct"] == pytest.approx(-1.5)


def test_missing_nav_is_none_not_zero():
    """取不到净值 → 相关字段为 None（**不写成 0**，否则"零盈亏"是假的）。"""
    lot = {"fund_code": "T1", "shares": 100, "buy_amount": 100,
           "confirm_date": "2026-09-30"}
    r = lot_breakeven(lot, {"fund_name": "x"}, TODAY)
    assert r["market_value"] is None and r["net_if_sell"] is None


# ── 汇总 ──────────────────────────────────────────────────────────

def _mk_db(tmp_path):
    from src.data.database import Database

    db = Database(str(tmp_path / "be.db"))
    db.upsert_fund_info({"fund_code": "T1", "fund_name": "测试C类基金",
                         "fund_type": "混合型", "redeem_fee": None})
    db.insert_nav_batch([("T1", "2026-10-02", 1.0, 1.0, 0.0)])
    for d, sh, amt in (("2026-09-30", 100.0, 100.0),     # 持有 3 天 → 惩罚期
                       ("2026-06-01", 50.0, 50.0)):       # 持有很久 → 免费
        db.conn.execute(
            "INSERT INTO holdings (fund_code, buy_date, confirm_date, shares, buy_amount, status)"
            " VALUES ('T1',?,?,?,?,'holding')", (d, d, sh, amt))
    db.conn.commit()
    return db


def test_analyze_summary(tmp_path):
    db = _mk_db(tmp_path)
    try:
        r = analyze(db, today=TODAY)
        assert r["n_lots"] == 2
        assert r["n_in_penalty"] == 1, "只有持有 3 天那笔在惩罚期"
        assert r["penalty_cost"] == pytest.approx(1.5)
        assert r["total_market_value"] == pytest.approx(150.0)
        assert any("7" in n for n in r["notes"]), "必须声明『只对 <7 天计费』的局限"
    finally:
        db.close()


def test_analyze_empty_is_explicit(tmp_path):
    from src.data.database import Database

    db = Database(str(tmp_path / "e.db"))
    try:
        assert "error" in analyze(db, today=TODAY)
    finally:
        db.close()
