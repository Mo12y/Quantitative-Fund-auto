"""`_peer_block` 单元测试（B2 纪律：**样本不足必须显式声明**）。

`percentiles` 为 null 且带 reason，绝不返回一个来路不明的具体数字
（本项目反面教材："五个维度全缺仍显示市场温度 50.0°"）。

⚠️ 2026-10-16 T2.2（删壳）：原文件还含 `/api/funds`、`/api/funds/board` 的
B4 字段契约测试与冷启动 warming 测试 —— 两条路由已随旧命题删除，相应测试一并删除。
`_peer_block` 本身**保留**（仍服务 `/api/explain`）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis import peer_percentile as pp
from src.web import app as webapp


def _cache():
    return {"version": pp.CACHE_VERSION,
            "groups": {"A 偏股混合": {"n": 3278, "quality_level": "full",
                                      "grid": {"momentum_3m": {"p10": -24.2, "p50": -9.9,
                                                               "p75": -1.8, "p90": 3.93, "p95": 7.8},
                                               "max_drawdown_1y": {"p10": 8.0, "p50": 24.8,
                                                                  "p75": 30.0, "p90": 40.0, "p95": 48.0},
                                               "sharpe": {"p10": -0.5, "p50": 0.06,
                                                          "p75": 0.5, "p90": 1.0, "p95": 1.4}}}}}


class TestPeerBlock:
    def test_long_history_gets_percentiles(self):
        b = webapp._peer_block(_cache(), "000001", "混合型-偏股",
                               {"momentum_3m": 8.0, "max_drawdown_1y": 48.0, "sharpe": 1.4},
                               {"span_years": 5.0, "nav_asof": "2026-09-18"})
        assert b["group"] == "A 偏股混合" and b["group_n"] == 3278
        assert b["insufficient_data"] is False
        assert b["nav_asof"] == "2026-09-18"
        assert set(b["percentiles"]) == {"momentum_3m", "max_drawdown_1y", "sharpe"}
        assert b["percentiles"]["momentum_3m"] == pytest.approx(95.0, abs=0.5)

    def test_short_history_declares_not_guesses(self):
        """B2 核心：存续不足 3 年 → 不给百分位，且**说明原因**。"""
        b = webapp._peer_block(_cache(), "000001", "混合型-偏股",
                               {"momentum_3m": 8.0}, {"span_years": 1.4, "nav_asof": "2026-09-18"})
        assert b["percentiles"] is None
        assert b["insufficient_data"] is True
        assert "存续不足3年" in b["reason"]
        assert "1.4" in b["reason"]                   # 如实给出跨度，不糊弄

    def test_no_cache_declares(self):
        b = webapp._peer_block(None, "000001", "混合型-偏股", {"momentum_3m": 1.0},
                               {"span_years": 5.0, "nav_asof": "2026-09-18"})
        assert b["percentiles"] is None and b["insufficient_data"] is True
        assert "参照系未构建" in b["reason"]

    def test_unmapped_type_declares(self):
        b = webapp._peer_block(_cache(), "000001", "没映射的类型", {"momentum_3m": 1.0},
                               {"span_years": 5.0, "nav_asof": "2026-09-18"})
        assert b["percentiles"] is None and "类型未映射" in b["reason"]

    def test_no_metrics_declares(self):
        b = webapp._peer_block(_cache(), "000001", "混合型-偏股", {},
                               {"span_years": 5.0, "nav_asof": "2026-09-18"})
        assert b["percentiles"] is None and "无可用指标" in b["reason"]

    def test_never_returns_a_number_when_insufficient(self):
        """不论哪条不足路径，都不得出现具体百分位数字。"""
        cases = [
            (None, "混合型-偏股", {"momentum_3m": 1.0}, {"span_years": 5.0}),
            (_cache(), "没映射", {"momentum_3m": 1.0}, {"span_years": 5.0}),
            (_cache(), "混合型-偏股", {"momentum_3m": 1.0}, {"span_years": 0.5}),
            (_cache(), "混合型-偏股", {}, {"span_years": 5.0}),
        ]
        for cache, ftype, m, span in cases:
            b = webapp._peer_block(cache, "000001", ftype, m, span)
            assert b["insufficient_data"] is True and b["percentiles"] is None
            assert b["reason"], "样本不足时必须给出 reason"


# 2026-10-16 T2.2（删壳）：原 `_seed` / `client` fixture / `TestBoardApiBackwardCompat`
# / `TestColdStartWarming`×2（后者在原文件里重复定义了两次）已删除 —— 它们测的
# /api/funds、/api/funds/board 已随旧命题删除。`_peer_block` 的单元测试保留（见上）。
