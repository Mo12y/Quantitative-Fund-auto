"""`Database` 建连健壮性 —— readonly 竞态回归哨兵（2026-09-27）。

背景：WAL 模式下多个连接**同时首次打开**一个新库时，部分连接会以只读建成，
直到第一次真正写才抛 `attempt to write a readonly database`。
`tests/test_web_api.py::test_concurrent_reconcile_settles_once` 在整目录跑时
会随机命中（该测试文件本身没改过，是预先存在的健壮性缺口）。

修复：`Database._open_writable` 建连后做一次**写探针**（BEGIN IMMEDIATE + ROLLBACK），
失败就关掉重开。本文件用**确定性**手法锁住它（不靠并发碰运气）：
  · 用假连接模拟 readonly，断言"会重开且恢复"；
  · 断言全部重试失败后**如实抛错**（不静默降级成只读可用）。
"""
import os
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import Database  # noqa: E402


class _FakeReadOnlyConn:
    """只读连接替身：PRAGMA 能跑，写事务抛 readonly（复刻 SQLite 竞态后的状态）。"""

    def __init__(self, real):
        self._real = real
        self.closed = False

    def execute(self, sql, *args):
        if sql.strip().upper().startswith("BEGIN"):
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return self._real.execute(sql, *args)

    def close(self):
        self.closed = True
        self._real.close()


def _fresh_db(tmp_path, name="ro.db") -> str:
    dbp = str(tmp_path / name)
    Database(dbp).close()
    return dbp


def test_retries_once_then_recovers(tmp_path, monkeypatch):
    """首次返回只读连接 → 应当关掉重开，最终拿到**真的可写**连接。"""
    dbp = _fresh_db(tmp_path)
    orig, calls, fakes = Database._connect_raw, [], []

    def fake(self):
        calls.append(1)
        c = orig(self)
        if len(calls) == 1:
            f = _FakeReadOnlyConn(c)
            fakes.append(f)
            return f
        return c

    monkeypatch.setattr(Database, "_connect_raw", fake)
    d = Database(dbp)
    try:
        assert len(calls) == 2, "只读连接应触发一次重开"
        assert fakes[0].closed, "只读连接必须被关掉（不能泄漏）"
        assert d.journal_mode == "wal", "探针后应如实读回实际 journal_mode"
        # 端到端证明这个连接真的能写
        d.conn.execute("BEGIN IMMEDIATE")
        d.conn.execute("CREATE TABLE IF NOT EXISTS _probe_ok (x INTEGER)")
        d.conn.execute("ROLLBACK")
    finally:
        d.close()


def test_raises_when_all_tries_are_readonly(tmp_path, monkeypatch):
    """所有重试都只读 → **如实抛错**，不得静默降级成"只读也能用"。"""
    dbp = _fresh_db(tmp_path, "ro2.db")
    orig, calls = Database._connect_raw, []

    def fake(self):
        calls.append(1)
        return _FakeReadOnlyConn(orig(self))

    monkeypatch.setattr(Database, "_connect_raw", fake)
    with pytest.raises(sqlite3.OperationalError) as ei:
        Database(dbp)
    assert "readonly" in str(ei.value)
    assert len(calls) == Database._OPEN_TRIES, "应当用尽全部重试次数"


def test_journal_mode_recorded_on_normal_open(tmp_path):
    dbp = _fresh_db(tmp_path, "ok.db")
    d = Database(dbp)
    try:
        assert d.journal_mode == "wal"
    finally:
        d.close()


def test_concurrent_first_open_all_writable(tmp_path):
    """端到端：N 个连接**同时首次打开**同一个新库，之后每个都必须可写。

    这正是 `test_concurrent_reconcile_settles_once` 的建连形态（那里只在整目录
    跑时才偶发）。探针修复前列实测 29~30/30 轮复现只读失败。
    """
    dbp = str(tmp_path / "conc_open.db")
    Database(dbp).close()          # 建库（关闭后 -wal/-shm 会被清掉，回到"首次打开"场景）

    n, errs, barrier = 8, [], threading.Barrier(8)

    def worker(i):
        # 连接必须**在创建它的线程里**关闭（sqlite3 默认 check_same_thread=True）
        try:
            c = Database(dbp)
            try:
                barrier.wait(timeout=15)
                c.conn.execute("BEGIN IMMEDIATE")
                c.conn.execute("CREATE TABLE IF NOT EXISTS t_probe (x INTEGER)")
                c.conn.execute("ROLLBACK")
            finally:
                c.close()
        except Exception as e:                        # noqa: BLE001
            errs.append(repr(e)[:100])

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, errs
