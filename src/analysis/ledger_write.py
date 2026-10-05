# -*- coding: utf-8 -*-
"""写操作统一门面 —— 账本写的**唯一入口**（buy/sell 除外，见下）。

为什么需要（2026-10-05，方案 B 的 B-2）：
  · `app.py::api_holdings_action` 的 `dividend_policy` 是**裸 SQL**（`executemany` + `conn.commit()`，
    绕开了锁和事务 —— 现存并发风险最高处）；
  · `update` / `delete` 各自 `commit()`，且有两个口径缺陷（见 `update_holding` / `delete_holding`）。
  · 这里把所有写收拢成「`_WRITE_LOCK` + `db.immediate()`」，事务要么全成、要么全滚。

⚠️ **复用 `portfolio._WRITE_LOCK`（同一把锁）**：buy/sell 在 `portfolio.py`、update/delete 在这里，
   必须用**同一把锁**才能让所有写操作互斥；否则两个入口并发写会互相覆盖（丢失更新）。

⚠️ 门面里的写**一律在 `immediate()` 里直接操作 `conn`**，不调用 `database.py` 那些会各自
   `commit()` 的方法 —— 否则外层事务会被内部 `commit()` 提前提交，`immediate()` 形同虚设。
   （这是 2026-10-05 盘查发现的更深一层问题：`add_holding`/`add_transaction`/`update_transaction`
   内部都 `self.conn.commit()`，导致 `portfolio.add_buy_transaction` 包的 `immediate()` 被提前提交。
   buy/sell 的原子性修复属**独立议题**，已记入计划书 §12 B-2 未决项，本轮不顺手改。）
"""
from __future__ import annotations

import json
import time
from typing import Optional

from src.analysis.portfolio import _WRITE_LOCK, purchase_fee_rate
from src.data.database import Database

__all__ = [
    "set_dividend_policy", "update_holding", "delete_holding",
    "snap", "record", "rollback", "recent_commits", "latest_commit_id", "COMMIT_RETENTION",
]

# ── 撤销机制（B-4b-1）────────────────────────────────────────────────────
# 只允许撤销**最近这么多条**提交。理由：撤销是"手滑兜底"，不是版本管理；
# 允许回滚很久以前的提交，等于把后来的所有操作一起抹掉（那些操作本身没错）。
COMMIT_RETENTION = 20


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def snap(db: Database, spec: dict) -> dict:
    """按 `spec` 取相关行的快照。

    `spec = {table: (where_sql, params)}`，如
        {"holdings": ("id = ?", (12,)), "transactions": ("holding_id = ?", (12,))}
    返回 `{table: [整行 dict, ...]}`。

    ⚠️ 定位一律用**主键或外键**（`id` / `holding_id`），不用 `fund_code` ——
    同一只基金可能有多笔持仓，按 `fund_code` 取快照会把别的批次一起卷进撤销范围。
    """
    out: dict = {}
    for table, (where, params) in spec.items():
        try:
            rows = db.conn.execute(f"SELECT * FROM {table} WHERE {where}", tuple(params)).fetchall()
            out[table] = [dict(r) for r in rows]
        except Exception:
            out[table] = []
    return out


def record(db: Database, *, action: str, label: str, spec: dict, before: dict) -> Optional[int]:
    """记录一条提交（**在 `immediate()` 事务内**调用，与本次写同生共死）。

    `before` 是写**前**的 `snap()` 结果；写后的状态这里自己取（`after`）。
    撤销 = 按 `spec` 删掉当前行、再把 `before` 插回去 —— 于是**新增/修改/删除都能还原**：
      · 新增：before 为空 → 撤销后行消失
      · 修改：before 有旧值 → 撤销后覆盖回旧值
      · 删除：before 有整行 → 撤销后插回来
    """
    after = snap(db, spec)
    payload = json.dumps(
        {
            "spec": {t: [w, list(p)] for t, (w, p) in spec.items()},
            "before": before,
            "after": after,
        },
        ensure_ascii=False, default=str)
    cur = db.conn.execute(
        "INSERT INTO ledger_commits (created_at, action, label, payload) VALUES (?, ?, ?, ?)",
        (_now(), action, label, payload))
    return cur.lastrowid


def recent_commits(db: Database, limit: int = 10) -> list:
    """最近的提交（供界面显示"可撤销的操作"）。"""
    rows = db.conn.execute(
        "SELECT id, created_at, action, label, rolled_back_at FROM ledger_commits "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def latest_commit_id(db: Database) -> Optional[int]:
    """最新一条提交的 id —— 写操作**紧接其后**调用即可拿到"本次"的 id，供界面显示「撤销」。

    ⚠️ 前提：同一连接、且在写操作的 `_WRITE_LOCK` 临界区内（本项目的写都串行化，
    所以"最新一条"就是"本次那条"）。
    """
    row = db.conn.execute("SELECT MAX(id) AS m FROM ledger_commits").fetchone()
    return int(row["m"]) if row and row["m"] is not None else None


def rollback(db: Database, commit_id: int, retention: int = COMMIT_RETENTION) -> dict:
    """凭 `commit_id` 反向重放那一次写。

    ⚠️ 约束（缺一不可）：
      · 只允许**最近 `retention` 条**内的提交 —— 撤销是手滑兜底，不是版本管理；
        允许回滚很久以前的，会把之后所有正确操作一起抹掉。
      · **不能重复撤销**（`rolled_back_at` 非空即拒）—— 否则第二次会把"已撤销"的状态
        再按同一份 before 覆盖一遍，语义混乱。
      · 走同一把 `_WRITE_LOCK` + `immediate()`，与其他写操作互斥。

    返回 `{"commit_id", "action", "label", "restored":{表:行数}}`。
    失败抛 `ValueError`（带中文原因，供界面直接显示）。
    """
    row = db.conn.execute(
        "SELECT * FROM ledger_commits WHERE id = ?", (commit_id,)).fetchone()
    if not row:
        raise ValueError("找不到这条操作记录（可能已被清理）")
    if row["rolled_back_at"]:
        raise ValueError("这条操作已经撤销过了")

    recent_ids = [r["id"] for r in db.conn.execute(
        "SELECT id FROM ledger_commits ORDER BY id DESC LIMIT ?", (retention,))]
    if commit_id not in recent_ids:
        raise ValueError(f"只允许撤销最近 {retention} 次操作")

    payload = json.loads(row["payload"])
    spec = {t: (w, tuple(p)) for t, (w, p) in payload["spec"].items()}
    before = payload["before"]

    restored = {}
    with _WRITE_LOCK:
        with db.immediate():
            for table, (where, params) in spec.items():
                db.conn.execute(f"DELETE FROM {table} WHERE {where}", params)
                rows_back = before.get(table) or []
                for r in rows_back:
                    cols = list(r.keys())
                    db.conn.execute(
                        f"INSERT INTO {table} ({','.join(cols)}) "
                        f"VALUES ({','.join('?' * len(cols))})",
                        [r[c] for c in cols])
                restored[table] = len(rows_back)
            db.conn.execute(
                "UPDATE ledger_commits SET rolled_back_at = ? WHERE id = ?", (_now(), commit_id))
    return {"commit_id": commit_id, "action": row["action"], "label": row["label"],
            "restored": restored}


def set_dividend_policy(
    db: Database,
    *,
    code: str = None,
    holding_id: Optional[int] = None,
    policy: str,
) -> int:
    """分红策略切换（`reinvest` | `cash`）—— 取代 `app.py` 里的裸 SQL。

    只改**以后**检测到的除息日怎么入账；已入账历史不动（不重算、不回填）。
    返回变更的行数；没匹配到持仓返回 0。

    定位方式：`code` → 该基金**全部**持仓；`holding_id` → 单笔持仓。
    """
    if policy not in ("reinvest", "cash"):
        raise ValueError("policy 只能是 reinvest 或 cash")

    with _WRITE_LOCK:
        with db.immediate():
            if code:
                rows = db.conn.execute(
                    "SELECT id FROM holdings WHERE fund_code=?", (code,)).fetchall()
            elif holding_id is not None:
                rows = db.conn.execute(
                    "SELECT id FROM holdings WHERE id=?", (holding_id,)).fetchall()
            else:
                rows = []
            if not rows:
                return 0
            ids = tuple(r["id"] for r in rows)
            spec = {"holdings": ("id IN (%s)" % ",".join("?" * len(ids)), ids)}
            before = snap(db, spec)                     # 写前快照（供撤销）
            db.conn.executemany(
                "UPDATE holdings SET dividend_policy=? WHERE id=?",
                [(policy, r["id"]) for r in rows])
            record(db, action="dividend_policy",
                   label="分红方式改为%s（%d 笔）"
                         % ("红利再投" if policy == "reinvest" else "现金分红", len(ids)),
                   spec=spec, before=before)
            return len(rows)


def update_holding(
    db: Database,
    holding_id: int,
    *,
    buy_amount: Optional[float] = None,
    buy_date: Optional[str] = None,
    **extra,
) -> bool:
    """更新持仓；改 `buy_amount` 时**同步** `transactions` 里对应买入流水的 amount/fee/shares。

    ⚠️ 口径缺陷①（2026-10-05 修复）：旧实现改 `buy_amount` 只重算 `holdings.shares`，
    **不同步** `transactions` —— 改一次成本，复式记账就和流水对不上（"和平了两次"）。
    """
    row = db.conn.execute(
        "SELECT fund_code, fund_name, buy_nav FROM holdings WHERE id=?",
        (holding_id,)).fetchone()
    if not row:
        return False

    updates: list[str] = []
    params: list = []
    if buy_amount is not None:
        updates.append("buy_amount = ?")
        params.append(buy_amount)
    if buy_date:
        updates.append("buy_date = ?")
        params.append(buy_date)
    # 其余字段走白名单（与 database.update_holding 的 allowed 一致）
    for col in ("buy_nav", "shares", "notes", "status", "sell_date", "sell_amount",
                "apply_date", "apply_after_cutoff", "confirm_date", "confirm_nav",
                "accrual_start"):
        if col in extra and extra[col] is not None:
            updates.append(f"{col} = ?")
            params.append(extra[col])
    if not updates:
        return False

    with _WRITE_LOCK:
        with db.immediate():
            _spec = {"holdings": ("id = ?", (holding_id,)),
                     "transactions": ("holding_id = ?", (holding_id,))}
            before = snap(db, _spec)                    # 写前快照（供撤销）
            if buy_amount is not None:
                bnav = row["buy_nav"]
                if bnav and bnav > 0:
                    shares = round(buy_amount / bnav, 2)
                    updates.append("shares = ?")
                    params.append(shares)
                    # 同步 transactions 里 kind='buy' 的那笔（amount/fee/shares）
                    rate = purchase_fee_rate(row["fund_name"] or "")
                    net = buy_amount / (1.0 + rate)
                    fee = round(buy_amount - net, 2)
                    db.conn.execute(
                        "UPDATE transactions SET amount=?, fee=?, shares=? "
                        "WHERE holding_id=? AND kind='buy'",
                        (buy_amount, fee, shares, holding_id))
            params.append(holding_id)
            db.conn.execute(f"UPDATE holdings SET {', '.join(updates)} WHERE id=?", params)
            record(db, action="update",
                   label="修改持仓 ID=%d 的金额/日期" % holding_id,
                   spec=_spec, before=before)
    return True


def delete_holding(db: Database, holding_id: int) -> bool:
    """删除持仓，**连带删除对应流水，且两张表都备份**（口径缺陷②）。

    旧实现只备份 `holdings` 单表、且**不删** `transactions` —— 删除后流水变"孤儿"，
    既无法事后恢复、又污染复式记账恒等式。修复后：
      · 删除前把 `holdings` 行**和**该 `holding_id` 的 `transactions` 行都写进 backups/；
      · 删除时**连带删** `transactions`（不再留孤儿）。
    """
    row = db.conn.execute("SELECT * FROM holdings WHERE id=?", (holding_id,)).fetchone()
    if not row:
        return False
    txs = db.conn.execute(
        "SELECT * FROM transactions WHERE holding_id=?", (holding_id,)).fetchall()

    with _WRITE_LOCK:
        with db.immediate():
            spec = {"holdings": ("id = ?", (holding_id,)),
                    "transactions": ("holding_id = ?", (holding_id,))}
            before = snap(db, spec)                     # 写前快照（供撤销）
            db._backup_rows("holdings", [dict(row)])
            if txs:
                db._backup_rows("transactions", [dict(t) for t in txs])
            db.conn.execute("DELETE FROM transactions WHERE holding_id=?", (holding_id,))
            db.conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
            record(db, action="delete", label="删除持仓 ID=%d" % holding_id,
                   spec=spec, before=before)
    return True
