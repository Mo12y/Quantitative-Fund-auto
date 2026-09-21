# -*- coding: utf-8 -*-
"""①-补：口径不一致导致的漏采量化 + purchase_status 分布"""
import io
import os
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.analysis.nav_series import valuation_nav_sql
V = valuation_nav_sql("n")
conn = sqlite3.connect(os.path.join(ROOT, "data", "fund_quant.db"))
cur = conn.cursor()

print("=" * 82)
print("purchase_status 分布（决定「可买池」口径）")
print("=" * 82)
print("%-30s %8s %10s %10s %8s" % ("purchase_status", "只数", "有净值", ">=756天", "采集范围"))
for st, n, withnav, deep in cur.execute(f"""
    select coalesce(nullif(i.purchase_status,''),'(空/NULL)') st,
           count(*) n,
           sum(case when t.pts > 0 then 1 else 0 end),
           sum(case when t.pts >= 756 then 1 else 0 end)
    from fund_info i
    left join (select fund_code, count(*) pts from fund_nav n
               where {V} > 0 group by fund_code) t on t.fund_code = i.fund_code
    group by st order by n desc"""):
    scope = "深采(含)" if st == "开放申购" else ("深采(不含!)" if st == "(空/NULL)" else "深采(不含)")
    print("%-30s %8s %10s %10s %8s" % (st[:28], f"{n:,}", f"{withnav or 0:,}", f"{deep or 0:,}", scope))

print("\n" + "=" * 82)
print("口径不一致的代价：按深采窄口径 vs 审计宽口径")
print("=" * 82)
NARROW = "i.purchase_status like '%开放%'"
WIDE = f"({NARROW} or i.purchase_status is null or i.purchase_status = '')"
for label, w in [("深采窄口径 like '%开放%'", NARROW), ("审计宽口径 ∪ 空", WIDE)]:
    n = cur.execute(f"select count(*) from fund_info i where {w}").fetchone()[0]
    print("  %-28s %8s 只" % (label, f"{n:,}"))
gap = (cur.execute(f"select count(*) from fund_info i where {WIDE}").fetchone()[0]
       - cur.execute(f"select count(*) from fund_info i where {NARROW}").fetchone()[0])
print("  %-28s %8s 只  ← **从未进入深采目标清单**" % ("差集（被漏掉的）", f"{gap:,}"))

print("\n" + "=" * 82)
print("被漏掉的这批是什么？")
print("=" * 82)
rows = cur.execute(f"""
    select coalesce(nullif(i.fund_type,''),'(空)') t, count(*) n,
           sum(case when x.pts > 0 then 1 else 0 end) withnav,
           sum(case when i.fund_name like '%(后端)%' then 1 else 0 end) backend
    from fund_info i
    left join (select fund_code, count(*) pts from fund_nav n where {V}>0 group by fund_code) x
      on x.fund_code = i.fund_code
    where {WIDE} and not ({NARROW})
    group by t order by n desc limit 18""").fetchall()
print("%-22s %8s %10s %12s" % ("类型", "只数", "有净值", "其中(后端)"))
tot = bk = 0
for t, n, wn, b in rows:
    tot += n; bk += b
    print("%-22s %8s %10s %12s" % (t[:20], f"{n:,}", f"{wn or 0:,}", f"{b:,}"))
allgap = cur.execute(f"""select count(*),
    sum(case when i.fund_name like '%(后端)%' then 1 else 0 end)
    from fund_info i where {WIDE} and not ({NARROW})""").fetchone()
print("-" * 56)
print("合计 %s 只，其中「(后端)」份额 %s 只 (%.1f%%)"
      % (f"{allgap[0]:,}", f"{allgap[1]:,}", allgap[1] / allgap[0] * 100))

print("\n" + "=" * 82)
print("这些基金有多少能从源头采到深历史？（抽 40 只实测，规则 12 实测对比）")
print("=" * 82)
import json
import re
import urllib.request
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
       "Referer": "https://fund.eastmoney.com/"}
sample = [r[0] for r in cur.execute(f"""
    select i.fund_code from fund_info i where {WIDE} and not ({NARROW})
    order by i.fund_code limit 40""")]
ok = fail = 0
lens = []
for c in sample:
    try:
        req = urllib.request.Request("https://fund.eastmoney.com/pingzhongdata/%s.js" % c, headers=HDR)
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", errors="replace")
        m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\])\s*;", txt, re.S)
        n = len(json.loads(m.group(1))) if m else 0
        if n > 0:
            ok += 1; lens.append(n)
        else:
            fail += 1
    except Exception:
        fail += 1
print("  抽样 %d 只：可采到 %d 只 / 采不到 %d 只" % (len(sample), ok, fail))
if lens:
    lens.sort()
    print("  可采到的历史长度：中位 %d 天 | 最短 %d | 最长 %d | >=756 天的 %d 只"
          % (lens[len(lens)//2], lens[0], lens[-1], sum(1 for x in lens if x >= 756)))
