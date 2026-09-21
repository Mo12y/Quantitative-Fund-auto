# -*- coding: utf-8 -*-
"""诊断 4：确定三个真缺陷的影响面"""
import io
import os
import re
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
conn = sqlite3.connect(os.path.join(ROOT, 'data', 'fund_quant.db'))
cur = conn.cursor()
q = lambda s, a=(): cur.execute(s, a).fetchone()[0]

print("=== 1. 哪 40 行 unit_nav <= 0 ===")
for r in cur.execute("""select fund_code, nav_date, unit_nav, acc_nav from fund_nav
                        where unit_nav <= 0 order by unit_nav limit 15"""):
    print("   ", r)

print("\n=== 2. acc_nav = 0.0 的基金，是否在深历史池(>=756天)里 ===")
rows = cur.execute("""
  select n.fund_code, i.fund_name, i.fund_type,
         (select count(*) from fund_nav x where x.fund_code=n.fund_code) as total_days,
         sum(case when n.acc_nav = 0.0 then 1 else 0 end) as zero_days,
         max(n.nav_date) as last_date,
         sum(case when n.acc_nav = 0.0 and n.nav_date >= date('now','-400 day') then 1 else 0 end) as zero_recent
  from fund_nav n left join fund_info i on i.fund_code=n.fund_code
  where n.fund_code in (select fund_code from fund_nav where acc_nav = 0.0)
  group by n.fund_code order by zero_recent desc, zero_days desc
""").fetchall()
print("   %-8s %-20s %-14s %8s %6s %6s  %s" % ("code", "name", "type", "days", "zero", "recent", "last"))
in_deep = 0
recent_bad = 0
for fc, nm, ft, tot, z, last, zr in rows:
    flag = ""
    if tot >= 756:
        in_deep += 1
        flag += " [DEEP]"
    if zr:
        recent_bad += 1
        flag += " [!! 近期窗口内有 0.0]"
    print("   %-8s %-20s %-14s %8d %6d %6d  %s%s" % (fc, (nm or "?")[:18], (ft or "?")[:12],
                                                      tot, z, zr, last, flag))
print("\n   acc=0.0 基金总数 %d | 其中在深历史池 %d | 近期窗口内有 0.0 的 %d"
      % (len(rows), in_deep, recent_bad))

print("\n=== 3. acc_nav IS NULL 的基金是否在深历史池 ===")
print("   NULL 行数:", q("select count(*) from fund_nav where acc_nav is null"))
print("   受影响基金:", q("select count(distinct fund_code) from fund_nav where acc_nav is null"))
print("   其中 >=756 天的:",
      q("""select count(*) from (select fund_code from fund_nav group by fund_code having count(*)>=756)
           where fund_code in (select distinct fund_code from fund_nav where acc_nav is null)"""))
print("\n   NULL 分布 top10:")
for r in cur.execute("""select fund_code, count(*) c,
                               (select count(*) from fund_nav y where y.fund_code=x.fund_code) tot
                        from fund_nav x where acc_nav is null
                        group by fund_code order by c desc limit 10"""):
    print("     %s null=%d / total=%d" % r)

print("\n=== 4. 007868 / 007858 为什么只有 2 行 ===")
for fc in ('007868', '007858', '007696', '511880', '000425'):
    r = cur.execute("""select count(*), min(nav_date), max(nav_date) from fund_nav where fund_code=?""", (fc,)).fetchone()
    fi = cur.execute("select fund_name, fund_type, purchase_status from fund_info where fund_code=?", (fc,)).fetchone()
    print("   %s rows=%-5d %s..%s  %s" % (fc, r[0], r[1], r[2], fi))

print("\n=== 5. 货币型基金在 fund_nav 里的整体情况 ===")
for r in cur.execute("""select i.fund_type, count(distinct n.fund_code) funds, count(*) rows,
                               avg(cnt) avg_days
                        from fund_nav n join fund_info i on i.fund_code=n.fund_code
                        join (select fund_code, count(*) cnt from fund_nav group by fund_code) t
                          on t.fund_code=n.fund_code
                        where i.fund_type like '货币型%'
                        group by i.fund_type"""):
    print("   %-18s funds=%-5d rows=%-9d avg_days=%.0f" % (r[0], r[1], r[2], r[3]))

print("\n=== 6. 指标代码是否使用 daily_return ===")
for dirpath, dirnames, filenames in os.walk(ROOT):
    if any(s in dirpath for s in ('.git', 'node_modules', '__pycache__')):
        continue
    for fn in filenames:
        if not fn.endswith('.py'):
            continue
        p = os.path.join(dirpath, fn)
        try:
            txt = open(p, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        if 'daily_return' in txt:
            for i, line in enumerate(txt.splitlines(), 1):
                if 'daily_return' in line:
                    print("   %s:%d  %s" % (os.path.relpath(p, ROOT), i, line.strip()[:110]))
