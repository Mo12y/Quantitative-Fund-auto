# -*- coding: utf-8 -*-
"""数据契约守卫 **本身** 的测试（E3）。

"把约定变成红灯"的前提是**灯真的会亮**。本文件不测数据，测**守卫**：
  1. 一个满足全部契约的最小库 → 退出码 0；
  2. 逐条注入违规 → 退出码 1，且**点名到具体那一条**。

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


def test_freshness_violation_metrics_behind_ods(tmp_path):
    """第 19 条：物化表落后于 ODS（消费者会拿到旧指标）。"""
    path = _violate(tmp_path, "UPDATE fund_metrics SET asof = '2026-09-01'")
    code, out = _run(path)
    assert code == 1 and "第 19 条" in out, out


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
    """物化表不存在（新库/未跑 E2）→ 跳过第 17/19 条，**不许崩**。"""
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
