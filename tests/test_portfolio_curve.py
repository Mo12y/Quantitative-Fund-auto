"""组合累计曲线（第 13 项）单测：日期并集 / 前向填充 / 待确认排除。"""
from src.data.database import Database
from src.analysis.portfolio import PortfolioTracker


def _nav(db, code, d, nav):
    db.conn.execute("INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
                    "VALUES (?,?,?,?,?)", (code, d, nav, nav, 0.0))
    db.conn.commit()


def _holding(db, code, amount, shares, accrual, status="holding"):
    return db.add_holding({"fund_code": code, "fund_name": code, "buy_date": accrual,
                           "buy_amount": amount, "buy_nav": 1.0, "shares": shares,
                           "apply_date": accrual, "confirm_date": accrual,
                           "accrual_start": accrual, "status": status})


def test_curve_union_forward_fill(tmp_path):
    db = Database(str(tmp_path / "curve.db"))
    # H1: 01-07 起算，净值 1.0 → 1.2（01-08 缺净值 → 前向填充）
    _holding(db, "C1", 100.0, 100.0, "2026-01-07")
    _nav(db, "C1", "2026-01-07", 1.0)
    _nav(db, "C1", "2026-01-09", 1.2)
    # H2: 01-08 起算，净值 2.0 → 2.2
    _holding(db, "C2", 200.0, 100.0, "2026-01-08")
    _nav(db, "C2", "2026-01-08", 2.0)
    _nav(db, "C2", "2026-01-09", 2.2)

    c = PortfolioTracker(db).get_portfolio_curve()
    assert c["dates"] == ["2026-01-07", "2026-01-08", "2026-01-09"]
    assert c["funds_used"] == 2 and c["excluded_pending"] == 0
    # 01-07：只有 H1，市值 100，成本 100
    assert c["value"][0] == 100.0 and c["cost"][0] == 100.0 and c["pnl"][0] == 0.0
    # 01-08：H1 用 01-07 净值前向填充 100×1.0=100；H2 100×2.0=200 → 300，成本 300
    assert c["value"][1] == 300.0 and c["cost"][1] == 300.0
    # 01-09：100×1.2 + 100×2.2 = 340，成本 300，收益 40
    assert c["value"][2] == 340.0 and c["pnl"][2] == 40.0
    assert abs(c["return_pct"][2] - 13.33) < 0.02
    db.close()


def test_curve_excludes_pending(tmp_path):
    db = Database(str(tmp_path / "curve2.db"))
    _holding(db, "C1", 100.0, 100.0, "2026-01-07")
    _nav(db, "C1", "2026-01-07", 1.0)
    _nav(db, "C1", "2026-01-08", 1.1)
    # 待确认买入：即使有份额也不应计入曲线
    _holding(db, "C2", 500.0, 250.0, "2026-01-08", status="pending_confirm")
    _nav(db, "C2", "2026-01-08", 2.0)

    c = PortfolioTracker(db).get_portfolio_curve()
    assert c["funds_used"] == 1
    assert c["excluded_pending"] == 1
    assert c["cost"][-1] == 100.0                      # 只含已确认的 H1
    assert c["value"][-1] == 110.0
    db.close()


def test_curve_empty_when_all_pending(tmp_path):
    db = Database(str(tmp_path / "curve3.db"))
    _holding(db, "C1", 100.0, 100.0, "2026-01-07", status="pending_confirm")
    _nav(db, "C1", "2026-01-07", 1.0)
    c = PortfolioTracker(db).get_portfolio_curve()
    assert c["dates"] == [] and c["excluded_pending"] == 1
    db.close()


def test_curve_cost_drops_after_partial_sell(tmp_path):
    """中途部分卖出后：成本随卖出按比例减少，曲线不再用原始全额成本。"""
    from src.analysis.portfolio import PortfolioTracker as PT
    db = Database(str(tmp_path / "curve4.db"))
    db.upsert_trade_dates(["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09"])
    _nav(db, "C1", "2026-01-05", 1.0)      # 买入定价日
    _nav(db, "C1", "2026-01-08", 1.10)     # 卖出定价日
    _nav(db, "C1", "2026-01-09", 1.20)

    t = PT(db)
    hid = t.add_buy_transaction("C1", "C1", "2026-01-05", 100.0)   # 100 份 @1.0
    t.record_sell(hid, "2026-01-08", shares=50)                    # 卖一半

    c = t.get_portfolio_curve()
    assert c["cost"][-1] == 50.0                       # 100 → 50（按比例减成本）
    assert c["value"][-1] == 60.0                      # 剩 50 份 × 1.20
    assert abs(c["pnl"][-1] - 10.0) < 0.01             # 60 − 50
    db.close()
