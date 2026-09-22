#!/usr/bin/env python3
"""TER（总运作费率）分布审计 —— 按参照系分组看。

用途：A2 落地后核查
  1. TER 覆盖率（管理费/托管费/销售服务费 三项各多少）
  2. 按组的 TER 分位网格（P10/P50/P75/P90）—— 组内分位判定的依据
  3. **类型分层是否成立**（权益 ~1.3% vs 指数 ~0.2% vs 货币 ~0.6%）
     —— 若成立，就再次证明"单一绝对阈值跨类型无意义"（A1 原则）

只读。用法: python scripts/audit_fee_ter.py
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "scripts"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.analysis import fund_fee                    # noqa: E402
from src.analysis import peer_percentile as pp       # noqa: E402
from src.data.database import Database               # noqa: E402


def main():
    db = Database(os.path.join(ROOT, "data", "fund_quant.db"))
    rows = list(db.conn.execute(
        "SELECT fund_code, fund_type, mgt_fee, custodian_fee, sales_service_fee FROM fund_info"))
    db.close()

    print("=" * 78)
    print("TER（总运作费率）分布审计 —— 口径: 管理费 + 托管费 + 销售服务费")
    print("=" * 78)
    print()
    print("[1] 字段覆盖率（分母 = fund_info 全部 %d 只）" % len(rows))
    for f in ("mgt_fee", "custodian_fee", "sales_service_fee"):
        n = sum(1 for r in rows if fund_fee._num(r[{"mgt_fee": 2, "custodian_fee": 3,
                                                    "sales_service_fee": 4}[f]]) is not None)
        print("   %-18s %6d  (%.1f%%)" % (f, n, n / len(rows) * 100))

    by_group = {}
    incomplete = 0
    unmapped = 0
    for code, ftype, m, c, s in rows:
        ter, missing = fund_fee.compute_ter(
            {"mgt_fee": m, "custodian_fee": c, "sales_service_fee": s})
        if ter is None:
            incomplete += 1
            continue
        g = pp.group_of(ftype)
        if not g:
            unmapped += 1
            continue
        by_group.setdefault(g, []).append(ter)

    total_ter = sum(len(v) for v in by_group.values())
    print()
    print("[2] TER 可算且类型可映射: %d 只  |  TER 不可算(缺必收项): %d  |  类型未映射: %d"
          % (total_ter, incomplete, unmapped))
    if not total_ter:
        print()
        print("   ⚠️ 还没有可算的 TER —— 先跑 `python src/main.py fees` 补采真费率。")
        return

    print()
    print("[3] 各组 TER 分位（%s）" % "口径见 fund_fee")
    print("   %-14s %6s %7s %7s %7s %7s %7s" % ("组", "n", "P10", "P50", "P75", "P90", "max"))
    for g in pp.group_names():
        a = np.array(sorted(by_group.get(g, [])))
        if a.size == 0:
            print("   %-14s %6d  （无样本）" % (g, 0))
            continue
        print("   %-14s %6d %6.2f%% %6.2f%% %6.2f%% %6.2f%% %6.2f%%" % (
            g, a.size, np.percentile(a, 10), np.percentile(a, 50),
            np.percentile(a, 75), np.percentile(a, 90), a.max()))

    print()
    print("[4] 结论检查：类型分层是否成立？")
    medians = {g: float(np.median(v)) for g, v in by_group.items() if len(v) >= 30}
    if len(medians) >= 2:
        lo = min(medians.values())
        hi = max(medians.values())
        print("   各组中位 TER 的最小/最大 = %.2f%% / %.2f%%（差 %.1f 倍）"
              % (lo, hi, hi / lo if lo else float("inf")))
        print("   → %s" % ("分层明显：**单一绝对阈值跨类型无意义**（支持 A1 的『相对→组内分位』）"
                          if hi > 2 * lo else "分层不明显，可再讨论"))
    print()
    print("注：样本不足的组（n<30）在 peer_percentile.grade() 里会判为 insufficient，")
    print("    费率检查会**如实声明**而不是硬判 —— 补采完成后重跑本脚本即可。")


if __name__ == "__main__":
    main()
