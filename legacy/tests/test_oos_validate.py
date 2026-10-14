"""批次 4.3 滚动样本外验证测试。

四条哨兵（全部用**合成数据**，不依赖真实库、不联网）：
  1. **无前视**：把验证期的净值篡改成极端值，选择期的选人结果**必须一字不变**；
  2. **能检出真实能力**：构造"打分高且未来继续涨"的基金 → S1 必须跑赢对照；
  3. **不误报**：所有基金走势完全相同（打分法无从区分）→ S1 与对照**必须相等**；
  4. **样本不足如实说**：窗口不足时不编结论。
另含 `_compile_proven_winners` 的**分桶交错**回归（修掉"榜单债基一边倒"）。
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.oos_validate import evaluate_windows, month_ends  # noqa: E402

START, END = "2019-01-01", "2023-12-29"


def _series(monthly: float, start=START, end=END):
    """合成净值序列：工作日点，按月复利 `monthly`。"""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    out, v, d = [], 1.0, d0
    while d <= d1:
        if d.weekday() < 5:
            v *= (1 + monthly / 21.0)
            out.append((d.isoformat(), round(v, 6)))
        d += timedelta(days=1)
    return out


def _mk(monthly_by_code: dict) -> tuple:
    nav = {c: _series(r) for c, r in monthly_by_code.items()}
    info = {c: ("债券型-长债" if c.startswith("B") else "混合型-偏股") for c in monthly_by_code}
    return nav, info


ENDS = month_ends("2020-01-01", END)


# ── 1. 无前视 ─────────────────────────────────────────────────────

def test_no_lookahead():
    """篡改**验证期**净值 → 选择期的选人结果不得改变（否则就是前视）。"""
    nav, info = _mk({**{f"A{i}": 0.015 for i in range(6)},
                     **{f"B{i}": 0.002 for i in range(6)}})
    base = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6,
                            top_k=5, min_picks=2)

    # 只把 2022-06-30 之后的点乘 5（验证期全变），选择期原样
    tampered = {}
    for c, s in nav.items():
        tampered[c] = [(d, v * 5 if d > "2022-06-30" else v) for d, v in s]
    after = evaluate_windows(tampered, ENDS, info, window_months=12, step_months=6,
                             top_k=5, min_picks=2)

    assert base["windows"], "应当至少产出一个窗口"
    n = min(len(base["windows"]), len(after["windows"]))
    assert n >= 2, "本用例需要多个窗口才有意义"
    for w1, w2 in zip(base["windows"][:n], after["windows"][:n]):
        assert w1["s1_codes"] == w2["s1_codes"], \
            "选择期选人受验证期数据影响 → 前视泄漏"


# ── 2. 能检出真实能力 ─────────────────────────────────────────────

def test_detects_real_ability():
    """打分高的基金（高动量+低回撤）未来继续涨 → S1 必须优于全池中位与随机。"""
    nav, info = _mk({**{f"A{i}": 0.020 for i in range(6)},     # 稳定上涨 → 打分高
                     **{f"B{i}": 0.001 for i in range(6)},     # 低波动低收益
                     **{f"A_low{i}": -0.010 for i in range(6)}})  # 下跌 → 打分低
    out = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6,
                           top_k=5, min_picks=2)
    s = out["summary"]
    assert s["n_windows"] >= 3, s
    assert s["s1_minus_c1"]["median"] > 0, "有真实能力时应跑赢全池中位：%s" % s["s1_minus_c1"]
    assert s["win_rate_vs_c1"] >= 70, "逐窗胜率应显著高于 50%：%s" % s["win_rate_vs_c1"]
    assert "迹象" in out["verdict"], out["verdict"]


# ── 3. 不误报（无能力 → 必须相等） ────────────────────────────────

def test_no_false_positive_when_indistinguishable():
    """所有基金走势完全相同 → 打分法无从区分 → S1 与对照收益必须相等（差 0）。"""
    nav, info = _mk({f"A{i}": 0.010 for i in range(10)})
    out = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6,
                           top_k=5, min_picks=2)
    s = out["summary"]
    assert s["n_windows"] >= 3, s
    assert abs(s["s1_minus_c1"]["median"]) < 1e-9, \
        "无法区分时不得凭空造出超额：%s" % s["s1_minus_c1"]
    assert "看不出" in out["verdict"], out["verdict"]


# ── 4. 样本不足 ───────────────────────────────────────────────────

def test_insufficient_windows_says_so():
    nav, info = _mk({f"A{i}": 0.01 for i in range(10)})
    ends = month_ends("2023-06-01", END)          # 只有 7 个月末
    out = evaluate_windows(nav, ends, info, window_months=12, step_months=6,
                           top_k=5, min_picks=2)
    assert out["summary"]["n_windows"] < 3
    assert "无法判定" in out["verdict"], out["verdict"]


def test_empty_input_is_safe():
    out = evaluate_windows({}, ENDS, {}, window_months=12, step_months=6)
    assert out["windows"] == [] and out["summary"]["n_windows"] == 0


# ── 5. 确定性 ─────────────────────────────────────────────────────

def test_reproducible():
    nav, info = _mk({**{f"A{i}": 0.015 for i in range(6)}, **{f"B{i}": 0.002 for i in range(6)}})
    a = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6, top_k=5, min_picks=2)
    b = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6, top_k=5, min_picks=2)
    assert a["summary"] == b["summary"], "同输入必须同输出（否则无法复核）"


# ── 6. 分桶交错（修掉「榜单债基一边倒」） ──────────────────────────

def test_compile_proven_winners_interleaves_buckets(tmp_path):
    """equity 与 bond 必须**交错**出现在榜单里，否则全局按 composite 排又会债基一边倒。"""
    from src.analysis.historical_recommender import HistoricalRecommender
    from src.data.database import Database

    db = Database(str(tmp_path / "pw.db"))
    try:
        for c, t in (("E1", "混合型-偏股"), ("E2", "混合型-偏股"),
                     ("B1", "债券型-长债"), ("B2", "债券型-长债")):
            db.upsert_fund_info({"fund_code": c, "fund_name": c, "fund_type": t})
        hr = HistoricalRecommender(db)

        def _st(score):
            return {"times_picked": 12, "total_score": score * 12,
                    "returns_1m": [0.5] * 12, "returns_3m": [1.0] * 12,
                    "returns_6m": [2.0] * 12, "first_pick": "2024-01-31",
                    "last_pick": "2024-12-31"}

        # 债基 composite 更高（模拟"低波动 → 高频选中 → 高分"）
        stats = {"E1": _st(80), "E2": _st(75), "B1": _st(95), "B2": _st(90)}
        got = hr._compile_proven_winners(stats, [f"2024-{m:02d}-31" for m in range(1, 13)])
        buckets = [p["bucket"] for p in got]
        assert buckets[:2] == ["bond", "equity"], \
            "分桶交错：第一名可以是最高的债基，第二名必须是另一个桶：%s" % buckets
        assert buckets.count("bond") == 2 and buckets.count("equity") == 2
    finally:
        db.close()


# ── 7. 统计工具（2026-09-27 新增）────────────────────────────────

def test_binom_p_is_one_tailed():
    from src.analysis.oos_validate import _binom_p
    assert _binom_p(0, 0) == 1.0
    assert _binom_p(10, 10) == pytest.approx(1 / 1024, abs=1e-9)     # 全胜
    assert _binom_p(5, 10) == pytest.approx(0.623, abs=0.001)        # 掷硬币附近
    assert _binom_p(99, 100) < 1e-25                                 # 极端显著


def test_bh_adjust_is_monotone_and_more_conservative():
    from src.analysis.oos_validate import _bh_adjust
    raw = [0.01, 0.04, 0.03, 0.20]
    adj = _bh_adjust(raw)
    assert adj[0] == pytest.approx(0.04)
    assert adj[3] == pytest.approx(0.20)
    for a, p in zip(adj, raw):
        assert a >= p - 1e-12, "BH 校正只会让 p 变大（更保守），不会更显著"


def test_ols_alpha_recovers_known_line():
    from src.analysis.oos_validate import _ols_alpha
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0 + 3.0 * xi for xi in x]          # α=2, β=3，完美拟合
    r = _ols_alpha(y, x)
    assert r["alpha"] == pytest.approx(2.0)
    assert r["beta"] == pytest.approx(3.0)
    assert r["r2"] == pytest.approx(1.0)
    assert r["n"] == 5


def test_ols_alpha_needs_three_points():
    from src.analysis.oos_validate import _ols_alpha
    assert _ols_alpha([1.0], [1.0]) is None
    assert _ols_alpha([1.0, 2.0], [1.0, 2.0]) is None


def test_c1_is_equal_weight_not_median():
    """C1 必须是**全池等权平均**。

    收益分布右偏（少数基金贡献大部分收益）时，中位数会**系统性低估**"随机持有"的
    期望收益，从而让策略显得没那么差 —— 这正是 2026-09-27 修掉的口径问题。
    """
    nav, info = _mk({**{f"B{i}": 0.001 for i in range(8)},      # 8 只几乎不动
                     **{f"A{i}": 0.050 for i in range(2)}})     # 2 只大涨（右尾）
    out = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6,
                           top_k=5, min_picks=2)
    w = out["windows"][0]
    assert w["c1_pool"] is not None and w["c1_pool_ref"] is not None
    assert w["c1_pool"] > w["c1_pool_ref"], \
        "右偏分布下等权均值必须 > 中位数：avg=%s med=%s" % (w["c1_pool"], w["c1_pool_ref"])


def test_random_control_is_averaged_over_repeats():
    """随机对照必须**重复多次**（单次抽样方差极大，实测中位 +0.15% vs 均值 +1.85%）。"""
    nav, info = _mk({**{f"A{i}": 0.02 for i in range(5)}, **{f"B{i}": 0.001 for i in range(5)}})
    out = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6,
                           top_k=5, min_picks=2, c2_repeats=20)
    assert out["windows"][0]["c2_n"] == 20, "应记录实际重复次数"


def test_top_pct_controls_holding_count():
    """给了 top_pct（十分位）时，每期持有只数由**池子比例**决定，不再固定。"""
    nav, info = _mk({**{f"A{i}": 0.015 for i in range(15)}, **{f"B{i}": 0.001 for i in range(15)}})
    out = evaluate_windows(nav, ENDS, info, window_months=12, step_months=6,
                           top_k=None, top_pct=0.1, min_picks=2)
    k = out["windows"][0]["k"]
    assert k == 3, "30 只候选 × 10% = 3 只（top-decile）"
