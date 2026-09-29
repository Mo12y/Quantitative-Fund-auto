"""数据链路下钻（`/api/explain`）的测试。

锁定四件事（对应用户「真金白银在实盘，不做虚拟测试」的要求）：

1. **五格齐全且有序**（采集→清洗→特征→建模→评估），每格都必须带
   `rows`（真实数字）+ `artifacts`（指回真实产物）—— 空壳等于文案，不算下钻；
2. **多批次持仓必须合并**：同一只基金定投/补仓多次 → 只出现一行
   （实测踩到：018392 出现两次，会被读成"你有两只黄金"）；
3. **账户与策略不许混**：评估格把「账户真实战绩（真金白银）」与
   「策略样本外负结论」**分成两行**，并各自标明来源；
4. **业务只读**：跑一次不改变账本与行情的行数（缓存快照回填不在此列，
   它是 cache 机制 —— 见 `_cached_get`）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis import peer_percentile as pp
from src.data.database import Database
from src.web import app as webapp

_CACHE = {"version": pp.CACHE_VERSION,
          "groups": {"A 偏股混合": {"n": 3278, "quality_level": "full",
                                    "grid": {"sharpe": {"p10": -0.5, "p50": 0.06, "p75": 0.5,
                                                        "p90": 1.0, "p95": 1.4},
                                             "max_drawdown_1y": {"p10": 8.0, "p50": 24.8,
                                                                "p75": 30.0, "p90": 40.0, "p95": 48.0},
                                             "annual_return": {"p10": -20.0, "p50": 5.0,
                                                               "p75": 12.0, "p90": 20.0, "p95": 30.0}}}}}

_TEMP = {"temperature": 46.0, "level": "normal", "level_desc": "🌤️ 适中",
         "target_equity_pct": 37.0, "scope": {"applies_to": ["A 股权益"]}, "degraded_dimensions": []}


def _seed(dbp):
    """临时库：两只基金（**017470 故意两条批次**，用于验证合并）+ 净值。"""
    import random
    from datetime import date, timedelta

    d = Database(dbp)
    d.upsert_fund_info({"fund_code": "017470", "fund_name": "嘉实上证科创板芯片ETF发起联接C",
                        "fund_type": "混合型-偏股"})
    d.upsert_fund_info({"fund_code": "000059", "fund_name": "国联安中证医药100A",
                        "fund_type": "混合型-偏股"})
    for i in range(2):                       # 同一只基金的**两条批次**（模拟两次定投）
        d.conn.execute(
            "INSERT INTO holdings (fund_code, fund_name, buy_date, confirm_date, accrual_start,"
            " shares, buy_amount, status, dividend_policy, cash_balance)"
            " VALUES (?,?,'2026-01-02','2026-01-02','2026-01-02',100,200.0,'holding','reinvest',0)",
            ("017470", "嘉实上证科创板芯片ETF发起联接C"))
    d.conn.execute(
        "INSERT INTO holdings (fund_code, fund_name, buy_date, confirm_date, accrual_start,"
        " shares, buy_amount, status, dividend_policy, cash_balance)"
        " VALUES (?,?,'2026-01-02','2026-01-02','2026-01-02',50,120.0,'holding','reinvest',0)",
        ("000059", "国联安中证医药100A"))
    d.conn.commit()

    for code, seed in (("017470", 3), ("000059", 7)):
        rnd, v, d0, rows = random.Random(seed), 1.0, date(2025, 8, 1), []
        for i in range(400):
            v *= (1 + rnd.gauss(0, 0.006))
            rows.append((code, (d0 + timedelta(days=i)).isoformat(), round(v, 6), round(v, 6), 0.0))
        d.insert_nav_batch(rows)
    d.close()


def _counts(dbp):
    """账本与行情行数（业务只读断言用）"""
    d = Database(dbp)
    n = {t: d.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
         for t in ("holdings", "transactions", "fund_nav")}
    d.close()
    return n


@pytest.fixture()
def client(tmp_path, monkeypatch):
    p = str(tmp_path / "explain.db")
    monkeypatch.setattr(webapp, "DB_PATH", p)
    monkeypatch.setattr(webapp.user_profile, "PROFILE_PATH", str(tmp_path / "no_profile.yaml"))
    monkeypatch.setattr(pp, "load_cache", lambda path=None: _CACHE)
    # 温度走桩（真实现会联网/读 akshare）—— 本测试只验链路结构，不验温度算法
    monkeypatch.setattr(webapp, "_all_temp", lambda: dict(_TEMP))
    _seed(p)
    with webapp._slot_lock:
        webapp._slot_store.clear()
    with webapp.app.test_client() as c:
        yield c, p


def _data(client):
    body = client.get("/api/explain?fresh=1").get_json()
    assert body["ok"] is True, body
    return body["data"]


class TestStagesShape:
    def test_five_stages_in_order(self, client):
        d = _data(client[0])
        assert [s["key"] for s in d["stages"]] == ["collect", "clean", "feature", "model", "eval"]

    def test_every_stage_points_to_real_artifacts(self, client):
        """每格都要指回真实产物（表名/脚本/文档）—— 否则就只是文案，不叫下钻。"""
        for s in _data(client[0])["stages"]:
            assert s["title"] and s["headline"], s
            assert isinstance(s["rows"], list) and isinstance(s["artifacts"], list)
            assert s["artifacts"], "%s 格没有 artifacts（无法追溯）" % s["key"]
            for a in s["artifacts"]:
                assert a.get("name") and a.get("detail"), s

    def test_collect_stage_reports_per_holding_spans(self, client):
        col = next(s for s in _data(client[0])["stages"] if s["key"] == "collect")
        assert "净值" in col["headline"] and "最新" in col["headline"]
        assert any("2025-08-01" in r["value"] for r in col["rows"]), "每只持仓要给出起止日期"

    def test_feature_stage_gives_peer_percentiles(self, client):
        feat = next(s for s in _data(client[0])["stages"] if s["key"] == "feature")
        assert all("组内" in r["value"] for r in feat["rows"]), \
            "特征格必须说明是**同类**分位（不是全市场排名）"


class TestMultiLotMerged:
    def test_same_fund_two_lots_show_once(self, client):
        """同只基金两条批次 → 只出现一行（否则会被读成"两只黄金"）。"""
        d = _data(client[0])
        col = next(s for s in d["stages"] if s["key"] == "collect")
        codes = [r["label"].split()[0] for r in col["rows"]]
        assert codes.count("017470") == 1, "多批次未合并：%s" % codes
        assert sorted(set(codes)) == ["000059", "017470"]

    def test_holdings_n_is_deduped_count(self, client):
        assert _data(client[0])["holdings_n"] == 2, "两条批次的 017470 应只算 1 只"


class TestAccountAndStrategySeparated:
    def test_eval_splits_account_from_strategy(self, client):
        ev = next(s for s in _data(client[0])["stages"] if s["key"] == "eval")
        labels = [r["label"] for r in ev["rows"]]
        assert "你的账面（真金白银）" in labels, "账户战绩必须单独一行"
        assert any("策略样本外" in x for x in labels), "策略结论必须单独一行（不许与账户混谈）"
        acct = next(r for r in ev["rows"] if r["label"] == "你的账面（真金白银）")
        assert "市值" in acct["value"] and "已实现" in acct["value"]
        strat = next(r for r in ev["rows"] if "策略样本外" in r["label"])
        assert "不显著" in strat["value"] or "无证据" in strat["value"], \
            "策略格必须如实给出负结论，不许美化"

    def test_model_stage_states_equity_scope(self, client):
        """权益占比要写明 SSOT 口径（QDII/黄金按权益归类），避免数字被误读。"""
        m = next(s for s in _data(client[0])["stages"] if s["key"] == "model")
        eq = next(r for r in m["rows"] if r["label"] == "你当前权益")
        assert "type_bucket" in eq["value"], "必须注明口径出处"


class TestBusinessReadOnly:
    def test_ledger_and_nav_untouched(self, client):
        c, p = client
        before = _counts(p)
        c.get("/api/explain?fresh=1")
        assert _counts(p) == before, "业务表被改动了：%s → %s" % (before, _counts(p))

    def test_holdings_status_unchanged(self, client):
        c, p = client
        d = Database(p)
        before = d.conn.execute("SELECT fund_code, status, shares FROM holdings ORDER BY fund_code,"
                                " shares").fetchall()
        d.close()
        c.get("/api/explain?fresh=1")
        d = Database(p)
        after = d.conn.execute("SELECT fund_code, status, shares FROM holdings ORDER BY fund_code,"
                               " shares").fetchall()
        d.close()
        assert [tuple(x) for x in after] == [tuple(x) for x in before]


class TestFailureTolerance:
    def test_summary_failure_returns_error_not_500(self, client, monkeypatch):
        """账本读不出来时：如实报错 + 空 stages（不抛 500、不假装有数据）。"""
        def _boom(*a, **k):
            raise RuntimeError("模拟账本读取失败")
        monkeypatch.setattr(webapp.PortfolioTracker, "get_portfolio_summary", _boom)
        body = client[0].get("/api/explain?fresh=1").get_json()
        assert body["ok"] is True, "端点本身不应 500"
        assert body["data"]["stages"] == []
        assert "失败" in body["data"]["error"]