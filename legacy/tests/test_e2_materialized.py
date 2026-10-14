# -*- coding: utf-8 -*-
"""E2 接入测试：`fund_scorer` 用 `fund_metrics` 物化指标，但**口径必须一模一样**。

三条地基：
1. **物化值 == 实时值**（同一 SSOT、同一输入 → 同一个数）；
2. **每个检查不再各算一遍** —— `_score_series` 里 `nav_metrics.compute` 至多 1 次
   （原来是回撤/动量/夏普各 1 次 = 2~3 次）；
3. ⚠️ **两个必须显式处理的坑**：
   · SQLite 不存 NaN → 物化值里的 `None` 必须归一成 `nan`（否则 `np.isfinite(None)` 抛 TypeError）；
   · 物化表是用 **SSOT 默认无风险利率** 算的 → 调用方传了别的 `risk_free_rate` 时
     **不得**使用物化值（否则同一只基金的夏普会随调用方而变）。
"""
import math
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import fund_metrics_store as ms  # noqa: E402
from src.analysis import fund_scorer as fs  # noqa: E402
from src.analysis import nav_metrics  # noqa: E402
from src.analysis.risk_free import RISK_FREE_ANNUAL  # noqa: E402


def _mk_db(tmp_path, n=800):
    """造一只有 800 天净值的基金（够 3 年窗口），并填充物化表。"""
    from src.data.database import Database

    db = Database(str(tmp_path / "e2.db"))
    db.upsert_fund_info({"fund_code": "T1", "fund_name": "测试一号",
                         "fund_type": "混合型-偏股", "mgt_fee": 1.0})
    d0 = date(2023, 1, 2)
    nav = 1.0
    for i in range(n):
        nav *= 1.0 + 0.0004 + 0.008 * math.sin(i / 7.0)     # 带波动的确定性序列
        d = str(d0 + timedelta(days=i))
        db.conn.execute(
            "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
            " VALUES ('T1',?,?,?,0)", (d, round(nav, 6), round(nav, 6)))
    db.conn.commit()
    ms.refresh(db)
    return db


def _series(db, code="T1"):
    s = fs.FundScreener(db)
    return s, s._load_nav_series([code])[code]


# ── ① 口径一致 ───────────────────────────────────────────────────

def test_materialized_equals_live(tmp_path):
    """⭐ 物化值必须与实时算**逐位一致**（这是 E2 敢接的前提）。"""
    db = _mk_db(tmp_path)
    try:
        s, (vals, dates) = _series(db)
        mats = s._load_materialized_metrics(["T1"])
        assert mats, "物化表应命中"
        live = nav_metrics.compute(vals, dates, risk_free=s.risk_free_rate)
        got = s._prepare_metrics(vals, dates, mats["T1"])
        assert live is not None
        for k in ("window_points", "annual_return", "ann_vol", "max_drawdown_1y",
                  "momentum_3m", "sharpe", "sortino", "calmar"):
            a, b = live.get(k), got.get(k)
            if a is None or b is None or a != a or b != b:
                assert (a is None or a != a) and (b is None or b != b), \
                    "字段 %s 物化/实时不一致：%r vs %r" % (k, a, b)
            else:
                assert abs(float(a) - float(b)) < 1e-9, \
                    "字段 %s 物化/实时不一致：%r vs %r" % (k, a, b)
    finally:
        db.close()


def test_screen_funds_output_identical(tmp_path):
    """⭐ 最直接的验收：开关物化，`screen_funds()` 的结果必须**完全相同**。"""
    db = _mk_db(tmp_path)
    try:
        s = fs.FundScreener(db)
        on = s.screen_funds(fund_types=["混合型"])
        orig = fs.FundScreener._load_materialized_metrics
        fs.FundScreener._load_materialized_metrics = lambda self, codes: {}
        try:
            off = s.screen_funds(fund_types=["混合型"])
        finally:
            fs.FundScreener._load_materialized_metrics = orig
        assert len(on) == len(off)
        if len(on):
            cols = ["fund_code", "risk_label", "metrics"]
            assert on[cols].to_dict("records") == off[cols].to_dict("records")
    finally:
        db.close()


# ── ② 不再重复计算 ───────────────────────────────────────────────

def test_compute_called_at_most_once_per_fund(tmp_path, monkeypatch):
    """原来是 3 次（回撤/动量/夏普各一），现在至多 1 次。"""
    db = _mk_db(tmp_path)
    calls = {"n": 0}
    real = nav_metrics.compute

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(nav_metrics, "compute", counting)
    try:
        s, (vals, dates) = _series(db)
        s._score_series(vals, {"fund_code": "T1", "fund_type": "混合型-偏股"},
                        dates, materialized=None)
        assert calls["n"] == 1, "未命中物化时应恰好算 1 次，实测 %d 次" % calls["n"]
    finally:
        db.close()


def test_materialized_path_computes_zero_times(tmp_path, monkeypatch):
    db = _mk_db(tmp_path)
    calls = {"n": 0}
    real = nav_metrics.compute

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(nav_metrics, "compute", counting)
    try:
        s, (vals, dates) = _series(db)
        mats = s._load_materialized_metrics(["T1"])
        calls["n"] = 0
        s._score_series(vals, {"fund_code": "T1", "fund_type": "混合型-偏股"},
                        dates, materialized=mats["T1"])
        assert calls["n"] == 0, "命中物化时不该再实时算，实测 %d 次" % calls["n"]
    finally:
        db.close()


def test_insufficient_data_not_recomputed(tmp_path, monkeypatch):
    """数据不足（compute → None）时，也不该让三个检查各再算一遍。"""
    db = _mk_db(tmp_path)
    calls = {"n": 0}

    def fake(*a, **kw):
        calls["n"] += 1
        return None

    monkeypatch.setattr(nav_metrics, "compute", fake)
    try:
        s = fs.FundScreener(db)
        import numpy as np
        s._score_series(np.linspace(1.0, 1.1, 100),
                        {"fund_code": "X", "fund_type": "混合型"}, None, materialized=None)
        assert calls["n"] == 1, "上游已给出 None，三个检查不该再各算一遍（实测 %d）" % calls["n"]
    finally:
        db.close()


# ── ③ 两个坑 ─────────────────────────────────────────────────────

def test_none_from_sqlite_becomes_nan(tmp_path):
    """SQLite 不存 NaN → 物化值是 None；直接 `np.isfinite(None)` 会 TypeError。"""
    db = _mk_db(tmp_path)
    try:
        s = fs.FundScreener(db)
        import numpy as np
        m = s._prepare_metrics(None, None, {"sharpe": None, "ann_vol": None,
                                            "momentum_3m": None, "max_drawdown_1y": None})
        assert all(isinstance(m[k], float) and m[k] != m[k] for k in
                   ("sharpe", "ann_vol", "momentum_3m", "max_drawdown_1y"))
        lvl, txt, _, _, _ = s._check_sharpe(None, None, m)
        assert lvl == "unknown" and "数据不足" in txt
    finally:
        db.close()


def test_non_default_risk_free_disables_materialized(tmp_path):
    """⭐ 传了非默认无风险利率 → **不得**用物化值（否则夏普随调用方而变）。"""
    db = _mk_db(tmp_path)
    try:
        s = fs.FundScreener(db)
        assert s._load_materialized_metrics(["T1"]), "默认利率下应命中"
        s2 = fs.FundScreener(db, risk_free_rate=float(RISK_FREE_ANNUAL) + 0.01)
        assert s2._load_materialized_metrics(["T1"]) == {}, \
            "非默认 risk_free 时必须回退实时算"
    finally:
        db.close()


def test_no_db_is_safe():
    s = fs.FundScreener.__new__(fs.FundScreener)
    s.db = None
    s.risk_free_rate = float(RISK_FREE_ANNUAL)
    assert s._load_materialized_metrics(["X"]) == {}
