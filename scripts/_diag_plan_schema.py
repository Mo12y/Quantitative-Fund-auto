# -*- coding: utf-8 -*-
"""查项目里与「计划 / 目标 / 画像」相关的表结构，判断有无可复用的配置位"""
import io
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = sqlite3.connect("data/fund_quant.db")
cur = c.cursor()

print("=== 表清单（名字含 plan/profile/config/user/goal）===")
for (n,) in cur.execute("select name from sqlite_master where type='table' order by name"):
    if any(k in n.lower() for k in ("plan", "profile", "config", "user", "goal")):
        print("  ", n)

print("\n=== 各表结构 ===")
for (n,) in cur.execute("select name from sqlite_master where type='table' order by name"):
    if any(k in n.lower() for k in ("plan", "profile", "config", "user", "goal")):
        print("\n--- %s" % n)
        for r in cur.execute("PRAGMA table_info(%s)" % n):
            print("    %-22s %s" % (r[1], r[2]))

print("\n=== 计划表当前内容 ===")
for t in ("investment_plan", "plan", "user_profile"):
    try:
        rows = cur.execute("select * from %s limit 5" % t).fetchall()
        cols = [d[0] for d in cur.execute("select * from %s limit 1" % t).description]
        print("\n--- %s (%d 行样例)" % (t, len(rows)))
        for r in rows:
            print("   ", dict(zip(cols, r)))
    except Exception as e:
        print("\n--- %s: %s" % (t, e))

print("\n=== 计划条目表（plan_items?）===")
for (n,) in cur.execute("select name from sqlite_master where type='table' order by name"):
    if "item" in n.lower() or "holding" in n.lower():
        print("  表:", n)
