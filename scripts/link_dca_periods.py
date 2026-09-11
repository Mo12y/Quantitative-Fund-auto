"""
给历史定投期次补上买入凭证（dca_periods.holding_id）。

背景（决策 B）：`executed` 现在必须由买入凭证派生 —— 只有 holding_id 非空的期次
才算真的投过。但历史期次是旧版写进去的，holding_id 全为空，凭证只能按
`planned_date == holdings.buy_date` 且同 fund_code 配对。

默认 **dry-run，只报告不写库**。写库是对既有记录的批量 UPDATE，必须显式 --apply。

用法：
    python scripts/link_dca_periods.py            # 只看方案（只读）
    python scripts/link_dca_periods.py --apply    # 真正写库（请先整库快照）
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.analysis.dca import match_periods_to_holdings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "fund_quant.db")


def report(conn) -> tuple:
    """只读：算出补链方案"""
    plans = [dict(r) for r in conn.execute("SELECT * FROM dca_plans ORDER BY id")]
    all_links, all_amb = [], []
    for plan in plans:
        holds = [dict(r) for r in conn.execute(
            "SELECT id, fund_code, buy_date, status FROM holdings WHERE fund_code = ?",
            (plan["fund_code"],))]
        periods = [dict(r) for r in conn.execute(
            "SELECT * FROM dca_periods WHERE plan_id = ? ORDER BY period_no", (plan["id"],))]
        m = match_periods_to_holdings(periods, holds)
        for l in m["links"]:
            all_links.append({**l, "plan_id": plan["id"],
                              "fund_name": plan.get("fund_name")})
        for a in m["ambiguous"]:
            all_amb.append({**a, "plan_id": plan["id"]})
    return all_links, all_amb


def main() -> int:
    apply_it = "--apply" in sys.argv
    if not os.path.exists(DB_PATH):
        print(f"未找到数据库: {DB_PATH}")
        return 1

    # 方案计算一律走只读连接（mode=ro），保证 dry-run 绝对零写入
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    links, ambiguous = report(ro)
    ro.close()

    print("定投期次 ↔ 买入凭证 补链方案" + ("（dry-run，只读）" if not apply_it else "（将写库）"))
    print("=" * 78)
    if not links and not ambiguous:
        print("没有需要补链的期次（都已挂上凭证）。")
        return 0
    for l in links:
        print(f"  期次 #{l['period_no']:<3} {l['date']}  status={l['status']:<9} "
              f"→ holding_id={l['holding_id']}  ({l['fund_name']})")
    for a in ambiguous:
        print(f"  [歧义] 期次 #{a['period_no']} {a['date']} 同日多笔持仓 {a['candidates']} —— 跳过")
    print("-" * 78)
    print(f"可补链 {len(links)} 条，歧义跳过 {len(ambiguous)} 条。")

    if not apply_it:
        print()
        print("这是 dry-run，**没有写任何数据**。")
        print("确认无误后执行（请先整库快照）：")
        print("    copy data\\fund_quant.db data\\backups\\fund_quant_<时间戳>.db")
        print("    python scripts/link_dca_periods.py --apply")
        return 0

    # 写库路径：只补空值（link_dca_period 带 holding_id IS NULL 守卫，不会覆盖已有凭证）
    from src.data.database import Database
    db = Database(DB_PATH)
    n = 0
    for l in links:
        if db.link_dca_period(l["period_id"], l["holding_id"]):
            n += 1
    db.close()
    print(f"已写入 {n} 条（只补空值，未覆盖任何已有凭证）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
