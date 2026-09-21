# -*- coding: utf-8 -*-
"""临时诊断：acc_nav < unit_nav 的基金是谁"""
import sqlite3

c = sqlite3.connect('data/fund_quant.db')
cur = c.cursor()

rows = cur.execute("""
  select n.fund_code, i.fund_name, i.fund_type, count(*) as bad_days,
         min(n.unit_nav), max(n.unit_nav), min(n.acc_nav), max(n.acc_nav)
  from fund_nav n left join fund_info i on i.fund_code = n.fund_code
  where n.acc_nav is not null and n.unit_nav is not null
    and n.acc_nav < n.unit_nav - 0.0001
  group by n.fund_code order by bad_days desc limit 30
""").fetchall()

print("=== funds with acc_nav < unit_nav (top 30 by bad-day count) ===")
hdr = "%-8s %-24s %-14s %7s  %-24s %-22s" % ("code", "name", "type", "bad", "unit[min,max]", "acc[min,max]")
print(hdr)
for fc, name, ft, bad, umn, umx, amn, amx in rows:
    print("%-8s %-24s %-14s %7d  [%8.4f,%9.4f] [%7.4f,%8.4f]" % (
        fc, (name or "?")[:22], (ft or "?")[:12], bad, umn, umx, amn, amx))

n_funds = cur.execute("""
  select count(distinct fund_code) from fund_nav
  where acc_nav is not null and unit_nav is not null and acc_nav < unit_nav - 0.0001
""").fetchone()[0]
print("\naffected funds:", n_funds)

print("\n=== distribution of (unit_nav - acc_nav) ratio: is it a scale factor? ===")
ratio = cur.execute("""
  select fund_code, nav_date, unit_nav, acc_nav from fund_nav
  where acc_nav is not null and unit_nav is not null and acc_nav < unit_nav - 0.0001
  order by (unit_nav - acc_nav) desc limit 12
""").fetchall()
for fc, d, u, a in ratio:
    print("  %s %s unit=%9.4f acc=%8.4f  unit/acc=%8.4f" % (fc, d, u, a, u / a if a else float('inf')))

print("\n=== detail series for 160641 (recent 12) ===")
for r in cur.execute("select nav_date, unit_nav, acc_nav, daily_return from fund_nav where fund_code='160641' order by nav_date desc limit 12"):
    print("  ", r)

print("\n=== detail series for 007858 (recent 8) ===")
for r in cur.execute("select nav_date, unit_nav, acc_nav, daily_return from fund_nav where fund_code='007858' order by nav_date desc limit 8"):
    print("  ", r)

print("\n=== fund_info for these ===")
for r in cur.execute("select fund_code, fund_name, fund_type, establish_date, purchase_status from fund_info where fund_code in ('160641','007858','007868','007869','006401')"):
    print("  ", r)

print("\n=== do ANY rows have acc_nav > unit_nav (the expected direction)? ===")
print("  acc>unit:", cur.execute("select count(*) from fund_nav where acc_nav > unit_nav + 0.0001").fetchone()[0])
print("  acc==unit:", cur.execute("select count(*) from fund_nav where abs(acc_nav-unit_nav) <= 0.0001").fetchone()[0])
print("  acc<unit :", cur.execute("select count(*) from fund_nav where acc_nav < unit_nav - 0.0001").fetchone()[0])
