"""投资计划持久化/CRUD 与行业板块归类单测。"""
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
