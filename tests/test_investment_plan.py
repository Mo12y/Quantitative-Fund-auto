"""投资计划持久化/CRUD 与行业板块归类单测。"""
import os

from src.data.database import Database
from src.analysis.investment_plan import ensure_seed, get_plan, get_progress
from src.analysis import fund_boards as fb


def _db(tmp_path):
    return Database(str(tmp_path / "plan.db"))


def test_seed_once_and_idempotent(tmp_path):
    db = _db(tmp_path)
    pid = ensure_seed(db)
    assert pid
    p = db.get_active_plan()
    assert p and len(p["items"]) == 3
    # 再次 seed 不应重复插入
    assert ensure_seed(db) is None
    assert len(db.get_active_plan()["items"]) == 3
    db.close()


def test_plan_meta_crud(tmp_path):
    db = _db(tmp_path)
    ensure_seed(db)
    pid = db.get_active_plan()["id"]

    assert db.update_plan(pid, name="我的新计划", total_capital=2000.0, risk_pref="稳健")
    p = get_plan(db)
    assert p["name"] == "我的新计划"
    assert p["total_capital"] == 2000.0
    assert p["risk_pref"] == "稳健"
    assert db.update_plan(pid, bogus_field="x") is False      # 非法字段被忽略
    db.close()


def test_plan_item_crud(tmp_path):
    db = _db(tmp_path)
    ensure_seed(db)
    pid = db.get_active_plan()["id"]

    iid = db.add_plan_item({"plan_id": pid, "fund_code": "999888", "fund_name": "测试基金",
                            "role": "🧪 测试", "target_amount": 120.0, "target_pct": 12.0,
                            "tranches": "[]"})
    assert iid
    assert any(i["fund_code"] == "999888" for i in db.get_plan_items(pid))

    assert db.update_plan_item(iid, target_amount=150.0, role="改过")
    lst = [i for i in db.get_plan_items(pid) if i["id"] == iid]
    assert lst[0]["target_amount"] == 150.0 and lst[0]["role"] == "改过"
    # 计划基金出现在 get_plan 的 funds 中，且带 item_id
    plan = get_plan(db)
    item = [f for f in plan["funds"] if f["code"] == "999888"][0]
    assert item["item_id"] == iid and item["target_amount"] == 150.0

    assert db.delete_plan_item(iid)
    assert not any(i["fund_code"] == "999888" for i in db.get_plan_items(pid))
    db.close()


def test_delete_plan_cascades_items(tmp_path):
    db = _db(tmp_path)
    ensure_seed(db)
    pid = db.get_active_plan()["id"]
    assert db.delete_plan(pid)
    assert db.get_active_plan() is None
    assert db.get_plan_items(pid) == []
    db.close()


def test_progress_counts_pending_confirm(tmp_path):
    """待确认买入也要计入计划进度（钱已花，只是净值未确认）。"""
    db = _db(tmp_path)
    ensure_seed(db)
    code = db.get_active_plan()["items"][0]["fund_code"]
    db.add_holding({"fund_code": code, "fund_name": "测试", "buy_date": "2026-09-10",
                    "buy_amount": 300.0, "buy_nav": None, "shares": 0,
                    "apply_date": "2026-09-10", "confirm_date": "2026-09-11",
                    "accrual_start": "2026-09-14", "status": "pending_confirm"})
    prog = get_progress(db)
    row = [f for f in prog["funds"] if f["code"] == code][0]
    assert row["invested"] == 300.0
    assert prog["total_invested"] == 300.0
    db.close()


def test_board_classification():
    assert fb.classify("南方中证半导体产业ETF联接C") == "半导体芯片"
    assert fb.classify("华夏中证机器人ETF") == "机器人智造"
    assert fb.classify("某某上海金ETF联接C") == "黄金对冲"
    assert fb.classify("易方达中证500ETF联接C") == "宽基指数"
    assert fb.classify("查无此基") == "其他"


def test_board_allocation_sums_to_100():
    holds = [
        {"fund_name": "某某上海金ETF联接C", "current_value": 300.0},
        {"fund_name": "某某中证500ETF联接C", "current_value": 700.0},
    ]
    alloc = fb.board_allocation(holds)
    assert abs(sum(alloc.values()) - 100.0) < 0.2
    assert alloc["黄金对冲"] == 30.0 and alloc["宽基指数"] == 70.0


# =====================================================================
# F-01（2026-09-25）：投资计划外置到本地配置
# =====================================================================

class TestPlanExternalized:
    """计划不再硬编码在代码里：本地 YAML 优先，缺失则退到内置**示例**。"""

    def test_load_local_plan_reads_yaml(self, tmp_path, monkeypatch):
        from src.analysis import investment_plan as ip
        f = tmp_path / "plan.yaml"
        f.write_text("""plan_name: 测试计划
start_date: '2026-02-02'
total_capital: 5000
cash_reserve: 800
funds:
  - code: '111111'
    name: 测试基金
    target_amount: 1000
""", encoding="utf-8")
        monkeypatch.setattr(ip, "LOCAL_PLAN_PATH", str(f))
        d = ip._load_local_plan()
        assert d["plan_name"] == "测试计划"
        assert d["total_capital"] == 5000
        assert d["funds"][0]["code"] == "111111"

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        from src.analysis import investment_plan as ip
        monkeypatch.setattr(ip, "LOCAL_PLAN_PATH", str(tmp_path / "nope.yaml"))
        assert ip._load_local_plan() is None

    def test_broken_yaml_returns_none_not_raise(self, tmp_path, monkeypatch):
        """坏文件不能把整个程序带崩 —— 退到示例计划。"""
        from src.analysis import investment_plan as ip
        f = tmp_path / "bad.yaml"
        f.write_text("a: [1, 2\n  b: :::\n", encoding="utf-8")
        monkeypatch.setattr(ip, "LOCAL_PLAN_PATH", str(f))
        assert ip._load_local_plan() is None

    def test_sample_plan_is_clearly_a_placeholder(self):
        """内置兜底必须是**示例**，不能混入任何真实基金。"""
        from src.analysis import investment_plan as ip
        assert "示例" in ip.SAMPLE_PLAN_NAME
        assert ip.SAMPLE_FUNDS, "示例计划不能为空"
        assert all(f["code"] == "000001" for f in ip.SAMPLE_FUNDS), \
            "内置示例不得含真实基金代码"

    def test_local_plan_file_is_gitignored(self):
        """个人计划不得进版本库 —— .gitignore 必须覆盖它。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gi = open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
        assert "config/investment_plan.local.yaml" in gi

    def test_example_template_is_tracked_and_parseable(self):
        """示例模板要入库（供新用户照填），且能被 yaml 解析。"""
        import yaml
        from src.analysis import investment_plan as ip
        assert os.path.exists(ip.EXAMPLE_PLAN_PATH), "示例模板应存在"
        d = yaml.safe_load(open(ip.EXAMPLE_PLAN_PATH, encoding="utf-8"))
        assert {"plan_name", "total_capital", "funds"}.issubset(d.keys())
