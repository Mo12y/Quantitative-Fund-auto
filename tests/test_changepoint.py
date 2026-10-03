"""变点检测（M5）测试。

四条地基（对应模块 docstring 的承诺）：
1. **PELT 的惩罚倍率是被校准过的** —— 纯噪声（60日滚动波动）上**不许**报出变点
   （首版用教科书 BIC 惩罚，在纯噪声上报了 22 个"变点"，全是人工产物）；
2. **真实跃迁必须被检出**（校准不能把灵敏度一起校没了）；
3. **变点检测必须有市场对照** —— 基准缺失时**不许**给出 `excess_*` 字段；
4. **β 的对照不是基准自己**（那会让超额恒为 0，首版的真 bug）。
"""
import math
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.changepoint import (  # noqa: E402
    BETA_SHIFT_ABS, CONTROL_CODE, IT_CRIT_5PCT, PELT_PENALTY_MULT, VOL_SHIFT_REL,
    ann_vol_pct, analyze, beta, cusum_squares, daily_returns, fund_returns,
    pelt, rolling_vol, scan,
)


# ── 确定性伪随机（不用 numpy，跨环境稳定） ─────────────────────────

def _noise(n, sd, seed):
    x = seed
    out = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        u1 = (x % 100000 + 1) / 100001.0
        x = (1103515245 * x + 12345) % (2 ** 31)
        u2 = (x % 100000 + 1) / 100001.0
        z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        out.append(z * sd)
    return out


def _dates(n, start="2020-01-01"):
    d0 = date.fromisoformat(start)
    return [str(d0 + timedelta(days=i)) for i in range(n)]


# ── 算法原语 ─────────────────────────────────────────────────────

def test_ann_vol_pct_known_value():
    # 交替 ±1%（100 个点，均值 0）→ **样本** sd = 0.01·√(100/99)（用 n−1 作分母）
    r = [0.01, -0.01] * 50
    expected = 0.01 * math.sqrt(100 / 99) * math.sqrt(244) * 100
    assert ann_vol_pct(r) == pytest.approx(expected, rel=1e-6)
    assert ann_vol_pct([0.01]) is None, "单点无法算波动（不猜）"


def test_daily_returns_and_bad_nav():
    pairs = [("d1", 1.0), ("d2", 1.1), ("d3", 0.0), ("d4", 1.21)]
    out = daily_returns([p for p in pairs if p[1] > 0])
    assert [round(v, 6) for _, v in out] == [pytest.approx(0.1), pytest.approx(0.1)]


def test_cusum_squares_flags_variance_shift():
    x = _noise(600, 0.004, 11) + _noise(600, 0.02, 12)
    r = cusum_squares(x)
    assert r["ok"] and r["significant"], "1:25 的方差跃迁必须显著"
    assert 450 <= r["k"] <= 750, "变点应落在真实分割点(600)附近，实测 %s" % r["k"]


def test_cusum_squares_quiet_on_stationary_noise():
    x = _noise(1200, 0.01, 21)
    r = cusum_squares(x)
    assert r["ok"]
    assert r["it"] < IT_CRIT_5PCT, "同方差噪声不该显著（it=%.3f）" % r["it"]


def test_cusum_squares_needs_sample():
    assert cusum_squares([0.01] * 10)["ok"] is False
    assert cusum_squares([0.0] * 100)["ok"] is False


# ── PELT ─────────────────────────────────────────────────────────

def test_pelt_no_false_positive_on_smoothed_noise():
    """⭐ 校准回归：纯噪声的 60 日滚动波动上**必须 0 个变点**。

    首版用教科书 BIC 惩罚（倍率 1）→ 报了 22 个"变点"。这条断言锁住校准结果。
    """
    x = _noise(1200, 0.01, 31)
    rv = [v for v in rolling_vol(x, 60) if v is not None]
    assert pelt(rv, min_size=40) == []


def test_pelt_detects_real_regime_shift():
    """校准**不能**把灵敏度一起校没：2.5× 的真实波动跃迁必须被检出。"""
    x = _noise(600, 0.01, 41) + _noise(600, 0.025, 42)
    rv = [v for v in rolling_vol(x, 60) if v is not None]
    cps = pelt(rv, min_size=40)
    assert cps, "真实跃迁没检出 = 惩罚过重"
    assert any(500 <= c <= 820 for c in cps), "变点应靠近真实分割（rv 下标≈541），实测 %s" % cps


def test_pelt_two_levels():
    """明显的两段均值 → 恰好一个变点，且落在交界附近。"""
    x = [0.0] * 100 + [5.0] * 100
    cps = pelt(x, min_size=20)
    assert len(cps) == 1 and 80 <= cps[0] <= 120


def test_pelt_flat_and_short():
    assert pelt([1.0] * 200, min_size=20) == []
    assert pelt([1.0] * 10, min_size=20) == []


def test_pelt_penalty_multiplier_is_documented():
    assert PELT_PENALTY_MULT >= 800, "倍率必须 ≥ 纯噪声校准下限（800）"


def test_rolling_vol_leading_nones():
    out = rolling_vol([0.01] * 100, 60)
    assert out[:59] == [None] * 59
    assert out[59] is not None


# ── β ────────────────────────────────────────────────────────────

def test_beta_known():
    b = [("d%d" % i, 0.01) for i in range(30)]
    f = [("d%d" % i, 0.02) for i in range(30)]
    assert beta(f, b) is None, "基准无波动 → var=0 → None（不猜）"
    b2 = [("d%d" % i, 0.01 if i % 2 else -0.01) for i in range(30)]
    f2 = [("d%d" % i, 0.02 if i % 2 else -0.02) for i in range(30)]
    assert beta(f2, b2) == pytest.approx(2.0)


def test_beta_needs_overlap():
    f = [("d%d" % i, 0.01) for i in range(10)]
    b = [("d%d" % i, 0.01) for i in range(10)]
    assert beta(f, b) is None


# ── 造库 ─────────────────────────────────────────────────────────

def _mk_db(tmp_path, *, bench=True, control=True, fund_sd=(0.005, 0.02), n=600):
    from src.data.database import Database

    db = Database(str(tmp_path / "cp.db"))
    db.upsert_fund_info({"fund_code": "F", "fund_name": "测试基金", "fund_type": "混合型"})
    ds = _dates(n)

    def _insert(code, table, rets, start_nav=1.0):
        nav = start_nav
        for i, d in enumerate(ds):
            if table == "fund_nav":
                db.conn.execute(
                    "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav,"
                    " daily_return) VALUES (?,?,?,?,0)", (code, d, nav, nav))
            else:
                db.conn.execute(
                    "INSERT OR REPLACE INTO index_daily (index_code, trade_date, close)"
                    " VALUES (?,?,?)", (code, d, nav))
            if i < len(rets) - 1:
                nav = nav * (1.0 + rets[i + 1])

    half = n // 2
    fr = _noise(half, fund_sd[0], 101) + _noise(n - half, fund_sd[1], 102)
    _insert("F", "fund_nav", fr)
    if bench:
        _insert("000300", "index_daily", _noise(n, 0.009, 201))
    if control:
        _insert(CONTROL_CODE, "index_daily", _noise(n, 0.010, 301))
    db.conn.commit()
    return db


# ── 应用层 ───────────────────────────────────────────────────────

def test_analyze_detects_vol_regime_shift(tmp_path):
    db = _mk_db(tmp_path)
    try:
        r = analyze(db, "F")
        assert r["ok"] is True
        assert r["cusum_squares"]["significant"] is True
        assert r["vol"]["excess_rel_change"] > VOL_SHIFT_REL
        assert r["alerts"], "超额波动变化远超门槛，必须有告警"
        assert r["flag"].startswith("⚠️")
        assert r["bench_available"] and r["control_available"]
    finally:
        db.close()


def test_analyze_beta_control_is_not_the_benchmark(tmp_path):
    """⭐ 回归：β 的对照必须是另一个宽基（中证500），不能是沪深300 自己。

    用沪深300 当对照 → 它对自己的 β 恒为 1 → `excess_delta` 恒等于 `delta`（首版真 bug）。
    """
    db = _mk_db(tmp_path)
    try:
        r = analyze(db, "F")
        b = r["beta"]
        assert b["control_name"] == "中证500"
        assert "control_delta" in b and "excess_delta" in b
        # 对照源与基准是两条不同的序列，差值不该恰好等于 delta
        assert b["excess_delta"] != pytest.approx(b["delta"], abs=1e-9), \
            "超额 β 变化与自身 β 变化完全相同 → 说明又拿基准当对照了"
    finally:
        db.close()


def test_analyze_without_control_index_degrades_explicitly(tmp_path):
    db = _mk_db(tmp_path, control=False)
    try:
        r = analyze(db, "F")
        assert r["control_available"] is False
        assert "excess_delta" not in r["beta"]
        assert any("中证500" in n for n in r["notes"])
    finally:
        db.close()


def test_analyze_without_benchmark_has_no_excess(tmp_path):
    """⭐ 没有对照就**不许**给超额 —— 否则会把市场 regime 读成风格漂移。"""
    db = _mk_db(tmp_path, bench=False, control=False)
    try:
        r = analyze(db, "F")
        assert r["bench_available"] is False
        assert "excess_rel_change" not in r["vol"]
        assert any("基准序列缺失" in n or "没有市场对照" in n for n in r["notes"])
    finally:
        db.close()


def test_analyze_short_history_refuses(tmp_path):
    db = _mk_db(tmp_path, n=200)
    try:
        r = analyze(db, "F")
        assert r["ok"] is False
        assert r["flag"] == "历史不足"
        assert r["alerts"] == []
        assert "变点判断" in "".join(r["notes"])
    finally:
        db.close()


def test_thresholds_are_preregistered():
    """判据写死在模块常量里（不随数据挪）。"""
    assert VOL_SHIFT_REL == 0.30
    assert BETA_SHIFT_ABS == 0.50
    assert IT_CRIT_5PCT == 1.358


# ── 取数：估值净值口径 ───────────────────────────────────────────

def test_fund_returns_uses_acc_nav_not_unit_nav(tmp_path):
    """分红日 `unit_nav` 跳水、`acc_nav` 不跳 —— 用错列会凭空造出一次大跌。"""
    from src.data.database import Database

    db = Database(str(tmp_path / "nav.db"))
    rows = [("2026-01-01", 1.00, 1.00), ("2026-01-02", 1.10, 1.10),
            ("2026-01-03", 0.50, 1.10), ("2026-01-04", 0.55, 1.21)]   # 01-03 分红除息
    for d, u, a in rows:
        db.conn.execute(
            "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
            " VALUES ('X',?,?,?,0)", (d, u, a))
    db.conn.commit()
    try:
        rets = fund_returns(db, "X")
        vals = [round(v, 6) for _, v in rets]
        assert vals[:2] == [pytest.approx(0.10), pytest.approx(0.0)], \
            "分红日不该出现 −54% 的假下跌：%s" % vals
    finally:
        db.close()


# ── 扫描：去重 ───────────────────────────────────────────────────

def test_scan_dedupes_holding_lots(tmp_path):
    """`holdings` 是**批次**表：同一只基金多个批次只能报一次。"""
    db = _mk_db(tmp_path)
    try:
        for d in ("2026-01-05", "2026-02-05", "2026-03-05"):
            db.conn.execute(
                "INSERT INTO holdings (fund_code, fund_name, buy_date, buy_amount, status)"
                " VALUES ('F','测试基金',?,100.0,'holding')", (d,))
        db.conn.commit()
        out = scan(db)
        assert out["n"] == 1, "3 个批次 = 1 只基金，不许重复报告"
        assert out["results"][0]["code"] == "F"
    finally:
        db.close()
