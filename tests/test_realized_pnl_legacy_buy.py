"""回归：买入流水 shares=0 的老 lot 不得从「已实现收益」里消失。

背景（2026-09-22 实测）：
- `reconcile()` 结算"下单时净值未公布"的买入时，历史实现**只回填持仓的 shares，
  没回填买入流水的 shares**，留下 0.0；
- `get_realized_pnl()` 原来用 `sum(buy 流水的 shares)`，遇 0 就 `continue` →
  **整只 lot 被跳过**，它的卖出从已实现收益里静默消失。
- 实测：库里 14 只 lot 受影响，其中 2 只已卖出 → 毛额 **¥145.61** 凭空消失。

修复：① 读路径回退 `round(金额 / 确认净值, 2)`（不改写历史）；② 写路径在 reconcile
结算时一并回填买入流水的 shares；③ 卖出成本加"不得超过该 lot 总成本"的护栏。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis.portfolio import PortfolioTracker
from src.data.database import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def _seed_legacy_lot(db, code="999001", amount=50.0, buy_nav=1.1521, shares=43.40,
                     tx_nav=None):
    """造一只"买入流水 shares=0"的老 lot（模拟 reconcile 漏回填的后果）。

    `tx_nav` 可单独指定买入流水上的 confirm_nav —— 真实库 #43 就是"持仓已被修正为
    1.1521、但买入流水还留着旧值 1.1715"的不一致状态。
    """
    hid = db.add_holding({
        "fund_code": code, "fund_name": "测试基金C", "buy_date": "2026-09-09",
        "buy_amount": amount, "buy_nav": buy_nav, "shares": shares,
        "apply_date": "2026-09-09", "confirm_date": "2026-09-11", "status": "holding",
    })
    db.add_transaction({
        "holding_id": hid, "fund_code": code, "kind": "buy", "apply_date": "2026-09-09",
        "confirm_date": "2026-09-11", "confirm_nav": (tx_nav if tx_nav is not None else buy_nav),
        "shares": 0.0, "amount": amount, "fee": 0.0,
        "status": "confirmed",          # ← 关键：状态 confirmed 但 shares=0
    })
    return hid


def _sell(db, hid, code, shares, nav, fee=0.0):
    db.add_transaction({
        "holding_id": hid, "fund_code": code, "kind": "sell", "apply_date": "2026-09-18",
        "confirm_date": "2026-09-22", "confirm_nav": nav, "shares": shares,
        "amount": None, "fee": fee, "status": "confirmed",
    })


class TestLegacyBuySharesZero:
    def test_sell_of_zero_share_buy_lot_is_counted(self, db):
        hid = _seed_legacy_lot(db)
        _sell(db, hid, "999001", shares=43.40, nav=1.1676)
        rz = PortfolioTracker(db).get_realized_pnl()
        assert rz["count"] == 1, "买入流水 shares=0 的 lot 的卖出被整只跳过了"
        s = rz["sales"][0]
        assert s["holding_id"] == hid
        assert s["gross"] == pytest.approx(50.67, abs=0.01)

    def test_missing_sell_is_not_silently_dropped_from_totals(self, db):
        hid = _seed_legacy_lot(db)
        _sell(db, hid, "999001", shares=43.40, nav=1.1676)
        rz = PortfolioTracker(db).get_realized_pnl()
        assert rz["total_gross"] == pytest.approx(50.67, abs=0.01), "毛额没算进去"
        assert rz["total_pnl"] != 0.0

    def test_cost_never_exceeds_lot_cost(self, db):
        """卖出份额 > 推导份额时（历史数据不一致），成本不得被摊超实付额。"""
        # 持仓 buy_nav=1.1521 但买入流水 confirm_nav 是旧的 1.1715 →
        # 推导份额 = 50/1.1715 = 42.68 < 实际卖出 43.40（真实库 #43 的样子）
        hid = _seed_legacy_lot(db, amount=50.0, buy_nav=1.1521, shares=43.40,
                               tx_nav=1.1715)
        _sell(db, hid, "999001", shares=43.40, nav=1.1676)
        rz = PortfolioTracker(db).get_realized_pnl()
        s = rz["sales"][0]
        assert s["cost"] <= 50.0 + 1e-6, "卖出成本超过了该 lot 的总成本"
        assert s["cost"] == pytest.approx(50.0, abs=0.01)
        assert s["pnl"] == pytest.approx(0.67, abs=0.01)   # 实付 50、卖出 50.67

    def test_normal_lot_unaffected(self, db):
        """正常 lot（买入流水有 shares）行为不变。"""
        hid = db.add_holding({
            "fund_code": "999002", "fund_name": "正常基金C", "buy_date": "2026-09-09",
            "buy_amount": 100.0, "buy_nav": 2.0, "shares": 50.0,
            "apply_date": "2026-09-09", "confirm_date": "2026-09-11", "status": "holding",
        })
        db.add_transaction({"holding_id": hid, "fund_code": "999002", "kind": "buy",
                            "apply_date": "2026-09-09", "confirm_date": "2026-09-11",
                            "shares": 50.0, "amount": 100.0, "fee": 0.0, "status": "confirmed"})
        _sell(db, hid, "999002", shares=50.0, nav=2.5)
        rz = PortfolioTracker(db).get_realized_pnl()
        assert rz["sales"][0]["pnl"] == pytest.approx(25.0, abs=0.01)


class TestReconcileBackfillsBuyShares:
    def test_reconcile_writes_shares_into_buy_transaction(self, db):
        """reconcile 结算待确认买入时，必须把 shares 一并写回买入流水。"""
        db.insert_nav_batch([("999003", "2026-09-18", 2.0, 2.0, 0.0)])
        pt = PortfolioTracker(db)
        pt.add_buy_transaction("999003", "测试基金C", "2026-09-18", 100.0,
                               after_cutoff=False)
        # 直接以"确认日已到"的 now 结算
        pt.reconcile(today="2026-12-31")
        rows = list(db.conn.execute(
            "SELECT shares, status, confirm_nav FROM transactions "
            "WHERE fund_code='999003' AND kind='buy'"))
        assert rows, "没有买入流水"
        sh, st, nav = rows[0]
        assert st == "confirmed"
        assert float(sh or 0) > 0, "reconcile 结算后买入流水的 shares 仍是 0（会漏掉未来的卖出）"
        assert float(sh) == pytest.approx(50.0, abs=0.01)
