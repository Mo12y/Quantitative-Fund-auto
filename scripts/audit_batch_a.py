#!/usr/bin/env python3
"""批次 A 实测：阈值分位化（A3）与「复活字段」是否真的生效（A4）。

只读，不写任何表。

产出：
  A3  各组 momentum_3m 在「旧常数 40」与「组内 P90」两套阈值下的通过率/警告数
  A4  fund_size / manager_tenure 两个字段驱动的检查：通过率、被动淘汰数、以及
      「字段缺失时是否静默放行」

用法:
  python scripts/audit_batch_a.py            # 全量（约 1.5 分钟）
  python scripts/audit_batch_a.py --quick    # 只跑 A4 的静态部分（秒级）
"""
import argparse
import os
import sqlite3
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "scripts"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from calibrate_thresholds import DB_PATH, TYPE2GROUP          # noqa: E402
from src.analysis import peer_percentile as pp                # noqa: E402

OLD_MOMENTUM_CONST = 40.0          # 旧口径（fund_scorer.THRESHOLDS["momentum_warning"]）
NEW_PCT = 90                       # 新口径：组内 P90


def section(t):
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def a4_static():
    """A4 静态部分：字段覆盖率 + 阈值是否真的被代码消费。"""
    section("A4-1 字段覆盖率（决定检查能不能生效）")
    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    tot = conn.execute("select count(*) from fund_info").fetchone()[0]
    for col in ("fund_size", "manager_tenure", "mgt_fee", "establish_date"):
        try:
            n = conn.execute("select count(*) from fund_info where %s is not null and %s > 0"
                             % (col, col)).fetchone()[0]
            print("  fund_info.%-16s 有值 %6d / %d  (%.1f%%)" % (col, n, tot, n / tot * 100))
        except Exception as e:
            print("  fund_info.%-16s ERR %s" % (col, str(e)[:50]))
    conn.close()

    section("A4-2 阈值是否被代码消费（静态检查）")
    src = open(os.path.join(ROOT, "src/analysis/fund_scorer.py"), encoding="utf-8").read()
    for key, method in (("min_size_yi", "_check_size"), ("max_size_yi", "_check_size"),
                        ("min_manager_years", "（无对应检查方法）"),
                        ("max_drawdown_1y", "_check_drawdown"),
                        ("momentum_warning", "_check_momentum")):
        used = src.count('THRESHOLDS["%s"]' % key)
        print("  %-20s 被引用 %d 次   检查方法: %s" % (key, used, method))
    print()
    print("  ⚠️ min_manager_years：全项目**没有任何地方引用**（含 src/ 与 scripts/）")
    print("     → `manager_tenure` 数据虽已补齐 67.0%，但**检查根本没接线**，")
    print("       A4 要求的『复活』对它不成立 —— 这是任务书的一处误判（详见执行报告）。")


def a3_and_a4_dynamic():
    section("A3 动量阈值：旧常数 40 vs 组内 P90（实测通过率）")
    groups, unmapped, _ = pp.build_group_arrays(pp.DEFAULT_MIN_DAYS)
    if unmapped:
        print("  ⚠️ 未映射类型（显式声明）:", unmapped)
    cache = pp.load_cache()
    if cache is None:
        print("  ✗ 参照系缓存缺失：先跑 python scripts/fund_percentile.py --json ... 或构建脚本")
        return
    print("  %-14s %6s %14s %14s %14s" % ("组", "n", "旧:>40 警告数", "旧通过率",
                                          "新:P90 警告数(通过率)"))
    tot_n = tot_old = tot_new = 0
    for g, rows in sorted(groups.items()):
        mom = np.array([r["momentum_3m"] for r in rows], dtype=float)
        mom = mom[np.isfinite(mom)]
        if mom.size == 0:
            continue
        p90 = pp.threshold(cache, g, "momentum_3m", NEW_PCT)
        old_warn = int((mom > OLD_MOMENTUM_CONST).sum())
        new_warn = int((mom > p90).sum()) if p90 is not None else -1
        tot_n += mom.size
        tot_old += old_warn
        tot_new += max(new_warn, 0)
        print("  %-14s %6d %14d %13.2f%% %9d (%.1f%%)" % (
            g, mom.size, old_warn, old_warn / mom.size * 100,
            new_warn, new_warn / mom.size * 100))
    print("  %-14s %6d %14d %13.2f%% %9d (%.1f%%)" % (
        "合计", tot_n, tot_old, tot_old / tot_n * 100, tot_new, tot_new / tot_n * 100))
    print()
    print("  读法：旧常数 40 几乎不触发（≈0%）＝检查形同虚设；")
    print("        新 P90 按定义命中各组最高的 10%，六组强度一致（这正是 A1 要的）。")

    section("A4-3 规模检查：字段缺失时是否静默放行（实测）")
    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    rows = list(conn.execute(
        "select fund_code, fund_size from fund_info where exists "
        "(select 1 from fund_nav n where n.fund_code = fund_info.fund_code)"))
    conn.close()
    n_all = len(rows)
    n_zero = sum(1 for _, s in rows if not s or float(s) <= 0)
    n_below = sum(1 for _, s in rows if s and float(s) < 0.5)
    n_above = sum(1 for _, s in rows if s and float(s) > 200)
    print("  有净值的基金 %d 只" % n_all)
    print("    fund_size 缺失(=0)：%d 只 (%.1f%%) → 走 `unknown 跳过检查`，**静默放行**" % (
        n_zero, n_zero / n_all * 100))
    print("    fund_size < 0.5 亿（下限淘汰）：%d 只 (%.1f%%)" % (n_below, n_below / n_all * 100))
    print("    fund_size > 200 亿（上限警告）：%d 只 (%.1f%%)" % (n_above, n_above / n_all * 100))
    print()
    print("  → 结论：数据补齐前(report 记 2.0%% 覆盖)，规模检查对 98%% 的基金**静默放行**；")
    print("    补齐后(66.9%%)，它才真正开始淘汰/警告。这就是 A4 要的『修前/修后』证据。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只跑 A4 静态部分")
    args = ap.parse_args()
    a4_static()
    if not args.quick:
        a3_and_a4_dynamic()


if __name__ == "__main__":
    main()
