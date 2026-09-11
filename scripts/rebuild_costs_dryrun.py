"""
只读 dry-run：如果按「申请日(T)净值」重算历史成本，会发生什么变化？

背景（决策 A）：新流水已改为按申请日/生效日(T)净值成交（符合公募规则），
但**历史 24 条持仓不回填** —— 它们仍是旧口径（用确认日 T+1 净值算的份额）。
本脚本只**输出对照表**，帮你评估"如果回溯重算会怎样"。

- **绝对只读**：用 sqlite 的 `mode=ro` 打开库，只 SELECT，不建表、不开 WAL、不写一个字节。
- **默认不执行任何修改**。要真正回填需要另行确认（且必须先整库快照）。

用法：
    python scripts/rebuild_costs_dryrun.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.analysis import trade_rules  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "fund_quant.db")


def _exact_nav(conn, code, date_str):
    row = conn.execute("SELECT unit_nav FROM fund_nav WHERE fund_code = ? AND nav_date = ?",
                       (code, date_str)).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"未找到数据库: {DB_PATH}")
        return 1
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cal = {r[0] for r in conn.execute(
        "SELECT trade_date FROM trade_calendar WHERE is_open = 1")}

    rows = [dict(r) for r in conn.execute("SELECT * FROM holdings ORDER BY id")]
    if not rows:
        print("库中没有持仓记录。")
        return 0

    print("按「申请日(T)净值」重算历史成本的对照表（只读，不改任何数据）")
    print("=" * 118)
    hdr = (f"{'ID':>4} {'代码':<8} {'申请日':<11} {'定价日':<11} {'金额':>9} "
           f"{'旧份额':>10} {'新份额':>10} {'旧净值':>9} {'新净值':>9} {'新-旧份额':>10}")
    print(hdr)
    print("-" * 118)

    cmp_cnt = missing = sold = 0
    moved = []
    for h in rows:
        apply_date = h.get("apply_date") or h["buy_date"]
        eff = trade_rules.effective_apply_date(apply_date, cal)
        new_nav = _exact_nav(conn, h["fund_code"], eff)
        old_nav = h.get("confirm_nav") or h.get("buy_nav")
        amount = float(h.get("buy_amount") or 0)
        old_sh = float(h.get("shares") or 0)
        is_sold = h.get("status") == "sold"
        label = "已卖" if is_sold else ""

        new_sh = round(amount / new_nav, 2) if new_nav else None
        if new_sh is None:
            dstr = "—"
            missing += 1
        elif is_sold or old_sh <= 0:
            # 已卖出/待确认的 lot 当前份额为 0，差额没有比较意义
            dstr = f"({new_sh:.2f} 若按T日)" if is_sold else f"({new_sh:.2f} 预估)"
            sold += 1 if is_sold else 0
        else:
            d = new_sh - old_sh
            dstr = f"{d:+.2f}"
            cmp_cnt += 1
            if abs(d) >= 0.05:
                moved.append((h["id"], h["fund_code"], round(old_sh, 2), round(new_sh, 2), round(d, 2)))

        print(f"{h['id']:>4} {h['fund_code']:<8} {str(apply_date):<11} {eff:<11} {amount:>9.2f} "
              f"{old_sh:>10.2f} {(f'{new_sh:.2f}' if new_sh is not None else '—'):>10} "
              f"{(f'{old_nav:.4f}' if old_nav else '—'):>9} "
              f"{(f'{new_nav:.4f}' if new_nav else '—'):>9} {dstr:>13} {label}")

    print("-" * 118)
    print(f"可直接比较(在持)的 lot: {cmp_cnt} 条；已卖出(份额已归零) {sold} 条；定价日净值缺失 {missing} 条")
    if moved:
        print()
        print("份额变化 ≥0.05 的 lot：")
        for hid, code, o, n, d in sorted(moved, key=lambda x: -abs(x[4])):
            print(f"  ID {hid:>3} {code}  {o:>9.2f} → {n:>9.2f}  ({d:+.2f} 份)")
    print()
    print("说明：")
    print("  · 差额来自「成交价取 T 日还是 T+1 日净值」，方向随行情随机，不是系统性偏差。")
    print("  · 回填会**改写既有持仓的份额与成本基数**，属于高风险操作 —— 需要先整库快照再执行。")
    print("  · 决策 A 已定：历史不回填，仅新流水用 T 日口径。本表仅用于评估。")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
