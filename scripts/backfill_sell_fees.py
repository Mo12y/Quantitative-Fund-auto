"""
回填历史卖出的赎回费（transactions.fee）。

背景：赎回费是批次二才加进系统的，之前成交的卖单 fee 全是 NULL/0，
所以"已实现收益"漏掉了这笔真实成本。本脚本按与实盘同一套口径补算：
    fee = 卖出毛额 × 费率,  费率 = 优先解析 fund_info.redeem_fee，否则监管下限 1.5%
    持有天数 = 赎回确认日 − 申购确认日（缺确认日的老记录退回申请日/买入日）

**只改 transactions.fee 一列**，不动份额/成本/状态，不动 holdings。

默认 dry-run 只打印。写库要显式 --apply，且请先整库快照。

用法：
    python scripts/backfill_sell_fees.py            # 只看改动
    python scripts/backfill_sell_fees.py --apply    # 写库
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "fund_quant.db")


def plan_changes(db):
    """算出每笔已确认卖单应有的 fee（不写库）"""
    from src.analysis.portfolio import PortfolioTracker
    tracker = PortfolioTracker(db)          # 只借用它的费率/持有天数口径
    rows = [dict(r) for r in db.conn.execute(
        "SELECT t.id, t.fund_code, t.confirm_date, t.confirm_nav, t.shares, t.fee, t.status, "
        "       h.confirm_date AS buy_confirm, h.apply_date AS buy_apply, h.buy_date, "
        "       h.fund_name "
        "FROM transactions t LEFT JOIN holdings h ON h.id = t.holding_id "
        "WHERE t.kind = 'sell' ORDER BY t.id")]
    out = []
    for r in rows:
        if r.get("status") != "confirmed":
            continue
        nav = float(r.get("confirm_nav") or 0)
        shares = float(r.get("shares") or 0)
        if nav <= 0 or shares <= 0:
            continue
        gross = round(shares * nav, 2)
        held = tracker._sell_held_days(
            {"confirm_date": r["buy_confirm"], "apply_date": r["buy_apply"],
             "buy_date": r["buy_date"]}, r["confirm_date"])
        rate = tracker._redeem_fee_rate(r["fund_code"], held)
        new_fee = round(gross * rate, 2)
        old_fee = round(float(r.get("fee") or 0), 2)
        out.append({"id": r["id"], "fund_code": r["fund_code"],
                    "fund_name": r.get("fund_name") or r["fund_code"],
                    "confirm_date": r["confirm_date"], "shares": shares, "nav": nav,
                    "gross": gross, "held_days": held, "rate": rate,
                    "old_fee": old_fee, "new_fee": new_fee,
                    "changed": abs(new_fee - old_fee) > 1e-9})
    return out


def main() -> int:
    apply_it = "--apply" in sys.argv
    if not os.path.exists(DB_PATH):
        print(f"未找到数据库: {DB_PATH}")
        return 1

    from src.data.database import Database
    db = Database(DB_PATH)
    changes = plan_changes(db)

    print("历史卖单赎回费回填方案" + ("（dry-run，只读）" if not apply_it else "（将写库）"))
    print("=" * 100)
    print(f"{'tx':>4} {'确认日':<11} {'基金':<26} {'份额':>9} {'毛额':>9} "
          f"{'持有':>5} {'费率':>6} {'旧费':>7} {'新费':>7}")
    print("-" * 100)
    for c in changes:
        mark = "  *" if c["changed"] else ""
        print(f"{c['id']:>4} {str(c['confirm_date']):<11} {(c['fund_name'] or '')[:24]:<26} "
              f"{c['shares']:>9.2f} {c['gross']:>9.2f} {c['held_days']:>5} "
              f"{c['rate']*100:>5.2f}% {c['old_fee']:>7.2f} {c['new_fee']:>7.2f}{mark}")
    todo = [c for c in changes if c["changed"]]
    delta = round(sum(c["new_fee"] - c["old_fee"] for c in todo), 2)
    print("-" * 100)
    print(f"需要改动 {len(todo)} 笔，赎回费合计 +{delta}（已实现收益会相应减少 {delta}）")

    if not apply_it:
        print()
        print("这是 dry-run，**没有写任何数据**。确认无误后（请先整库快照）：")
        print("    copy data\\fund_quant.db data\\backups\\fund_quant_<时间戳>.db")
        print("    python scripts/backfill_sell_fees.py --apply")
        db.close()
        return 0

    n = 0
    try:
        with db.immediate():
            for c in todo:
                db.conn.execute("UPDATE transactions SET fee = ? WHERE id = ?", (c["new_fee"], c["id"]))
                n += 1
    finally:
        db.close()
    print(f"已写入 {n} 笔（只改 transactions.fee）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
