"""§4.4 第 3 层接入（推荐 = 同类百分位 ∩ 用户约束）与 §4.6 可解释性的后端测试。

锁定五件事：
1. `_apply_user_constraints`：持仓←库、画像←本地文件，kept/dropped/skipped 三分不混；
2. **失败不阻断**：读持仓失败 → 返回原 picks + error 声明（不假装筛过）；
3. `/api/recommend` 的接线：kept 进 `current_picks`，落选/未评估 + 理由进 `constraint_review`；
4. `_pick_metrics`：只注入**有限值**（NaN/±inf/缺失一律不注入，宁可判"缺指标"）；
5. `user_profile.load_profile`：来源/解析失败都显式声明（**不编造默认画像**）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis import peer_percentile as pp
from src.analysis import user_profile
from src.data.database import Database
from src.web import app as webapp

_PICKS = [
    {"code": "017470", "name": "嘉实上证科创板芯片ETF发起联接C"},
    {"code": "000059", "name": "国联安中证医药100A"},
]

_CACHE = {"version": pp.CACHE_VERSION,
          "groups": {"A 偏股混合": {"n": 3278, "quality_level": "full",
                                    "grid": {"sharpe": {"p10": -0.5, "p50": 0.06, "p75": 0.5,
                                                        "p90": 1.0, "p95": 1.4},
                                             "max_drawdown_1y": {"p10": 8.0, "p50": 24.8,
                                                                "p75": 30.0, "p90": 40.0, "p95": 48.0},
                                             "momentum_3m": {"p10": -24.2, "p50": -9.9, "p75": -1.8,
                                                             "p90": 3.93, "p95": 7.8}}}}}


def _db(dbp):
    return Database(dbp)


def _seed(dbp, holdings=(("017470", "嘉实上证科创板芯片ETF发起联接C"),)):
    """临时库：基金信息 + 持仓（默认含一只半导体基金，供重叠/已持有判定）。"""
    d = _db(dbp)
    d.upsert_fund_info({"fund_code": "017470", "fund_name": "嘉实上证科创板芯片ETF发起联接C",
                        "fund_type": "混合型-偏股"})
    d.upsert_fund_info({"fund_code": "000059", "fund_name": "国联安中证医药100A",
                        "fund_type": "混合型-偏股"})
    for code, name in holdings:
        d.conn.execute(
            "INSERT INTO holdings (fund_code, fund_name, buy_date, confirm_date, accrual_start,"
            " shares, buy_amount, status, dividend_policy, cash_balance)"
            " VALUES (?,?,'2026-01-02','2026-01-02','2026-01-02',100,200.0,'holding','reinvest',0)",
            (code, name))
    d.conn.commit()
    d.close()


@pytest.fixture()
def dbp(tmp_path, monkeypatch):
    p = str(tmp_path / "constraint.db")
    monkeypatch.setattr(webapp, "DB_PATH", p)
    # 画像文件路径固定到一个不存在的位置 —— 用例不受开发机本地配置影响
    monkeypatch.setattr(webapp.user_profile, "PROFILE_PATH", str(tmp_path / "no_profile.yaml"))
    return p


class TestApplyUserConstraints:
    def test_pass_through_when_nothing_to_build(self, dbp):
        """既无持仓也无画像 → 空约束 pass-through（picks 原样、note 显式声明）。"""
        _seed(dbp, holdings=())
        d = _db(dbp)
        kept, review = webapp._apply_user_constraints(d, list(_PICKS))
        d.close()
        assert [p["code"] for p in kept] == ["017470", "000059"]
        assert review["applied"] == []
        assert "未生成任何约束" in review["note"]
        assert "不存在" in review["profile_note"]
        assert review["counts"] == {"before": 2, "kept": 2, "dropped": 0, "skipped": 0}

    def test_held_code_is_dropped_with_reason(self, dbp):
        _seed(dbp)
        d = _db(dbp)
        kept, review = webapp._apply_user_constraints(d, list(_PICKS))
        d.close()
        assert [p["code"] for p in kept] == ["000059"]
        assert [x["code"] for x in review["dropped"]] == ["017470"]
        assert "已持有" in review["dropped"][0]["reasons"][0]
        assert review["counts"]["dropped"] == 1 and review["counts"]["skipped"] == 0

    def test_board_cap_from_profile_file(self, tmp_path, monkeypatch, dbp):
        """画像文件生效：单板块上限 15% → 未持有的半导体候选也因板块占比被剔除。"""
        _seed(dbp)                      # 持仓 = 1 只半导体基金 → 该板块占 100%
        prof = tmp_path / "user_profile.local.yaml"
        prof.write_text("overlap_max_board_pct: 15\n", encoding="utf-8")
        monkeypatch.setattr(webapp.user_profile, "PROFILE_PATH", str(prof))
        picks = [{"code": "007300", "name": "国联安中证半导体ETF联接A"},
                 {"code": "000059", "name": "国联安中证医药100A"}]
        d = _db(dbp)
        kept, review = webapp._apply_user_constraints(d, picks)
        d.close()
        assert [p["code"] for p in kept] == ["000059"]
        assert [x["code"] for x in review["dropped"]] == ["007300"]
        assert "半导体芯片" in review["dropped"][0]["reasons"][0]
        assert review["holdings_n"] == 1
        assert any(c["kind"] == "holding_overlap" and c["params"]["max_board_pct"] == 15.0
                   for c in review["applied"])

    def test_unclassified_board_goes_to_skipped_not_dropped(self, dbp):
        """板块"未归类"→ 算不了 → skipped（绝不当作通过、也不当作淘汰）。"""
        _seed(dbp)                                  # 有持仓 → 会生成重叠约束
        d = _db(dbp)
        kept, review = webapp._apply_user_constraints(
            d, [{"code": "X1", "name": "某某灵活配置混合"}] + list(_PICKS))
        d.close()
        assert [p["code"] for p in kept] == ["000059"]
        assert [x["code"] for x in review["dropped"]] == ["017470"]
        skipped = {x["code"]: x["reasons"] for x in review["skipped"]}
        assert "X1" in skipped and any("未归类" in r for r in skipped["X1"])

    def test_read_failure_does_not_break(self, dbp):
        class _BadConn:
            def execute(self, *a, **k):
                raise RuntimeError("模拟读库失败")

        class _BadDB:
            conn = _BadConn()

        kept, review = webapp._apply_user_constraints(_BadDB(), list(_PICKS))
        assert [p["code"] for p in kept] == ["017470", "000059"], "失败必须原样返回，不得清空"
        assert "持仓读取失败" in review["error"]


class TestPickMetrics:
    def test_only_finite_values_are_injected(self):
        out = webapp._pick_metrics({"metrics": {
            "sharpe": 1.23456789, "max_drawdown_1y": float("nan"),
            "ann_vol": float("inf"), "momentum_3m": None, "annual_return": "x"}})
        assert out == {"sharpe": 1.2346}, "只注入有限值（NaN/inf/None/非数一律不注入）"

    def test_missing_metrics_is_empty(self):
        assert webapp._pick_metrics({}) == {}
        assert webapp._pick_metrics(None) == {}


class TestProfileLoader:
    def test_missing_file_declares(self, tmp_path, monkeypatch):
        p = tmp_path / "nope.yaml"
        monkeypatch.setattr(user_profile, "PROFILE_PATH", str(p))
        prof, note = user_profile.load_profile()
        assert prof == {} and "不存在" in note

    def test_valid_file(self, tmp_path):
        p = tmp_path / "u.yaml"
        p.write_text("preferred_groups: ['A 偏股混合']\noverlap_max_board_pct: 20\n", encoding="utf-8")
        prof, note = user_profile.load_profile(str(p))
        assert prof["overlap_max_board_pct"] == 20 and "本地画像" in note

    def test_broken_file_declares_not_silent(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("a: [1,\n  b: {oops\n", encoding="utf-8")
        prof, note = user_profile.load_profile(str(p))
        assert prof == {} and "解析失败" in note

    def test_non_dict_declares(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n", encoding="utf-8")
        prof, note = user_profile.load_profile(str(p))
        assert prof == {} and "不是键值表" in note


class _StubHR:
    """替身：跳过 15-20s 的历史回测，只喂 2 只候选给端点测试。"""

    def __init__(self, db):
        pass

    def recommend(self, lookback_years=1.5):
        return {"proven_winners": [], "stats": {"methodology": "in-sample"},
                "current_picks": [dict(p) for p in _PICKS]}


class TestRecommendEndpointWiring:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch, dbp):
        _seed(dbp)
        monkeypatch.setattr(webapp, "HistoricalRecommender", _StubHR)
        monkeypatch.setattr(webapp.FundScreener, "_screen_single_fund",
                            lambda self, code, info: {"metrics": {"momentum_3m": 8.0,
                                                                  "max_drawdown_1y": 20.0,
                                                                  "sharpe": 1.2}})
        monkeypatch.setattr(pp, "load_cache", lambda path=None: _CACHE)
        with webapp.app.test_client() as c:
            yield c

    def test_kept_and_review_in_payload(self, client):
        body = client.get("/api/recommend").get_json()
        assert body["ok"] is True
        data = body["data"]
        assert [p["code"] for p in data["current_picks"]] == ["000059"], "已持有的 017470 应被约束剔除"
        cr = data["constraint_review"]
        assert [x["code"] for x in cr["dropped"]] == ["017470"]
        assert cr["counts"]["before"] == 2
        assert any(c["kind"] == "holding_overlap" for c in cr["applied"])
        kept = data["current_picks"][0]
        assert kept["metrics"]["max_drawdown_1y"] == 20.0, "原始指标须注入（供绝对阈值口径）"
        assert kept["percentiles"], "同侪百分位块保留（为什么入选）"