"""定投期次推算（第 7 项）与自动同步/补录单测。"""
from datetime import date, timedelta

import pytest

from src.analysis import trade_rules as tr
from src.analysis.dca import DcaManager
from src.data.database import Database


def weekdays(y1, m1, d1, y2, m2, d2, holidays=()):
    s, e = date(y1, m1, d1), date(y2, m2, d2)
    out, cur = set(), s
    while cur <= e:
        if cur.weekday() < 5 and cur.isoformat() not in holidays:
            out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


HOL = {"2026-10-01", "2026-10-02", "2026-10-05"}
CAL = weekdays(2026, 1, 1, 2026, 12, 31, HOL)


# ---------------- 期次日期推算 ----------------

def test_weekly_skips_weekend_shift_and_dedupe():
    # 周六起算 → 顺延到周一，且不会因为周末两天塌成两期
    ds = tr.period_dates("2026-09-05", "weekly", "2026-09-28", CAL)
    assert [d["date"] for d in ds] == ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    assert [d["period_no"] for d in ds] == [1, 2, 3, 4]


def test_daily_counts_only_trade_days():
    ds = tr.period_dates("2026-09-07", "daily", "2026-09-11", CAL)
    assert [d["date"] for d in ds] == ["2026-09-07", "2026-09-08", "2026-09-09",
                                       "2026-09-10", "2026-09-11"]


def test_daily_shifts_weekend_without_double_count():
    # 09-11(五) 起算；周六周日都顺延到周一 09-14，去重后只记 09-11 与 09-14 两期
    ds = tr.period_dates("2026-09-11", "daily", "2026-09-14", CAL)
    assert [d["date"] for d in ds] == ["2026-09-11", "2026-09-14"]


def test_daily_before_shift_target_not_counted():
    # 窗口结束在周末（09-13）→ 顺延后的 09-14 落在窗口外，不计入
    ds = tr.period_dates("2026-09-11", "daily", "2026-09-13", CAL)
    assert [d["date"] for d in ds] == ["2026-09-11"]


def test_monthly_and_holiday_shift():
    ds = tr.period_dates("2026-09-01", "monthly", "2026-12-31", CAL)
    dates = [d["date"] for d in ds]
    assert dates[:2] == ["2026-09-01", "2026-10-01" if "2026-10-01" in CAL else "2026-10-06"]
    # 10-01 是节假日 → 顺延到 10-06
    assert "2026-10-06" in dates


def test_no_periods_when_start_after_end():
    assert tr.period_dates("2026-12-01", "weekly", "2026-11-01", CAL) == []


def test_monthly_clamps_month_end():
    """月末起投：1-31 这类日期要夹到当月最后一天，不能滚进下个月。"""
    ds = tr.period_dates("2026-03-31", "monthly", "2026-05-31", CAL)
    dates = [d["date"] for d in ds]
    assert dates[0] == "2026-03-31"
    assert "2026-04-30" in dates                     # 4 月只有 30 天 → 夹到 04-30
    assert not any(d.startswith("2026-05-01") for d in dates)


def test_daily_terminates_on_long_range():
    """超长区间必须靠 max_periods 收住，不能死循环。

    注意：guard 计的是**迭代次数**（含被去重跳过的周末锚点），不是返回期数，
    所以 50 次迭代只会产出「约 5/7」的期次。
    """
    ds = tr.period_dates("2026-01-05", "daily", "2035-12-31", CAL, max_periods=50)
    assert 0 < len(ds) <= 50
    assert ds[-1]["date"] < "2027-01-01"      # 远未到 2035 就被 guard 截断


# ---------------- 同步与补录 ----------------

@pytest.fixture()
def mgr(tmp_path):
    db = Database(str(tmp_path / "dca.db"))
    db.upsert_trade_dates(sorted(CAL))
    # 读路径已彻底离线（净值只读本地 DB），无需再 stub akshare
    m = DcaManager(db)
    # 起始日为过去 → 有多个应投期次
    db.add_dca_plan({"fund_code": "D001", "fund_name": "定投测试", "amount_per_period": 100.0,
                     "frequency": "weekly", "start_date": "2026-01-05", "next_run_date": "2026-01-12"})
    yield m, db
    db.close()


def test_sync_creates_periods_and_auto_executes_latest(mgr):
    m, db = mgr
    plan = db.get_dca_plans()[0]
    # 只同步，不自动执行：01-05,01-12,01-19,01-26,02-02 共 5 期
    st = m.sync_plan(plan, today="2026-02-02")
    assert st["expected"] == 5 and st["executed"] == 0 and st["pending"] == 5

    # sync_all：自动补录**最近一期**，其余仍待补录
    r = m.sync_all(today="2026-02-02")
    assert len(r["auto_executed"]) == 1
    assert r["auto_executed"][0]["date"] == "2026-02-02"        # 最近一期
    mine = [p for p in r["plans"] if p["plan_id"] == plan["id"]][0]
    assert mine["executed"] == 1 and mine["pending"] == 4
    # 该期已生成买入（待确认）
    h = db.get_current_holdings()
    assert len(h) == 1 and h[0]["buy_amount"] == 100.0
    assert db.get_dca_periods(plan["id"])[-1]["status"] == "executed"


def test_backfill_executes_all_pending(mgr):
    m, db = mgr
    plan = db.get_dca_plans()[0]
    r = m.backfill(plan["id"], today="2026-02-02")
    assert r["count"] == 5
    assert len(db.get_current_holdings()) == 5                    # 5 期各一笔买入
    periods = db.get_dca_periods(plan["id"])
    assert all(p["status"] == "executed" for p in periods)
    assert db.count_dca_periods(plan["id"], "executed") == 5


def test_paused_plan_not_auto_executed(mgr):
    m, db = mgr
    plan = db.get_dca_plans()[0]
    db.update_dca_plan(plan["id"], status="paused")
    r = m.sync_all(today="2026-02-02")
    assert r["auto_executed"] == []


# ---------------- 决策 B：executed 由买入凭证派生 ----------------

def test_out_of_order_execute_then_sync_keeps_others_pending(mgr):
    """乱序执行第 5 期后再 sync_plan，第 1~4 期必须仍是 pending。

    旧逻辑按 `period_no <= total_periods` 补记，会把 1~4 期凭空标成已执行
    —— 界面显示"已投 5 期"、实际只买了 1 笔，诱导用户以为投过了。
    """
    m, db = mgr
    plan = db.get_dca_plans()[0]
    m.sync_plan(plan, today="2026-02-02")                    # 5 期 pending
    m.execute_installment(plan["id"], period_no=5)           # 只投第 5 期（乱序）

    rows = {p["period_no"]: p for p in db.get_dca_periods(plan["id"])}
    assert rows[5]["status"] == "executed" and rows[5]["holding_id"] is not None

    # 用 fresh plan 重新对账（这正是 backfill / 页面刷新会走的路）
    m.sync_plan(db.get_dca_plans()[0], today="2026-02-02")
    rows = {p["period_no"]: p for p in db.get_dca_periods(plan["id"])}
    for n in (1, 2, 3, 4):
        assert rows[n]["status"] == "pending", f"第{n}期被凭空标记为已执行"
        assert rows[n]["holding_id"] is None
    assert len(db.get_current_holdings()) == 1               # 有且只有 1 笔真实买入
    assert db.get_dca_plans()[0]["total_periods"] == 1       # 派生值 = 有凭证的期数


def test_total_periods_and_amount_are_derived(mgr):
    """total_periods / total_amount 派生自期次表，不随期号回退或重复累加。"""
    m, db = mgr
    plan = db.get_dca_plans()[0]
    m.sync_plan(plan, today="2026-02-02")
    m.execute_installment(plan["id"], period_no=3)           # 只投第 3 期
    p = db.get_dca_plans()[0]
    assert p["total_periods"] == 1 and p["total_amount"] == 100.0   # 不是 3 / 300
    m.execute_installment(plan["id"], period_no=1)           # 再投第 1 期
    p = db.get_dca_plans()[0]
    assert p["total_periods"] == 2 and p["total_amount"] == 200.0   # 累加正确、不重复


def test_run_without_sync_still_records_period(mgr):
    """未 sync 过的计划直接执行 → 仍要落一行期次，保证 executed 有凭证。"""
    m, db = mgr
    plan = db.get_dca_plans()[0]
    assert db.get_dca_periods(plan["id"]) == []              # 还没 sync
    r = m.execute_installment(plan["id"], buy_date="2026-01-05")
    assert r["ok"]
    rows = db.get_dca_periods(plan["id"])
    assert len(rows) == 1 and rows[0]["status"] == "executed"
    assert rows[0]["holding_id"] is not None
    assert db.get_dca_plans()[0]["total_periods"] == 1


def test_link_periods_dry_run_does_not_write(mgr):
    """历史期次补链：dry_run 只报告不写库。"""
    m, db = mgr
    plan = db.get_dca_plans()[0]
    m.sync_plan(plan, today="2026-02-02")
    m.execute_installment(plan["id"], period_no=1)
    # 模拟历史数据：把凭证抹掉
    db.conn.execute("UPDATE dca_periods SET holding_id = NULL WHERE plan_id = ?", (plan["id"],))
    db.conn.commit()

    r = m.link_periods(plan["id"], dry_run=True)
    assert r["dry_run"] is True
    assert [l["period_no"] for l in r["linked"]] == [1]
    assert db.get_dca_periods(plan["id"])[0]["holding_id"] is None   # 没写库

    r2 = m.link_periods(plan["id"], dry_run=False)
    assert r2["count"] == 1
    assert db.get_dca_periods(plan["id"])[0]["holding_id"] is not None
