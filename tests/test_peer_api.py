"""批次 B 测试：同侪参照系接入 API（B2 样本不足显式声明 / B4 接口字段）。

B2 的核心纪律：**样本不足必须显式声明**，`percentiles` 为 null 且带 reason，
绝不返回一个来路不明的具体数字（本项目反面教材："五个维度全缺仍显示市场温度 50.0°"）。
B4 的兼容纪律：**只增不改** —— 既有字段一个都不能少。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis import peer_percentile as pp
from src.data.database import Database
from src.web import app as webapp

PEER_KEYS = ("group", "group_n", "percentiles", "nav_asof",
             "insufficient_data", "reason")


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


def _seed(db_path, code="000011", n=320):
    """造一只有 ~1.3 年历史的基金（点数够评估，但跨度 <3 年 → 触发 B2 声明）。"""
    db = Database(db_path)
    db.upsert_fund_info({"fund_code": code, "fund_name": "测试混合C",
                         "fund_type": "混合型-偏股", "fund_size": 10.0,
                         "mgt_fee": 1.0, "establish_date": "2025-01-01"})
    import pandas as pd
    rows = []
    start = pd.Timestamp("2025-01-01")
    for i in range(n):
        d = (start + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append((code, d, 1.0 + i * 0.0005, 1.0 + i * 0.0005, 0.0))
    db.insert_nav_batch(rows)
    db.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "peer.db")
    _seed(dbp)
    monkeypatch.setattr(webapp, "DB_PATH", dbp)
    with webapp._slot_lock:
        webapp._slot_store.clear()
    webapp._dash_cache = None
    with webapp.app.test_client() as c:
        yield c, dbp


class TestBoardApiBackwardCompat:
    def test_board_keeps_old_fields_and_adds_new(self, client, monkeypatch):
        c, dbp = client
        monkeypatch.setattr(pp, "load_cache", lambda path=None: _cache())
        r = c.get("/api/funds/board?size=5&limit=50")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert set(("boards", "total_funds", "size")).issubset(body["data"].keys()), \
            "既有字段被删了（B4 要求只增不改）"
        for b in body["data"]["boards"]:
            for f in b.get("funds", []):
                assert set(("code", "name", "risk", "fee", "momentum_3m")).issubset(f.keys())
                for k in PEER_KEYS:
                    assert k in f, "缺少新字段 %s" % k

    def test_funds_api_keeps_old_fields_and_adds_new(self, client, monkeypatch):
        c, dbp = client
        monkeypatch.setattr(pp, "load_cache", lambda path=None: _cache())
        r = c.get("/api/funds")
        assert r.status_code == 200
        d = r.get_json()["data"]
        assert set(("funds", "summary")).issubset(d.keys())
        for f in d["funds"]:
            for k in PEER_KEYS:
                assert k in f
