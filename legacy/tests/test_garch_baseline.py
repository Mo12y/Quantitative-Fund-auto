"""GARCH/EGARCH 基线测试（批次 4）。

要点：**arch 不在时也必须能测**（CI 上通常没装）—— 所以主要测**降级路径**，
真正跑模型的部分用 `skipif` 保护。核心断言是"不可用时必须给出 reason"，
因为静默跳过正是本项目要防的事（铁律 5）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.analysis.garch_baseline as gb  # noqa: E402


def test_available_returns_reason_not_exception():
    ok, why = gb.available()
    assert isinstance(ok, bool) and isinstance(why, str) and why
    if not ok:
        assert "arch" in why and "install" in why.lower() or "安装" in why


def test_fit_degrades_when_arch_missing(monkeypatch):
    """arch 不可用 → `ok=False` + 原因，**绝不静默返回一个数**。"""
    monkeypatch.setattr(gb, "available", lambda: (False, "stub：arch 未安装"))
    r = gb.fit([0.01] * 300)
    assert r["ok"] is False and "stub" in r["reason"]


def test_fit_rejects_short_samples():
    """样本不足要**明说**不足，而不是硬拟合出一个数。"""
    r = gb.fit([0.01] * 50)
    assert r["ok"] is False
    assert "样本不足" in r["reason"], r


def test_fit_filters_dirty_returns():
    """NaN / None 先过滤；过滤后不足 100 条 → 仍然报"样本不足"。"""
    dirty = [0.01, float("nan"), None] * 20
    r = gb.fit(dirty)
    assert r["ok"] is False and "样本不足" in r["reason"]


def test_compare_shape_is_stable_even_when_unavailable(monkeypatch):
    """`compare()` 无论如何都要返回**稳定结构**（调用方不必分支处理）。"""
    monkeypatch.setattr(gb, "available", lambda: (False, "stub"))
    out = gb.compare([0.01] * 300)
    assert set(out) >= {"GARCH(1,1)", "EGARCH(1,1)", "best_by_aic", "note"}
    assert out["best_by_aic"] is None
    assert out["note"]


@pytest.mark.skipif(not gb.available()[0], reason="arch 未安装（沙箱常见）")
def test_garch_and_egarch_fit_on_synthetic_data():
    """有 arch 时：两个模型都能拟合并给出**合理量级**的年化 vol（5%~200%）。"""
    import numpy as np

    rng = np.random.default_rng(42)
    r = rng.normal(0, 0.01, 600)          # 日波动 1% → 年化约 15.6%
    out = gb.compare(r, horizon=21)
    for k in ("GARCH(1,1)", "EGARCH(1,1)"):
        v = out[k]
        assert v["ok"] is True, (k, v.get("reason"))
        assert 5.0 < v["forecast_vol_pct"] < 200.0, (k, v["forecast_vol_pct"])
        assert v["n_obs"] == 600
    assert out["best_by_aic"] in ("GARCH(1,1)", "EGARCH(1,1)")


@pytest.mark.skipif(not gb.available()[0], reason="arch 未安装")
def test_forecast_horizon_keeps_same_order_of_magnitude():
    """回归哨兵：多步预测必须取**累计**方差（曾经只取第 1 步）。

    判据用**量级**而非大小：horizon=21 折算出的"平均日波动→年化"应与 horizon=1 同量级
    （GARCH 均值回复，长期方差稳定）。若误把第 1 步方差当成 21 天累计，
    会低估约 √21≈4.6 倍 —— 比值检查能抓住这种量级错误。
    """
    import numpy as np

    r = np.random.default_rng(7).normal(0, 0.012, 600)
    v1 = gb.fit(r, horizon=1)
    v21 = gb.fit(r, horizon=21)
    assert v1["ok"] and v21["ok"], (v1.get("reason"), v21.get("reason"))
    ratio = v21["forecast_vol_pct"] / v1["forecast_vol_pct"]
    assert 0.5 < ratio < 2.0, \
        "两个 horizon 应同量级（累计口径），实际比值 %.2f（v1=%s v21=%s）" % (
            ratio, v1["forecast_vol_pct"], v21["forecast_vol_pct"])
