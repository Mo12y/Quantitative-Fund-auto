"""有效 N 与 BH 校正（M3）测试。

**方向是本模块最容易搞错的地方**，所以专门用测试钉死：
`m_eff` 更小 → 校正后 p **更小**（更易显著）——因为相关的检验不该被重复惩罚。

（审计原文写的是"名义 N 让门限偏松"，方向说反了；已在模块 docstring 更正。
 本测试只认数学：BH 的 `p_adj = p × M / rank`，M 越大 → p_adj 越大 → 越难显著。）
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.multiple_testing import (  # noqa: E402
    bh_adjust, cheverud_nyholt, corr_from_series, describe,
)


# ── Cheverud–Nyholt ──────────────────────────────────────────────

def test_independent_tests_keep_name_count():
    """完全独立（单位矩阵）→ M_eff == M（退化为名义 N）。"""
    assert cheverud_nyholt(np.eye(4)) == pytest.approx(4.0)
    assert cheverud_nyholt(np.eye(10)) == pytest.approx(10.0)


def test_perfectly_correlated_collapses_to_one():
    """完全相关（全 1 矩阵）→ M_eff == 1（其实只有 1 个独立检验）。"""
    assert cheverud_nyholt(np.ones((4, 4))) == pytest.approx(1.0)
    assert cheverud_nyholt(np.ones((12, 12))) == pytest.approx(1.0)


def test_partial_correlation_is_between():
    """部分相关 → 1 < M_eff < M。"""
    C = np.full((5, 5), 0.5)
    np.fill_diagonal(C, 1.0)
    me = cheverud_nyholt(C)
    assert 1.0 < me < 5.0


def test_never_exceeds_nominal():
    """M_eff 不可能大于名义个数（否则等于放宽到不存在的"更多检验"）。"""
    rng = np.random.default_rng(0)
    for _ in range(5):
        X = rng.normal(0, 1, (6, 200))
        C = np.corrcoef(X)
        assert cheverud_nyholt(C) <= 6.0 + 1e-9


def test_bad_input_is_explicit():
    assert np.isnan(cheverud_nyholt([]))
    assert np.isnan(cheverud_nyholt(np.zeros((2, 3))))     # 非方阵
    assert cheverud_nyholt(np.eye(1)) == pytest.approx(1.0)


# ── BH 的方向（最容易写错的一条） ────────────────────────────────

def test_smaller_m_eff_gives_smaller_adjusted_p():
    """⭐ 方向：`m_eff` 更小 → 校正后 p **更小**（门限更松）。

    这是本模块与审计原话（"名义 N 让门限偏松"）**相反**的地方，
    所以必须有测试守着，否则以后有人"顺手改回去"没人发现。
    """
    p = [0.01, 0.02, 0.03, 0.04]
    nominal = bh_adjust(p)
    effective = bh_adjust(p, m_eff=2)
    assert all(e <= n + 1e-12 for e, n in zip(effective, nominal)), \
        "有效 N 更小必须给出更小的校正 p：%s vs %s" % (effective, nominal)
    assert effective[0] == pytest.approx(0.02, abs=1e-9)
    assert nominal[0] == pytest.approx(0.04, abs=1e-9)


def test_none_m_eff_equals_nominal():
    """不传 m_eff → 与旧行为（名义 N）**完全一致**（向后兼容）。"""
    p = [0.005, 0.02, 0.4, 0.6]
    assert bh_adjust(p) == bh_adjust(p, m_eff=len(p))


def test_adjusted_is_monotone_and_bounded():
    p = [0.001, 0.01, 0.02, 0.5]
    adj = bh_adjust(p, m_eff=2.5)
    assert all(0.0 <= a <= 1.0 for a in adj)
    # 与输入同序：最小的 p 仍最小
    assert adj[0] <= adj[-1]
    # ⚠️ 注意：`m_eff < 名义 N` 时，校正后 p **可以小于**原始 p —— 这正是修正的意义。
    # 所以这里比的是"相对**名义 N** 的校正结果"，不是相对原始 p。
    assert all(a <= n + 1e-12 for a, n in zip(adj, bh_adjust(p, m_eff=4)))


def test_m_eff_clamped_to_nominal():
    """传入大于名义个数的 m_eff → 夹到名义值（不许放宽到不存在的检验数）。"""
    p = [0.01, 0.02]
    assert bh_adjust(p, m_eff=99) == bh_adjust(p, m_eff=2)


def test_empty():
    assert bh_adjust([]) == []


# ── 相关矩阵与诊断串 ─────────────────────────────────────────────

def test_corr_from_series_basic():
    a = [1.0, 2.0, 3.0, 4.0]
    C = corr_from_series([a, a])                      # 完全相关
    assert C is not None and C.shape == (2, 2)
    assert cheverud_nyholt(C) == pytest.approx(1.0)


def test_corr_from_series_rejects_short_or_nan():
    assert corr_from_series([[1.0, 2.0]]) is None                 # 只有 1 条
    assert corr_from_series([[1.0], [2.0]]) is None               # 长度不足
    # 含 None 的序列：保留 NaN 后按**整行**剔除（不能按元素过滤 —— 那会错位）
    C = corr_from_series([[1.0, 2.0, 3.0, 4.0],
                          [1.0, None, 3.0, 4.0],                  # ← 这条应被整行剔除
                          [2.0, 3.0, 4.0, 5.0]])
    assert C is not None and C.shape == (2, 2), "含 NaN 的序列应被整行剔除，剩 2 条"


def test_describe_says_direction_correctly():
    """诊断串必须说"更松"（与审计原话相反）—— 防回归。"""
    txt = describe(4, 1.335)
    assert "更松" in txt and "有效 N" in txt
    assert "1.3" in txt
    bad = describe(4, float("nan"))
    assert "退回名义" in bad
