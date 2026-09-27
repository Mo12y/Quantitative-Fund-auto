"""`GET /api/calendar` 交易日历查询。

《前端优化设计方案》原标注此项"未实现（没有 /api/calendar 路由）"，本批补上。
要点：**只返回开市日**（非交易日不得混入，否则下游会拿它当"可交易日"）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client(tmp_path, monkeypatch):
    import src.web.app as webapp
    from src.data.database import Database

    dbp = str(tmp_path / "cal.db")
    d = Database(dbp)
    d.upsert_trade_dates(["2026-01-05", "2026-01-06", "2026-01-07"])
    d.upsert_trade_dates(["2026-01-08"], is_open=0)        # 非交易日
    d.conn.commit()
    d.close()

    monkeypatch.setattr(webapp, "DB_PATH", dbp)
    with webapp._slot_lock:
        webapp._slot_store.clear()
    webapp._dash_cache = None
    return webapp.app.test_client()


def test_calendar_returns_open_dates_only(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        j = c.get("/api/calendar").get_json()
    assert j["ok"] is True
    assert j["data"]["dates"] == ["2026-01-05", "2026-01-06", "2026-01-07"], \
        "非交易日不得混入（下游会把它当可交易日）"
    assert j["data"]["count"] == 3
    assert "calendar" in j["data"]["refresh_cmd"]


def test_calendar_supports_range(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        j = c.get("/api/calendar?start=2026-01-06&end=2026-01-06").get_json()
    assert j["data"]["dates"] == ["2026-01-06"]
