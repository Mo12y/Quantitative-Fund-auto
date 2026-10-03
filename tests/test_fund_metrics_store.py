"""物化指标表（E2 / DWS 层）测试。

两条地基：
1. **增量语义**：日期没前进就不重算（这是"长期低成本"的关键）；
2. **不是单点故障**：物化缺失时 `load_or_compute` 必须**回退实时算**，且标明来源。
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import fund_metrics_store as ms  # noqa: E402
from src.analysis.nav_metrics import MIN_POINTS  # noqa: E402


def _mk_db(tmp_path, n_days=None):
    """造一只基金 + 足够长的净值序列（`compute` 需要 >= MIN_POINTS 个点）。"""
    from src.data.database import Database

    db = Database(str(tmp_path / "m.db"))
    n = n_days or (MIN_POINTS + 60)
    d0 = date(2023, 1, 2)
    rows = []
    for i in range(n):
        d = d0 + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        v = 1.0 + 0.0004 * i
        rows.append(("T1", d.isoformat(), round(v, 6), round(v, 6), 0.0))
    db.insert_nav_batch(rows)
    db.upsert_fund_info({"fund_code": "T1", "fund_name": "t", "fund_type": "混合型"})
    return db


def test_ensure_table_creates_it(tmp_path):
    db = _mk_db(tmp_path)
    try:
        ms.ensure_table(db)
        names = [r[0] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        assert ms.TABLE in names
    finally:
        db.close()


def test_stale_codes_lists_unmaterialized(tmp_path):
    db = _mk_db(tmp_path)
    try:
        assert "T1" in ms.stale_codes(db), "没物化过 → 应判过期"
    finally:
        db.close()


def test_refresh_then_not_stale(tmp_path):
    """刷新后**不再过期** —— 增量语义的核心。"""
    db = _mk_db(tmp_path)
    try:
        r1 = ms.refresh(db, codes=["T1"])
        assert r1["refreshed"] == 1 and r1["failed"] == 0, r1
        assert ms.stale_codes(db, ["T1"]) == [], "刷新后不该再判过期"

        r2 = ms.refresh(db, codes=["T1"])
        assert r2["scanned"] == 0, "第二次刷新应是 no-op（日期没前进）：%s" % r2
    finally:
        db.close()


def test_new_nav_date_makes_it_stale_again(tmp_path):
    db = _mk_db(tmp_path)
    try:
        ms.refresh(db, codes=["T1"])
        # 追加一条更新的净值 → 应重新判过期
        db.insert_nav_batch([("T1", "2030-01-02", 9.99, 9.99, 0.0)])
        assert ms.stale_codes(db, ["T1"]) == ["T1"], "净值日期前进 → 必须重算"
    finally:
        db.close()


def test_load_returns_materialized_metrics(tmp_path):
    db = _mk_db(tmp_path)
    try:
        ms.refresh(db, codes=["T1"])
        got = ms.load(db, ["T1"])
        assert "T1" in got
        assert got["T1"]["sharpe"] is not None
        assert got["T1"]["asof"] is not None
    finally:
        db.close()


def test_load_does_not_trigger_compute(tmp_path):
    """没物化过 → `load` 返回空（**不偷偷算**），避免请求路径里意外触发长计算。"""
    db = _mk_db(tmp_path)
    try:
        assert ms.load(db, ["T1"]) == {}
    finally:
        db.close()


def test_load_or_compute_falls_back(tmp_path):
    """物化缺失 → 回退实时算，并标 `computed_at=None` 表明"这是刚算的"。"""
    db = _mk_db(tmp_path)
    try:
        got = ms.load_or_compute(db, ["T1"])
        assert "T1" in got, "必须回退，不能因为没物化就返回空"
        assert got["T1"]["sharpe"] is not None
        assert got["T1"]["computed_at"] is None, "回退来的值要能区分来源"
    finally:
        db.close()


def test_load_or_compute_matches_materialized(tmp_path):
    """回退算出来的指标必须与物化的**同口径**（都来自 nav_metrics.compute）。"""
    db = _mk_db(tmp_path)
    try:
        live = ms.load_or_compute(db, ["T1"])["T1"]
        ms.refresh(db, codes=["T1"])
        mat = ms.load(db, ["T1"])["T1"]
        for k in ("sharpe", "annual_return", "max_drawdown_1y", "sortino", "calmar"):
            a, b = mat[k], live[k]
            # ⚠️ SQLite 不存 NaN → 写进去变成 NULL，读回来是 None。
            # 所以"实时算出的 NaN"与"物化的 None"**语义等价**，不能当不一致。
            if a is None or b is None or b != b:
                assert (a is None or a != a) and (b is None or b != b), \
                    "缺失语义应一致：%s（物化 %r vs 实时 %r）" % (k, a, b)
                continue
            assert a == pytest.approx(b, rel=1e-9), "口径必须一致：%s" % k
    finally:
        db.close()


def test_force_recomputes(tmp_path):
    db = _mk_db(tmp_path)
    try:
        ms.refresh(db, codes=["T1"])
        r = ms.refresh(db, codes=["T1"], force=True)
        assert r["refreshed"] == 1, "force 应无视 asof 重算：%s" % r
    finally:
        db.close()


def test_stats_shape(tmp_path):
    db = _mk_db(tmp_path)
    try:
        ms.refresh(db, codes=["T1"])
        s = ms.stats(db)
        assert s["rows"] >= 1 and s["asof_max"] is not None
    finally:
        db.close()


def test_refresh_handles_insufficient_series(tmp_path):
    """净值太短的基金 → 计入 failed，不写半截数据。"""
    from src.data.database import Database

    db = Database(str(tmp_path / "short.db"))
    try:
        db.insert_nav_batch([("S1", "2026-01-05", 1.0, 1.0, 0.0)])
        r = ms.refresh(db, codes=["S1"])
        assert r["refreshed"] == 0 and r["failed"] == 1, r
        assert ms.load(db, ["S1"]) == {}
    finally:
        db.close()
