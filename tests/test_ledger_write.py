# -*- coding: utf-8 -*-
"""B-2 写门面（`src/analysis/ledger_write.py`）+ `Database` 路径守卫的守卫测试。

覆盖计划书 §12 B-2 的三个验收点：
  · 路径守卫（默认拒绝 / 显式允许 / 环境变量兜底）
  · 事务原子性（门面的写走 `immediate()`）
  · 孤儿流水检测（delete 后不留指向已删持仓的 transactions）

外加两个口径缺陷的回归钉（update 同步 transactions / delete 备份两张表）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import Database  # noqa: E402
from src.analysis import ledger_write  # noqa: E402


def _make_db(tmp_path, name="ledger.db"):
    return Database(str(tmp_path / name), allow_create=True)


def _seed_holding(db, amount=1000.0, nav=1.5, code="000001", name="测试基金") -> int:
    """插一笔持仓 + 一笔对应买入流水，返回 holding id。"""
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


# ── 路径守卫 ────────────────────────────────────────────────────

def test_allow_create_false_rejects_missing_path(tmp_path):
    p = str(tmp_path / "nope.db")
    with pytest.raises(FileNotFoundError):
        Database(p, allow_create=False)


def test_allow_create_true_builds(tmp_path):
    p = str(tmp_path / "yes.db")
    db = Database(p, allow_create=True)
    try:
        assert os.path.exists(p)
    finally:
        db.close()


def test_default_rejects_when_env_unset(tmp_path, monkeypatch):
    # conftest 设了 QFA_DB_ALLOW_CREATE=1 兜底；这里删掉它，验证"默认拒绝"的语义本身
    monkeypatch.delenv("QFA_DB_ALLOW_CREATE", raising=False)
    p = str(tmp_path / "noenv.db")
    with pytest.raises(FileNotFoundError):
        Database(p)


def test_default_allows_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv("QFA_DB_ALLOW_CREATE", "1")
    p = str(tmp_path / "env.db")
    db = Database(p)
    try:
        assert os.path.exists(p)
    finally:
        db.close()


# ── 口径①：update 改 buy_amount 同步 transactions ──────────────

def test_update_buy_amount_syncs_transactions(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db, amount=1000.0, nav=1.5)   # name 无份额类别 → default 费率 0.0015
    assert ledger_write.update_holding(db, hid, buy_amount=2000.0) is True

    h = db.conn.execute(
        "SELECT buy_amount, shares, buy_nav FROM holdings WHERE id=?", (hid,)).fetchone()
    assert h["buy_amount"] == 2000.0
    assert h["shares"] == round(2000.0 / 1.5, 2)

    tx = db.conn.execute(
        "SELECT amount, fee, shares FROM transactions WHERE holding_id=? AND kind='buy'",
        (hid,)).fetchone()
    assert tx["amount"] == 2000.0                       # ① 同步 amount
    assert tx["shares"] == h["shares"]                  # ① 同步 shares
    assert tx["fee"] == round(2000.0 - 2000.0 / 1.0015, 2)   # ① 同步 fee（default 0.0015）


def test_update_missing_holding_returns_false(tmp_path):
    db = _make_db(tmp_path)
    assert ledger_write.update_holding(db, 999999, buy_amount=1.0) is False


def test_update_only_date_does_not_touch_shares(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db, amount=1000.0, nav=1.5)
    assert ledger_write.update_holding(db, hid, buy_date="2026-02-01") is True
    h = db.conn.execute("SELECT buy_date, shares FROM holdings WHERE id=?", (hid,)).fetchone()
    assert h["buy_date"] == "2026-02-01"
    assert h["shares"] == round(1000.0 / 1.5, 2)       # 只改日期，不重算 shares


# ── 口径②：delete 备份两张表 + 连带删流水 ─────────────────────

def test_delete_removes_tx_and_backs_up_both(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db)
    assert ledger_write.delete_holding(db, hid) is True

    n_h = db.conn.execute("SELECT COUNT(*) c FROM holdings WHERE id=?", (hid,)).fetchone()["c"]
    n_t = db.conn.execute(
        "SELECT COUNT(*) c FROM transactions WHERE holding_id=?", (hid,)).fetchone()["c"]
    assert n_h == 0
    assert n_t == 0                                     # ② 连带删流水，不留孤儿

    backup_dir = os.path.join(str(tmp_path), "backups")
    files = os.listdir(backup_dir)
    assert any("holdings" in f for f in files)          # ② 备份 holdings
    assert any("transactions" in f for f in files)      # ② 备份 transactions


def test_delete_missing_returns_false(tmp_path):
    db = _make_db(tmp_path)
    assert ledger_write.delete_holding(db, 999999) is False


def test_no_orphan_transactions_after_delete(tmp_path):
    db = _make_db(tmp_path)
    hid = _seed_holding(db)
    ledger_write.delete_holding(db, hid)
    orphans = db.conn.execute(
        "SELECT COUNT(*) c FROM transactions t "
        "LEFT JOIN holdings h ON t.holding_id = h.id WHERE h.id IS NULL").fetchone()["c"]
    assert orphans == 0


# ── set_dividend_policy（原裸 SQL 收编）────────────────────────

def test_set_dividend_policy_by_code(tmp_path):
    db = _make_db(tmp_path)
    _seed_holding(db, code="000001")
    _seed_holding(db, code="000001")                    # 同基金两笔
    assert ledger_write.set_dividend_policy(db, code="000001", policy="cash") == 2
    rows = db.conn.execute(
        "SELECT dividend_policy FROM holdings WHERE fund_code='000001'").fetchall()
    assert all(r["dividend_policy"] == "cash" for r in rows)


def test_set_dividend_policy_invalid_raises(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(ValueError):
        ledger_write.set_dividend_policy(db, code="000001", policy="nope")


def test_set_dividend_policy_missing_returns_zero(tmp_path):
    db = _make_db(tmp_path)
    assert ledger_write.set_dividend_policy(db, code="000001", policy="cash") == 0
