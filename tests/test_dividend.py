"""现金分红 / 红利再投 —— 检测与入账测试。

实现依据：`docs/现金分红设计方案.md`（§3 检测 / §5 落账 / §7 落地顺序）。
重点锁三件事（都是"自动落账"的安全底线，用户 2026-09-16 已拍板不设人工确认）：
  1. **真分红必须检出**；
  2. **份额折算必须拒收**（unit 与 acc 同幅下挫 → diff 不变 → 无跳幅）；
  3. **脏数据**（acc < unit）整条序列弃用 + 金额/幂等正确。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.dividend import (KIND_CASH, KIND_REINVEST, detect_dividends,
                                   post_dividend, scan_holding)
from src.data.database import Database


def _rows(*spec):
    """spec: (date, unit, acc) 三元组序列。"""
    return [{"nav_date": d, "unit_nav": u, "acc_nav": a} for d, u, a in spec]


class TestDetector:
    def test_real_dividend_is_detected(self):
        """真分红：unit 下挫、acc 不下挫 → diff 台阶上跳，跳幅 = 每份分红。

        注意这里刻意让**当日也有涨跌**（1.0000 → 1.0500）：
        这正是设计稿原条件①（jump≈unit跌幅）会误拒的情形 —— 见下一条测试。
        """
        rows = _rows(
            ("2026-01-02", 1.0000, 1.0000),
            ("2026-01-05", 1.0500, 1.0500),     # 正常上涨
            ("2026-01-06", 1.0300, 1.0800),     # 除息：unit 掉 0.02，acc 继续涨
        )
        ev, sp = detect_dividends(rows)
        assert len(ev) == 1 and not sp, (ev, sp)
        assert ev[0]["date"] == "2026-01-06"
        assert ev[0]["per_share"] == pytest.approx(0.05, abs=1e-6)   # diff: 0 → 0.05

    def test_jump_differs_from_unit_fall_on_a_moving_day(self):
        """⚠️ 回归哨兵：设计稿原条件①（jump≈unit跌幅）是错的 —— 本用例专门钉住它已被删除。

        推导：jump − fall = r·acc₀。当日 r=+5% 时两者相差 0.05，远大于容差 1e-4；
        若把该条件加回去，本用例会**检不出分红**（这正是 000016 只检出 3/10 的原因）。
        """
        rows = _rows(
            ("2026-01-02", 1.0000, 1.0000),
            ("2026-01-05", 1.0500, 1.0500),
            ("2026-01-06", 1.0300, 1.0800),
        )
        ev, _ = detect_dividends(rows)
        assert len(ev) == 1, "当日有涨跌时仍必须检出分红（原条件①会误拒）"
        assert abs(ev[0]["per_share"] - ev[0]["unit_fall"]) > 1e-3, \
            "本用例构造的正是 jump≠fall 的情形"

    def test_share_split_is_rejected(self):
        """份额折算/净值归一化：unit 与 acc **同幅**下挫 → diff 不动 → 不是分红。"""
        rows = _rows(
            ("2026-01-02", 2.0000, 2.0000),
            ("2026-01-05", 1.0000, 1.0000),     # 折算：两列同幅腰斩，diff 恒为 0
            ("2026-01-06", 1.0100, 1.0100),
        )
        ev, sp = detect_dividends(rows)
        assert ev == [] and sp == [], "折算不得被误判为分红"

    def test_acc_below_unit_discards_whole_series(self):
        """脏数据：acc < unit → 整条序列弃用并说明原因（不猜）。"""
        rows = _rows(
            ("2026-01-02", 1.0100, 1.0000),     # acc(1.00) < unit(1.01)
            ("2026-01-05", 1.0200, 1.0900),
        )
        ev, sp = detect_dividends(rows)
        assert ev == [] and sp and "不可信" in sp[0]["reason"]

    def test_absurd_jump_is_suspect_not_posted(self):
        """每份分红 ≥ 当日 unit（脏数据）→ 只列存疑，不落账。"""
        rows = _rows(
            ("2026-01-02", 1.0000, 1.0000),
            ("2026-01-05", 0.4000, 1.6000),     # jump=1.2 > unit=0.4
        )
        ev, sp = detect_dividends(rows)
        assert ev == [] and len(sp) == 1 and "脏数据" in sp[0]["reason"]

    def test_missing_acc_nav_is_skipped_not_guessed(self):
        """无累计净值 → 无法判断，跳过（**不补 0、不猜**）。"""
        ev, sp = detect_dividends([{"nav_date": "2026-01-02", "unit_nav": 1.0, "acc_nav": None},
                                   {"nav_date": "2026-01-05", "unit_nav": 0.9, "acc_nav": None}])
        assert ev == [] and sp == []


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "div.db"))
    yield d
    d.close()


def _seed_holding(db, code="D001", shares=1000.0, policy="reinvest"):
    db.upsert_fund_info({"fund_code": code, "fund_name": "分红测试基金C", "fund_type": "债券型-长债"})
    db.conn.execute(
        "INSERT INTO holdings (fund_code, fund_name, buy_date, confirm_date, accrual_start,"
        " shares, buy_amount, status, dividend_policy, cash_balance)"
        " VALUES (?,?,?,?,?,?,?,?,?,0)",
        (code, "分红测试基金C", "2026-01-02", "2026-01-02", "2026-01-02", shares, 1000.0, "holding", policy))
    db.conn.commit()
    rows = db.get_current_holdings()
    # 多只基金时 `[0]` 会拿到**别的**基金 → 必须按 code 取，否则跨基金用例会静默测错对象
    return next((r for r in rows if r["fund_code"] == code), rows[0])


class TestPosting:
    def test_reinvest_keeps_cost_and_total_value(self, db):
        """红利再投：份额增加、成本不动 → 经济实质"什么都没发生"。"""
        h = _seed_holding(db, policy="reinvest")
        ev = {"date": "2026-01-06", "per_share": 0.05, "unit_nav": 1.03}
        r = post_dividend(db, h, ev)
        assert r["posted"] and r["kind"] == KIND_REINVEST
        assert r["amount"] == pytest.approx(50.0)            # 1000 份 × 0.05
        row = db.get_current_holdings()[0]
        assert row["shares"] == pytest.approx(1000 + 50.0 / 1.03, abs=1e-6)
        assert float(row["buy_amount"]) == pytest.approx(1000.0), "成本不得变"
        assert float(row["cash_balance"] or 0) == 0, "再投不产生现金"

    def test_cash_increases_balance_and_keeps_shares(self, db):
        h = _seed_holding(db, policy="cash")
        ev = {"date": "2026-01-06", "per_share": 0.05, "unit_nav": 1.03}
        r = post_dividend(db, h, ev)
        assert r["posted"] and r["kind"] == KIND_CASH
        row = db.get_current_holdings()[0]
        assert float(row["cash_balance"]) == pytest.approx(50.0)
        assert float(row["shares"]) == pytest.approx(1000.0), "现金分红不动份额"

    def test_idempotent_by_holding_date_kind(self, db):
        """幂等键 `(holding_id, apply_date, kind)`：重跑不得重复入账。"""
        h = _seed_holding(db)
        ev = {"date": "2026-01-06", "per_share": 0.05, "unit_nav": 1.03}
        assert post_dividend(db, h, ev)["posted"] is True
        h2 = db.get_current_holdings()[0]
        again = post_dividend(db, h2, ev)
        assert again["posted"] is False and "幂等" in again["reason"]
        assert db.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE kind=?", (KIND_REINVEST,)).fetchone()[0] == 1

    def test_audit_evidence_is_written(self, db):
        """可审计：每笔自动落账必须留下每份分红（便于按 apply_date 一次性撤销）。"""
        h = _seed_holding(db)
        post_dividend(db, h, {"date": "2026-01-06", "per_share": 0.05, "unit_nav": 1.03})
        v = db.conn.execute(
            "SELECT dividend_per_share, apply_date FROM transactions WHERE kind=?",
            (KIND_REINVEST,)).fetchone()
        assert v[0] == pytest.approx(0.05) and v[1] == "2026-01-06"

    def test_zero_share_holding_is_not_posted(self, db):
        h = _seed_holding(db, shares=0.0)
        r = post_dividend(db, h, {"date": "2026-01-06", "per_share": 0.05, "unit_nav": 1.03})
        assert r["posted"] is False


class TestScanHolding:
    def test_scan_is_read_only(self, db):
        """扫描只读净值、不写库（落账由 post_dividend 显式触发）。"""
        h = _seed_holding(db)
        db.conn.executemany(
            "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) VALUES (?,?,?,?,0)",
            [("D001", "2026-01-02", 1.0, 1.0), ("D001", "2026-01-05", 1.0, 1.0),
             ("D001", "2026-01-06", 0.95, 1.05)])
        db.conn.commit()
        before = db.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        r = scan_holding(db, h)
        assert len(r["events"]) == 1
        assert db.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == before


class TestAggregationIncludesCash:
    """设计稿 §6：现金分红入账后，**总市值/已实现/调仓口径都必须算上 `cash_balance`**。

    只落账不改聚合的话，那笔现金会"隐身"（记进了 cash_balance 但没人读）——比不记更糟。
    """

    def _mk(self, db, policy, div=50.0):
        h = _seed_holding(db, policy=policy)
        ev = {"date": "2026-01-06", "per_share": 0.05, "unit_nav": 1.03}
        assert post_dividend(db, h, ev)["posted"]
        return h

    def test_cash_dividend_is_counted_in_total_market_value(self, db):
        from src.analysis.portfolio import PortfolioTracker
        h = self._mk(db, "cash")
        # 给这只持仓一个可读的净值，避免"缺净值退回成本"的干扰
        db.conn.executemany(
            "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) VALUES (?,?,?,?,0)",
            [("D001", "2026-01-06", 0.98, 1.03)])
        db.conn.commit()
        s = PortfolioTracker(db).get_portfolio_summary()
        row = [x for x in s["holdings_detail"] if x["fund_code"] == "D001"][0]
        # 市值 = 份额 × 净值 + cash_balance（少了 cash 就会比这里小 50）
        assert row["current_value"] == pytest.approx(1000 * 0.98 + 50.0, abs=0.02), row

    def test_cash_dividend_appears_separately_in_realized(self, db):
        """分红收益与买卖价差**分开列**（设计稿 §6），两者相加才是总收益。"""
        from src.analysis.portfolio import PortfolioTracker
        self._mk(db, "cash")
        rz = PortfolioTracker(db).get_realized_pnl()
        assert rz["dividend_count"] == 1
        assert rz["dividend_total"] == pytest.approx(50.0)
        assert rz["count"] == 0, "分红不是卖出，不得混进价差笔数"

    def test_reinvest_dividend_counts_in_shares_not_cash(self, db):
        from src.analysis.portfolio import PortfolioTracker
        self._mk(db, "reinvest")
        rz = PortfolioTracker(db).get_realized_pnl()
        assert rz["dividend_count"] == 1
        assert rz["dividend_total"] == pytest.approx(50.0, abs=0.01)
        h = db.get_current_holdings()[0]
        assert float(h["cash_balance"] or 0) == 0, "再投不产生现金"


class TestRealizedDividendDetail:
    """前端「已了结」表要按笔列出分红（设计稿 §7 第 6 步）→ 聚合必须给出**明细**，不能只有合计。

    少了明细，前端就只能显示一个总额，用户无法核对是哪天、哪只、按什么方式入的账。
    """

    def test_detail_rows_cover_both_modes(self, db):
        from src.analysis.portfolio import PortfolioTracker
        h1 = _seed_holding(db, code="D001", policy="cash")
        h2 = _seed_holding(db, code="D002", policy="reinvest")
        assert post_dividend(db, h1, {"date": "2026-01-06", "per_share": 0.05, "unit_nav": 1.03})["posted"]
        assert post_dividend(db, h2, {"date": "2026-01-07", "per_share": 0.08, "unit_nav": 1.00})["posted"]
        rz = PortfolioTracker(db).get_realized_pnl()
        rows = rz["dividends"]
        assert len(rows) == 2 and rz["dividend_count"] == 2
        by_code = {r["fund_code"]: r for r in rows}
        cash, rein = by_code["D001"], by_code["D002"]
        # 现金：金额即流水 amount；不涉及份额/净值
        assert cash["mode"] == "现金" and cash["amount"] == pytest.approx(50.0)
        assert cash["shares"] is None and cash["date"] == "2026-01-06"
        # 再投：金额 = 新增份额 × 再投净值（存量口径 amount 记 0，必须换算，否则这里会是 0）
        assert rein["mode"] == "再投" and rein["amount"] == pytest.approx(80.0, abs=0.01)
        assert rein["nav"] == pytest.approx(1.00) and rein["shares"] > 0
        assert rein["fund_name"], "明细要带基金名，便于前端直接显示"

    def test_detail_is_sorted_desc_by_date(self, db):
        from src.analysis.portfolio import PortfolioTracker
        h = _seed_holding(db)
        for d in ("2026-01-06", "2026-03-06", "2026-02-06"):
            post_dividend(db, h, {"date": d, "per_share": 0.05, "unit_nav": 1.03})
            h = db.get_current_holdings()[0]
        rows = PortfolioTracker(db).get_realized_pnl()["dividends"]
        assert [r["date"] for r in rows] == ["2026-03-06", "2026-02-06", "2026-01-06"]

    def test_portfolio_payload_keeps_dividend_fields(self):
        """⚠️ 回归哨兵：`_portfolio_payload` 的 `realized` 是**白名单字段**。

        曾因没把 `dividend_total/dividend_count/dividends` 放进去，导致前端「已实现收益」
        整块漏掉分红（只分红账户看到 ¥0.00）——聚合层明明算对了，payload 层剥掉了。
        """
        from src.web.app import _portfolio_payload
        data = {"has_holdings": True, "total_invested": 100, "total_market_value": 200,
                "total_pnl": 100, "total_return_pct": 100.0, "asset_allocation": {},
                "holdings": [],
                "realized": {"total_pnl": 50.0, "total_gross": 60, "total_cost": 10,
                             "total_fee": 0, "count": 1, "sales": [],
                             "dividend_total": 30.0, "dividend_count": 2,
                             "dividends": [{"date": "2026-01-06", "fund_code": "D001",
                                            "fund_name": "X", "mode": "现金", "per_share": 0.05,
                                            "shares": None, "nav": None, "amount": 30.0}]}}
        rz = _portfolio_payload(data)["realized"]
        assert rz["dividend_total"] == 30.0 and rz["dividend_count"] == 2
        assert len(rz["dividends"]) == 1 and rz["dividends"][0]["mode"] == "现金"


class TestDividendPolicyApi:
    """`POST /api/holdings {action:'dividend_policy'}` —— 前端持仓行上的分红方式切换按钮。

    库内粒度是**每一笔持仓**，而 UI 按基金汇总 → 传 `code` 时必须一次改完整只基金的所有笔，
    否则用户会看到"点了按钮但只有部分笔生效"（切完仍显示「分红方式不一致」）。
    """

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        import src.web.app as webapp
        dbp = str(tmp_path / "divpolicy.db")
        d = Database(dbp)
        d.upsert_fund_info({"fund_code": "D001", "fund_name": "分红测试基金C", "fund_type": "债券型-长债"})
        d.upsert_fund_info({"fund_code": "D002", "fund_name": "另一只C", "fund_type": "债券型-长债"})
        for i, code in enumerate(("D001", "D001", "D002"), start=1):
            d.conn.execute(
                "INSERT INTO holdings (fund_code, fund_name, buy_date, confirm_date, accrual_start,"
                " shares, buy_amount, status, dividend_policy, cash_balance)"
                " VALUES (?,?,'2026-01-02','2026-01-02','2026-01-02',100,1000,'holding','reinvest',0)",
                (code, "分红测试基金C" if code == "D001" else "另一只C"))
        d.conn.commit()
        d.close()
        monkeypatch.setattr(webapp, "DB_PATH", dbp)
        with webapp._slot_lock:
            webapp._slot_store.clear()
        webapp._dash_cache = None
        with webapp.app.test_client() as c:
            yield c, dbp

    @staticmethod
    def _pols(dbp):
        d = Database(dbp)
        rows = [dict(r) for r in d.conn.execute(
            "SELECT fund_code, dividend_policy FROM holdings ORDER BY id")]
        d.close()
        return rows

    def test_switch_by_code_updates_every_lot_of_that_fund(self, client):
        c, dbp = client
        j = c.post("/api/holdings", json={"action": "dividend_policy", "code": "D001", "policy": "cash"})
        assert j.status_code == 200 and j.get_json()["ok"] is True
        assert j.get_json()["changed"] == 2, "D001 有两笔，必须全部改掉"
        got = {r["fund_code"]: r["dividend_policy"] for r in self._pols(dbp)}
        assert got["D001"] == "cash"
        assert got["D002"] == "reinvest", "只改被点的那只基金，别动别的"

    def test_switch_by_id_is_single_lot(self, client):
        c, dbp = client
        d = Database(dbp)
        hid = d.conn.execute("SELECT id FROM holdings ORDER BY id").fetchone()["id"]
        d.close()
        j = c.post("/api/holdings", json={"action": "dividend_policy", "id": hid, "policy": "cash"})
        assert j.get_json()["changed"] == 1
        assert [r["dividend_policy"] for r in self._pols(dbp)] == ["cash", "reinvest", "reinvest"]

    def test_bad_policy_is_rejected(self, client):
        c, _ = client
        j = c.post("/api/holdings", json={"action": "dividend_policy", "code": "D001", "policy": "dividend"})
        assert j.status_code == 400 and "policy" in j.get_json()["error"]

    def test_unknown_code_is_rejected(self, client):
        c, dbp = client
        j = c.post("/api/holdings", json={"action": "dividend_policy", "code": "999999", "policy": "cash"})
        assert j.status_code == 400
        assert {r["dividend_policy"] for r in self._pols(dbp)} == {"reinvest"}, "拒绝时不得改动任何数据"

    def test_missing_locator_is_rejected(self, client):
        c, _ = client
        j = c.post("/api/holdings", json={"action": "dividend_policy", "policy": "cash"})
        assert j.status_code == 400


class TestAutoPostAll:
    def test_auto_post_is_inert_without_dividends_and_idempotent(self, db):
        from src.analysis.dividend import auto_post_all
        h = _seed_holding(db)
        db.conn.executemany(
            "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) VALUES (?,?,?,?,0)",
            [("D001", "2026-01-02", 1.0, 1.0), ("D001", "2026-01-05", 1.01, 1.01)])
        db.conn.commit()
        before = (db.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
                  db.conn.execute("SELECT ROUND(SUM(shares),6) FROM holdings").fetchone()[0])
        r1 = auto_post_all(db)
        r2 = auto_post_all(db)                    # 再跑一遍：必须幂等
        assert r1["posted_n"] == 0 and r2["posted_n"] == 0
        after = (db.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
                 db.conn.execute("SELECT ROUND(SUM(shares),6) FROM holdings").fetchone()[0])
        assert before == after, "无分红时不得改动账本"

    def test_auto_post_posts_real_dividend_once(self, db):
        from src.analysis.dividend import auto_post_all
        _seed_holding(db)
        db.conn.executemany(
            "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) VALUES (?,?,?,?,0)",
            [("D001", "2026-01-02", 1.00, 1.00),
             ("D001", "2026-01-05", 1.00, 1.00),
             ("D001", "2026-01-06", 0.95, 1.05)])   # 除息：diff 0 → 0.05
        db.conn.commit()
        r1 = auto_post_all(db)
        r2 = auto_post_all(db)
        assert r1["posted_n"] == 1, r1
        assert r2["posted_n"] == 0, "第二次必须被幂等键挡住"
