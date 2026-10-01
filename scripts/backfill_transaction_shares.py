# -*- coding: utf-8 -*-
"""回填 `transactions.shares`（买入缺 12 条）与 `holdings.sell_amount`（sold 缺 1 条）。

背景（2026-10-01 审计第三批 §2）：补录/导入流水时只写了金额没写份额 —— 12 条买入流水
`shares=0`（占买入额 28%），导致复式记账恒等式 7 只基金不平。**目前是潜在债不是活 bug**
（全项目以 holdings 为持仓真源，账户数字没受影响），但任何人下次"从流水重算持仓"
都会错 28% 且看不出来。

口径（每一条都能复算，**只补缺失、绝不改任何已有值**）：

- **买入 shares = 该批次（holding_id）当前份额 + 该批次全部卖出份额之和**（= 原始申购份额）。
  对部分卖出（份额被摊薄）与清仓（份额已归 0）两种批次都成立 ——
  实测 12/12 与 `amount ÷ confirm_nav` 一致（唯一例外 025833 流水 nav 记 1.1715，
  但批次与卖出两侧都指向 1.1521，属 nav 记录问题，**不在本脚本范围**）。
- **sold 批次 sell_amount = 该批次卖出流水的 `Σ(shares × confirm_nav)`**。
- 算不出来（无 holding_id / 复算结果 ≤0 / 无可用卖出流水）→ **不猜**，跳过并逐条报告。

用法：
    python scripts/backfill_transaction_shares.py            # dry-run（默认，只读）
    python scripts/backfill_transaction_shares.py --apply    # 写库（须先整库快照）

⚠️ **写库前先整库快照**（铁律·写库先快照）：
    copy data\\fund_quant.db data\\_snapshot_<时间戳>.db
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import Database  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")


def plan_fixes(conn) -> tuple:
    """只读：算出全部待回填项 → `(fixes, skipped)`。

    fixes   = [(table, id, field, old, new, note)]；skipped = [(table, id, 原因)]（不猜）。
    """
    fixes, skipped = [], []

    # ① 买入流水缺 shares
    for tx_id, fc, hid, amount, nav in conn.execute(
            "SELECT id, fund_code, holding_id, amount, confirm_nav FROM transactions"
            " WHERE kind='buy' AND (shares IS NULL OR shares=0) AND COALESCE(amount,0)>0"
            " ORDER BY id"):
        if not hid:
            skipped.append(("transactions", tx_id, "无 holding_id，无法定位批次（不猜）"))
            continue
        h = conn.execute("SELECT shares FROM holdings WHERE id=?", (hid,)).fetchone()
        if h is None:
            skipped.append(("transactions", tx_id, "holding_id=%s 批次不存在" % hid))
            continue
        seg = conn.execute("SELECT COALESCE(SUM(shares),0) FROM transactions"
                           " WHERE holding_id=? AND kind='sell'", (hid,)).fetchone()[0]
        cur, sold = float(h[0] or 0), float(seg or 0)
        new = round(cur + sold, 2)
        if new <= 0:
            skipped.append(("transactions", tx_id,
                            "复算份额 %.2f ≤ 0（批次现份额 %.2f + 卖出 %.2f）" % (new, cur, sold)))
            continue
        implied = (float(amount) / new) if new else 0.0
        fixes.append(("transactions", tx_id, "shares", 0.0, new,
                      "%s 批次#%s：现份额 %.2f + 卖出 %.2f ｜ 隐含净值 %.4f"
                      % (fc, hid, cur, sold, implied)))

    # ② sold 批次缺 sell_amount
    for hid, fc in conn.execute("SELECT id, fund_code FROM holdings"
                                " WHERE status='sold' AND sell_amount IS NULL ORDER BY id"):
        rows = list(conn.execute("SELECT shares, confirm_nav FROM transactions"
                                 " WHERE holding_id=? AND kind='sell'", (hid,)))
        if not rows:
            skipped.append(("holdings", hid, "无卖出流水可依（不猜）"))
            continue
        if any(s is None or n is None for s, n in rows):
            skipped.append(("holdings", hid, "卖出流水 shares/confirm_nav 有缺失（不猜）"))
            continue
        amt = round(sum(float(s) * float(n) for s, n in rows), 2)
        if amt <= 0:
            skipped.append(("holdings", hid, "复算金额 %.2f ≤ 0" % amt))
            continue
        fixes.append(("holdings", hid, "sell_amount", None, amt,
                      "%s 卖出 %d 笔合计（%.2f 份）"
                      % (fc, len(rows), sum(float(s) for s, _ in rows))))
    return fixes, skipped


def apply_fixes(conn, fixes) -> int:
    """写库：**WHERE 带「字段仍缺失」守卫** —— 幂等、且永不覆盖已有值。返回实际写入行数。"""
    n = 0
    cur = conn.cursor()
    for table, pk, field, _old, new, _note in fixes:
        cur.execute("UPDATE %s SET %s=? WHERE id=? AND (%s IS NULL OR %s=0)"
                    % (table, field, field, field), (new, pk))
        if cur.rowcount > 0:
            n += cur.rowcount
    conn.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run 只读）")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("找不到账本库：%s" % DB)
    db = Database(DB)
    c = db.conn

    fixes, skipped = plan_fixes(c)
    print("=" * 96)
    print("待回填 %d 项：" % len(fixes))
    print("=" * 96)
    for table, pk, field, old, new, note in fixes:
        print("  %-12s #%-3s %-12s %s → %s ｜ %s" % (table, pk, field, old, new, note))
    if skipped:
        print()
        print("跳过 %d 项（算不出来就不猜）：" % len(skipped))
        for table, pk, why in skipped:
            print("  %-12s #%-3s %s" % (table, pk, why))

    if not args.apply:
        print()
        print("--dry-run：未写库。实际执行加 --apply（写前须整库快照，见模块 docstring）。")
        db.close()
        return 0

    n = apply_fixes(c, fixes)
    print()
    print("已写入 %d 行。回滚：copy data\\_snapshot_<时间戳>.db data\\fund_quant.db" % n)

    rest, _ = plan_fixes(c)
    print("复跑：剩余待回填 %d 项（应为 0）" % len(rest))
    db.close()
    print()
    print("建议立即跑 `python scripts/check_ledger_invariants.py`（恒等式应「不平 0 只」）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())