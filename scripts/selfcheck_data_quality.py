# -*- coding: utf-8 -*-
"""
数据质量抽检（规则 7 地基验证）v2
==================================
目的：全量采集「成功 18,627 只」只证明了"能采到"，没证明"采到的对不对"。
本脚本给出可检验的地基结论，任一 FATAL 项失败则 ②③④ 的结论都不可信。

────────────────────────────────────────────────────────────────
v1 → v2 修订记录（2026-09-21）：v1 的三个「致命」里有两个是**我自己的错误假设**，
自检脚本喊狼来了比没有自检更糟，故重写判定逻辑。
  ✗ v1-A1「acc_nav >= unit_nav 处处成立」—— **错**。反例是真实存在的正常数据：
        · 份额折算/反向拆分过的基金（如 161810 银华内需精选：unit 4.735 / acc 4.502）
        · 货币型基金按每百份报价（如 160641 鹏华丰锐债券LOF：unit 105.02 / acc 1.5135）
        实测 129,523 行"违反"，全部是这两类，**不是数据错误**。
  ✗ v1-A2「daily_return 应与 acc_nav 变化吻合」—— **错**。源字段 equityReturn 本来就是
        **单位净值涨跌幅**（与 unit_nav 吻合 99.31%），不是分红调整后的收益。
        此事实早已被 src/analysis/vol_predictor.py:42 记录并绕开（"daily_return 列实测不可靠"）。
        本脚本改为**断言这个已知行为**，而不是判它失败。
  ✓ v1-B1「acc_nav = 0.0」—— **对，真缺陷**。0.0 是缺失哨兵而非真值，
        已由 scripts/repair_sentinel_nav.py 修为 NULL，本脚本改为**回归检查**。

────────────────────────────────────────────────────────────────
真值验证（外部比对）由 scripts/_diag_db_vs_source2.py 承担，实测结论：
  · unit_nav 与天天基金源头 **16,775/16,775 = 100% 一致**
  · acc_nav 与源头 16,773/16,775（2 处差异均为已知 0.0 哨兵）
  · 日期时区正确（源时间戳是北京零点；用 UTC 解析会整体错一天 —— v1 诊断脚本踩过）
"""
import io
import json
import os
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "fund_quant.db")
OUT = os.path.join(ROOT, "docs", "data_quality_report.json")

report = {"checks": {}, "verdict": None}
FATAL, WARN = [], []


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    q = lambda s, a=(): cur.execute(s, a).fetchone()[0]

    def check(name, value, limit, fatal=True, msg=""):
        ok = value <= limit if limit is not None else True
        tag = "OK " if ok else ("FAIL" if fatal else "WARN")
        print("  [%s] %-42s = %-12s %s" % (tag, name, f"{value:,}", msg))
        if not ok:
            (FATAL if fatal else WARN).append(f"{name}={value:,} {msg}")
        return ok

    print("=" * 78)
    print("数据质量抽检 v2")
    print("=" * 78)

    # ---------- 规模 ----------
    total = q("select count(*) from fund_nav")
    funds = q("select count(distinct fund_code) from fund_nav")
    dmin, dmax = cur.execute("select min(nav_date), max(nav_date) from fund_nav").fetchone()
    report["checks"]["scale"] = {"rows": total, "funds": funds,
                                 "date_min": dmin, "date_max": dmax}
    print("\n[规模] rows=%s funds=%s dates=%s .. %s" % (f"{total:,}", f"{funds:,}", dmin, dmax))

    # ---------- A. 主键与格式完整性 ----------
    print("\n[A. 主键与格式]  （任一非零 → FATAL）")
    dup = q("""select count(*) from (select fund_code, nav_date from fund_nav
               group by fund_code, nav_date having count(*) > 1)""")
    badfmt = q("""select count(*) from fund_nav
                  where nav_date not glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'""")
    check("重复主键 (fund_code, nav_date)", dup, 0, msg="主键唯一性被破坏")
    check("非法日期格式", badfmt, 0, msg="日期无法解析")

    # ---------- B. 缺失哨兵回归检查（v1-B1 的真缺陷） ----------
    print("\n[B. 缺失哨兵回归]  （修复后必须为 0；scripts/repair_sentinel_nav.py）")
    z = q("select count(*) from fund_nav where acc_nav = 0.0")
    nz = q("select count(*) from fund_nav where unit_nav <= 0")
    check("acc_nav = 0.0 （应为 NULL，不是 0）", z, 0,
          msg="哨兵值复发：会骗过朴素 COALESCE，导致整行被丢弃而非回退 unit_nav")
    check("unit_nav <= 0", nz, 0, msg="非正净值")

    # ---------- C. 口径自洽（断言已知行为，不判失败） ----------
    print("\n[C. 口径自洽]  （描述性断言，非质量门）")
    nn = q("select count(*) from fund_nav where acc_nav is null")
    report["checks"]["acc_nav_null"] = {"rows": nn,
                                        "funds": q("select count(distinct fund_code) from fund_nav where acc_nav is null")}
    print("  [INF] acc_nav IS NULL                            = %-12s (%.4f%%)"
          % (f"{nn:,}", nn / total * 100))
    print("  [INF] 其中分级基金 161xxx/502xxx 为源头本身缺累计净值，非采集缺陷")

    # SSOT 有效估值点数（这才是下游真正看到的）
    from src.analysis.nav_series import VALUATION_NAV_SQL
    valid = q(f"select count(*) from fund_nav where {VALUATION_NAV_SQL} > 0")
    unusable = total - valid
    report["checks"]["ssot_valid_points"] = {"valid": valid, "unusable": unusable}
    print("  [INF] SSOT 口径有效估值点数                      = %-12s" % f"{valid:,}")
    check("SSOT 口径不可用行（unit/acc 双缺）", unusable, total * 0.001,
          msg="占比超 0.1%%")

    # ---------- D. 已知事实断言（daily_return 口径） ----------
    print("\n[D. daily_return 口径断言]  （源字段本来就是单位净值涨跌幅，见 vol_predictor.py:42）")
    rows = cur.execute("""
        select fund_code, nav_date, unit_nav, acc_nav, daily_return from fund_nav
        where fund_code in (select fund_code from fund_nav group by fund_code
                            having count(*) >= 500 order by fund_code limit 200)
        order by fund_code, nav_date""").fetchall()
    from collections import defaultdict
    seq = defaultdict(list)
    for r in rows:
        seq[r[0]].append(r[1:])
    tot = ok_u = ok_a = 0
    for fc, s in seq.items():
        for i in range(1, len(s)):
            d0, u0, a0, r0 = s[i - 1]
            d1, u1, a1, r1 = s[i]
            if r1 is None or not u0 or u1 is None or a0 in (None, 0) or a1 is None:
                continue
            tot += 1
            if abs((u1 / u0 - 1) * 100 - r1) < 0.05:
                ok_u += 1
            if abs((a1 / a0 - 1) * 100 - r1) < 0.05:
                ok_a += 1
    report["checks"]["daily_return_semantics"] = {
        "pairs": tot, "match_unit_nav_rate": ok_u / tot if tot else 0,
        "match_acc_nav_rate": ok_a / tot if tot else 0}
    print("  [INF] daily_return == unit_nav 涨跌幅 : %.2f%%  ← 期望接近 100%%" %
          (ok_u / tot * 100 if tot else 0))
    print("  [INF] daily_return == acc_nav  涨跌幅 : %.2f%%  ← 分红/折算日不吻合，属已知" %
          (ok_a / tot * 100 if tot else 0))
    check("daily_return 确为单位净值口径（断言 ≥95%%）", 0 if ok_u / tot >= 0.95 else 1, 0,
          msg="源字段口径可能已变，需重新评估所有依赖它的模块")

    # ---------- E. 极端值（信息性，不判失败） ----------
    print("\n[E. 极端值]  （拆分/折算日的源字段噪声）")
    ext = q("select count(*) from fund_nav where daily_return is not null and abs(daily_return) > 20")
    extf = q("""select count(distinct fund_code) from fund_nav
                where daily_return is not null and abs(daily_return) > 20""")
    report["checks"]["extreme_daily_return"] = {"rows": ext, "funds": extf}
    print("  [INF] |daily_return| > 20%% : %s 行 / %s 只 (%.5f%%)"
          % (f"{ext:,}", f"{extf:,}", ext / total * 100))
    check("极端 daily_return 占比", ext, total * 0.001, fatal=False,
          msg="需确认无模块直接使用 daily_return 算指标")

    # ---------- 结论 ----------
    print("\n" + "=" * 78)
    report["verdict"] = {"fatal": FATAL, "warn": WARN, "pass": not FATAL}
    if FATAL:
        print("❌ 地基不通过：")
        for f in FATAL:
            print("   -", f)
    else:
        print("✅ 地基通过（库内一致性 + 哨兵回归 + 口径断言）")
        print("   外部真值比对见 scripts/_diag_db_vs_source2.py：unit_nav 100% 一致")
    if WARN:
        print("⚠️  警告：")
        for w in WARN:
            print("   -", w)
    print("=" * 78)

    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("报告:", os.path.relpath(OUT, ROOT))
    return 0 if not FATAL else 1


if __name__ == "__main__":
    sys.exit(main())
