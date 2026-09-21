# -*- coding: utf-8 -*-
"""
外部抽样比对（计划 C 步）：DB 里的净值 vs 天天基金源头的净值，逐行对齐。
这是唯一能判定「采到的对不对」的方法。
"""
import io
import json
import re
import sqlite3
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://fund.eastmoney.com/",
}
CODES = ["007868", "007858", "160641", "161810", "000001", "110022", "003376"]


def fetch(code):
    url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def grab(text, varname):
    m = re.search(re.escape(varname) + r"\s*=\s*(\[.*?\])\s*;", text, re.S)
    return json.loads(m.group(1)) if m else None


def ts2date(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


conn = sqlite3.connect('data/fund_quant.db')
cur = conn.cursor()

stats = {"unit_match": 0, "unit_mismatch": 0, "acc_match": 0, "acc_mismatch": 0,
         "acc_null_in_src": 0, "acc_zero_in_db": 0, "missing_in_db": 0}
problems = []

for code in CODES:
    print("=" * 78)
    print("FUND", code)
    txt = fetch(code)
    name = re.search(r'fS_name\s*=\s*"([^"]*)"', txt)
    name = name.group(1) if name else "?"
    net = grab(txt, "Data_netWorthTrend") or []
    acw = grab(txt, "Data_ACWorthTrend") or []
    amap = {}
    for item in acw:
        amap[ts2date(item[0])] = item[1]

    db = {d: (u, a) for d, u, a in cur.execute(
        "select nav_date, unit_nav, acc_nav from fund_nav where fund_code=?", (code,))}
    print("  name=%s  src_net=%d src_acw=%d db_rows=%d" % (name, len(net), len(acw), len(db)))

    n_unit_bad = n_acc_bad = 0
    shown = 0
    for pt in net:
        d = ts2date(pt["x"])
        su = pt.get("y")
        sa = amap.get(d, "NO_TS_IN_ACW")
        if d not in db:
            stats["missing_in_db"] += 1
            continue
        du, da = db[d]
        # unit 比对
        if su is not None and abs(su - du) < 1e-6:
            stats["unit_match"] += 1
        else:
            stats["unit_mismatch"] += 1
            n_unit_bad += 1
            if shown < 3:
                print("   UNIT MISMATCH %s src=%s db=%s" % (d, su, du))
                shown += 1
        # acc 比对
        if sa == "NO_TS_IN_ACW":
            continue
        if sa is None:
            stats["acc_null_in_src"] += 1
            if da == 0.0:
                stats["acc_zero_in_db"] += 1
            continue
        if da is not None and abs(sa - da) < 1e-6:
            stats["acc_match"] += 1
        else:
            stats["acc_mismatch"] += 1
            n_acc_bad += 1
            if da == 0.0:
                stats["acc_zero_in_db"] += 1
            if n_acc_bad <= 3:
                problems.append((code, d, sa, da))
                print("   ACC  MISMATCH %s src=%s db=%s" % (d, sa, da))
    print("   -> unit bad %d / acc bad %d" % (n_unit_bad, n_acc_bad))

print()
print("=" * 78)
print("SUMMARY over", len(CODES), "funds")
for k, v in stats.items():
    print("  %-18s %d" % (k, v))
print()
print("problems sample:", problems[:10])
