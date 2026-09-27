# -*- coding: utf-8 -*-
"""`fund_size` 口径自检（只读）—— 历史上曾混入「万元」口径的旧值。

背景（2026-09-27 实测发现并修复）
--------------------------------
库里曾混着两批来源**口径不同**的 `fund_size`：
  · 东财 F10「净资产规模」（阶段 1 采集，15,674 条）= **亿元**（单只基金规模，正确）
  · 更早的旧源 = **万元**，数值比 F10 大 **10000 倍**
决定性证据：`008872` 库 999.94 / F10 **0.1** → 比值 **9999.4**；
对照组 `001338` / `000033` / `001553` 的 库/F10 比值均为 **1.0**。
污染集中在**大值区**（旧源把"真实 0.05~1 亿"写成"500~10000"）：共 214 条（>500 亿），已重采修复。

本脚本用于**回归检查**与将来扩采后的自检。

用法
----
    python scripts/check_fund_size_scale.py

判据
----
- 全库 `fund_size > 1000 亿` 应为 **0 条**（非货币基金罕见此量级；货币基金基本不在筛选域）；
- 若发现大值，先抽样与 F10 真值比一比（比值≈10000 即口径事故），再重采：
  `python scripts/collect_fund_jbgk.py --codes <列表> --workers 2`
"""
import os
import statistics
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "fund_quant.db")

ALERT_YI = 1000.0        # 超过这个值就该人工看一眼


def main() -> int:
    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT fund_code, fund_name, fund_size,"
        " (SELECT COUNT(*) FROM fund_nav n WHERE n.fund_code = f.fund_code) pts,"
        " COALESCE(NULLIF(purchase_status,''),'') ps FROM fund_info f WHERE fund_size > 0")]
    conn.close()

    vals = sorted(r["fund_size"] for r in rows)
    n = len(vals)
    print("有正值 fund_size: %d 条" % n)
    print("  中位 %.2f 亿 | P90 %.2f | P99 %.2f | 最大 %.2f" % (
        statistics.median(vals), vals[int(n * 0.9)], vals[int(n * 0.99)], vals[-1]))

    bad = [r for r in rows if r["fund_size"] > ALERT_YI]
    if not bad:
        print("✅ 无 %g 亿以上的异常值 —— 口径正常" % ALERT_YI)
        return 0

    print("\n⚠️ 发现 %d 条 > %g 亿，请人工核对（与 F10 比值≈10000 即『万元口径』污染）：" % (len(bad), ALERT_YI))
    for r in sorted(bad, key=lambda x: -x["fund_size"])[:20]:
        in_scope = "筛选域内" if (r["pts"] >= 252 and "开放" in r["ps"]) else "域外"
        print("   %-8s %-26s %10.2f  %s" % (r["fund_code"], str(r["fund_name"])[:24],
                                            r["fund_size"], in_scope))
    print("\n修复：python scripts/collect_fund_jbgk.py --codes <逗号分隔> --workers 2")
    return 1


if __name__ == "__main__":
    sys.exit(main())
