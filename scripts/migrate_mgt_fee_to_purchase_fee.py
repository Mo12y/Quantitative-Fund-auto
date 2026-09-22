#!/usr/bin/env python3
"""一次性迁移：把被误当成"管理费"的**手续费（申购费）**搬到 `purchase_fee`。

背景（见 docs/参照系接入执行报告 §8）：
`fund_info.mgt_fee` 里装的其实是天天基金宽表末列【手续费(申购费)】，
四重证据确证（官方文档×3 / A类0.15%·C类0.00% / 我们库A类94.1%有值·C类99.6%为空 /
反证：若为管理费 1.2、1.5 应占多数，实测仅 233 只）。

本脚本：
  1. **先整库快照**（铁律 1）
  2. `purchase_fee = mgt_fee`（仅在 purchase_fee 为空时）
  3. `mgt_fee = NULL`（所有现有值都是申购费，一个不留）
  4. 打印前后计数与抽样对照，供人工核对

用法:
    python scripts/migrate_mgt_fee_to_purchase_fee.py --dry-run   # 只看不写
    python scripts/migrate_mgt_fee_to_purchase_fee.py             # 执行
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "fund_quant.db")


def _ensure_schema():
    """先开一次 Database（会跑幂等的 ALTER 迁移建出 sales_service_fee），再关掉。

    否则裸 sqlite3 连上时该列还不存在 → no such column。"""
    from src.data.database import Database
    d = Database(DB)
    d.close()


def counts(conn):
    q = lambda s: conn.execute(s).fetchone()[0]
    return {
        "mgt_fee>0": q("SELECT COUNT(*) FROM fund_info WHERE mgt_fee>0"),
        "purchase_fee>0": q("SELECT COUNT(*) FROM fund_info WHERE purchase_fee>0"),
        "sales_service_fee>0": q("SELECT COUNT(*) FROM fund_info WHERE sales_service_fee>0"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    _ensure_schema()

    if args.dry_run:
        conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        print("【dry-run】当前状态:", counts(conn))
        print("  将执行: purchase_fee <- mgt_fee（仅 purchase_fee 为空的行）；随后 mgt_fee := NULL")
        n1 = conn.execute("SELECT COUNT(*) FROM fund_info WHERE mgt_fee>0 "
                          "AND (purchase_fee IS NULL OR purchase_fee=0)").fetchone()[0]
        print("  受影响(补 purchase_fee):", n1)
        print("  受影响(mgt_fee 置空)   :", counts(conn)["mgt_fee>0"])
        conn.close()
        return

    snap = os.path.join(ROOT, "data",
                        "_snapshot_%s_pre_fee_migration.db" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(DB, snap)
    print("① 快照:", snap, "%.1f MB" % (os.path.getsize(snap) / 1024 / 1024))

    conn = sqlite3.connect(DB)
    before = counts(conn)
    print("② 迁移前:", before)

    cur = conn.cursor()
    with conn:                                     # 一个事务，要么全成要么全滚
        n1 = cur.execute(
            "UPDATE fund_info SET purchase_fee = mgt_fee "
            "WHERE mgt_fee > 0 AND (purchase_fee IS NULL OR purchase_fee = 0)").rowcount
        # 连同 mgt_fee=0 一起清：按项目约定"数字字段 0 = 未采到"，而管理费不可能真为 0。
        # （首跑只清了 >0，残留 14,327 行 0 值 —— 已复核并补清。）
        n2 = cur.execute("UPDATE fund_info SET mgt_fee = NULL WHERE mgt_fee IS NOT NULL").rowcount
    print("③ 迁移: purchase_fee 补 %d 行；mgt_fee 置空 %d 行" % (n1, n2))

    after = counts(conn)
    print("④ 迁移后:", after)
    print()
    print("⑤ 抽样对照（前 5 只）:")
    for r in conn.execute("SELECT fund_code, fund_name, fund_type, mgt_fee, purchase_fee, "
                          "custodian_fee, sales_service_fee FROM fund_info "
                          "WHERE purchase_fee>0 ORDER BY fund_code LIMIT 5"):
        print("   ", r)
    print()
    print("⑥ 残留检查（mgt_fee 应全空）:", conn.execute(
        "SELECT COUNT(*) FROM fund_info WHERE mgt_fee IS NOT NULL").fetchone()[0])
    conn.close()
    print()
    print("回滚命令: copy data\\%s data\\fund_quant.db" % os.path.basename(snap))


if __name__ == "__main__":
    main()
