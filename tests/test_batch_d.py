"""批次 D 测试：F-07（采集成功计数） / E-04（畸形请求返 4xx） / E-07（端口可配）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.data.database import Database
from src.web import app as webapp

CODE = "000011"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "d.db")
    db = Database(dbp)
    db.upsert_fund_info({"fund_code": CODE, "fund_name": "测试混合C", "fund_type": "混合型-偏股"})
    db.close()
    monkeypatch.setattr(webapp, "DB_PATH", dbp)
    with webapp._slot_lock:
        webapp._slot_store.clear()
    webapp._dash_cache = None
    with webapp.app.test_client() as c:
        yield c, dbp


class TestE04HttpStatus:
    """E-04：畸形/非法请求必须返回 4xx，不能一律 200 + 错误体。"""

    def test_unknown_action_is_400(self, client):
        c, _ = client
        r = c.post("/api/holdings", json={"action": "no_such_action"})
        assert r.status_code == 400, "未知操作应返回 400"
        b = r.get_json()
        assert b["ok"] is False
        # 旧文案是 f"未知操作: {action}" —— 空 action 时会拼出"未知操作: "（尾随空值）
        assert "no_such_action" in b["error"]

    def test_empty_action_message_has_no_trailing_blank(self, client):
        c, _ = client
        r = c.post("/api/holdings", json={})
        assert r.status_code == 400
        assert r.get_json()["error"].strip() != "未知操作:"
        assert "(空)" in r.get_json()["error"], "空 action 应显式标出，而不是拼成空串"

    def test_missing_code_is_400(self, client):
        c, _ = client
        r = c.post("/api/holdings", json={"action": "buy", "amount": 100})
        assert r.status_code == 400
        assert "缺少基金代码" in r.get_json()["error"]

    def test_nonpositive_amount_is_400(self, client):
        c, _ = client
        r = c.post("/api/holdings", json={"action": "buy", "code": CODE, "amount": -1})
        assert r.status_code == 400
        assert "大于 0" in r.get_json()["error"]

    def test_unregistered_code_is_400(self, client):
        c, _ = client
        r = c.post("/api/holdings", json={"action": "buy", "code": "999999", "amount": 10})
        assert r.status_code == 400
        assert "库内没有基金代码" in r.get_json()["error"]

    def test_success_still_200(self, client):
        c, _ = client
        r = c.post("/api/holdings", json={"action": "buy", "code": CODE, "amount": 10,
                                          "date": "2026-09-18"})
        assert r.status_code == 200 and r.get_json()["ok"] is True


class TestE07PortConfig:
    """E-07：端口不能硬编码（否则开不了第二个实例）。"""

    def _port(self, argv, env=None, monkeypatch=None):
        monkeypatch.setattr(sys, "argv", argv)
        if env is None:
            monkeypatch.delenv("QFA_PORT", raising=False)
        else:
            monkeypatch.setenv("QFA_PORT", env)
        return webapp._resolve_port()

    def test_default_5020(self, monkeypatch):
        assert self._port(["main.py", "web"], monkeypatch=monkeypatch) == 5020

    def test_env_var(self, monkeypatch):
        assert self._port(["main.py", "web"], env="5099", monkeypatch=monkeypatch) == 5099

    def test_positional_arg_wins_over_env(self, monkeypatch):
        assert self._port(["main.py", "web", "5101"], env="5099", monkeypatch=monkeypatch) == 5101

    def test_flag_arg_wins(self, monkeypatch):
        assert self._port(["main.py", "web", "--port=5102"], env="5099",
                          monkeypatch=monkeypatch) == 5102

    def test_bad_env_falls_back(self, monkeypatch):
        assert self._port(["main.py", "web"], env="not-a-port", monkeypatch=monkeypatch) == 5020


class TestF07NavCounting:
    """F-07：净值"成功"必须按**实际入库行数**计，而不是"接口没抛异常"。"""

    def test_save_fund_nav_batch_returns_row_count(self, tmp_path):
        from src.data.collector import DataCollector
        import pandas as pd
        db = Database(str(tmp_path / "nav.db"))
        col = DataCollector(db)
        df = pd.DataFrame({
            "净值日期": ["2026-09-17", "2026-09-18"],
            "单位净值": [1.0, 1.1],
            "累计净值": [1.0, 1.1],
            "日增长率": [0.0, 10.0],
        })
        n = col.save_fund_nav_batch("999001", df)
        assert n == 2, "应回报实际写入行数"
        db.close()

    def test_empty_dataframe_returns_zero_not_none(self, tmp_path):
        """源头无数据时返回 0（可被调用方识别为"空"，而不是当成成功）。"""
        from src.data.collector import DataCollector
        import pandas as pd
        db = Database(str(tmp_path / "nav2.db"))
        col = DataCollector(db)
        n = col.save_fund_nav_batch("999002", pd.DataFrame())
        assert n == 0
        db.close()
