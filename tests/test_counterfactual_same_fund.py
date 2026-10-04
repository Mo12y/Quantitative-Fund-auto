# -*- coding: utf-8 -*-
"""同标的反事实（B9）的守卫。

核的是**"锁死了什么"**这件事本身 —— 因为整个方法的可信度都建立在这上面：
  ① 三情形**总投入必须相等**（否则比的就是"投入多少"而不是"时机"）；
  ② 一次性**只有 1 笔**、等额定投**笔数 = 实际笔数**；
  ③ 单调上涨的净值下，**越早买越好** → 一次性 > 等额定投（方向性 sanity check）；
  ④ 无流水时不崩（返回 error，不是抛异常）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.counterfactual import same_fund_counterfactual  # noqa: E402
from src.data.database import Database  # noqa: E402


def _build_tmp_db(tmp_path, rising: bool = True):
    """临时库：1 只基金 + 每周一笔共 6 笔买入，净值单调（涨或跌）。"""
    p = str(tmp_path / "t.db")
    db = Database(p)
    db.conn.execute("INSERT INTO fund_info (fund_code, fund_name, fund_type)"
                    " VALUES ('T0001', '测试基金', '混合型-偏股')")
    nav = 1.0
    dates = ["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26",
             "2026-02-02", "2026-02-09", "2026-02-16"]
    for i, d in enumerate(dates):
        db.conn.execute(
            "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav) VALUES (?,?,?,?)",
            ("T0001", d, nav, nav))
        nav *= 1.02 if rising else 0.98
    for i, d in enumerate(dates[:6]):        # 6 笔等额买入
        db.conn.execute(
            "INSERT INTO transactions (fund_code, kind, apply_date, confirm_date,"
            " confirm_nav, shares, amount, status, notes) VALUES (?,?,?,?,?,?,?,?,?)",
            ("T0001", "buy", d, d, 1.0, 100.0, 100.0, "confirmed", "测试"))
    db.conn.commit()
    return db


def test_locks_total_invested_and_shapes(tmp_path):
    """① 三情形总投入相等 ② 一次性 1 笔 ③ 等额定投笔数 = 实际笔数。"""
    db = _build_tmp_db(tmp_path)
    r = same_fund_counterfactual(db)
    db.close()
    assert r["summary"]["n_funds"] == 1
    f = r["funds"][0]
    assert f["actual"]["invested"] == pytest.approx(600.0)
    assert f["lump"]["invested"] == pytest.approx(600.0), "总投入没锁死"
    assert (f["even_dca"] or {}).get("invested", 600.0) == pytest.approx(600.0)
    assert f["lump"]["n_buys"] == 1
    assert f["even_dca"]["n_buys"] == f["n_buys"] == 6


def test_rising_nav_lump_beats_even_dca(tmp_path):
    """③ 单调上涨 → 越早买越好：一次性的期末市值必须高于等额定投。"""
    db = _build_tmp_db(tmp_path, rising=True)
    r = same_fund_counterfactual(db)
    db.close()
    f = r["funds"][0]
    assert f["lump"]["end_value"] > f["even_dca"]["end_value"]


def test_difference_is_actual_minus_benchmarks(tmp_path):
    """`vs_*` 的定义必须就是「实际 − 对照」，不能是别的。"""
    db = _build_tmp_db(tmp_path)
    r = same_fund_counterfactual(db)
    db.close()
    f = r["funds"][0]
    assert f["vs_lump"] == pytest.approx(
        round(f["actual"]["pnl_pct"] - f["lump"]["pnl_pct"], 2), abs=0.01)
    assert f["vs_even_dca"] == pytest.approx(
        round(f["actual"]["pnl_pct"] - f["even_dca"]["pnl_pct"], 2), abs=0.01)


def test_no_buys_returns_error_not_raises(tmp_path):
    """④ 没有流水 → 返回 error，不许抛。"""
    db = Database(str(tmp_path / "empty.db"))
    db.conn.execute("INSERT INTO fund_nav (fund_code, nav_date, unit_nav) VALUES ('X','2026-01-01',1.0)")
    db.conn.commit()
    r = same_fund_counterfactual(db)
    db.close()
    assert "error" in r


def test_skipped_counts_are_reported(tmp_path):
    """⚠️ 样本局限必须可见：被排除的只数要报出来，不能静默丢。"""
    db = _build_tmp_db(tmp_path)
    r = same_fund_counterfactual(db, min_buys=10)      # 故意把门槛抬到不可能
    db.close()
    s = r["summary"]
    assert s["n_funds"] == 0
    assert s.get("skipped_few_buys") == 1
    assert any("样本很小" in n or "没有满足条件" in n for n in r["notes"])
