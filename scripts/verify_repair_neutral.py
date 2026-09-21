# -*- coding: utf-8 -*-
"""
修复后行为中性验证（规则 15：完成即验证）
==========================================
断言：把哨兵 0.0 改成 NULL 之后，**SSOT 口径下**每条受影响基金的估值序列
      必须与修复前（快照）**逐点完全一致**。

因为 SSOT 用 `acc_nav IS NOT NULL AND acc_nav > 0` 判有效性，
NULL 与 0.0 同样无效 → 都应回退 unit_nav。若两者序列不一致，说明我的推断错了。
"""
import io
import os
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.analysis.nav_series import VALUATION_NAV_SQL  # noqa: E402

DB = os.path.join(ROOT, "data", "fund_quant.db")
SNAP = os.path.join(ROOT, "data", "_snapshot_20260921_092812_post_collect.db")

codes = [r[0] for r in sqlite3.connect(DB).execute(
    "select distinct fund_code from fund_nav where acc_nav is null or unit_nav is null")]
print("受影响基金（acc 或 unit 为 NULL）: %d 只" % len(codes))

SQL = f"""select nav_date, {VALUATION_NAV_SQL} from fund_nav
          where fund_code = ? order by nav_date"""

live = sqlite3.connect(DB)
snap = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)

diff_funds = []
same = 0
total_pts = 0
for fc in codes:
    a = snap.execute(SQL, (fc,)).fetchall()
    b = live.execute(SQL, (fc,)).fetchall()
    total_pts += len(b)
    if a != b:
        # 找出差异
        d = [(x, y) for x, y in zip(a, b) if x != y]
        diff_funds.append((fc, len(a), len(b), len(d), d[:2]))

print("比对点数: %d" % total_pts)
print()
if not diff_funds:
    print("✅ 全部 %d 只基金的 SSOT 估值序列 [修复前 == 修复后]，逐点一致" % len(codes))
    print("   → 修复是行为中性的，只影响踩了 COALESCE 陷阱的读者")
else:
    print("⚠️ 有 %d 只基金序列发生变化：" % len(diff_funds))
    for fc, na, nb, nd, s in diff_funds[:20]:
        print("   %s  快照=%d 点  现库=%d 点  差异=%d  %s" % (fc, na, nb, nd, s))

# 再验：全库有效估值点数 修复前 vs 修复后
CNT = f"select count(*) from fund_nav where {VALUATION_NAV_SQL} > 0"
ca = snap.execute(CNT).fetchone()[0]
cb = live.execute(CNT).fetchone()[0]
print("\n全库有效估值点数：快照(修前) %d | 现库(修后) %d | 差 %+d" % (ca, cb, cb - ca))
print("判定：", "✅ 一致" if ca == cb else "❌ 不一致")
