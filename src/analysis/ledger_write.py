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

from typing import Optional

from src.analysis.portfolio import _WRITE_LOCK, purchase_fee_rate
from src.data.database import Database

__all__ = ["set_dividend_policy", "update_holding", "delete_holding"]


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
            db.conn.executemany(
                "UPDATE holdings SET dividend_policy=? WHERE id=?",
                [(policy, r["id"]) for r in rows])
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
            db._backup_rows("holdings", [dict(row)])
            if txs:
                db._backup_rows("transactions", [dict(t) for t in txs])
            db.conn.execute("DELETE FROM transactions WHERE holding_id=?", (holding_id,))
            db.conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
    return True
