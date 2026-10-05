# -*- coding: utf-8 -*-
"""B-4b-1 撤销机制（`ledger_commits` + `ledger_write.rollback`）的守卫测试。

撤销 = 按 `spec` 删掉当前行、再把**写前**的行插回去 —— 所以新增/修改/删除三种语义都能还原。
本文件逐条钉住：
  · 买入（新增）：撤销后 holdings + transactions 两行**都消失**
  · 改金额（修改）：撤销后金额/份额/流水**都回到改前**
  · 删除：撤销后行**插回来**
  · 分红策略（批量修改）：撤销后策略回到原值
  · 不能**重复**撤销；超出 `COMMIT_RETENTION` 的不能撤销；不存在的 id 报错
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import Database  # noqa: E402
from src.analysis import ledger_write  # noqa: E402
from src.analysis.portfolio import PortfolioTracker  # noqa: E402


def _make_db(tmp_path, name="rollback.db") -> Database:
    return Database(str(tmp_path / name), allow_create=True)


def _last_commit(db) -> int:
    return db.conn.execute("SELECT id FROM ledger_commits ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _count(db, table: str, where: str = "1=1", params=()) -> int:
    return db.conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {where}", params).fetchone()["c"]


def _seed_holding(db, amount=1000.0, nav=1.5, code="000001", name="测试基金") -> int:
    cur = db.conn.execute(
        "INSERT INTO holdings (fund_code, fund_name, buy_date, buy_amount, buy_nav, shares, status) "
        "VALUES (?, ?, '2026-01-01', ?, ?, ?, 'holding')",
        (code, name, amount, nav, round(amount / nav, 2)))
    hid = cur.lastrowid
    db.conn.execute(
        "INSERT INTO transactions (holding_id, fund_code, kind, amount, fee, shares, status) "
        "VALUES (?, ?, 'buy', ?, 0, ?, 'confirmed')",
        (hid, code, amount, round(amount / nav, 2)))
    return hid


# ── 新增语义（买入）────────────────────────────────────────────

def test_buy_then_rollback_removes_holding_and_transaction(tmp_path):
    db = _make_db(tmp_path)
    hid = PortfolioTracker(db).add_buy_transaction("000001", "测试基金", "2026-01-05", 1000.0)

    assert _count(db, "holdings") == 1
    assert _count(db, "transactions") == 1

    r = ledger_write.rollback(db, _last_commit(db))
    assert r["action"] == "buy"
    assert _count(db, "holdings", "id = ?", (hid,)) == 0
    assert _count(db, "transactions", "holding_id = ?", (hid,)) == 0


# ── 修改语义（update）──────────────────────────────────────────

def test_update_then_rollback_restores_amount_shares_and_transaction(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db, amount=1000.0, nav=1.5)
    ledger_write.update_holding(db, hid, buy_amount=2000.0)

    h = db.conn.execute("SELECT buy_amount, shares FROM holdings WHERE id=?", (hid,)).fetchone()
    assert h["buy_amount"] == 2000.0

    ledger_write.rollback(db, _last_commit(db))

    h = db.conn.execute("SELECT buy_amount, shares FROM holdings WHERE id=?", (hid,)).fetchone()
    assert h["buy_amount"] == 1000.0
    assert h["shares"] == round(1000.0 / 1.5, 2)
    tx = db.conn.execute(
        "SELECT amount, shares FROM transactions WHERE holding_id=? AND kind='buy'", (hid,)).fetchone()
    assert tx["amount"] == 1000.0                     # 流水也回到改前
    assert tx["shares"] == round(1000.0 / 1.5, 2)


# ── 删除语义（delete）──────────────────────────────────────────

def test_delete_then_rollback_inserts_rows_back(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db)
    ledger_write.delete_holding(db, hid)
    assert _count(db, "holdings") == 0
    assert _count(db, "transactions") == 0

    ledger_write.rollback(db, _last_commit(db))

    assert _count(db, "holdings", "id = ?", (hid,)) == 1
    assert _count(db, "transactions", "holding_id = ?", (hid,)) == 1


# ── 批量修改（分红策略）────────────────────────────────────────

def test_dividend_policy_then_rollback_restores(tmp_path):
    db = _make_db(tmp_path)
    _seed_holding(db, code="000001")
    _seed_holding(db, code="000001")
    ledger_write.set_dividend_policy(db, code="000001", policy="cash")
    assert _count(db, "holdings", "dividend_policy = 'cash'") == 2

    ledger_write.rollback(db, _last_commit(db))

    assert _count(db, "holdings", "dividend_policy = 'cash'") == 0   # 回到默认 reinvest


# ── 约束 ───────────────────────────────────────────────────────

def test_cannot_rollback_twice(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db)
    ledger_write.delete_holding(db, hid)
    cid = _last_commit(db)
    ledger_write.rollback(db, cid)
    with pytest.raises(ValueError, match="已经撤销过"):
        ledger_write.rollback(db, cid)


def test_cannot_rollback_beyond_retention(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db)                       # 直接 INSERT，**不产生提交**
    ledger_write.update_holding(db, hid, buy_date="2026-02-01")
    old_cid = _last_commit(db)
    # 再制造若干条提交，把它挤出保留窗口（用**存在**的持仓，否则门面会提前 return 不记录）
    for i in range(ledger_write.COMMIT_RETENTION + 2):
        ledger_write.update_holding(db, hid, buy_date="2026-03-%02d" % (i % 28 + 1))
    with pytest.raises(ValueError, match="只允许撤销最近"):
        ledger_write.rollback(db, old_cid)


def test_rollback_unknown_id_raises(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(ValueError, match="找不到"):
        ledger_write.rollback(db, 999999)


def test_recent_commits_lists_newest_first(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db)
    ledger_write.update_holding(db, hid, buy_date="2026-03-01")
    ledger_write.delete_holding(db, hid)
    rows = ledger_write.recent_commits(db, 10)
    assert rows[0]["action"] == "delete"              # 最新在最前
    assert rows[1]["action"] == "update"


# ── 端到端（走 HTTP）：买入 → 拿 commit_id → 撤销 ──────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """临时库 + Flask test client。库内容与 `test_web_api.py` 的 fixture 同构。"""
    from src.web import app as webapp

    dbp = str(tmp_path / "api.db")
    db = Database(dbp)
    db.upsert_fund_info({"fund_code": "000011", "fund_name": "测试混合C", "fund_type": "混合型"})
    db.conn.execute(
        "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
        "VALUES ('000011', '2026-01-05', 2.0, 2.0, 0)")
    db.conn.executemany(
        "INSERT OR REPLACE INTO trade_calendar (trade_date, is_open) VALUES (?, 1)",
        [("2026-01-05",), ("2026-01-06",), ("2026-01-07",), ("2026-01-08",)])
    db.conn.commit()
    db.close()

    monkeypatch.setattr(webapp, "DB_PATH", dbp)
    with webapp._slot_lock:
        webapp._slot_store.clear()
    webapp._dash_cache = None
    with webapp.app.test_client() as c:
        yield c, dbp


def _count_in(dbp, table, where="1=1"):
    db = Database(dbp)
    n = db.conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {where}").fetchone()["c"]
    db.close()
    return n


def test_api_buy_returns_commit_id_and_rollback_undoes_it(client):
    c, dbp = client
    body = c.post("/api/holdings", json={
        "action": "buy", "code": "000011", "date": "2026-01-05", "amount": 1000,
    }).get_json()
    assert body["ok"], body
    assert body.get("commit_id"), "写端点必须回 commit_id —— 前端的「撤销」靠它"
    assert _count_in(dbp, "holdings") == 1
    assert _count_in(dbp, "transactions") == 1

    r = c.post("/api/holdings/rollback", json={"commit_id": body["commit_id"]})
    assert r.get_json()["ok"], r.get_json()
    assert _count_in(dbp, "holdings") == 0            # 持仓与流水一起回滚
    assert _count_in(dbp, "transactions") == 0


def test_api_rollback_rejects_second_time(client):
    c, dbp = client
    body = c.post("/api/holdings", json={
        "action": "buy", "code": "000011", "date": "2026-01-05", "amount": 500,
    }).get_json()
    cid = body["commit_id"]
    assert c.post("/api/holdings/rollback", json={"commit_id": cid}).get_json()["ok"]

    r = c.post("/api/holdings/rollback", json={"commit_id": cid})
    assert r.status_code == 400
    assert "已经撤销过" in r.get_json()["error"]


def test_api_rollback_requires_commit_id(client):
    c, _ = client
    r = c.post("/api/holdings/rollback", json={})
    assert r.status_code == 400
    assert "commit_id" in r.get_json()["error"]


def test_api_commits_endpoint_lists_recent(client):
    c, _ = client
    c.post("/api/holdings", json={
        "action": "buy", "code": "000011", "date": "2026-01-05", "amount": 500,
    })
    j = c.get("/api/holdings/commits").get_json()
    assert j["ok"]
    assert j["data"]["retention"] == ledger_write.COMMIT_RETENTION
    assert j["data"]["commits"][0]["action"] == "buy"
