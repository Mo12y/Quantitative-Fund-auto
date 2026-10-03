"""经验贝叶斯收缩测试（M1）。

最关键的一条：**`lo_se(0, 3) == 0.577`** —— 它必须复现审计里那个「3 年夏普 SE ≈ 0.58」，
否则说明公式抄错了，整条链（收缩权重 → 排序）都跟着错。
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.shrinkage import (  # noqa: E402
    diagnosis, eb_shrink, estimate_tau2, lo_se, shrink_array,
)


# ── Lo (2002) 标准误 ─────────────────────────────────────────────

def test_lo_se_reproduces_audit_number():
    """审计结论 A：3 年窗口 SE ≈ 0.58。这是整条链路的地基，必须精确对上。"""
    assert lo_se(0.0, 3.0) == pytest.approx(math.sqrt(1 / 3), abs=1e-9)
    assert lo_se(0.0, 3.0) == pytest.approx(0.577, abs=5e-4)


def test_lo_se_grows_with_sharpe():
    """SR 越极端，SE 越大（Lo 的 `1 + SR²/2` 项）→ 极端夏普更不可信。"""
    assert lo_se(2.0, 3.0) > lo_se(1.0, 3.0) > lo_se(0.0, 3.0)


def test_lo_se_shrinks_with_more_years():
    """样本越长越可信：SE ∝ 1/√T（10 年应约为 3 年的 √(3/10) 倍）。"""
    assert lo_se(0.5, 10.0) == pytest.approx(lo_se(0.5, 3.0) * math.sqrt(3 / 10), rel=1e-9)


def test_lo_se_guard_returns_inf():
    """无信息的情形要返回 inf（而不是 0）—— 0 会让收缩权重变成 1（= 假装很可信）。"""
    assert lo_se(1.0, 0) == float("inf")
    assert lo_se(1.0, -1) == float("inf")
    assert lo_se(None, 3) == float("inf")


# ── τ² 矩估计 ────────────────────────────────────────────────────

def test_tau2_is_zero_when_all_noise():
    """全是噪音：观测方差 ≈ 噪声方差 → τ² 应被夹到 0（而不是负数）。"""
    # 取一组"刚好等于各自 SE 量级"的散点：用 3 年窗口，SE≈0.577
    # 造 8 个值，样本方差与 mean(SE²) 同量级 → τ² 接近 0 或很小
    vals = [-0.58, -0.41, -0.25, -0.08, 0.08, 0.25, 0.41, 0.58]
    t2 = estimate_tau2(vals, 3.0)
    assert t2 >= 0.0
    assert t2 < 0.05, "这组几乎全是噪音，τ² 应接近 0：%s" % t2


def test_tau2_positive_when_real_spread():
    """真有组间差异：观测方差远大于噪声 → τ² 明显为正。"""
    vals = [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
    t2 = estimate_tau2(vals, 3.0)
    assert t2 > 3.0, "观测方差 ≈ 5.3，噪声 ≈ 0.4 → τ² 应显著为正：%s" % t2


def test_tau2_needs_two_points():
    assert estimate_tau2([1.0], 3.0) == 0.0
    assert estimate_tau2([], 3.0) == 0.0


# ── 收缩本身 ─────────────────────────────────────────────────────

def test_shrink_to_mean_when_no_signal():
    """τ²=0（无信号）→ 权重 0 → **完全**收缩到组均值（排序信息归零，如实反映）。"""
    s, w = eb_shrink(2.0, 0.5, 0.0, 3.0)
    assert w == 0.0
    assert s == pytest.approx(0.5)


def test_shrink_barely_when_strong_signal():
    """τ² 远大于 SE² → 权重接近 1 → 几乎不动。"""
    s, w = eb_shrink(2.0, 0.5, 100.0, 3.0)
    assert w > 0.99
    assert s == pytest.approx(2.0, abs=0.05)


def test_extreme_values_are_shrunk_harder():
    """同一组里，**越极端的夏普收得越狠**（Lo 的 SE 随 |SR| 上升）。

    这是该方法的正确性质：夏普 3.0 的估计比夏普 0.5 的更不可信。
    """
    _, w_mild = eb_shrink(0.5, 0.0, 0.5, 3.0)
    _, w_extreme = eb_shrink(3.0, 0.0, 0.5, 3.0)
    assert w_extreme < w_mild, "极端值应获得更低权重：%s vs %s" % (w_extreme, w_mild)


def test_shrink_handles_bad_input():
    """脏输入 → 退回组均值且权重 0，不抛异常、不编造。"""
    assert eb_shrink(None, 0.4, 1.0, 3.0) == (0.4, 0.0)
    assert eb_shrink(float("nan"), 0.4, 1.0, 3.0) == (0.4, 0.0)


# ── 整体收缩与诊断 ───────────────────────────────────────────────

def test_shrink_array_shape_and_preserves_order_of_magnitude():
    vals = [0.1, 0.5, 0.9, 1.3, 1.7]
    d = shrink_array(vals, 3.0)
    assert d["n"] == 5 and len(d["shrunk"]) == 5 and len(d["weights"]) == 5
    assert d["mu"] == pytest.approx(sum(vals) / 5)
    # 收缩后极差必然**小于**原始极差（这就是收缩的定义）
    assert max(d["shrunk"]) - min(d["shrunk"]) < max(vals) - min(vals)


def test_mean_weight_drops_for_short_windows():
    """同样的横截面差异，样本越短 → 信度越低（T=1 明显低于 T=10）。"""
    vals = [-1.0, -0.5, 0.0, 0.5, 1.0]
    assert shrink_array(vals, 1.0)["mean_weight"] < shrink_array(vals, 10.0)["mean_weight"]


def test_diagnosis_is_honest_when_no_signal():
    """无信号时必须直说『全部是噪音』，而不是给个好看的百分数。"""
    txt = diagnosis([], 3.0)
    assert "无样本" in txt
    txt2 = diagnosis([0.5, 0.5, 0.5, 0.5], 3.0)
    assert "0%" in txt2 and "噪音" in txt2


def test_diagnosis_reports_weight_when_signal_exists():
    txt = diagnosis([-2.0, -1.0, 0.0, 1.0, 2.0], 3.0)
    assert "平均信度" in txt and "收缩后" in txt
