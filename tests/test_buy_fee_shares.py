"""买入份额公式（批次 5.2）：净申购金额 = 申购金额 / (1 + 申购费率)。

权威口径（D1 费率页原文）：
    净申购金额 = 申购金额 / (1 + 申购费率)
    申购费用   = 申购金额 − 净申购金额
    申购份额   = 净申购金额 / T日基金份额净值

防回归要点：
- C 类（费率 0）份额与费用必须与旧口径**完全一致**（shares = amount/nav，fee = 0）
- A 类份额必须扣除申购费；费用记入 transactions.fee
- reconcile 结算待确认买入时与 add_buy_transaction 同口径
- sell 路径的份额计算不受影响（不扣申购费）
"""
from datetime import date, timedelta

import pytest

from src.analysis import trade_rules as tr
from src.analysis import portfolio as portfolio_mod
from src.analysis.portfolio import PortfolioTracker, DEFAULT_PURCHASE_FEE
from src.data.database import Database


def weekdays(y1, m1, d1, y2, m2, d2, holidays=()):
    s, e = date(y1, m1, d1), date(y2, m2, d2)
    out, cur = set(), s
    while cur <= e:
        if cur.weekday() < 5 and cur.isoformat() not in holidays:
            out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


CAL = weekdays(2026, 1, 1, 2027, 6, 30, {"2026-01-01", "2026-10-01", "2026-10-02",
                                        "2026-10-05", "2027-01-01"})


@pytest.fixture()
def tracker(tmp_path):
    db = Database(str(tmp_path / "fee.db"))
    db.upsert_trade_dates(sorted(CAL))
    t = PortfolioTracker(db)
    yield t, db
    db.close()


def _add_nav(db, code, d, nav):
    db.conn.execute("INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
                    "VALUES (?,?,?,?,?)", (code, d, nav, nav, 0.0))
    db.conn.commit()


def _get_tx(db, hid):
    return dict(db.conn.execute(
        "SELECT * FROM transactions WHERE holding_id=? AND kind='buy'", (hid,)).fetchone())


# ---------------- C 类：费率 0，结果必须与旧口径完全一致 ----------------

def test_c_class_shares_and_fee_unchanged(tracker):
    t, db = tracker
    _add_nav(db, "FC", "2026-01-05", 2.0)                     # 生效日净值
    hid = t.add_buy_transaction("FC", "南方中证500ETF联接发起式C", "2026-01-05", 100.0)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    tx = _get_tx(db, hid)
    # 旧口径：shares = 100/2.0 = 50.0；fee = 0
    assert h["shares"] == 50.0
    assert tx["fee"] == 0.0


def test_c_class_pending_reconcile_unchanged(tracker):
    """净值晚公布 → reconcile 结算：C 类份额仍是 amount/nav。"""
    t, db = tracker
    apply_date = "2026-12-30"
    effective = tr.effective_apply_date(apply_date, CAL)
    confirm, _ = tr.resolve_apply(apply_date, False, CAL)
    if confirm <= date.today().isoformat():
        return                                   # 日历漂移保护，正常不会走到
    _add_nav(db, "FC2", effective, 2.0)
    hid = t.add_buy_transaction("FC2", "某沪深300指数C", apply_date, 100.0)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "pending_confirm" and (h["shares"] or 0) == 0
    tx = _get_tx(db, hid)
    assert tx["fee"] == 0.0                      # 申请时已入账
    assert t.reconcile(today=confirm)["settled_buys"] == 1
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["shares"] == 50.0                    # 与旧口径一致


# ---------------- A 类：份额扣申购费，费用入账 ----------------

def test_a_class_default_fee_netted(tracker):
    """A 类用保守默认 0.15%：net = amount/1.0015，fee = amount − net。"""
    t, db = tracker
    _add_nav(db, "FA", "2026-01-05", 2.0)
    amount = 100.0
    hid = t.add_buy_transaction("FA", "某某灵活配置混合A", "2026-01-05", amount)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    tx = _get_tx(db, hid)
    assert DEFAULT_PURCHASE_FEE == 0.0015
    expected_net = amount / (1 + DEFAULT_PURCHASE_FEE)
    assert h["shares"] == round(expected_net / 2.0, 2)         # 49.93
    assert tx["fee"] == round(amount - expected_net, 2)        # 0.15


def test_a_class_fee_1_5pct(tracker, monkeypatch):
    """费率 1.5% 的 A 类：shares == round(amount/1.015/nav, 2)。"""
    t, db = tracker
    monkeypatch.setattr(portfolio_mod, "purchase_fee_rate",
                        lambda fund_name, default=0.015: 0.015)
    _add_nav(db, "FA2", "2026-01-05", 2.0)
    amount = 100.0
    hid = t.add_buy_transaction("FA2", "某某股票型A", "2026-01-05", amount)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    tx = _get_tx(db, hid)
    assert h["shares"] == round(amount / 1.015 / 2.0, 2)       # 49.26
    assert tx["fee"] == round(amount - amount / 1.015, 2)       # 1.48


def test_a_class_pending_reconcile_netted(tracker, monkeypatch):
    """待确认买入经 reconcile 结算：同样按净申购金额算份额（两路一致）。"""
    t, db = tracker
    monkeypatch.setattr(portfolio_mod, "purchase_fee_rate",
                        lambda fund_name, default=0.015: 0.015)
    apply_date = "2026-12-30"
    effective = tr.effective_apply_date(apply_date, CAL)
    confirm, _ = tr.resolve_apply(apply_date, False, CAL)
    if confirm <= date.today().isoformat():
        return
    _add_nav(db, "FA3", effective, 2.0)
    hid = t.add_buy_transaction("FA3", "某某混合A", apply_date, 100.0)
    assert t.reconcile(today=confirm)["settled_buys"] == 1
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["shares"] == round(100.0 / 1.015 / 2.0, 2)         # 49.26


# ---------------- sell 路径不受影响 ----------------

def test_sell_shares_not_netted(tracker):
    """卖出按金额折份额仍是 amount/nav，不扣申购费（要求：不改 sell 路径）。"""
    t, db = tracker
    _add_nav(db, "FS", "2026-01-05", 1.0)
    _add_nav(db, "FS", "2026-01-08", 1.0)
    hid = t.add_buy_transaction("FS", "南方中证500ETF联接发起式C", "2026-01-05", 100.0)
    assert t.record_sell(hid, "2026-01-08", sell_amount=50.0)
    tx = dict(db.conn.execute(
        "SELECT * FROM transactions WHERE holding_id=? AND kind='sell'", (hid,)).fetchone())
    assert tx["shares"] == 50.0                                # 50.0 / 1.0，未净额化
