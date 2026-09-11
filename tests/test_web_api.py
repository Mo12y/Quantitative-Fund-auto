"""Web 写接口测试：/api/holdings、/api/dca、/api/plan、/api/reconcile。

这批接口此前 0 覆盖（审计第 7 节 C 组）。全部跑在临时库上，不碰真实资金库。
"""
import pytest

from src.data.database import Database
from src.web import app as webapp

CODE = "000011"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "api.db")
    db = Database(dbp)
    db.upsert_fund_info({"fund_code": CODE, "fund_name": "测试混合A", "fund_type": "混合型"})
    db.conn.execute(
        "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
        "VALUES (?, '2026-01-05', 2.0, 2.0, 0)", (CODE,))
    db.conn.execute("INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
                    "VALUES (?, '2026-01-08', 2.5, 2.5, 0)", (CODE,))
    db.conn.executemany("INSERT OR REPLACE INTO trade_calendar (trade_date, is_open) VALUES (?, 1)",
                        [("2026-01-05",), ("2026-01-06",), ("2026-01-07",), ("2026-01-08",), ("2026-01-09",)])
    db.conn.commit()
    db.close()

    monkeypatch.setattr(webapp, "DB_PATH", dbp)
    # 清掉进程内缓存，避免用例之间互相污染
    with webapp._slot_lock:
        webapp._slot_store.clear()
    webapp._dash_cache = None
    with webapp.app.test_client() as c:
        yield c, dbp


def _q(dbp, sql, args=()):
    db = Database(dbp)
    rows = [dict(r) for r in db.conn.execute(sql, args)]
    db.close()
    return rows


def test_buy_rejects_bad_input(client):
    c, dbp = client
    assert c.post("/api/holdings", json={"action": "buy", "code": "", "amount": 10}).get_json()["ok"] is False
    assert c.post("/api/holdings", json={"action": "buy", "code": CODE, "amount": 0}).get_json()["ok"] is False
    assert c.post("/api/holdings", json={"action": "nope"}).get_json()["ok"] is False
    assert _q(dbp, "SELECT COUNT(*) c FROM holdings")[0]["c"] == 0


def test_buy_prices_at_apply_day_and_sells(client):
    c, dbp = client
    # 申请日 2026-01-05 的净值是 2.0（确认日 01-06 无净值）→ 100/2.0 = 50 份
    r = c.post("/api/holdings", json={"action": "buy", "code": CODE, "date": "2026-01-05", "amount": 100}).get_json()
    assert r["ok"] is True
    h = _q(dbp, "SELECT * FROM holdings")[0]
    assert h["shares"] == 50.0 and h["confirm_nav"] == 2.0
    assert h["status"] == "holding" and h["confirm_date"] == "2026-01-06"

    # 卖出：申请日 01-08 净值 2.5 → 卖 50 份 = 125 元；持有 3 天(<7) 收 1.5% 赎回费
    r2 = c.post("/api/holdings", json={"action": "sell", "id": h["id"], "date": "2026-01-08", "amount": 125}).get_json()
    assert r2["ok"] is True
    h2 = _q(dbp, "SELECT * FROM holdings WHERE id=?", (h["id"],))[0]
    assert h2["status"] == "sold" and h2["shares"] == 0.0
    tx = _q(dbp, "SELECT * FROM transactions WHERE kind='sell'")[0]
    assert tx["confirm_nav"] == 2.5
    assert abs(tx["fee"] - round(125 * 0.015, 2)) < 1e-9      # 1.88


def test_update_amount_recomputes_shares(client):
    c, dbp = client
    c.post("/api/holdings", json={"action": "buy", "code": CODE, "date": "2026-01-05", "amount": 100})
    hid = _q(dbp, "SELECT id FROM holdings")[0]["id"]
    r = c.post("/api/holdings", json={"action": "update", "id": hid, "amount": 200}).get_json()
    assert r["ok"] is True
    h = _q(dbp, "SELECT * FROM holdings WHERE id=?", (hid,))[0]
    assert h["buy_amount"] == 200 and h["shares"] == 100.0     # 200 / 2.0
    assert c.post("/api/holdings", json={"action": "update", "id": hid, "amount": -1}).get_json()["ok"] is False


def test_delete_backs_up_and_removes(client):
    c, dbp = client
    c.post("/api/holdings", json={"action": "buy", "code": CODE, "date": "2026-01-05", "amount": 100})
    hid = _q(dbp, "SELECT id FROM holdings")[0]["id"]
    assert c.post("/api/holdings", json={"action": "delete", "id": hid}).get_json()["ok"] is True
    assert _q(dbp, "SELECT COUNT(*) c FROM holdings")[0]["c"] == 0


def test_holding_write_invalidates_cache(client):
    c, dbp = client
    c.get("/api/overview")                                     # 预热缓存
    assert webapp._slot_store.get("overview") is not None
    c.post("/api/holdings", json={"action": "buy", "code": CODE, "date": "2026-01-05", "amount": 100})
    assert "overview" not in webapp._slot_store                # 写后内存缓存已失效
    assert _q(dbp, "SELECT COUNT(*) c FROM analysis_snapshot WHERE key='overview'")[0]["c"] == 0


def test_dca_add_run_and_guard(client):
    c, dbp = client
    r = c.post("/api/dca", json={"action": "add", "code": CODE, "amount": 10,
                                 "frequency": "daily", "date": "2026-01-05"}).get_json()
    assert r["ok"] is True
    # 非法频率回退 weekly
    c.post("/api/dca", json={"action": "add", "code": CODE, "amount": 10, "frequency": "hourly", "date": "2026-01-05"})
    freqs = {p["frequency"] for p in _q(dbp, "SELECT frequency FROM dca_plans")}
    assert freqs == {"daily", "weekly"}
    assert c.post("/api/dca", json={"action": "add", "code": CODE, "amount": 0}).get_json()["ok"] is False

    pid = _q(dbp, "SELECT id FROM dca_plans ORDER BY id LIMIT 1")[0]["id"]
    assert c.post("/api/dca", json={"action": "run", "id": pid}).get_json()["ok"] is True
    rows = _q(dbp, "SELECT status, holding_id FROM dca_periods WHERE plan_id=?", (pid,))
    ex = [r for r in rows if r["status"] == "executed"]
    assert ex and all(r["holding_id"] is not None for r in ex)     # executed 必有凭证


def test_reconcile_endpoint_is_idempotent(client):
    c, dbp = client
    db = Database(dbp)
    db.add_holding({"fund_code": CODE, "fund_name": "待确认", "buy_date": "2026-01-05",
                    "buy_amount": 100.0, "apply_date": "2026-01-05",
                    "confirm_date": "2026-01-06", "accrual_start": "2026-01-07",
                    "status": "pending_confirm"})
    db.close()
    j1 = c.post("/api/reconcile").get_json()
    assert j1["ok"] and j1["changed"] is True and j1["data"]["settled_buys"] == 1
    h = _q(dbp, "SELECT shares, status FROM holdings")[0]
    assert h["status"] == "holding" and h["shares"] == 50.0     # 100 / NAV(01-05)=2.0
    j2 = c.post("/api/reconcile").get_json()
    assert j2["changed"] is False                              # 幂等
    assert _q(dbp, "SELECT shares FROM holdings")[0]["shares"] == 50.0


def test_plan_crud_via_api(client):
    c, dbp = client
    c.get("/api/plan")          # 首次访问会 seed 硬编码计划（3 个条目），先让它落库
    before = _q(dbp, "SELECT COUNT(*) c FROM investment_plan_items")[0]["c"]
    assert c.post("/api/plan", json={"action": "add_item", "code": CODE, "target_amount": 100}).get_json()["ok"] is True
    assert _q(dbp, "SELECT COUNT(*) c FROM investment_plan_items")[0]["c"] == before + 1
    iid = _q(dbp, "SELECT id FROM investment_plan_items WHERE fund_code=? ORDER BY id DESC LIMIT 1",
             (CODE,))[0]["id"]
    assert c.post("/api/plan", json={"action": "update_item", "item_id": iid,
                                     "target_amount": 250}).get_json()["ok"] is True
    assert _q(dbp, "SELECT target_amount t FROM investment_plan_items WHERE id=?", (iid,))[0]["t"] == 250.0
    assert c.post("/api/plan", json={"action": "delete_item", "item_id": iid}).get_json()["ok"] is True
    assert _q(dbp, "SELECT COUNT(*) c FROM investment_plan_items WHERE id=?", (iid,))[0]["c"] == 0


def test_concurrent_reconcile_settles_once(tmp_path):
    """8 个线程并发对账同一笔待确认卖出 → 只扣一次份额、无异常。

    这是写路径事务化（_WRITE_LOCK + BEGIN IMMEDIATE）的核心回归项。
    """
    import threading

    from src.analysis.portfolio import PortfolioTracker

    dbp = str(tmp_path / "conc.db")
    db = Database(dbp)
    db.upsert_trade_dates(["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09"])
    for d, v in [("2026-01-05", 1.0), ("2026-01-08", 1.1), ("2026-01-09", 1.2)]:
        db.conn.execute("INSERT OR REPLACE INTO fund_nav "
                        "(fund_code, nav_date, unit_nav, acc_nav, daily_return) VALUES ('C1',?,?,?,0)",
                        (d, v, v))
    hid = db.add_holding({"fund_code": "C1", "fund_name": "c", "buy_date": "2026-01-05",
                          "buy_amount": 100.0, "shares": 100.0, "apply_date": "2026-01-05",
                          "confirm_date": "2026-01-06", "accrual_start": "2026-01-07",
                          "status": "sell_pending"})
    db.add_transaction({"holding_id": hid, "fund_code": "C1", "kind": "sell",
                        "apply_date": "2026-01-08", "confirm_date": "2026-01-09",
                        "shares": 40.0, "status": "pending_confirm"})
    db.close()

    errs, barrier = [], threading.Barrier(8)

    def worker():
        try:
            d = Database(dbp)
            t = PortfolioTracker(d)
            barrier.wait(timeout=10)
            t.reconcile(today="2026-01-09")
            d.close()
        except Exception as e:                      # noqa: BLE001
            errs.append(repr(e)[:90])

    workers = [threading.Thread(target=worker) for _ in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert not errs, errs
    db = Database(dbp)
    shares = db.conn.execute("SELECT shares FROM holdings WHERE id=?", (hid,)).fetchone()[0]
    sells = db.conn.execute("SELECT COUNT(*) FROM transactions WHERE kind='sell' "
                            "AND status='confirmed'").fetchone()[0]
    db.close()
    assert shares == 60.0        # 100 − 40，只扣一次
    assert sells == 1


def test_nav_update_writes_and_invalidates_funds_snapshot(client, monkeypatch):
    """/api/nav/update 端到端（stub 掉抓取，不联网）：写净值 + 失效 funds 快照。

    这是批次一遗留的「同一 helper 已验证、路由本身未跑通」的收口。
    """
    import pandas as pd

    from src.data.collector import DataCollector

    c, dbp = client
    c.get("/api/funds")                                    # 让 funds 快照落库
    assert _q(dbp, "SELECT COUNT(*) c FROM analysis_snapshot WHERE key='funds'")[0]["c"] == 1

    def fake_collect(self, code):                          # 不联网
        return pd.DataFrame({"净值日期": ["2026-01-08", "2026-01-09"],
                             "单位净值": [2.5, 2.8],
                             "日增长率": [0.0, 0.12]})
    monkeypatch.setattr(DataCollector, "collect_fund_nav", fake_collect)

    r = c.post("/api/nav/update", json={"codes": [CODE]}).get_json()
    assert r["ok"] is True and r["failed"] == []
    assert _q(dbp, "SELECT unit_nav FROM fund_nav WHERE fund_code=? AND nav_date='2026-01-09'",
              (CODE,))[0]["unit_nav"] == 2.8
    # 净值变了 → 依赖 fund_nav 的 funds 快照必须同步失效，否则端点会读回旧池
    assert _q(dbp, "SELECT COUNT(*) c FROM analysis_snapshot WHERE key='funds'")[0]["c"] == 0


def test_sectors_cold_path_does_not_block(client, monkeypatch, tmp_path):
    """板块冷启动改为后台计算：立刻返回 warming，不再卡住请求线程。"""
    c, _dbp = client
    monkeypatch.setattr(webapp, "SECTORS_CACHE_FILE", str(tmp_path / "none.json"))
    webapp._sectors_cache = None
    started = []
    monkeypatch.setattr(webapp, "_start_sectors_warm", lambda: started.append(1))
    j = c.get("/api/sectors").get_json()
    assert j["ok"] is True and j["status"] == "warming" and j["retry_in"] > 0
    assert started == [1]                                  # 确实去起了后台任务
