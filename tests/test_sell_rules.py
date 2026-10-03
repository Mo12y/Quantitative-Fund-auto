"""同标的卖出规则模拟（P1 扩展）测试。

地基三条：
1. **买入被冻结**：所有规则的 `invested` 必须**完全相同**（否则就不是"同标的"了）；
2. **会计自洽**：`回收总额 − 投入 == 盈亏`（曾经因为只把"未卖部分"算进市值，
   出现「XIRR 为正、盈亏为负」的自相矛盾 —— 2026-10-03 修）；
3. **规则真的会触发**：涨了就止盈、跌了就止损、温度到了才卖。
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.counterfactual import (  # noqa: E402
    _nav_path, simulate_sell_rules,
)


def _mk_db(tmp_path, path_vals):
    """造一只基金 + 每天注入给定净值序列；再插两笔买入。"""
    from src.data.database import Database

    db = Database(str(tmp_path / "sr.db"))
    db.upsert_fund_info({"fund_code": "T1", "fund_name": "t", "fund_type": "混合型"})
    d0 = date(2026, 1, 5)
    rows = []
    for i, v in enumerate(path_vals):
        d = d0 + timedelta(days=i)
        rows.append(("T1", d.isoformat(), float(v), float(v), 0.0))
    db.insert_nav_batch(rows)
    for d, amt in ((d0.isoformat(), 100.0), ((d0 + timedelta(days=2)).isoformat(), 100.0)):
        db.conn.execute(
            "INSERT INTO transactions (fund_code, kind, apply_date, confirm_date, amount, status, notes)"
            " VALUES ('T1','buy',?,?,?,'confirmed','')", (d, d, amt))
    # 温度：先低后高，后段超过 70（`computed_at` 是 NOT NULL，必须一起给）
    for i, t in enumerate([20.0] * 5 + [80.0] * 10):
        db.conn.execute(
            "INSERT OR REPLACE INTO market_temperature (trade_date, temperature, computed_at)"
            " VALUES (?,?,?)", ((d0 + timedelta(days=i)).isoformat(), t, 0.0))
    db.conn.commit()
    return db


def test_frozen_buys_same_invested(tmp_path):
    """⭐ 所有规则的投入必须相同 —— 这是"同标的"的定义。"""
    db = _mk_db(tmp_path, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0,
                           2.1, 2.2, 2.3, 2.4])
    try:
        r = simulate_sell_rules(db)
        invested = {x["invested"] for x in r["results"]}
        assert len(invested) == 1, "各规则投入应完全一致：%s" % invested
        assert invested.pop() == pytest.approx(200.0)
    finally:
        db.close()


def test_accounting_identity_holds_for_every_rule(tmp_path):
    """⭐ 回收总额 − 投入 == 盈亏（防止"回款没算进市值"的老 bug 回归）。"""
    db = _mk_db(tmp_path, [1.0, 1.05, 0.95, 1.15, 1.25, 1.1, 0.9, 1.0, 1.3, 1.4,
                           1.2, 1.5, 1.6, 1.1, 1.0])
    try:
        r = simulate_sell_rules(db)
        for x in r["results"]:
            lhs = x["total_value"] - x["invested"]
            assert abs(lhs - x["pnl"]) < 0.02, \
                "%s 会计不自洽：%.2f vs %.2f" % (x["label"], lhs, x["pnl"])
            assert abs((x["end_value"] + x["proceeds"]) - x["total_value"]) < 0.02
    finally:
        db.close()


def test_take_profit_triggers_when_price_rises(tmp_path):
    """单调上涨 → 止盈规则必然触发；`never` 永不触发。"""
    db = _mk_db(tmp_path, [1.0 + 0.05 * i for i in range(15)])
    try:
        r = simulate_sell_rules(db)
        by = {x["key"]: x for x in r["results"]}
        assert by["never"]["n_sold"] == 0
        assert by["tp10"]["n_sold"] > 0, "涨了 70% 应该触发 +10% 止盈"
        assert by["tp20"]["n_sold"] > 0
    finally:
        db.close()


def test_stop_loss_triggers_when_price_falls(tmp_path):
    db = _mk_db(tmp_path, [1.0, 0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92,
                           0.91, 0.90, 0.89, 0.88, 0.87, 0.86])
    try:
        r = simulate_sell_rules(db)
        by = {x["key"]: x for x in r["results"]}
        assert by["sl5"]["n_sold"] > 0 and by["sl10"]["n_sold"] > 0
        assert by["never"]["n_sold"] == 0
    finally:
        db.close()


def test_never_selling_equals_end_value(tmp_path):
    """`never` 规则：`end_value` 应等于全部份额的期末市值，且回款为 0。"""
    db = _mk_db(tmp_path, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9,
                           2.0, 2.1, 2.2, 2.3, 2.4])
    try:
        r = simulate_sell_rules(db, rules=[("never", "从不卖", lambda *a: False)])
        x = r["results"][0]
        assert x["proceeds"] == 0.0 and x["n_sold"] == 0
        assert x["total_value"] == x["end_value"]
        # 200 元买入 → 份额 = 100/1.0 + 100/1.2；期末净值 2.4
        expect = (100.0 / 1.0 + 100.0 / 1.2) * 2.4
        assert x["end_value"] == pytest.approx(expect, abs=0.05)
    finally:
        db.close()


def test_temp_rule_respects_temperature(tmp_path):
    """温度规则：只在温度真正越过阈值后才触发。"""
    db = _mk_db(tmp_path, [1.0] * 20)          # 价格不变 → 只有温度规则会触发
    try:
        r = simulate_sell_rules(db)
        by = {x["key"]: x for x in r["results"]}
        assert by["temp70"]["n_sold"] > 0, "温度后段是 80° 应触发"
    finally:
        db.close()


def test_empty_or_missing_nav_is_explicit(tmp_path):
    from src.data.database import Database

    db = Database(str(tmp_path / "e.db"))
    try:
        r = simulate_sell_rules(db)
        assert "error" in r, "没有买入流水时应显式报错，而不是给一张空表"
    finally:
        db.close()


def test_nav_path_shape(tmp_path):
    db = _mk_db(tmp_path, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9,
                           2.0, 2.1, 2.2, 2.3, 2.4])
    try:
        p = _nav_path(db.conn, "T1", "2026-01-05", "2026-01-19")
        assert p and p[0][1] == pytest.approx(1.0) and p[-1][1] == pytest.approx(2.4)
    finally:
        db.close()
