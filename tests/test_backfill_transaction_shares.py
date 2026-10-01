"""回填脚本测试（`scripts/backfill_transaction_shares.py`）——
买入 shares 复算（部分卖出摊薄批次 / 清仓批次）、sell_amount 复算、算不出来不猜、幂等。

口径（与脚本 docstring 同一份）：
    买入 shares = 批次当前份额 + 批次全部卖出份额（= 原始申购份额）；
    sold 批次 sell_amount = Σ(卖出流水 shares × confirm_nav)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backfill_transaction_shares as bf  # noqa: E402
from src.data.database import Database  # noqa: E402


def _mk(tmp_path):
    return Database(str(tmp_path / "ledger.db"))


def _hold(db, **kw):
    cols = dict(fund_code="X1", fund_name="甲", buy_date="2026-09-01", buy_amount=200.0,
                shares=52.47, status="holding", confirm_nav=1.9144, confirm_date="2026-09-02",
                accrual_start="2026-09-03", dividend_policy="reinvest", cash_balance=0.0)
    cols.update(kw)
    cur = db.conn.execute("INSERT INTO holdings (%s) VALUES (%s)"
                          % (",".join(cols), ",".join("?" * len(cols))), list(cols.values()))
    db.conn.commit()
    return cur.lastrowid


def _tx(db, **kw):
    cols = dict(holding_id=None, fund_code="X1", kind="buy", apply_date="2026-09-01",
                confirm_date="2026-09-02", confirm_nav=1.9144, shares=0.0, amount=200.0,
                fee=0.0, status="confirmed")
    cols.update(kw)
    cur = db.conn.execute("INSERT INTO transactions (%s) VALUES (%s)"
                          % (",".join(cols), ",".join("?" * len(cols))), list(cols.values()))
    db.conn.commit()
    return cur.lastrowid


class TestPlanFixes:
    def test_partial_sell_lot_reconstructs_original_shares(self, tmp_path):
        """部分卖出（份额摊薄）：买入 shares = 现份额 + 已卖出份额。"""
        db = _mk(tmp_path)
        h = _hold(db, shares=52.47, buy_amount=100.45)
        _tx(db, holding_id=h, shares=0.0, amount=200.0)
        _tx(db, holding_id=h, kind="sell", shares=52.00, confirm_nav=1.94, amount=None)
        fixes, skipped = bf.plan_fixes(db.conn)
        assert skipped == [] and len(fixes) == 1
        table, _pk, field, _old, new, _note = fixes[0]
        assert (table, field, new) == ("transactions", "shares", 104.47)
        db.close()

    def test_fully_sold_lot_uses_sell_leg(self, tmp_path):
        """清仓批次（份额归 0）：只能靠卖出流水还原原始份额 + 补 sell_amount。"""
        db = _mk(tmp_path)
        h = _hold(db, shares=0.0, status="sold", sell_date="2026-09-16", sell_amount=None)
        _tx(db, holding_id=h, shares=0.0, amount=100.0, confirm_nav=1.502)
        _tx(db, holding_id=h, kind="sell", shares=66.58, confirm_nav=1.4259, amount=None)
        fixes, _ = bf.plan_fixes(db.conn)
        by_field = {f[2]: f for f in fixes}
        assert by_field["shares"][4] == 66.58
        assert by_field["sell_amount"][4] == round(66.58 * 1.4259, 2)
        db.close()

    def test_uncomputable_is_skipped_not_guessed(self, tmp_path):
        """无 holding_id / 无卖出流水可依 → 一律跳过（不猜）。"""
        db = _mk(tmp_path)
        _tx(db, holding_id=None, shares=0.0, amount=50.0)
        _hold(db, shares=0.0, status="sold", sell_amount=None, fund_code="X2")
        fixes, skipped = bf.plan_fixes(db.conn)
        assert fixes == [] and len(skipped) == 2
        db.close()

    def test_apply_is_idempotent_and_never_overwrites(self, tmp_path):
        """apply 后复跑 = 0 项；已有非零值绝不被动。"""
        db = _mk(tmp_path)
        h = _hold(db, shares=4.41, buy_amount=10.0)
        target = _tx(db, holding_id=h, shares=0.0, amount=10.0)
        other = _tx(db, holding_id=h, shares=99.0, amount=1.0)
        fixes, _ = bf.plan_fixes(db.conn)
        n = bf.apply_fixes(db.conn, fixes)
        assert n == 1
        assert db.conn.execute("SELECT shares FROM transactions WHERE id=?", (target,)).fetchone()[0] == 4.41
        assert db.conn.execute("SELECT shares FROM transactions WHERE id=?", (other,)).fetchone()[0] == 99.0
        rest, _ = bf.plan_fixes(db.conn)
        assert rest == [], "幂等：apply 后不再有待回填项"
        db.close()