#!/usr/bin/env python3
"""回填 `index_daily.pe_percentile` / `pb_percentile`（历史遗留：全表 15,215 行留了 0）。

背景见 `src/analysis/percentiles.py` 模块 docstring。分位是派生列，采集侧算不出来，
之前两处采集都硬编码 0；`0` 看起来像"PE 分位 0 = 极冷"，是典型静默误读。

用法：
    python scripts/backfill_index_percentiles.py           # 实际写库（按日期覆盖）
    python scripts/backfill_index_percentiles.py --dry-run # 只看会写多少行、不改库

⚠️ **写库前先整库快照**（铁律·写库先快照）：
    copy data\\fund_quant.db data\\_snapshot_<时间戳>.db
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.percentiles import recompute_index_percentiles  # noqa: E402
from src.data.database import Database  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")
INDEXES = ("000300", "000905", "000016")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("找不到账本库：%s" % DB)
    db = Database(DB)

    print("回填前（各指数分位列的 0 值 / 非 0 值 / 空值）：")
    for r in db.conn.execute(
        "SELECT index_code, SUM(pe_percentile=0), SUM(pe_percentile IS NULL),"
        " SUM(pb_percentile=0), SUM(pb_percentile IS NULL)"
        " FROM index_daily GROUP BY index_code ORDER BY index_code"):
        print("   %s: pe 0=%s NULL=%s ｜ pb 0=%s NULL=%s" % tuple(r))

    if args.dry_run:
        print("\n--dry-run：未写库。实际执行去掉该参数。")
        db.close()
        return 0

    print("\n回填中（按库内 pe/pb 用扩展窗口分位重算）…")
    for code in INDEXES:
        n = recompute_index_percentiles(db, code)
        print("   %s → 处理 %d 行" % (code, n))

    print("\n回填后：")
    for r in db.conn.execute(
        "SELECT index_code, ROUND(MIN(pe_percentile),1), ROUND(MAX(pe_percentile),1),"
        " ROUND(MIN(pb_percentile),1), ROUND(MAX(pb_percentile),1)"
        " FROM index_daily GROUP BY index_code ORDER BY index_code"):
        print("   %s: pe 分位 [%s, %s] ｜ pb 分位 [%s, %s]" % tuple(r))
    db.close()
    print("\n完成。建议立即跑 `pytest tests/test_percentiles.py -q` 与全量回归确认无回归。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
