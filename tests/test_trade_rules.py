"""交易规则(T+1/T+2) 与持仓落账单测。"""
from datetime import date, datetime, timedelta

import pytest

from src.analysis import trade_rules as tr
from src.analysis.portfolio import PortfolioTracker
from src.data.database import Database


def weekdays(y1, m1, d1, y2, m2, d2, holidays=()):
    """构造某区间的交易日集合（工作日，剔除给定节假日）"""
    s, e = date(y1, m1, d1), date(y2, m2, d2)
    out, cur = set(), s
    while cur <= e:
        if cur.weekday() < 5 and cur.isoformat() not in holidays:
            out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


HOL = {"2026-01-01", "2026-10-01", "2026-10-02", "2026-10-05", "2027-01-01"}
CAL = weekdays(2026, 1, 1, 2027, 6, 30, HOL)


# ---------------- 纯规则 ----------------

def test_friday_before_cutoff():
    confirm, accrual = tr.resolve_apply("2026-09-04", after_cutoff=False, calendar=CAL)
    assert confirm == "2026-09-07"       # 周五15:00前 → 下周一确认
    assert accrual == "2026-09-08"


def test_friday_after_cutoff():
    confirm, accrual = tr.resolve_apply("2026-09-04", after_cutoff=True, calendar=CAL)
    assert confirm == "2026-09-08"       # 顺延 → 下周二确认
    assert accrual == "2026-09-09"


def test_saturday_apply():
    confirm, accrual = tr.resolve_apply("2026-09-05", after_cutoff=False, calendar=CAL)
    assert confirm == "2026-09-08"
    assert accrual == "2026-09-09"


def test_sunday_after_cutoff():
    """周日下单 = **周五收盘后下单**（D3）→ 不再叠加一次 15:00 顺延。

    2026-09-06 是周日 → 生效日 09-07(周一) → 确认日 09-08 → 起算日 09-09。
    旧行为（09-09 / 09-10）是"非交易日顺延"与"15:00 顺延"被叠加了两次的结果。
    """
    confirm, accrual = tr.resolve_apply("2026-09-06", after_cutoff=True, calendar=CAL)
    assert confirm == "2026-09-08"
    assert accrual == "2026-09-09"
    # 非交易日：cutoff 传什么都不该改变结果
    assert (tr.resolve_apply("2026-09-06", after_cutoff=False, calendar=CAL)
            == tr.resolve_apply("2026-09-06", after_cutoff=True, calendar=CAL))


def test_holiday_span_before_cutoff():
    confirm, accrual = tr.resolve_apply("2026-09-30", after_cutoff=False, calendar=CAL)
    assert confirm == "2026-10-06"       # 跳过 10-01/02/05 假期与周末
    assert accrual == "2026-10-07"


def test_holiday_span_after_cutoff():
    confirm, accrual = tr.resolve_apply("2026-09-30", after_cutoff=True, calendar=CAL)
    assert confirm == "2026-10-07"
    assert accrual == "2026-10-08"


def test_cutoff_detection():
    assert tr.is_after_cutoff(datetime(2026, 9, 4, 14, 59)) is False
    assert tr.is_after_cutoff(datetime(2026, 9, 4, 15, 0)) is True


def test_weekend_fallback_without_calendar():
    confirm, accrual = tr.resolve_apply("2026-09-05", calendar=None)   # 周六
    assert confirm == "2026-09-08"
    assert accrual == "2026-09-09"


def test_sell_and_status():
    confirm, payout = tr.resolve_sell("2026-09-04", after_cutoff=True, calendar=CAL)
    assert confirm == "2026-09-08"
    assert payout == "2026-09-09"
    assert tr.holding_status("2026-09-08", today="2026-09-08") == "holding"
    assert tr.holding_status("2026-09-09", today="2026-09-08") == "pending_confirm"


def test_count_trade_days():
    assert tr.count_trade_days("2026-09-07", "2026-09-11", CAL) == 5
    assert tr.count_trade_days("2026-09-05", "2026-09-06", CAL) == 0


# ---------------- 持仓落账（T+1） ----------------

@pytest.fixture()
def tracker(tmp_path):
    db = Database(str(tmp_path / "t1.db"))
    db.upsert_trade_dates(sorted(CAL))
    # 读路径已彻底离线（净值只读本地 DB），无需再 stub akshare
    t = PortfolioTracker(db)
    yield t, db
    db.close()


def _add_nav(db, code, d, nav):
    db.conn.execute("INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
                    "VALUES (?,?,?,?,?)", (code, d, nav, nav, 0.0))
    db.conn.commit()


# ---------------- 定价日（决策 A：按申请日 T 日净值成交） ----------------

def test_buy_priced_at_apply_day_nav(tracker):
    """¥300 / NAV(申请日 T)=2.0 / NAV(确认日 T+1)=2.5 → 份额必须是 150，不是 120。"""
    t, db = tracker
    _add_nav(db, "TA", "2026-01-05", 2.0)                    # 申请日 T（周一）
    _add_nav(db, "TA", "2026-01-06", 2.5)                    # 确认日 T+1
    hid = t.add_buy_transaction("TA", "T日定价C", "2026-01-05", 300.0)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["confirm_date"] == "2026-01-06"
    assert h["confirm_nav"] == 2.0                           # 成交价 = T 日净值
    assert h["shares"] == 150.0                              # 300 / 2.0
    tx = db.get_transactions(holding_id=hid)[0]
    assert tx["confirm_nav"] == 2.0 and tx["shares"] == 150.0


def test_sell_priced_at_apply_day_nav(tracker):
    """卖出同样按申请日(T)净值结算：用 NAV(T)=1.10，不是 NAV(T+1)=2.00。"""
    t, db = tracker
    _add_nav(db, "TB", "2026-01-05", 1.0)
    _add_nav(db, "TB", "2026-01-08", 1.10)                   # 卖出申请日 T
    _add_nav(db, "TB", "2026-01-09", 2.00)                   # 卖出确认日 T+1（不该被用）
    hid = t.add_buy_transaction("TB", "卖出T日定价C", "2026-01-05", 100.0)
    t.record_sell(hid, "2026-01-08", shares=100)
    tx = [x for x in db.get_transactions(holding_id=hid) if x["kind"] == "sell"][0]
    assert tx["confirm_nav"] == 1.10                         # 用 T 日净值
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "sold"
    assert h["sell_amount"] == 110.0                         # 100 份 × 1.10


# ---------------- 结算（精确日期 / 待确认） ----------------

def test_buy_confirmed_in_past(tracker):
    t, db = tracker
    _add_nav(db, "T001", "2026-01-05", 1.0)                  # 申请日 = 定价日（周一）
    hid = t.add_buy_transaction("T001", "测试基金C", "2026-01-05", 100.0)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "holding"
    assert h["confirm_date"] == "2026-01-06"
    assert h["accrual_start"] == "2026-01-07"
    assert h["shares"] == 100.0
    assert len(db.get_transactions(holding_id=hid)) == 1


def test_buy_pending_then_settle(tracker):
    t, db = tracker
    today = date.today().isoformat()
    apply_date = "2026-12-30"                                # 相对今天(2026-09)在未来
    effective = tr.effective_apply_date(apply_date, CAL)
    confirm, _ = tr.resolve_apply(apply_date, False, CAL)
    _add_nav(db, "T002", effective, 2.0)
    hid = t.add_buy_transaction("T002", "待确认基金C", apply_date, 100.0)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    if confirm > today:
        assert h["status"] == "pending_confirm" and (h["shares"] or 0) == 0
        res = t.settle_pending(today=confirm)
        assert res["settled_buys"] == 1
        h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
        assert h["status"] == "holding" and h["shares"] == 50.0   # 100 / 2.0
    else:
        assert h["status"] == "holding"


def test_settle_requires_exact_nav_date(tracker):
    """生效日净值未公布 → 保持待确认（绝不用邻近日近似落账）；公布后才结算。"""
    t, db = tracker
    apply_date = "2026-12-30"
    effective = tr.effective_apply_date(apply_date, CAL)
    confirm, _ = tr.resolve_apply(apply_date, False, CAL)
    if confirm <= date.today().isoformat():
        return
    _add_nav(db, "T006", "2026-12-29", 9.99)                 # 只有邻近日，没有生效日
    hid = t.add_buy_transaction("T006", "净值未公布C", apply_date, 100.0)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "pending_confirm" and (h["shares"] or 0) == 0
    assert t.settle_pending(today=confirm)["settled_buys"] == 0
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "pending_confirm" and (h["shares"] or 0) == 0   # 没被 9.99 蒙混过去
    _add_nav(db, "T006", effective, 2.0)                     # 净值公布
    assert t.settle_pending(today=confirm)["settled_buys"] == 1
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["shares"] == 50.0


def test_reconcile_is_idempotent(tracker):
    """重复对账不会重复扣减份额（并发安全的基础）。"""
    t, db = tracker
    _add_nav(db, "T007", "2026-01-05", 1.0)
    _add_nav(db, "T007", "2026-01-08", 1.10)
    hid = t.add_buy_transaction("T007", "幂等C", "2026-01-05", 100.0)
    t.record_sell(hid, "2026-01-08", shares=40)
    for _ in range(3):
        t.reconcile()
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["shares"] == 60.0                               # 只扣一次
    assert h["status"] == "holding"


def test_summary_does_not_write_by_default(tracker):
    """get_portfolio_summary() 默认纯读：不结算、不改持仓；reconcile=True 才结算。"""
    t, db = tracker
    _add_nav(db, "T008", "2026-01-05", 2.0)                  # 生效日净值（已过去很久）
    # 直接构造一条"确认日已过、但还没结算"的待确认买入
    hid = db.add_holding({
        "fund_code": "T008", "fund_name": "纯读C", "buy_date": "2026-01-05",
        "buy_amount": 100.0, "apply_date": "2026-01-05",
        "confirm_date": "2026-01-06", "accrual_start": "2026-01-07",
        "status": "pending_confirm",
    })
    t.get_portfolio_summary()                                # 不应触发结算
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "pending_confirm" and (h["shares"] or 0) == 0

    t.get_portfolio_summary(reconcile=True)                  # 显式对账才结算
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "holding" and h["shares"] == 50.0  # 100 / 2.0


# ---------------- 部分卖出 / 赎回费 ----------------

def test_partial_sell_keeps_position(tracker):
    t, db = tracker
    _add_nav(db, "T003", "2026-01-05", 1.0)                  # 买入定价日
    _add_nav(db, "T003", "2026-01-08", 1.25)                 # 卖出定价日
    hid = t.add_buy_transaction("T003", "部分卖出C", "2026-01-05", 100.0)
    assert dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())["shares"] == 100.0
    t.record_sell(hid, "2026-01-08", shares=40)              # 周四申请 → 周五确认
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "holding"                          # 部分卖出后回到持有，不再卡在 sell_pending
    assert h["shares"] == 60.0
    assert abs(h["buy_amount"] - 60.0) < 0.01                # 成本按比例减少
    assert len(db.get_transactions(holding_id=hid)) == 2


def test_partial_then_full_sell(tracker):
    """部分卖出后再全部卖出：份额归零、状态 sold。"""
    t, db = tracker
    _add_nav(db, "T009", "2026-01-05", 1.0)
    _add_nav(db, "T009", "2026-01-08", 1.10)
    _add_nav(db, "T009", "2026-01-09", 1.20)
    hid = t.add_buy_transaction("T009", "先部分再全卖C", "2026-01-05", 100.0)
    t.record_sell(hid, "2026-01-08", shares=40)              # 卖 40
    t.record_sell(hid, "2026-01-09", shares=60)              # 再卖 60（周五申请 → 下周一确认）
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["shares"] == 0.0 and h["status"] == "sold"
    assert db.get_current_holdings() == []


def test_short_hold_redeem_fee(tracker):
    """持有 3 天（<7）→ 赎回费 = 毛额 × 1.5%，净到账 = 毛额 × 0.985。"""
    t, db = tracker
    _add_nav(db, "T010", "2026-01-05", 1.0)                  # 买入：100 元 → 100 份
    _add_nav(db, "T010", "2026-01-08", 1.10)                 # 卖出定价日
    hid = t.add_buy_transaction("T010", "短期赎回C", "2026-01-05", 100.0)
    t.record_sell(hid, "2026-01-08", shares=100)             # 确认日 01-09 − 买入确认日 01-06 = 3 天
    tx = [x for x in db.get_transactions(holding_id=hid) if x["kind"] == "sell"][0]
    gross = round(100 * 1.10, 2)                             # 110.00
    assert tx["confirm_nav"] == 1.10
    assert abs(tx["fee"] - round(gross * 0.015, 2)) < 1e-9   # 1.65
    assert abs((gross - tx["fee"]) - gross * 0.985) < 1e-9   # 净到账 = 毛额 × 0.985

    r = t.get_realized_pnl()
    assert r["count"] == 1
    sale = r["sales"][0]
    assert abs(sale["gross"] - 110.0) < 1e-9
    assert abs(sale["cost"] - 100.0) < 1e-9
    assert abs(sale["fee"] - 1.65) < 1e-9
    assert abs(sale["pnl"] - 8.35) < 1e-9                    # 110 − 100 − 1.65


def test_full_sell_marks_sold(tracker):
    t, db = tracker
    _add_nav(db, "T004", "2026-01-05", 1.0)
    _add_nav(db, "T004", "2026-01-08", 1.10)
    hid = t.add_buy_transaction("T004", "全卖C", "2026-01-05", 100.0)
    t.record_sell(hid, "2026-01-08", sell_amount=110.0)      # 110 / 1.10 = 100 份 → 全卖
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["status"] == "sold"
    assert db.get_current_holdings() == []


def test_pending_sell_then_settle(tracker):
    t, db = tracker
    _add_nav(db, "T005", "2026-01-05", 1.0)
    hid = t.add_buy_transaction("T005", "待确认卖出C", "2026-01-05", 100.0)
    # 卖出申请在未来 → 进入 sell_pending，不立即变动份额
    t.record_sell(hid, "2026-12-30", shares=50)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    effective = tr.effective_apply_date("2026-12-30", CAL)
    confirm, _ = tr.resolve_sell("2026-12-30", False, CAL)
    _add_nav(db, "T005", effective, 1.5)
    if confirm > date.today().isoformat():
        assert h["status"] == "sell_pending"
        res = t.settle_pending(today=confirm)
        assert res["settled_sells"] == 1
        h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
        assert h["shares"] == 50.0                            # 部分卖出后剩余
        assert h["status"] == "holding"


# ---------------- 已实现 + 未实现 = 总收益 ----------------

def test_ledger_consistency(tracker):
    """持仓与流水必须一致：份额/金额对得上，且买入必有对应流水。"""
    t, db = tracker
    _add_nav(db, "T012", "2026-01-05", 2.0)
    hid = t.add_buy_transaction("T012", "一致性C", "2026-01-05", 100.0)
    txs = db.get_transactions(holding_id=hid)
    assert len(txs) == 1 and txs[0]["kind"] == "buy"
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert txs[0]["shares"] == h["shares"] == 50.0
    assert txs[0]["amount"] == h["buy_amount"] == 100.0
    assert txs[0]["confirm_date"] == h["confirm_date"]


def test_realized_plus_unrealized_equals_total(tracker):
    """Σ已实现 + Σ未实现 == 总收益（同一笔买入：卖一半 + 留一半）。"""
    t, db = tracker
    _add_nav(db, "T011", "2026-01-05", 1.0)                  # 100 元 → 100 份
    _add_nav(db, "T011", "2026-01-08", 1.20)                 # 卖出定价日
    _add_nav(db, "T011", "2026-01-09", 1.50)                 # 最新净值（未实现部分）
    hid = t.add_buy_transaction("T011", "半卖半留C", "2026-01-05", 100.0)
    t.record_sell(hid, "2026-01-08", shares=50)              # 卖 50 份 @1.20

    real = t.get_realized_pnl()["total_pnl"]
    s = t.get_portfolio_summary()
    unreal = s["total_pnl"]

    assert abs(unreal - (50 * 1.50 - 50.0)) < 0.01           # 剩余 50 份 × 1.50 − 剩余成本 50
    assert abs(real - (60.0 - 50.0 - 0.9)) < 0.01            # 毛额 60 − 成本 50 − 费 0.9
    assert abs((real + unreal) - ((60.0 - 0.9) + 50 * 1.50 - 100.0)) < 0.02
    assert abs(s["total_invested"] - 50.0) < 0.01            # 成本按比例减到 50
