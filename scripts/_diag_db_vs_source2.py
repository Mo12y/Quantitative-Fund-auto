# -*- coding: utf-8 -*-
"""
诊断 3（修正时区后）：DB vs 源头逐行比对。
关键修正：源时间戳是「北京零点」的 UTC 毫秒，必须用 Asia/Shanghai 转换。
            上一版诊断用 UTC → 整体错一天，产生了 12,747 条假不匹配。
"""
import datetime
import io
import json
import re
import sqlite3
import sys
import urllib.request
from zoneinfo import ZoneInfo

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SH = ZoneInfo("Asia/Shanghai")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://fund.eastmoney.com/",
}
CODES = ["007868", "007858", "160641", "161810", "000001", "110022", "003376"]


def fetch(code):
    req = urllib.request.Request("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
                                 headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def grab(text, var):
    m = re.search(re.escape(var) + r"\s*=\s*(\[.*?\])\s*;", text, re.S)
    return json.loads(m.group(1)) if m else None


def ts2date(ts):
    return datetime.datetime.fromtimestamp(ts / 1000, SH).strftime("%Y-%m-%d")


conn = sqlite3.connect('data/fund_quant.db')
cur = conn.cursor()

st = dict(unit_match=0, unit_bad=0, acc_match=0, acc_bad=0,
          src_acc_null=0, db_acc_zero=0, src_only=0, db_only=0, code_only=0)
detail = []

for code in CODES:
    txt = fetch(code)
    nm = re.search(r'fS_name\s*=\s*"([^"]*)"', txt)
    net = grab(txt, "Data_netWorthTrend") or []
    acw = grab(txt, "Data_ACWorthTrend") or []
    amap = {ts2date(i[0]): i[1] for i in acw}
    uu = {ts2date(p["x"]): p.get("y") for p in net}
    dd = {d: (u, a) for d, u, a in cur.execute(
        "select nav_date, unit_nav, acc_nav from fund_nav where fund_code=?", (code,))}

    ub = ab = 0
    for d, su in uu.items():
        if d not in dd:
            st["src_only"] += 1
            continue
        du = dd[d][0]
        if su is not None and du is not None and abs(su - du) < 1e-6:
            st["unit_match"] += 1
        else:
            st["unit_bad"] += 1
            ub += 1
            if ub <= 2:
                detail.append(("UNIT", code, d, su, du))
        sa = amap.get(d, "NA")
        da = dd[d][1]
        if sa == "NA":
            st["code_only"] += 1
            continue
        if sa is None:
            st["src_acc_null"] += 1
        elif da is not None and abs(sa - da) < 1e-6:
            st["acc_match"] += 1
        else:
            st["acc_bad"] += 1
            ab += 1
            if ab <= 2:
                detail.append(("ACC", code, d, sa, da))
        if da == 0.0:
            st["db_acc_zero"] += 1
    st["db_only"] += len(set(dd) - set(uu))
    print("%-8s %-24s net=%-5d acw=%-5d db=%-5d | unit_bad=%-4d acc_bad=%-4d db_acc_zero=%d"
          % (code, (nm.group(1) if nm else "?")[:22], len(net), len(acw), len(dd), ub, ab,
             sum(1 for v in dd.values() if v[1] == 0.0)))

print("\n=== 逐行比对汇总（7 只，时区已修正）===")
for k, v in st.items():
    print("  %-14s %d" % (k, v))
print("\n不匹配样本:", detail[:8])

# ---------- 全库扫描真缺陷 ----------
print("\n=== 全库真缺陷扫描 ===")
q = lambda s: cur.execute(s).fetchone()[0]
print("  acc_nav IS NULL      :", q("select count(*) from fund_nav where acc_nav is null"))
print("  acc_nav = 0.0        :", q("select count(*) from fund_nav where acc_nav = 0.0"))
print("  unit_nav IS NULL     :", q("select count(*) from fund_nav where unit_nav is null"))
print("  unit_nav <= 0        :", q("select count(*) from fund_nav where unit_nav <= 0"))
print("  受影响基金数(acc=0)  :", q("select count(distinct fund_code) from fund_nav where acc_nav = 0.0"))
print("  受影响基金数(acc空)  :", q("select count(distinct fund_code) from fund_nav where acc_nav is null"))
print()
print("  acc=0 的基金（全部）:")
for r in cur.execute("""select n.fund_code, i.fund_name, i.fund_type, count(*),
                               min(n.nav_date), max(n.nav_date)
                        from fund_nav n left join fund_info i on i.fund_code=n.fund_code
                        where n.acc_nav = 0.0 group by n.fund_code
                        order by count(*) desc"""):
    print("    %s %-22s %-14s n=%-4d %s..%s" % (r[0], (r[1] or "?")[:20], (r[2] or "?")[:12],
                                                 r[3], r[4], r[5]))
