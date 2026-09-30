# -*- coding: utf-8 -*-
"""看 investment_plans / investment_plan_items 的结构与内容"""
import io
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = sqlite3.connect("data/fund_quant.db")
cur = c.cursor()

for t in ("investment_plans", "investment_plan_items"):
    print("=" * 70)
    print("TABLE", t)
    print("=" * 70)
    for r in cur.execute("PRAGMA table_info(%s)" % t):
        print("  %-22s %s" % (r[1], r[2]))
    rows = cur.execute("select * from %s limit 4" % t).fetchall()
    cols = [d[0] for d in cur.execute("select * from %s limit 1" % t).description]
    print("  ── %d 行样例" % len(rows))
    for r in rows:
        for k, v in zip(cols, r):
            print("     %-22s %r" % (k, v))
        print("     " + "-" * 40)
    print()

print("=" * 70)
print("本地画像文件（rebalance.constraint_review 提到 user_profile.local.yaml）")
print("=" * 70)
import glob
import os
for p in glob.glob("**/user_profile*.y*ml", recursive=True):
    print("  找到:", p)
    print(open(p, encoding="utf-8", errors="replace").read())
