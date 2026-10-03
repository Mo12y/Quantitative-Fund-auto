# -*- coding: utf-8 -*-
"""刷新基金指标物化表（E2 / 分层宽表的 DWS 层）。**写库**。

为什么需要它
------------
`peer_percentile` / `fund_scorer` / `drawdown_warning` 原先**各自**从 `fund_nav`
（2,290 万行）拉数据、各自算一遍指标 —— 同一只基金的夏普一天可能被算 3 次。

本脚本把"每只基金 × 最新净值日"的全部指标**物化**一次，之后**增量刷新**：
只有 `MAX(nav_date)` 前进过的基金才重算。

用法
----
    python scripts/refresh_fund_metrics.py --dry-run          # 只看有多少要刷新
    python scripts/refresh_fund_metrics.py --limit 200        # 小规模试跑
    python scripts/refresh_fund_metrics.py                    # 增量全量（首次较慢）
    python scripts/refresh_fund_metrics.py --codes 007029,016453 --force

纪律
----
· **写库前必须已有整库快照**（脚本会检查 `data/_snapshot_*.db` 是否存在）
· 指标口径**不重写**，一律委托 `nav_metrics.compute`（SSOT）
· 幂等：`INSERT OR REPLACE`，重复跑不会产生重复行
"""
import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import fund_metrics_store as ms  # noqa: E402
from src.data.database import Database  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")


def newest_snapshot():
    files = glob.glob(os.path.join(ROOT, "data", "_snapshot_*.db"))
    return max(files, key=os.path.getmtime) if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="只刷这些（逗号分隔）")
    ap.add_argument("--limit", type=int, default=0, help="最多刷 N 只（试跑用）")
    ap.add_argument("--force", action="store_true", help="忽略 asof 重算")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    ap.add_argument("--allow-no-snapshot", action="store_true",
                    help="没有快照也继续（**不推荐**）")
    args = ap.parse_args()

    snap = newest_snapshot()
    if not args.dry_run and not snap and not args.allow_no_snapshot:
        print("❌ 未发现整库快照（data/_snapshot_*.db）。")
        print("   本脚本会写 fund_metrics 表 —— 按项目铁律「写库先快照」，请先整库备份。")
        print("   （见 AGENTS.md §三·铁律；或加 --allow-no-snapshot 跳过检查，**不推荐**）")
        return 2
    if snap:
        print("✓ 快照存在：%s" % os.path.basename(snap))

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or None
    db = Database(DB)
    try:
        ms.ensure_table(db)
        before = ms.stats(db)
        stale = ms.stale_codes(db, codes)
        if args.limit:
            stale = stale[:args.limit]
        print("物化表现有 %s 行（asof 最新 %s）" % (before["rows"], before["asof_max"]))
        print("待刷新 %d 只%s" % (len(stale), "（已按 --limit 截断）" if args.limit else ""))
        if args.dry_run:
            print("--dry-run：不写库，结束。")
            return 0
        if not stale and not args.force:
            print("无待刷新（增量语义：净值日期没前进就不重算）")
            return 0

        t0 = time.time()
        last = [0]

        def _p(done, total):
            if done - last[0] >= 400:
                last[0] = done
                el = time.time() - t0
                print("   %d/%d  %.1fs  (%.0f 只/秒)" % (done, total, el, done / max(el, 1e-9)))

        # ⚠️ 必须传**截断后的 stale**，不能传原始 `codes`：
        # 传 `codes=None` 时 refresh 内部会重新调 stale_codes() 拿回**全量**列表，
        # `--limit` 就形同虚设（2026-10-03 实测踩到：本意试跑 200 只，实际跑了 24,059 只）。
        r = ms.refresh(db, codes=stale, force=args.force, progress=_p)
        after = ms.stats(db)
        print()
        print("刷新完成：扫描 %d，写入 %d，失败 %d，用时 %.1fs"
              % (r["scanned"], r["refreshed"], r["failed"], r["seconds"]))
        print("物化表：%s 行 → %s 行（asof 最新 %s）"
              % (before["rows"], after["rows"], after["asof_max"]))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
