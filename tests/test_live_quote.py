"""盘中行情快照测试（数据源扩展计划书 阶段 5）。

锁定三件事（都不依赖网络）：
  1. 解析：停牌/无报价条目**跳过**，不填 0（0 会被读成"平盘"）；
  2. 交易时段判定按**北京时区**（本项目踩过时区坑），午休/周末为 False；
  3. 抓取失败**显式降级**（available=False + reason），不抛异常、不给假数据。
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.live_quote import TZ, fetch, is_trading_session, lag_note, parse_ulist  # noqa: E402


def test_parse_skips_suspended_instead_of_filling_zero():
    payload = {"data": {"diff": [
        {"f2": 4.1234, "f3": 0.56, "f12": "000300", "f14": "沪深300", "f124": 1758888000},
        {"f2": "-", "f3": "-", "f12": "000905", "f14": "中证500", "f124": None},   # 停牌
        {"f2": None, "f12": "000016", "f14": "上证50"},                            # 无报价
    ]}}
    rows = parse_ulist(payload)
    assert len(rows) == 1, "停牌/无报价必须跳过，绝不能填 0（会被读成平盘）"
    r = rows[0]
    assert r["code"] == "000300" and r["price"] == 4.1234 and r["pct"] == 0.56
    assert r["asof"], "有 f124 就必须给出 asof（否则等于假装是'现在'）"


def test_parse_handles_empty_and_malformed():
    assert parse_ulist({}) == []
    assert parse_ulist({"data": None}) == []
    assert parse_ulist({"data": {"diff": [{"f2": "abc", "f12": "X"}]}}) == []


def test_trading_session_uses_beijing_time():
    d = lambda *a: datetime(*a, tzinfo=TZ)      # noqa: E731
    assert is_trading_session(d(2026, 9, 25, 10, 0)) is True      # 周五上午
    assert is_trading_session(d(2026, 9, 25, 14, 30)) is True     # 周五下午
    assert is_trading_session(d(2026, 9, 25, 12, 0)) is False     # 午休
    assert is_trading_session(d(2026, 9, 25, 9, 0)) is False      # 开盘前
    assert is_trading_session(d(2026, 9, 26, 10, 0)) is False     # 周六
    assert is_trading_session(d(2026, 9, 27, 14, 0)) is False     # 周日


def test_fetch_degrades_explicitly(monkeypatch):
    """出网失败 → available=False + reason，**不抛异常、不给假数据**。"""
    import src.data.live_quote as lq

    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("模拟网络故障")

    monkeypatch.setitem(sys.modules, "requests", _Boom())
    r = fetch(["000300"])
    assert r["available"] is False and "模拟网络故障" in r["reason"]
    assert r["quotes"] == []


def test_fetch_degrades_on_empty_payload(monkeypatch):
    import src.data.live_quote as lq

    class _Empty:
        class _R:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"data": {"diff": []}}

        def get(self, *a, **k):
            return self._R()

    monkeypatch.setitem(sys.modules, "requests", _Empty())
    r = fetch(["000300"])
    assert r["available"] is False and "形状" in r["reason"]


def test_lag_note_quantifies():
    assert "落后 4 天" in lag_note("2026-09-22", "2026-09-26")
    assert "无时滞" in lag_note("2026-09-26", "2026-09-26")
    assert "不可解析" in lag_note("", "2026-09-26")


def test_market_live_endpoint_degrades_gracefully(tmp_path, monkeypatch):
    """端点：行情不可用时仍返回 ok=True + available=False —— **页面不该因此 500**。"""
    import src.data.live_quote as lq
    import src.web.app as webapp
    from src.data.database import Database

    dbp = str(tmp_path / "live.db")
    d = Database(dbp)
    d.upsert_fund_info({"fund_code": "X", "fund_name": "x", "fund_type": "混合型"})
    d.conn.execute("INSERT OR REPLACE INTO fund_nav "
                   "(fund_code, nav_date, unit_nav, acc_nav, daily_return) "
                   "VALUES ('X','2026-09-22',1.0,1.0,0)")
    d.conn.commit()
    d.close()

    monkeypatch.setattr(webapp, "DB_PATH", dbp)
    with webapp._slot_lock:
        webapp._slot_store.clear()
    webapp._dash_cache = None
    monkeypatch.setattr(lq, "fetch",
                        lambda *a, **k: {"available": False, "reason": "stub 故障", "quotes": []})

    with webapp.app.test_client() as c:
        resp = c.get("/api/market/live")
        assert resp.status_code == 200, "行情不可用不得让端点报错"
        j = resp.get_json()

    assert j["ok"] is True
    assert j["data"]["available"] is False and j["data"]["reason"] == "stub 故障"
    assert j["data"]["local_nav_latest"] == "2026-09-22"
    assert "时滞" in j["data"]["lag_note"] or "落后" in j["data"]["lag_note"]
