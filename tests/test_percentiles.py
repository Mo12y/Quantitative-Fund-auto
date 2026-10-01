# -*- coding: utf-8 -*-
"""`src/analysis/percentiles.py` 的守卫。

各断言钉住的事：
  ① 与采集侧的**权威口径**逐位一致（`collector` 是 `(x < current).sum()/len(dropna)`）；
  ② 无 NaN 时 = 老代码 `(w<w[-1]).sum()/len(w)`（两者此时等价，证明没改坏 vol_predictor）；
  ③ NaN **不进分母**（这是 2026-10-01 对老代码的一处修正：老代码把 NaN 也算进分母）；
  ④ **无未来函数**：改后半段，前半段分位逐位不变 —— 回测的生命线。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.percentiles import MIN_PERIODS, expanding_percentile  # noqa: E402


def _collector_reference(values):
    """采集侧权威口径（`save_index_val_to_db`）：严格小于 / 非 NaN 个数，全历史一次性算。"""
    s = pd.Series(values, dtype="float64").dropna()
    cur = s.iloc[-1]
    return (s < cur).sum() / len(s) * 100.0


def test_matches_collector_on_nan_free():
    """无 NaN 时与采集侧口径逐位一致（末值，也是全样本 = 扩展窗口的末值）。"""
    rng = np.random.default_rng(7)
    s = pd.Series(rng.normal(15, 4, size=800).cumsum() / 10 + 15)
    got = expanding_percentile(s).iloc[-1]
    assert got == pytest.approx(_collector_reference(s), abs=1e-9)


def test_matches_legacy_on_nan_free():
    """无 NaN 时，老代码 `(w<w[-1]).sum()/len(w)` 与本实现**逐位相等**。

    两者此时等价（分母都是"全部非 NaN 个数"）—— 这条保证 `vol_predictor` 重构没改坏输出。
    """
    rng = np.random.default_rng(7)
    s = pd.Series(rng.normal(15, 4, size=800).cumsum() / 10 + 15)
    got = expanding_percentile(s)
    want = (s.expanding(min_periods=MIN_PERIODS)
            .apply(lambda w: float((w < w[-1]).sum()) / len(w), raw=True) * 100.0)
    pd.testing.assert_series_equal(got, want, check_names=False)


def test_drops_nan_from_denominator():
    """NaN **不进分母** —— 这是对老代码的一处修正，用真实 000300 的情形复现。"""
    rng = np.random.default_rng(11)
    s = pd.Series(rng.normal(15, 4, size=600).cumsum() / 10 + 15)
    s.iloc[5:20] = np.nan                              # 早期空值，模拟 000300 的 23 个空 pe
    got = expanding_percentile(s).iloc[-1]

    n_nan_free = s.notna().sum()                       # 修正后的分母 = 非 NaN 个数
    strictly_less = (s.dropna() < s.dropna().iloc[-1]).sum()
    want = strictly_less / n_nan_free * 100.0
    assert got == pytest.approx(want, abs=1e-9)

    # 老代码（NaN 算进分母）会得到一个**更低**的值 —— 证明本实现确实改了
    legacy = (s < s.iloc[-1]).sum() / len(s) * 100.0
    assert got > legacy


def test_warmup_is_nan_not_fifty():
    """样本不足的前段必须是 NaN —— 不许拿 50 伪装成"中性读数"。"""
    s = pd.Series(np.arange(300, dtype=float))
    got = expanding_percentile(s)
    assert got.iloc[: MIN_PERIODS - 1].isna().all()
    assert not np.isnan(got.iloc[MIN_PERIODS - 1])


def test_no_lookahead():
    """**无未来函数**：改掉后半段，前半段的分位必须逐位不变。"""
    rng = np.random.default_rng(3)
    base = pd.Series(rng.normal(10, 2, size=1000).cumsum() / 5 + 20)
    tampered = base.copy()
    tampered.iloc[600:] = tampered.iloc[600:] * 3 + 50      # 只动后半段

    a = expanding_percentile(base).iloc[:600]
    b = expanding_percentile(tampered).iloc[:600]
    pd.testing.assert_series_equal(a, b, check_names=False)


def test_range_is_0_100():
    """返回 0–100（与 `index_valuation.*_percentile` 的百分数口径一致）。"""
    s = pd.Series(np.linspace(1, 100, 500))
    got = expanding_percentile(s).dropna()
    assert got.min() >= 0 and got.max() <= 100
