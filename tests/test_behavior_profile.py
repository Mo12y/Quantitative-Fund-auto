"""行为画像（P3）测试。

三条地基（与模块 docstring 对应）：
1. **追高必须带对照**，且对照不能塌缩 —— 只买过 1 次的基金也必须得到**真实**差值
   （2026-10-03 首版那个"恒为 0"的 bug 要守住）；
2. **分位只用当日及之前**（无未来函数）；
3. **卖后走势**样本不足时返回 None 而不是拿不完整区间冒充。
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.behavior_profile import (  # noqa: E402
    AFTER_SELL_LOOKBACK, _index_on_or_before, analyze, buy_discipline, frequency,
    held_days, forward_return, load_buys, nav_percentile, nav_series,
    percentile_list, sell_discipline, trailing_return,
)


# ── 纯函数：净值序列工具 ──────────────────────────────────────────

def _s(*pairs):
    return [(d, v) for d, v in pairs]


def test_trailing_return_basic():
    ser = [("2026-01-%02d" % (i + 1), 1.0 + 0.01 * i) for i in range(30)]
    # 第 20 个点（下标 20）对下标 0 的涨幅 = (1.20/1.00 - 1) * 100
    assert trailing_return(ser, "2026-01-21", lookback=20) == pytest.approx(20.0, abs=1e-6)


def test_trailing_return_insufficient_is_none():
    ser = [("2026-01-01", 1.0), ("2026-01-02", 1.1)]
    assert trailing_return(ser, "2026-01-02", lookback=20) is None
    assert trailing_return(ser, "2025-01-01", lookback=1) is None


def test_trailing_return_uses_on_or_before():
    """买在非交易日 → 取之前最近那天，**不拿之后的**。"""
    ser = [("2026-01-01", 1.0), ("2026-01-10", 1.5), ("2026-01-11", 9.9)]
    assert trailing_return(ser, "2026-01-05", lookback=0) == pytest.approx(0.0)


def test_forward_return_and_none_at_tail():
    ser = [("2026-01-%02d" % (i + 1), 1.0 + 0.01 * i) for i in range(30)]
    assert forward_return(ser, "2026-01-01", lookback=20) == pytest.approx(20.0, abs=1e-6)
    # 尾部不足 lookback → None（不拿不完整区间冒充）
    assert forward_return(ser, "2026-01-29", lookback=20) is None


def test_index_on_or_before():
    ser = _s(("d1", 1), ("d5", 2), ("d9", 3))
    assert _index_on_or_before(ser, "d0") is None
    assert _index_on_or_before(ser, "d1") == 0
    assert _index_on_or_before(ser, "d6") == 1
    assert _index_on_or_before(ser, "d9") == 2
    assert _index_on_or_before(ser, "zzz") == 2


def test_nav_percentile_no_lookahead():
    """把**最后一天**改成天价，之前某天的分位必须**一点不变**（无未来函数）。"""
    ser = [("2026-01-%02d" % (i + 1), 1.0 + 0.001 * i) for i in range(300)]
    before = nav_percentile(ser, "2026-01-20")
    mutated = ser[:-1] + [("2026-01-30", 999.0)]
    assert nav_percentile(mutated, "2026-01-20") == before


def test_nav_percentile_needs_min_periods():
    ser = [("2026-01-%02d" % (i + 1), 1.0 + 0.001 * i) for i in range(100)]
    assert nav_percentile(ser, "2026-01-50") is None      # 50 < 252 → 不给假分位
    assert percentile_list(ser)[-1] is None


def test_held_days():
    assert held_days("2026-01-01", "2026-01-11") == 10
    assert held_days(None, "2026-01-11") is None


# ── 造库 ─────────────────────────────────────────────────────────

def _dates(n, start="2025-01-01"):
    d0 = date.fromisoformat(start)
    return [str(d0 + timedelta(days=i)) for i in range(n)]


def _mk_db(tmp_path):
    """A：早期 + 末期两笔买入（建立全局起点）；B：只在**末期**一笔买入。

    B 的买点前面刚好涨过（nav 由平台转上行）→ 它的追高差值必须为正。
    """
    from src.data.database import Database

    db = Database(str(tmp_path / "bp.db"))
    for code in ("A", "B"):
        db.upsert_fund_info({"fund_code": code, "fund_name": code, "fund_type": "混合型"})

    ds = _dates(300)
    # A：全程 1.0（平坦）
    for d in ds:
        db.conn.execute(
            "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
            " VALUES ('A',?,?,?,0)", (d, 1.0, 1.0))
    # B：前 260 天 1.0，之后每天 +0.005（末值 1.2）
    for i, d in enumerate(ds):
        v = 1.0 if i < 260 else 1.0 + 0.005 * (i - 259)
        db.conn.execute(
            "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
            " VALUES ('B',?,?,?,0)", (d, v, v))

    # A 两笔（d10 建立全局起点、d299），B 一笔（d299）
    for code, d, amt in (("A", ds[10], 100.0), ("A", ds[299], 50.0), ("B", ds[299], 80.0)):
        db.conn.execute(
            "INSERT INTO transactions (fund_code, kind, apply_date, confirm_date, amount, status, notes)"
            " VALUES (?,'buy',?,?,?,'confirmed','')", (code, d, d, amt))
    # 卖出流水（frequency 计数用）
    for code, d, sh in (("A", ds[299], 10.0), ("B", ds[299], 5.0)):
        db.conn.execute(
            "INSERT INTO transactions (fund_code, kind, apply_date, confirm_date, shares, status, notes)"
            " VALUES (?,'sell',?,?,?,'confirmed','')", (code, d, d, sh))

    # 已卖批次：一笔赚、一笔亏、一笔早卖（早卖的那笔才有 ≥20 交易日的后续）
    db.conn.execute(
        "INSERT INTO holdings (fund_code, fund_name, buy_date, buy_amount, sell_date, sell_amount, status)"
        " VALUES ('A','a',?,100.0,?,130.0,'sold')", (ds[270], ds[290]))
    db.conn.execute(
        "INSERT INTO holdings (fund_code, fund_name, buy_date, buy_amount, sell_date, sell_amount, status)"
        " VALUES ('B','b',?,100.0,?,80.0,'sold')", (ds[270], ds[285]))
    db.conn.execute(
        "INSERT INTO holdings (fund_code, fund_name, buy_date, buy_amount, sell_date, sell_amount, status)"
        " VALUES ('A','a',?,100.0,?,110.0,'sold')", (ds[200], ds[250]))
    # 第 4 笔与第 3 笔**同一天卖出**（模拟"一个决策卖出多笔"）
    db.conn.execute(
        "INSERT INTO holdings (fund_code, fund_name, buy_date, buy_amount, sell_date, sell_amount, status)"
        " VALUES ('A','a',?,100.0,?,95.0,'sold')", (ds[240], ds[250]))
    db.conn.commit()
    return db


# ── 买入纪律 ─────────────────────────────────────────────────────

def test_buy_discipline_single_buy_fund_gets_real_spread(tmp_path):
    """⭐ 回归：只买过 1 次的基金**不能**得到恒为 0 的差值（对照窗口不得塌缩）。"""
    db = _mk_db(tmp_path)
    try:
        r = buy_discipline(db.conn)
        fb = r["per_fund"]["B"]
        assert fb["n_buys"] == 1
        assert fb["spread_trail20"] is not None
        assert fb["spread_trail20"] > 0, (
            "B 的那笔买在上涨之后，对照（同基金同期全部交易日）中位是 0 → 差值必须为正；"
            "得到 0 说明对照窗口又塌缩了")
    finally:
        db.close()


def test_buy_discipline_counts_chase(tmp_path):
    db = _mk_db(tmp_path)
    try:
        r = buy_discipline(db.conn)
        assert r["n_buys"] == 3
        assert r["n_with_trail"] == 2, "最早那笔前面不足 20 个交易日 → 不给 trail20（不猜）"
        assert r["frac_bought_after_rise"] == pytest.approx(0.5, abs=0.01)
        assert r["trail_range"] is not None and r["trail_range"][1] > 0
        assert r["n_funds_with_baseline"] == 2
    finally:
        db.close()


def test_buy_discipline_empty():
    assert buy_discipline(None)["n_buys"] == 0


# ── 卖出纪律 ─────────────────────────────────────────────────────

def test_sell_discipline_loss_and_range(tmp_path):
    db = _mk_db(tmp_path)
    try:
        r = sell_discipline(db.conn)
        assert r["n_lots"] == 4
        assert r["n_loss"] == 2 and r["frac_loss"] == pytest.approx(0.5)
        assert r["best_pct"] == pytest.approx(30.0) and r["worst_pct"] == pytest.approx(-20.0)
        assert r["range"] == [pytest.approx(-20.0), pytest.approx(30.0)]
        # 已卖批次 shares=0 → 只用金额算，不能出现除零
        assert {lot["held_days"] for lot in r["lots"]} == {50, 20, 15, 10}
    finally:
        db.close()


def test_lot_count_is_not_decision_count(tmp_path):
    """⚠️ 4 个批次只对应 3 个**卖出决策日** —— 笔数 ≠ 独立决策数（M3「有效 N」同源）。"""
    db = _mk_db(tmp_path)
    try:
        r = sell_discipline(db.conn)
        assert r["n_lots"] == 4
        assert r["n_distinct_sell_dates"] == 3
        assert len(r["by_sell_date"]) == 3
        same = [x for x in r["by_sell_date"] if x["n_lots"] == 2]
        assert len(same) == 1, "同一天的两笔必须合并成一个决策日"
        assert r["median_after20_pct_by_date"] is not None
    finally:
        db.close()


def test_sell_discipline_followup_coverage(tmp_path):
    """卖后 20 日走势：尾部批次算不出 → after20 为 None，且覆盖率如实反映。"""
    db = _mk_db(tmp_path)
    try:
        r = sell_discipline(db.conn)
        assert r["n_lots"] == 4
        # 只有最早卖的那两笔（ds[250]，之后还有 49 天）算得出
        assert r["n_with_followup"] == 2
        earliest = [x for x in r["lots"] if x["sell_date"] == _dates(300)[250]]
        assert len(earliest) == 2
        assert all(x["after20"] is not None for x in earliest)
        # 尾部两笔不可算
        assert all(x["after20"] is None for x in r["lots"] if x["sell_date"] >= _dates(300)[285])
    finally:
        db.close()


def test_sell_discipline_empty():
    assert sell_discipline(None)["n_lots"] == 0


# ── 频率 ─────────────────────────────────────────────────────────

def test_frequency_counts_and_buckets(tmp_path):
    db = _mk_db(tmp_path)
    try:
        f = frequency(db.conn)
        assert f["n_buys"] == 3 and f["n_sells"] == 2
        assert f["median_held_days"] is not None
        assert sum(b["n"] for b in f["hold_buckets"]) == 4   # 四个已卖批次都落档
        assert f["n_under_7d"] == 0
        assert f["monthly"] and all({"buy", "sell"} <= set(v) for v in f["monthly"].values())
    finally:
        db.close()


# ── 汇总 ─────────────────────────────────────────────────────────

def test_analyze_shape_and_sample(tmp_path):
    db = _mk_db(tmp_path)
    try:
        r = analyze(db)
        assert r["span"] and "buy" in r and "sell" in r and "frequency" in r
        assert "sample" in r and "benchmark_pct" in r
        assert r["sample"]["sufficient"] in (True, False)
        assert r["notes"] and any("对照" in n for n in r["notes"])
        assert r["verdict"]
    finally:
        db.close()


def test_analyze_short_window_does_not_conclude(tmp_path):
    """区间 < 2 年 → verdict 必须**拒绝出行为结论**（不许拿几个月当能力证据）。"""
    db = _mk_db(tmp_path)
    try:
        r = analyze(db)
        assert r["sample"]["sufficient"] is False
        assert "样本不足" in r["verdict"]
    finally:
        db.close()


def test_analyze_no_buys_is_explicit(tmp_path):
    from src.data.database import Database

    db = Database(str(tmp_path / "empty.db"))
    try:
        assert "error" in analyze(db)
    finally:
        db.close()


def test_load_buys_ignores_unconfirmed(tmp_path):
    db = _mk_db(tmp_path)
    try:
        db.conn.execute(
            "INSERT INTO transactions (fund_code, kind, apply_date, confirm_date, amount, status, notes)"
            " VALUES ('A','buy','2026-01-01','2026-01-01',10.0,'pending','')")
        db.conn.commit()
        assert len(load_buys(db.conn)) == 3, "未确认的买入不该进画像"
    finally:
        db.close()


def test_nav_series_filters_bad_values(tmp_path):
    db = _mk_db(tmp_path)
    try:
        db.conn.execute(
            "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
            " VALUES ('A','2099-01-01',0,0,0)")
        db.conn.commit()
        ser = nav_series(db.conn, "A")
        assert all(v > 0 for _, v in ser), "0 净值不算有效净值"
    finally:
        db.close()


def test_after_sell_lookback_constant_is_documented():
    assert AFTER_SELL_LOOKBACK == 20
