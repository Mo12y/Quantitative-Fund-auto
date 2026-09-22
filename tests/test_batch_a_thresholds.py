"""批次 A 测试：阈值的「绝对 vs 相对」落实（A1/A3/A6）。

要点
----
- `momentum_warning` 由**常数 40** 改为**组内 P90** → 同一只基金在不同组会得到不同结论；
- 参照系缺失 / 类型未映射时**必须显式声明**（level="unknown" + 说明），
  既不静默放行，也不退回已证失效的常数 40；
- 组别归属走 SSOT（`TYPE2GROUP`），测试顺带钉住 `混合型-偏债 → E 债券` 这条实测结论。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis import peer_percentile as pp
from src.analysis.fund_scorer import FundScreener


def _vals(mom_pct):
    """造一条 100 天净值序列，使近 63 日动量 = mom_pct%。"""
    a = np.linspace(1.0, 1.0, 100)
    a[-63] = 1.0
    a[-1] = 1.0 + mom_pct / 100.0
    return a


def _cache(p90_by_group):
    return {
        "version": pp.CACHE_VERSION,
        "groups": {g: {"n": 500, "quality_level": "full",
                       "grid": {"momentum_3m": {"p50": 0.0, "p75": 1.0, "p90": v}}}
                   for g, v in p90_by_group.items()},
    }


@pytest.fixture()
def screener(monkeypatch):
    """不连库的 FundScreener（_check_momentum 只用 vals + fund_type + 缓存）。"""
    s = FundScreener.__new__(FundScreener)          # 跳过 __init__（需要 Database）
    s._peer_cache = None
    s._peer_loaded = False
    return s


def _with_cache(screener, monkeypatch, cache):
    monkeypatch.setattr(FundScreener, "_peer_dist", lambda self: cache, raising=True)
    return screener


class TestMomentumPercentile:
    def test_threshold_comes_from_group_p90(self, screener, monkeypatch):
        _with_cache(screener, monkeypatch, _cache({"A 偏股混合": 3.93}))
        lvl, txt, warn, mom = screener._check_momentum(_vals(5.0), "混合型-偏股")
        assert lvl == "warn" and "超同类P90线" in txt
        lvl2, _, _, _ = screener._check_momentum(_vals(2.0), "混合型-偏股")
        assert lvl2 == "pass"

    def test_same_fund_different_group_different_verdict(self, screener, monkeypatch):
        """A3 的核心证据：同一个 +5%，在 A 组是追涨、在 C 组不是。"""
        _with_cache(screener, monkeypatch, _cache({"A 偏股混合": 3.93, "C 主动股票": 9.42}))
        lvl_a, _, _, _ = screener._check_momentum(_vals(5.0), "混合型-偏股")
        lvl_c, _, _, _ = screener._check_momentum(_vals(5.0), "股票型")
        assert lvl_a == "warn", "A 组 P90=3.93，+5% 应判追涨"
        assert lvl_c == "pass", "C 组 P90=9.42，同样的 +5% 不该判追涨"

    def test_old_constant_would_have_missed_it(self, screener, monkeypatch):
        """旧常数 40 对 +30% 才警告；新口径下 E 组（P90=0.87）的 +30% 早就该警告。"""
        _with_cache(screener, monkeypatch, _cache({"E 债券": 0.87}))
        lvl, _, _, _ = screener._check_momentum(_vals(30.0), "债券型-长期纯债")
        assert lvl == "warn"
        assert 30.0 < screener.THRESHOLDS["momentum_warning"], \
            "本用例的前提：30% 低于旧常数 40（旧口径不会警告）"

    def test_missing_reference_declares_not_guesses(self, screener, monkeypatch):
        """参照系缺失 → 显式声明'跳过'，**不得**退回常数 40 静默通过。"""
        _with_cache(screener, monkeypatch, None)
        lvl, txt, warn, mom = screener._check_momentum(_vals(35.0), "混合型-偏股")
        assert lvl == "unknown", "没有参照系时必须声明数据不足，而不是判 pass/fail"
        assert "参照系未构建" in txt
        assert mom == 35.0                                # 原始动量仍如实返回
        assert warn is None

    def test_unmapped_type_declares(self, screener, monkeypatch):
        _with_cache(screener, monkeypatch, _cache({"A 偏股混合": 3.93}))
        lvl, txt, _, _ = screener._check_momentum(_vals(1.0), "某种没映射的新类型")
        assert lvl == "unknown" and "类型未映射" in txt

    def test_insufficient_history(self, screener):
        lvl, txt, _, _ = screener._check_momentum(np.linspace(1, 1.1, 30), "混合型-偏股")
        assert lvl == "unknown" and "数据不足" in txt


class TestGroupMappingSSOT:
    def test_known_mappings(self):
        assert pp.group_of("混合型-偏股") == "A 偏股混合"
        assert pp.group_of("股票型") == "C 主动股票"
        assert pp.group_of("债券型-长期纯债") == "E 债券"
        assert pp.group_of("QDII-普通股票") == "F QDII/其他"

    def test_mixed_bond_goes_to_E_by_measurement(self):
        """`混合型-偏债` 归 E：上一批次用实测分位网格定的（差 5 倍），不是直觉。"""
        assert pp.group_of("混合型-偏债") == "E 债券"

    def test_unmapped_returns_none_not_a_guess(self):
        assert pp.group_of("") is None
        assert pp.group_of(None) is None
        assert pp.group_of("不存在的新类型") is None


class TestAbsVsRelativePrinciple:
    def test_drawdown_stays_absolute(self):
        """A1：回撤是绝对概念，必须仍是绝对限值（不得被分位化）。"""
        assert isinstance(FundScreener.THRESHOLDS["max_drawdown_1y"], (int, float))
        assert FundScreener.THRESHOLDS["max_drawdown_1y"] == 35

    def test_momentum_pct_is_declared(self):
        """A3：分位化阈值的分位点数要有明确出处（本项目自定，依据实测分布）。"""
        assert FundScreener.MOMENTUM_WARNING_PCT == 90
