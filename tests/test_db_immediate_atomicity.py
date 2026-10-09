# -*- coding: utf-8 -*-
"""`Database.immediate()` 的**原子性回归测试**（2026-10-07，E2）。

背景（2026-10-03 审计第三批记下的"独立议题"）
=============================================
`database.py` 的单条写方法**各自 `self.conn.commit()`**
（`add_holding` / `add_transaction` / `update_holding` / `add_dca_plan` …）。
于是"把它们的调用包进 `immediate()`"**并不能得到原子性**：
第一次内部 `commit()` 就把外层事务提交掉了，后面再失败就留下**半截数据**。

实测被踩到的形态（本文件第二个用例）：
`portfolio.add_buy_transaction()` / `sell()` 都是
「`with immediate():` → 调 `add_holding()` / `add_transaction()` / `update_holding()`」，
注释还写着"持仓与流水必须一起写，避免只落一半"——**而那个保证其实没有生效**。

修法：把内部 `self.conn.commit()` 改成 `self._commit()`，后者在 `immediate()` 事务内
**延迟提交**（只置脏标记，由最外层统一 commit）。见 `database.py` 的 `immediate()` docstring。

本文件的价值：**先复现再修** —— 修复前它会红；修完固化行为，防止将来有人把
`_commit()` 又写回 `conn.commit()`。
"""
import pytest

from src.data.database import Database


def _n(db: Database, sql: str) -> int:
    return db.conn.execute(sql).fetchone()[0]


def test_immediate_rolls_back_when_inner_writer_commits_then_failure(tmp_path):
    """`immediate()` 里调用"自带 commit 的写方法"后再抛异常 → 必须**整体回滚**。

    修复前：内部 `add_holding()` 的 `conn.commit()` 会把这条持仓**提前提交**，
    于是异常回滚只回滚了后面的语句 —— 持仓留在库里（半截数据）。
    """
    db = Database(str(tmp_path / "atomic.db"))
    with pytest.raises(RuntimeError):
        with db.immediate():
            db.add_holding({
                "fund_code": "000001", "fund_name": "测试基金", "buy_date": "2026-10-07",
                "buy_amount": 100.0, "shares": 10.0, "status": "holding",
            })
            raise RuntimeError("模拟事务内后续步骤失败")

    assert _n(db, "SELECT COUNT(*) FROM holdings") == 0, (
        "immediate() 没兜住内部 commit()：半截数据被提前提交了")


def test_add_buy_transaction_rollback_leaves_nothing(tmp_path):
    """真实形态：买入落库过程中失败 → holdings 与 transactions **都必须为空**。

    ⚠️ 这里不能用 monkeypatch 去改 portfolio 的内部步骤（那样测的是补丁不是代码），
    而是**直接按代码里的写法**复现：`immediate()` 里先 `add_holding()` 再 `add_transaction()`，
    中途失败。两者必须同生共死。
    """
    from src.analysis import portfolio as P

    pf = P.PortfolioTracker(Database(str(tmp_path / "buy.db")))
    with pytest.raises(RuntimeError):
        with pf.db.immediate():
            hid = pf.db.add_holding({
                "fund_code": "000002", "fund_name": "测试基金2", "buy_date": "2026-10-07",
                "buy_amount": 200.0, "shares": 20.0, "status": "holding",
            })
            pf.db.add_transaction({
                "holding_id": hid, "fund_code": "000002", "kind": "buy",
                "shares": 20.0, "amount": 200.0, "status": "confirmed",
            })
            raise RuntimeError("模拟第二步之后失败")

    assert _n(pf.db, "SELECT COUNT(*) FROM holdings") == 0, "持仓被提前提交（孤儿）"
    assert _n(pf.db, "SELECT COUNT(*) FROM transactions") == 0, "流水被提前提交"


def test_immediate_still_commits_on_success(tmp_path):
    """修复不能把"成功提交"也弄丢（反向对照）。"""
    db = Database(str(tmp_path / "ok.db"))
    with db.immediate():
        db.add_holding({
            "fund_code": "000003", "fund_name": "测试基金3", "buy_date": "2026-10-07",
            "buy_amount": 300.0, "shares": 30.0, "status": "holding",
        })
    assert _n(db, "SELECT COUNT(*) FROM holdings") == 1, "成功路径必须真落库"


def test_writer_outside_immediate_still_commits(tmp_path):
    """不打事务时的单条写仍然自提交（不能为了原子性把日常写变成"忘了提交"）。"""
    db = Database(str(tmp_path / "single.db"))
    db.add_holding({"fund_code": "000004", "buy_date": "2026-10-07",
                    "buy_amount": 400.0, "shares": 40.0, "status": "holding"})
    assert _n(db, "SELECT COUNT(*) FROM holdings") == 1
