# -*- coding: utf-8 -*-
"""决策台 API 测试（T2.1）：7 个路由 —— **真实数据锚点 + 与 CLI 同源函数一致**。

验收要求（执行计划书 §6 T2.1）："断言每个路由返回非空且与 CLI 输出一致的**真实数据**
（与 T2.0 spot-check 已知值对齐，不只断言"非空"）"。

⚠️ 为什么跑**真实库**（不建临时库）：行为审计 / 反事实的输入是真实账本 ——
临时的合成库造不出「59 笔买入 + 30 个已卖批次 + 7 只持仓」的真实结构，
而这正是"与 CLI 输出一致"的前提。代价：**锚点断言（如 chase_spread≈2.58）绑定当前账本快照**，
账本新增交易或重采历史净值后需同步更新（这是真实数据测试的固有代价，接受）。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.web import app as webapp  # noqa: E402


@pytest.fixture()
def client():
    with webapp.app.test_client() as c:
        yield c


def _get(client, path):
    r = client.get(path)
    assert r.status_code == 200, "%s -> HTTP %s" % (path, r.status_code)
    body = r.get_json()
    assert body.get("ok") is True, body
    return body["data"]


# ── /api/behavior（契约 §3.2）──────────────────────────────────────

def test_behavior_real_data_and_same_source(client):
    from src.analysis import behavior_profile
    from src.data.database import Database

    data = _get(client, "/api/behavior")
    # 真实数据锚点（T2.0 spot-check 已知值；2.51→2.58 因 2026-10-16 T0.1 数据刷新
    # —— 净值序列更新后追高对照窗口随之变化，属"真实数据锚点的固有代价"）
    assert data["buy"]["chase_spread"] == pytest.approx(2.58, abs=0.03)
    assert data["buy"]["n_buys"] == 59
    assert data["sell"]["n_lots"] == 30
    assert data["sell"]["frac_loss"] == pytest.approx(0.7333, abs=0.001)
    for k in ("span", "sample", "buy", "sell", "frequency", "verdict", "notes"):
        assert k in data, "契约字段缺失: " + k
    # 样本不足必须**如实声明**（不硬出结论）
    assert data["sample"]["sufficient"] is False
    assert "不足" in data["sample"]["warning"]
    # 与 CLI 同源一致（同一模块函数、同一库）
    db = Database(webapp.DB_PATH)
    try:
        expect = behavior_profile.analyze(db)
    finally:
        db.close()
    assert data["verdict"] == expect["verdict"]
    assert data["buy"]["chase_spread"] == pytest.approx(expect["buy"]["chase_spread"])


# ── /api/counterfactual（契约 §3.3）────────────────────────────────

def test_counterfactual_real_data_and_cross_check(client):
    data = _get(client, "/api/counterfactual")
    c = data["compare"]
    assert c["n_buys_total"] == 59
    assert c["n_buys_dca"] == 33
    assert set(c["cases"]) >= {"actual", "hold_all", "dca_only", "temp_tilt"}
    assert c["cases"]["actual"]["xirr_pct"] is not None
    # 交叉验证（T2.0 spot-check）：① 完全不动 与 sell_rules 的"从不卖"同算法 → XIRR 一致
    never = [r for r in data["sell_rules"]["results"] if r["key"] == "never"]
    assert len(never) == 1
    assert c["cases"]["hold_all"]["xirr_pct"] == pytest.approx(never[0]["xirr_pct"], abs=0.05)
    # 三块齐全
    assert data["same_fund"]["summary"]["n_funds"] == 3
    assert len(data["same_fund"]["funds"]) == 3
    assert len(data["sell_rules"]["results"]) == 6


# ── /api/breakeven（契约 §3.4）─────────────────────────────────────

def test_breakeven_real_data(client):
    data = _get(client, "/api/breakeven")
    assert data["n_lots"] == 29
    assert len(data["lots"]) == data["n_lots"]
    assert data["n_in_penalty"] == 0          # 最近买入距今均 >7 天
    # 854.52 → 780.18：2026-10-16 T0.1 刷新（净值 09-24 → 10-15，三周市场变动）
    assert data["total_market_value"] == pytest.approx(780.18, abs=3.0)
    assert "today" in data and "notes" in data


# ── /api/risk（契约 §3.5）──────────────────────────────────────────

def test_risk_real_data_and_same_source(client):
    from src.analysis import behavior_profile, nav_metrics
    from src.data.database import Database

    data = _get(client, "/api/risk")
    funds = data["funds"]
    assert funds, "持仓非空"
    for f in funds:
        assert set(f) >= {"fund_code", "fund_name", "metrics", "insufficient", "reason"}
        if f["insufficient"]:
            assert f["metrics"] is None and f["reason"]
        else:
            m = f["metrics"]
            assert m["window_points"] >= 60
            for k in ("annual_return", "ann_vol", "max_drawdown_1y", "sharpe",
                      "sortino", "calmar", "window_years"):
                assert k in m, "缺指标: " + k
    ok = [f for f in funds if not f["insufficient"]]
    assert ok, "至少一只持仓基金有足够净值点"
    # 与同源一致（同一 nav_metrics.compute、同一净值序列）
    f0 = ok[0]
    db = Database(webapp.DB_PATH)
    try:
        series = behavior_profile.nav_series(db.conn, f0["fund_code"])
        m2 = nav_metrics.compute([v for _, v in series], [d for d, _ in series])
    finally:
        db.close()
    assert f0["metrics"]["annual_return"] == pytest.approx(m2["annual_return"])
    assert f0["metrics"]["window_points"] == m2["window_points"]


# ── /api/peer（契约 §3.6）──────────────────────────────────────────

def test_peer_real_data(client):
    data = _get(client, "/api/peer")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["asof"] or "")
    funds = data["funds"]
    assert funds
    mapped = [f for f in funds if f.get("group")]
    assert mapped, "至少一只基金映射到参照系组"
    for f in mapped:
        assert set(f) >= {"fund_code", "fund_name", "group",
                          "sharpe", "max_drawdown_1y", "ter"}
        for k in ("sharpe", "max_drawdown_1y", "ter"):
            node = f[k]
            assert node is None or set(node) >= {"group_n", "percentile",
                                                 "insufficient_data", "reason"}
    # 至少一只的夏普分位可用；TER 分位**必须可用**（T2.1 已把 ter 补进参照系网格）
    assert any(not f["sharpe"]["insufficient_data"] for f in mapped)
    ter_ok = [f for f in mapped if not f["ter"]["insufficient_data"]]
    assert ter_ok, "TER 分位应可用（参照系缓存须含 ter 网格）"
    assert 0.0 <= ter_ok[0]["ter"]["percentile"] <= 100.0


# ── /api/exclusion（契约 §3.7）─────────────────────────────────────

def test_exclusion_real_data(client):
    from src.data.database import Database

    data = _get(client, "/api/exclusion")
    total = len(data["excluded"]) + len(data["clean"]) + len(data["unrated"])
    db = Database(webapp.DB_PATH)
    try:
        n = db.conn.execute("SELECT COUNT(DISTINCT fund_code) FROM holdings"
                            " WHERE COALESCE(status,'') != 'sold'").fetchone()[0]
    finally:
        db.close()
    assert total == n, "excluded/clean/unrated 必须覆盖全部持仓基金（不许丢）"
    for f in data["excluded"]:
        assert f["reasons"], "excluded 必须给出理由"
        for r in f["reasons"]:
            assert ("夏普" in r) or ("TER" in r)
    for f in data["clean"]:
        assert set(f) >= {"fund_code", "fund_name", "sharpe_percentile", "ter_percentile"}


# ── /api/research（契约 §3.8，原 /api/quant_models 改名）────────────

def test_research_renamed_and_snapshot(client):
    # 旧路径已退役；新路径可用
    assert client.get("/api/quant_models").status_code == 404
    data = _get(client, "/api/research")
    assert data["snapshot"] is True
    assert "离线快照" in data["note"]
    assert isinstance(data["vol"], list) and len(data["vol"]) >= 1
    assert isinstance(data["drawdown"], list) and len(data["drawdown"]) >= 1
    assert isinstance(data["portfolio"], list) and len(data["portfolio"]) >= 1
    assert data["reports"]["drawdown_warning_report.md"] is True


# ── "判据看不见数据必须显式声明"（参照系缺失分支）─────────────────

def test_peer_declares_when_cache_missing(client, monkeypatch):
    monkeypatch.setattr(webapp.peer_percentile, "load_cache", lambda *a, **k: None)
    for path in ("/api/peer", "/api/exclusion"):
        data = _get(client, path)
        assert "未构建" in data["error"], "缓存缺失必须显式声明，不许沉默通过"
