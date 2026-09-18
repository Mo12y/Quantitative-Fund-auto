"""
确认周期（QDII T+2）与 15:00 切点守卫 —— 批次 3.1 / 3.3。

两件事必须同时成立：
1. **QDII 比非 QDII 多一个交易日确认**（D1），但成交价完全不受影响；
2. **非 QDII 行为一字不变**（防回归），且 `after_cutoff` 只在**交易日**生效（D3）。
"""
from datetime import date, timedelta

import pytest

from src.analysis import trade_rules as tr
from src.analysis.portfolio import PortfolioTracker
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


# ==================== 3.1 QDII 确认周期 ====================

class TestConfirmLagDispatch:
    def test_qdii_by_fund_type(self):
        assert tr.confirm_lag_for("指数型-海外股票") == 2
        assert tr.confirm_lag_for("QDII") == 2

    def test_qdii_by_fund_name(self):
        """库里 016453 的 fund_type 是“指数型-海外股票”，名字里也带 (QDII) —— 两条线索都认。"""
        assert tr.confirm_lag_for(None, "南方纳斯达克100指数发起(QDII)C") == 2
        assert tr.confirm_lag_for("", "华夏海外收益债券") == 2

    def test_domestic_funds_stay_t1(self):
        for ft in ("混合型", "股票型", "指数型-股票", "债券型-长债", "", None):
            assert tr.confirm_lag_for(ft, "某某基金C") == 1

    def test_constants(self):
        assert tr.DEFAULT_CONFIRM_LAG == 1 and tr.QDII_CONFIRM_LAG == 2


class TestQdiiConfirmChain:
    def test_qdii_is_one_trading_day_later(self):
        """同一申请日，QDII 的确认日与起算日各比非 QDII **晚一个交易日**。"""
        dom = tr.resolve_apply("2026-09-07", False, CAL, tr.DEFAULT_CONFIRM_LAG)
        qdii = tr.resolve_apply("2026-09-07", False, CAL, tr.QDII_CONFIRM_LAG)
        assert dom == ("2026-09-08", "2026-09-09")
        assert qdii == ("2026-09-09", "2026-09-10")
        # 两个字段都往后挪一天（原实现两个都早一天）
        assert qdii[0] > dom[0] and qdii[1] > dom[1]

    def test_pricing_day_is_not_affected_by_confirm_lag(self):
        """确认周期**不参与定价** —— 成交价永远取生效日那天的净值。"""
        for lag in (1, 2, 3):
            assert tr.effective_apply_date("2026-09-07", CAL) == "2026-09-07"
            assert tr.effective_apply_date("2026-09-05", CAL) == "2026-09-07"   # 周六顺延

    def test_016453_next_record_chain(self):
        """016453 下一笔新记录的确认链实测（2026-09-14 是周一）。"""
        assert tr.resolve_apply("2026-09-14", False, CAL, tr.QDII_CONFIRM_LAG) \
            == ("2026-09-16", "2026-09-17")
        # 旧实现（T+1）给出的是 09-15 / 09-16 —— 各早一天
        assert tr.resolve_apply("2026-09-14", False, CAL, tr.DEFAULT_CONFIRM_LAG) \
            == ("2026-09-15", "2026-09-16")

    def test_confirm_lag_composes_with_cutoff(self):
        """15:00 后下单在 QDII 上再顺延一天（两个顺延互相独立）。"""
        assert tr.resolve_apply("2026-09-07", True, CAL, 1) == ("2026-09-09", "2026-09-10")
        assert tr.resolve_apply("2026-09-07", True, CAL, 2) == ("2026-09-10", "2026-09-11")


class TestDomesticRegression:
    """非 QDII 的既有行为必须**一字不变**。"""

    CASES = [
        ("2026-09-04", False, ("2026-09-07", "2026-09-08")),
        ("2026-09-04", True,  ("2026-09-08", "2026-09-09")),
        ("2026-09-05", False, ("2026-09-08", "2026-09-09")),   # 周六
        ("2026-09-07", False, ("2026-09-08", "2026-09-09")),
        ("2026-09-30", False, ("2026-10-06", "2026-10-07")),   # 跨国庆
        ("2026-09-30", True,  ("2026-10-07", "2026-10-08")),
    ]

    def test_unchanged(self):
        for apply_date, ac, expect in self.CASES:
            assert tr.resolve_apply(apply_date, ac, CAL, 1) == expect, apply_date

    def test_default_param_matches_explicit_t1(self):
        for apply_date, ac, _ in self.CASES:
            assert tr.resolve_apply(apply_date, ac, CAL) == tr.resolve_apply(apply_date, ac, CAL, 1)

    def test_sell_path_unchanged(self):
        assert tr.resolve_sell("2026-09-04", True, CAL) == ("2026-09-08", "2026-09-09")
        assert tr.resolve_sell("2026-09-04", True, CAL) == tr.resolve_sell("2026-09-04", True, CAL, 1)


# ==================== 3.3 after_cutoff 只在交易日生效 ====================

class TestCutoffGuard:
    NON_TRADING = ["2026-09-05", "2026-09-06",     # 周六 / 周日
                   "2026-10-01", "2026-10-02",     # 国庆假期
                   "2026-10-03", "2026-10-04"]     # 假期里的周末

    def test_non_trading_day_ignores_cutoff(self):
        for d in self.NON_TRADING:
            assert (tr.resolve_apply(d, True, CAL) == tr.resolve_apply(d, False, CAL)), d

    def test_trading_day_still_honours_cutoff(self):
        for d in ["2026-09-04", "2026-09-07", "2026-09-08"]:
            before = tr.resolve_apply(d, False, CAL)
            after = tr.resolve_apply(d, True, CAL)
            assert after[0] > before[0], f"{d} 交易日 15:00 后必须顺延确认日"

    def test_weekend_means_friday_close(self):
        """D3：周六/周日下单 = 周五收盘后下单 → 生效日/确认日/起算日。"""
        for d in ("2026-09-05", "2026-09-06"):          # 周六、周日
            assert tr.effective_apply_date(d, CAL) == "2026-09-07"
            assert tr.resolve_apply(d, True, CAL) == ("2026-09-08", "2026-09-09")
        # 与"周五 15:00 后下单"（09-04 + cutoff）的生效日/确认日/起算日一致
        assert tr.effective_apply_date("2026-09-04", CAL) == "2026-09-04"
        assert tr.resolve_apply("2026-09-04", True, CAL) == ("2026-09-08", "2026-09-09")


# ==================== 落账端到端 ====================

@pytest.fixture()
def tracker(tmp_path):
    db = Database(str(tmp_path / "lag.db"))
    db.upsert_trade_dates(sorted(CAL))
    t = PortfolioTracker(db)
    yield t, db
    db.close()


def _add_nav(db, code, d, nav):
    db.conn.execute("INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
                    "VALUES (?,?,?,?,?)", (code, d, nav, nav, 0.0))
    db.conn.commit()


def test_qdii_buy_lands_one_day_later(tracker):
    """同样 ¥100 / 同样的申请日：QDII 的确认日比境内基金晚一天，但**份额相同**（定价日没变）。"""
    t, db = tracker
    for code in ("TQ", "TD"):
        _add_nav(db, code, "2026-01-05", 2.0)
    db.upsert_fund_info({"fund_code": "TQ", "fund_name": "某纳斯达克100(QDII)C",
                         "fund_type": "指数型-海外股票"})
    db.upsert_fund_info({"fund_code": "TD", "fund_name": "某沪深300指数C",
                         "fund_type": "指数型-股票"})

    hq = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?",
                              (t.add_buy_transaction("TQ", "某纳斯达克100(QDII)C", "2026-01-05", 100.0),)).fetchone())
    hd = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?",
                              (t.add_buy_transaction("TD", "某沪深300指数C", "2026-01-05", 100.0),)).fetchone())

    assert hd["confirm_date"] == "2026-01-06" and hq["confirm_date"] == "2026-01-07"
    assert hd["accrual_start"] == "2026-01-07" and hq["accrual_start"] == "2026-01-08"
    assert hq["shares"] == hd["shares"] == 50.0            # 定价日相同 → 份额相同


def test_name_based_detection_without_fund_info(tracker):
    """fund_info 缺失时靠名字里的 (QDII) 也能认出来 —— 否则会静默退回 T+1。"""
    t, db = tracker
    _add_nav(db, "TN", "2026-01-05", 1.0)
    hid = t.add_buy_transaction("TN", "南方纳斯达克100指数发起(QDII)C", "2026-01-05", 100.0)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["confirm_date"] == "2026-01-07"               # T+2，不是 T+1


def test_legacy_deviation_flagged_not_backfilled(tracker):
    """历史记录（T+1 写的）**不回填**，只在持仓详情里标出偏差。"""
    t, db = tracker
    _add_nav(db, "TL", "2026-01-05", 1.0)
    db.upsert_fund_info({"fund_code": "TL", "fund_name": "某海外指数(QDII)",
                         "fund_type": "指数型-海外股票"})
    # 直接构造一条"按旧规则记账"的记录（确认日 01-06 = T+1）
    db.add_holding({
        "fund_code": "TL", "fund_name": "某海外指数(QDII)", "buy_date": "2026-01-05",
        "buy_amount": 100.0, "shares": 100.0, "apply_date": "2026-01-05",
        "confirm_date": "2026-01-06", "accrual_start": "2026-01-07",
        "buy_nav": 1.0, "confirm_nav": 1.0, "status": "holding",
    })
    t.reconcile()                                          # 不应覆写已有确认日
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE fund_code='TL'").fetchone())
    assert h["confirm_date"] == "2026-01-06"               # 原样保留

    det = t.get_portfolio_summary()["holdings_detail"]
    dev = [x for x in det if x["fund_code"] == "TL"][0]["legacy_rule_deviation"]
    assert dev is not None
    assert dev["stored"] == "2026-01-06"
    assert dev["current_rule"] == "2026-01-07"             # 现行 T+2 规则


def test_no_deviation_flag_for_conforming_record(tracker):
    """按现行规则记账的记录不该被误标。"""
    t, db = tracker
    _add_nav(db, "TOK", "2026-01-05", 2.0)
    hid = t.add_buy_transaction("TOK", "普通境内基金C", "2026-01-05", 100.0)
    det = t.get_portfolio_summary()["holdings_detail"]
    assert [x for x in det if x["holding_id"] == hid][0]["legacy_rule_deviation"] is None


def test_weekend_record_matches_friday_close_after_fix(tracker):
    """新记的"周六下单 + 15:00 后"不再双重顺延（D3 的行为固化）。"""
    t, db = tracker
    _add_nav(db, "TW", "2026-09-07", 1.0)
    hid = t.add_buy_transaction("TW", "周末下单C", "2026-09-05", 100.0, after_cutoff=True)
    h = dict(db.conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone())
    assert h["apply_date"] == "2026-09-05"
    assert h["confirm_date"] == "2026-09-08"               # 旧行为是 09-09
    assert h["accrual_start"] == "2026-09-09"              # 旧行为是 09-10
    det = t.get_portfolio_summary()["holdings_detail"]
    assert [x for x in det if x["holding_id"] == hid][0]["legacy_rule_deviation"] is None
