"""XIRR 测试（批次 3 统一评价口径的地基）。

核心要证明的一件事：**定投的 XIRR 明显高于"盈亏 ÷ 投入成本"** ——
因为定投的资金平均占用时间短，而简单收益率没有时间维度，
这正是《外部参考整合方案与任务书》里"点位分档 83.60% vs 固定 38.51% 不可直接比"的根因。
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.xirr import xirr  # noqa: E402


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_exact_one_year():
    """投 1000，满一年收回 1100 → 年化 10%（2020-01-01→2020-12-31 恰好 365 天）。"""
    r = xirr([("2020-01-01", -1000), ("2020-12-31", 1100)])
    assert r is not None and _approx(r, 0.10), r


def test_two_year_double_ish():
    """约两年（含一个闰日）→ 与 10% 年化接近。"""
    r = xirr([("2020-01-01", -1000), ("2022-01-01", 1210)])
    assert r is not None and _approx(r, 0.10, tol=2e-3), r


def test_loss_is_negative():
    r = xirr([("2020-01-01", -1000), ("2020-12-31", 900)])
    assert r is not None and _approx(r, -0.10), r


def test_accepts_date_objects():
    r = xirr([(date(2020, 1, 1), -1000), (date(2020, 12, 31), 1100)])
    assert r is not None and _approx(r, 0.10), r


def test_zero_amounts_ignored():
    r = xirr([("2020-01-01", -1000), ("2020-06-01", 0), ("2020-12-31", 1100)])
    assert r is not None and _approx(r, 0.10), r


# ── 批次 3 的核心论点 ─────────────────────────────────────────────

def test_monthly_dca_xirr_exceeds_simple_return():
    """定投 12 期共 12000、期末 13000：简单收益率 8.33%，但 XIRR 应**明显更高**
    （资金平均只占用了约半年）—— 这就是"不能拿收益率直接比"的原因。"""
    flows = [(f"2020-{m:02d}-01", -1000) for m in range(1, 13)]
    flows.append(("2020-12-31", 13000))
    simple = 13000 / 12000 - 1          # 8.33%
    r = xirr(flows)
    assert r is not None
    assert r > simple, "XIRR(%.4f) 应高于简单收益率(%.4f)" % (r, simple)
    assert r > 0.14, "实测应接近 15% 量级（资金占用期约半年）：%.4f" % r


def test_lump_sum_equals_simple_return_when_one_year():
    """一次性投入且恰好一年 → XIRR 与简单收益率**相等**（口径自洽性检查）。"""
    flows = [("2020-01-01", -12000), ("2020-12-31", 13200)]
    assert _approx(xirr(flows), 13200 / 12000 - 1, tol=1e-6)


# ── 无解 / 边界：必须返回 None，不编造 ────────────────────────────

def test_all_same_sign_has_no_solution():
    assert xirr([("2020-01-01", 100), ("2021-01-01", 200)]) is None
    assert xirr([("2020-01-01", -100), ("2021-01-01", -200)]) is None


def test_too_few_cashflows():
    assert xirr([]) is None
    assert xirr([("2020-01-01", -100)]) is None
    assert xirr(None) is None


def test_total_loss_returns_none_or_bounded():
    """全部亏光（期末 0）：无正的现金流 → 无解。库内不会编造 -100%。"""
    assert xirr([("2020-01-01", -1000), ("2020-12-31", 0)]) is None


# ── 接入层：PortfolioTracker.get_xirr ─────────────────────────────

def _mk_db(tmp_path):
    from src.data.database import Database

    db = Database(str(tmp_path / "x.db"))
    db.upsert_fund_info({"fund_code": "X1", "fund_name": "x", "fund_type": "混合型"})
    for d, v in (("2020-01-01", 1.0), ("2020-12-31", 1.1)):
        db.conn.execute("INSERT OR REPLACE INTO fund_nav "
                        "(fund_code, nav_date, unit_nav, acc_nav, daily_return) "
                        "VALUES ('X1',?,?,?,0)", (d, v, v))
    hid = db.add_holding({"fund_code": "X1", "fund_name": "x", "buy_date": "2020-01-01",
                          "buy_amount": 1000.0, "buy_nav": 1.0, "shares": 1000.0,
                          "confirm_date": "2020-01-01", "accrual_start": "2020-01-01",
                          "status": "holding"})
    db.add_transaction({"holding_id": hid, "fund_code": "X1", "kind": "buy",
                        "apply_date": "2020-01-01", "confirm_date": "2020-01-01",
                        "confirm_nav": 1.0, "shares": 1000.0, "amount": 1000.0,
                        "status": "confirmed"})
    db.conn.commit()
    return db


def test_get_xirr_matches_hand_calculation(tmp_path):
    """期初投 1000、期末市值 1100（整一年）→ 组合 XIRR 应 ≈ 10%。"""
    from src.analysis.portfolio import PortfolioTracker

    db = _mk_db(tmp_path)
    try:
        r = PortfolioTracker(db).get_xirr(market_value=1100.0)
        assert r["xirr"] is not None, r
        assert abs(r["xirr"] - 0.10) < 1e-4, r
        assert r["xirr_pct"] == 10.0
        assert r["invested"] == 1000.0 and r["settled_value"] == 1100.0
        assert r["first_date"] == "2020-01-01" and r["last_date"] == "2020-12-31"
        assert "XIRR" in r["methodology"]
    finally:
        db.close()


def test_get_xirr_excludes_pending(tmp_path):
    """待确认的流水不算现金流出 —— 钱还没真正出去。"""
    from src.analysis.portfolio import PortfolioTracker

    db = _mk_db(tmp_path)
    try:
        hid = db.get_current_holdings()[0]["id"]
        db.add_transaction({"holding_id": hid, "fund_code": "X1", "kind": "buy",
                            "apply_date": "2020-12-01", "confirm_date": None,
                            "shares": 0.0, "amount": 500.0, "status": "pending_confirm"})
        db.conn.commit()
        r = PortfolioTracker(db).get_xirr(market_value=1100.0)
        assert r["invested"] == 1000.0, "待确认的 500 不应计入投入：%s" % r
    finally:
        db.close()


def test_get_xirr_empty_db_returns_none(tmp_path):
    from src.analysis.portfolio import PortfolioTracker
    from src.data.database import Database

    db = Database(str(tmp_path / "e.db"))
    try:
        r = PortfolioTracker(db).get_xirr()
        assert r["xirr"] is None and r["n_flows"] == 0
        assert "XIRR" in r["note"], "必须带上口径说明（供报告直接引用）"
    finally:
        db.close()
