# -*- coding: utf-8 -*-
"""数据契约守卫 **本身** 的测试（E3）。

"把约定变成红灯"的前提是**灯真的会亮**。本文件不测数据，测**守卫**：
  1. 一个满足全部契约的最小库 → 退出码 0；
  2. 逐条注入违规 → 退出码 1，且**点名到具体那一条**。

2026-10-15 T0.3：第 19 条由"物化表 asof 追平最新净值日"（用全局 MAX，被少数被刷新的基金顶绿）
改为**净值新鲜度覆盖率**（>= 90% 的基金，其最新净值日落在最近 5 个交易日窗口内）
→ 相应新增/改造 `test_freshness_*` 三例（含"红 → 恢复 → 绿"完整负对照）。

⚠️ 用子进程真跑脚本（不是 import 它的内部函数）—— 因为交付形态就是"命令行 + 退出码"，
import 测试会绕过参数解析与汇总逻辑。
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_ledger_invariants.py")
sys.path.insert(0, ROOT)

NAV_DATE = "2026-09-24"


def _mk_db(tmp_path, name="contract.db"):
    """一个**满足全部契约**的最小库。"""
    from src.analysis.fund_metrics_store import ensure_table
    from src.data.database import Database

    path = str(tmp_path / name)
    db = Database(path)
    db.upsert_fund_info({"fund_code": "F", "fund_name": "测试", "fund_type": "混合型"})
    db.conn.execute(
        "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
        " VALUES ('F',?,?,?,0)", (NAV_DATE, 1.0, 1.0))
    db.conn.execute(
        "INSERT INTO index_daily (index_code, trade_date, close) VALUES ('000300',?,4000.0)",
        (NAV_DATE,))
    db.conn.execute(
        "INSERT INTO market_temperature (trade_date, temperature, computed_at) VALUES (?,50,0)",
        (NAV_DATE,))
    db.conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?,1)", (NAV_DATE,))
    ensure_table(db)
    db.conn.execute("INSERT INTO fund_metrics (fund_code, asof, window_years) VALUES ('F',?,3)",
                    (NAV_DATE,))
    db.conn.commit()
    return path, db


def _run(path):
    p = subprocess.run([sys.executable, SCRIPT, "--db", path],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_minimal_valid_db_passes(tmp_path):
    path, db = _mk_db(tmp_path)
    try:
        code, out = _run(path)
        assert code == 0, "最小合法库应通过，实际退出码 %s：\n%s" % (code, out)
        assert "0 项失败" in out
    finally:
        db.close()


def test_missing_db_exits_nonzero(tmp_path):
    code, out = _run(str(tmp_path / "nope.db"))
    assert code != 0 and "找不到" in out


# ── 逐条注入违规：每条都必须被点名 ────────────────────────────────

def _violate(tmp_path, sql, params=(), name="v.db"):
    path, db = _mk_db(tmp_path, name)
    db.conn.execute(sql, params)
    db.conn.commit()
    db.close()
    return path


def test_unit_violation_out_of_range(tmp_path):
    """第 13 条：指数点位越界（模拟万元/点混装）。"""
    path = _violate(tmp_path, "UPDATE index_daily SET close = 999999")
    code, out = _run(path)
    assert code == 1 and "第 13 条" in out, out


def test_unit_violation_zero_nav(tmp_path):
    """第 14 条：净值 <= 0。"""
    path = _violate(tmp_path, "UPDATE fund_nav SET unit_nav = 0")
    code, out = _run(path)
    assert code == 1 and "第 14 条" in out, out


def test_referential_violation_orphan_nav(tmp_path):
    """第 15 条：净值代码不在 fund_info。"""
    path = _violate(
        tmp_path,
        "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
        " VALUES ('NOPE',?,1.0,1.0,0)", (NAV_DATE,))
    code, out = _run(path)
    assert code == 1 and "第 15 条" in out, out


def test_referential_violation_orphan_temperature(tmp_path):
    """第 16 条：温度日期没有对应指数数据（血缘断裂）。"""
    path = _violate(
        tmp_path,
        "INSERT INTO market_temperature (trade_date, temperature, computed_at)"
        " VALUES ('2026-09-30', 50, 0)")
    code, out = _run(path)
    assert code == 1 and "第 16 条" in out, out


def test_freshness_violation_nav_coverage_red(tmp_path):
    """第 19 条（新）：>= 90% 的基金净值停在窗口外 → 判红；刷新回来 → 转绿（完整负对照）。"""
    path, db = _mk_db(tmp_path, "fresh.db")
    try:
        code, out = _run(path)
        assert code == 0, "基线（唯一基金净值=最新日）应绿：\n" + out
        # 注入：9 只基金停在窗口外（09-01）→ 10 只中 9 只落后 → 覆盖率 10% < 90% → 红
        for i in range(9):
            db.upsert_fund_info({"fund_code": "L%d" % i, "fund_name": "落后样本", "fund_type": "混合型"})
            db.conn.execute(
                "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
                " VALUES (?, '2026-09-01', 1.0, 1.0, 0)", ("L%d" % i,))
        db.conn.commit()
        code, out = _run(path)
        assert code == 1 and "第 19 条" in out, out
        # 恢复：把落后基金刷新到最新 → 覆盖率 100% → 转绿
        db.conn.execute("UPDATE fund_nav SET nav_date = ? WHERE fund_code LIKE 'L%'", (NAV_DATE,))
        db.conn.commit()
        code, out = _run(path)
        assert code == 0, "恢复后应转绿：\n" + out
    finally:
        db.close()


def test_freshness_window_left_edge(tmp_path):
    """第 19 条窗口左端：净值日 == 基准日（窗口第 5 个交易日）算覆盖；退一天出窗 → 红。

    ⚠️ 同步把交易日历也退到 09-18 —— 否则第 21 条（日历超前净值日 → lag 为负）会先红，
    污染"第 19 条单独红"的负对照。
    """
    path, db = _mk_db(tmp_path, "edge.db")
    try:
        # 补齐 4 个交易日（09-18/21/22/23 + 既有 09-24）→ 窗口 5 天、基准日 = 09-18
        for d in ("2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"):
            db.conn.execute("INSERT INTO index_daily (index_code, trade_date, close)"
                            " VALUES ('000300',?,4000.0)", (d,))
        db.conn.execute("UPDATE fund_nav SET nav_date = '2026-09-18'")
        db.conn.execute("UPDATE trade_calendar SET trade_date = '2026-09-18'")
        db.conn.commit()
        code, out = _run(path)
        assert code == 0, "净值日=窗口左端（闭区间）应绿：\n" + out
        # 退到 09-17（窗外一天）→ 0/1 覆盖 → 红
        db.conn.execute("UPDATE fund_nav SET nav_date = '2026-09-17'")
        db.conn.commit()
        code, out = _run(path)
        assert code == 1 and "第 19 条" in out, out
    finally:
        db.close()


# ── 函数级：CLI 触达不到的分支 ────────────────────────────────────
# ⚠️ 为什么破例不用子进程：`index_daily` 无交易日这一分支**无法经 CLI 触达** ——
#    空表会先触发第 13 条的既有崩溃（`MIN(close)` 为 NULL 时 `%.2f` 格式化 TypeError，
#    属既有缺陷、T0.3 未处理，见交接记录），且 `trade_date` 有 NOT NULL 约束无法置空。
#    而"判据看不见数据必须判红"是项目教训级要求 → 至少函数级必须有红灯。


def _stats_conn():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE index_daily (trade_date TEXT)")
    conn.execute("CREATE TABLE fund_nav (fund_code TEXT, nav_date TEXT)")
    return conn


def test_nav_freshness_dark_calendar_unit():
    """第 19 条函数级：index_daily 无交易日 → 判红（不许沉默通过）。"""
    from scripts.check_ledger_invariants import nav_freshness_stats
    ok, detail = nav_freshness_stats(_stats_conn(), 1)
    assert ok is False and "无交易日" in detail


def test_nav_freshness_no_nav_unit():
    """第 19 条函数级：fund_nav 无基金（total=0）→ 判红。"""
    from scripts.check_ledger_invariants import nav_freshness_stats
    conn = _stats_conn()
    conn.execute("INSERT INTO index_daily VALUES ('2026-09-24')")
    ok, detail = nav_freshness_stats(conn, 0)
    assert ok is False and "无基金" in detail


def test_freshness_violation_temperature_desync(tmp_path):
    """第 20 条：温度与指数最新日不一致。"""
    path = _violate(tmp_path, "UPDATE market_temperature SET trade_date = '2026-09-01'")
    code, out = _run(path)
    assert code == 1 and "第 20 条" in out, out


def test_coverage_violation_flags(tmp_path):
    """第 12 条：净值覆盖率为 0（fund_info 有基金但一条净值都没有）。"""
    path, db = _mk_db(tmp_path, "cov.db")
    db.conn.execute("DELETE FROM fund_nav")
    db.conn.commit()
    db.close()
    code, out = _run(path)
    assert code == 1 and "第 12 条" in out, out


def test_missing_materialized_table_is_skipped_not_crash(tmp_path):
    """物化表不存在（新库/未跑 E2）→ 跳过第 17 条，**不许崩**（第 19 条不依赖本表，照常跑）。"""
    path, db = _mk_db(tmp_path, "nofm.db")
    db.conn.execute("DROP TABLE fund_metrics")
    db.conn.commit()
    db.close()
    code, out = _run(path)
    assert "Traceback" not in out, out
    assert code == 0, out
    assert "fund_metrics" in out and "跳过" in out


def test_known_baseline_catches_regression(tmp_path):
    """KNOWN 段：`fund_nav` 净值双列全空超过基线 40 → 判失败（防已知问题悄悄变大）。"""
    path, db = _mk_db(tmp_path, "known.db")
    rows = [("E%d" % i, "2026-0%d-01" % (i + 1)) for i in range(41)]
    for code, d in rows:
        db.conn.execute(
            "INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)"
            " VALUES (?,?,NULL,NULL,0)", (code, d))
    db.conn.commit()
    db.close()
    code, out = _run(path)
    assert code == 1 and "净值双列全空" in out, out


@pytest.mark.parametrize("flag", ["--explain"])
def test_explain_flag_prints_reasons(tmp_path, flag):
    path, db = _mk_db(tmp_path, "explain.db")
    try:
        p = subprocess.run([sys.executable, SCRIPT, "--db", path, flag],
                           capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        assert p.returncode == 0
        assert "依据：" in p.stdout
    finally:
        db.close()
