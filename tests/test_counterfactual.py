"""反事实归因（P1）测试。

三条地基：
1. **定投识别规则**必须与文档一致（它决定 ② 的样本，换个规则结论会变）；
2. **温度倍率**必须单调递减且夹在 [0.5, 2.0]（③ 的策略口径）；
3. **`simulate` 的会计必须自洽**：投入 = Σ金额、市值 = Σ(份额×期末净值)、盈亏 = 市值 − 投入。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.counterfactual import (  # noqa: E402
    DCA_AMOUNT, benchmark_return, compare, is_dca, load_buy_flows, nav_lookup,
    sample_adequacy, simulate, temp_multiplier,
)


# ── 样本充分性：把「别被短期数据迷惑」机制化 ────────────────────

def test_sample_adequacy_flags_short_window():
    """3.5 个月必须**报警** —— 这是用户 2026-10-03 的提醒，不能只写在文档里。"""
    s = sample_adequacy("2026-06-15", "2026-09-24")
    assert s["sufficient"] is False
    assert s["warning"] and "不足以判断操作能力" in s["warning"]
    assert "beta" in s["warning"], "必须点出『会把 beta 记成 alpha』"
    assert s["years"] is not None and s["years"] < 1.0


def test_sample_adequacy_passes_long_window():
    assert sample_adequacy("2020-01-01", "2023-01-01")["sufficient"] is True
    assert sample_adequacy("2020-01-01", "2023-01-01")["warning"] is None


def test_sample_adequacy_bad_dates_is_explicit():
    s = sample_adequacy(None, "2026-09-24")
    assert s["sufficient"] is False and "无法解析" in s["warning"]


def test_benchmark_return_needs_index_data(tmp_path):
    """没有指数数据 → None（不编造基准）。"""
    from src.data.database import Database

    db = Database(str(tmp_path / "b.db"))
    try:
        assert benchmark_return(db.conn, "2026-06-15", "2026-09-24") is None
    finally:
        db.close()


def test_benchmark_return_computes_change(tmp_path):
    from src.data.database import Database

    db = Database(str(tmp_path / "b2.db"))
    try:
        for d, v in (("2026-06-15", 4000.0), ("2026-09-24", 3800.0)):
            db.conn.execute(
                "INSERT OR REPLACE INTO index_daily (index_code, trade_date, close)"
                " VALUES ('000300',?,?)", (d, v))
        db.conn.commit()
        assert benchmark_return(db.conn, "2026-06-15", "2026-09-24") == pytest.approx(-5.0)
    finally:
        db.close()


def test_compare_includes_sample_and_benchmark(tmp_path):
    """`compare()` 必须把样本与基准一并带出 —— 否则调用方又会只看绝对数。"""
    db = _mk_db(tmp_path)
    try:
        r = compare(db)
        assert "sample" in r and "benchmark_pct" in r, "缺这两项就等于默认读者会看 beta"
        assert r["sample"]["sufficient"] in (True, False)
    finally:
        db.close()


# ── 定投识别 ──────────────────────────────────────────────────────

def test_is_dca_by_note():
    assert is_dca("定投", 50.0) is True
    assert is_dca("补录-定投", 50.0) is True
    assert is_dca("手动补录", 50.0) is False


def test_is_dca_by_amount():
    """金额命中：历史上有相当一部分定投笔的 notes 是空的（早期"流水导入"未标注）。"""
    assert is_dca("", DCA_AMOUNT) is True
    assert is_dca(None, 10.0) is True
    assert is_dca("", 10.5) is False


def test_is_dca_handles_bad_input():
    assert is_dca("", None) is False
    assert is_dca("", "abc") is False
    assert is_dca(None, None) is False


# ── 温度倍率 ──────────────────────────────────────────────────────

def test_temp_multiplier_monotone_and_bounded():
    """温度越低投得越多；倍率夹在 [0.5, 2.0]。"""
    vals = [temp_multiplier(t) for t in range(0, 101, 10)]
    for a, b in zip(vals, vals[1:]):
        assert b <= a + 1e-9, "必须单调不增：%s" % vals
    assert max(vals) <= 2.0 and min(vals) >= 0.5


def test_temp_multiplier_anchor_points():
    """写死三个锚点，防止以后有人调了公式却没人发现。"""
    assert temp_multiplier(30) == pytest.approx(2.0)
    assert temp_multiplier(70) == pytest.approx(0.5)
    assert temp_multiplier(50) == pytest.approx(1.25)


def test_temp_multiplier_missing_is_neutral():
    """没有温度 → 倍率 1.0（不放大不缩小），而不是猜一个。"""
    assert temp_multiplier(None) == 1.0
    assert temp_multiplier("abc") == 1.0


# ── 模拟的会计自洽 ────────────────────────────────────────────────

def _mk_db(tmp_path):
    from src.data.database import Database

    db = Database(str(tmp_path / "cf.db"))
    db.upsert_fund_info({"fund_code": "T1", "fund_name": "t", "fund_type": "混合型"})
    for d, v in (("2026-01-05", 1.00), ("2026-06-30", 1.20)):
        db.conn.execute(
            "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
            " VALUES ('T1',?,?,?,0)", (d, v, v))
    for d, amt, note in (("2026-01-05", 100.0, "定投"), ("2026-01-05", 200.0, "主动")):
        db.conn.execute(
            "INSERT INTO transactions (fund_code, kind, apply_date, confirm_date, amount, status, notes)"
            " VALUES ('T1','buy',?,?,?,'confirmed',?)", (d, d, amt, note))
    db.conn.commit()
    return db


def test_simulate_accounting_is_consistent(tmp_path):
    """买 300 元、净值 1.00 → 300 份；期末 1.20 → 市值 360、盈亏 +60。"""
    db = _mk_db(tmp_path)
    try:
        buys = load_buy_flows(db.conn)
        assert len(buys) == 2
        r = simulate(buys, db.conn, "2026-06-30", label="x")
        assert r["invested"] == 300.0
        assert r["end_value"] == pytest.approx(360.0, abs=0.01)
        assert r["pnl"] == pytest.approx(60.0, abs=0.01)
        assert r["pnl_pct"] == pytest.approx(20.0, abs=0.05)
        assert r["xirr_pct"] is not None and r["xirr_pct"] > 0
        assert r["n_buys"] == 2 and r["missing_nav"] == 0
    finally:
        db.close()


def test_simulate_scale_changes_invested(tmp_path):
    """倍率 0.5 → 投入减半、份额减半、市值减半（比例关系要保持）。"""
    db = _mk_db(tmp_path)
    try:
        buys = load_buy_flows(db.conn)
        half = simulate(buys, db.conn, "2026-06-30", scale=lambda d: 0.5)
        full = simulate(buys, db.conn, "2026-06-30")
        assert half["invested"] == pytest.approx(full["invested"] / 2, abs=0.02)
        assert half["end_value"] == pytest.approx(full["end_value"] / 2, abs=0.02)
        # 同比例缩放不改变收益率
        assert half["pnl_pct"] == pytest.approx(full["pnl_pct"], abs=0.05)
    finally:
        db.close()


def test_simulate_skips_missing_nav_but_counts_it(tmp_path):
    """取不到净值的笔**计入 missing_nav**，不静默丢弃（否则投入与市值会对不上）。"""
    db = _mk_db(tmp_path)
    try:
        db.conn.execute(
            "INSERT INTO transactions (fund_code, kind, apply_date, confirm_date, amount, status, notes)"
            " VALUES ('NOPE','buy','2026-01-05','2026-01-05',50.0,'confirmed','')")
        db.conn.commit()
        buys = load_buy_flows(db.conn)
        r = simulate(buys, db.conn, "2026-06-30")
        assert r["missing_nav"] == 1
        assert r["invested"] == 300.0, "缺失净值那笔不该混进投入"
    finally:
        db.close()


def test_simulate_empty_is_safe():
    assert simulate([], None, "2026-06-30")["invested"] == 0.0


def test_nav_lookup_takes_on_or_before(tmp_path):
    """买在 2026-03-01（当天无净值）→ 应取 01-05 的 1.00（之前最近），不是 06-30 的 1.20。"""
    db = _mk_db(tmp_path)
    try:
        assert nav_lookup(db.conn, "T1", "2026-03-01") == pytest.approx(1.00)
        assert nav_lookup(db.conn, "T1", "2026-01-01") is None
    finally:
        db.close()


def test_compare_shape(tmp_path):
    db = _mk_db(tmp_path)
    try:
        r = compare(db)
        assert "cases" in r
        for key in ("actual", "hold_all", "dca_only", "temp_tilt"):
            assert key in r["cases"], key
        # ② 只定投：应只保留「定投」那一笔（100 元），而不是全部 300
        assert r["cases"]["dca_only"]["invested"] == pytest.approx(100.0, abs=0.01)
        assert r["n_buys_total"] == 2 and r["n_buys_dca"] == 1
        assert any("XIRR" in n for n in r["notes"]), "必须声明『只能比 XIRR』"
    finally:
        db.close()
