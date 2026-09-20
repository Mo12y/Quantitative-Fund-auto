"""L1 评估台的两条硬纪律测试：**无未来函数** + **purged/embargo**。

依据 `docs/基金推荐系统_ML可行性研究.md` §5 第 3 条：
- 断言无未来函数（用 t 之后的数据构造的特征必须为 NaN / 不参与）；
- 断言训练集不含标签重叠期。

这两条一旦破了，IC 会凭空变好 —— 而"凭空变好"比"结论为负"危险得多。
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis import recsys_ml
from src.analysis.recsys_dataset import FEATURES, MIN_HISTORY, _features_at, _forward_ret


def _series(n=600, seed=7):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.01, n)
    px = 1.0 * np.cumprod(1 + r)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(px, index=idx)


class TestNoLookAhead:
    def test_features_invariant_to_future_data(self):
        """特征只用 <= t 的数据：砍掉 t 之后的行，特征必须一字不变。"""
        s = _series()
        t = s.index[MIN_HISTORY + 120]
        full = _features_at(s, t, 1.5, 10.0, 3.0)
        trunc = _features_at(s.loc[:t], t, 1.5, 10.0, 3.0)     # 完全没有未来数据
        assert full is not None and trunc is not None
        for k in full:
            assert full[k] == pytest.approx(trunc[k], rel=1e-12), f"{k} 用到了未来数据"

    def test_features_change_when_history_changes(self):
        """反向断言：改掉 t 之前的历史，特征必须变（否则说明特征根本没读历史）。"""
        s = _series()
        t = s.index[MIN_HISTORY + 120]
        a = _features_at(s, t, 1.5, 10.0, 3.0)
        s2 = s.copy()
        s2.iloc[:MIN_HISTORY] *= 0.5
        b = _features_at(s2, t, 1.5, 10.0, 3.0)
        assert a["mom_12m"] != pytest.approx(b["mom_12m"])

    def test_insufficient_history_returns_none(self):
        s = _series(n=MIN_HISTORY - 10)
        assert _features_at(s, s.index[-1], 1.0, 1.0, 1.0) is None

    def test_forward_return_needs_future_only(self):
        """标签只用未来：t 之后不足 horizon 个交易日时必须返回 None（不拿现有数据凑）。"""
        s = _series()
        assert _forward_ret(s, s.index[-1]) is None            # 末尾没有未来
        assert _forward_ret(s, s.index[-10]) is None           # 只剩 10 个交易日 < 21
        y = _forward_ret(s, s.index[-22])
        assert y is not None and np.isfinite(y)


def _synthetic_panel(months=6, n_per=300):
    """造一个足够大的合成面板（每月 n_per 只），保证没有月份被样本量门槛滤掉 ——
    否则 spy 记录的训练窗口与 OOS 月份列表会错位，断言就没意义了。"""
    rows = []
    dates = pd.date_range("2024-01-31", periods=months, freq="ME").strftime("%Y-%m-%d")
    rng = np.random.default_rng(3)
    for d in dates:
        for i in range(n_per):
            rows.append({
                "date": d, "fund_code": f"{i:06d}", "bucket": "mixed",
                "y_forward": float(rng.normal(0, 0.02)),
                "y_pct": float(rng.random()),
                **{f: float(rng.normal()) for f in FEATURES},
            })
    return pd.DataFrame(rows)


class TestPurgeEmbargo:
    def test_training_never_touches_label_overlap(self, monkeypatch):
        """预测 t 月时，训练集最大日期必须 <= t-2 月。

        标签窗口 ≈ [t, t+1 月]（21 交易日）：若训练含 t-1 月的行，其标签会读到
        **测试月 t 的净值** —— 这就是泄露，必须 purge 掉。
        """
        seen = {"max": [], "n": []}
        real_fit = recsys_ml.fit_ridge

        def spy(train, lam=10.0):
            seen["max"].append(str(train["date"].max()))
            seen["n"].append(len(train))
            return real_fit(train, lam=lam)

        monkeypatch.setattr(recsys_ml, "fit_ridge", spy)
        panel = _synthetic_panel(months=8, n_per=300)
        recsys_ml.walk_forward(panel, oos_start="2024-04-30", embargo_months=2, k=5)
        assert seen["max"], "没有产生任何训练窗口"
        months = sorted(panel["date"].unique())
        oos = [m for m in months if m >= "2024-04-30"]
        assert len(seen["max"]) == len(oos), "有 OOS 月份被样本量门槛滤掉了，断言会错位"
        for t, mx in zip(oos, seen["max"]):
            idx = months.index(t)
            assert mx <= months[idx - 2], f"预测 {t} 时训练集含到 {mx}（标签重叠期泄入）"

    def test_embargo_is_monotone(self, monkeypatch):
        """embargo 越大，训练集越早截止（证明参数确实生效）。"""
        seen = {}
        real_fit = recsys_ml.fit_ridge

        def spy(train, lam=10.0):
            seen.setdefault("max", []).append(str(train["date"].max()))
            return real_fit(train, lam=lam)

        monkeypatch.setattr(recsys_ml, "fit_ridge", spy)
        recsys_ml.walk_forward(_synthetic_panel(months=8, n_per=300),
                               oos_start="2024-05-31", embargo_months=2, k=5)
        assert seen["max"] and seen["max"][0] == "2024-03-31"       # 5月预测 → 训练到 3月
