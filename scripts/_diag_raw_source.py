# -*- coding: utf-8 -*-
"""
诊断 2：直接拉原始 pingzhongdata，看 Data_netWorthTrend / Data_ACWorthTrend 的真实结构。
同时修复控制台编码问题（输出写 UTF-8 文件）。
"""
import io
import json
import re
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://fund.eastmoney.com/",
}

CODES = ["160641", "007858", "007868", "161810", "000001"]


def fetch(code):
    url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def grab(text, varname):
    """从 JS 里取 Data_xxx = [...] 的原始文本"""
    m = re.search(re.escape(varname) + r"\s*=\s*(\[.*?\])\s*;", text, re.S)
    return m.group(1) if m else None


for code in CODES:
    print("=" * 78)
    print("FUND", code)
    try:
        txt = fetch(code)
    except Exception as e:
        print("  fetch failed:", e)
        continue

    print("  js size:", len(txt))

    # fund name from fS_name
    m = re.search(r'fS_name\s*=\s*"([^"]*)"', txt)
    print("  name   :", m.group(1) if m else "?")
    m = re.search(r'fS_code\s*=\s*"([^"]*)"', txt)
    print("  code   :", m.group(1) if m else "?")

    for var in ["Data_netWorthTrend", "Data_ACWorthTrend"]:
        raw = grab(txt, var)
        if raw is None:
            print("  %s : NOT FOUND" % var)
            continue
        try:
            arr = json.loads(raw)
        except Exception as e:
            print("  %s : parse fail %s" % (var, e))
            continue
        print("  %s : len=%d" % (var, len(arr)))
        print("     first 2:", json.dumps(arr[:2], ensure_ascii=False)[:300])
        print("     last  2:", json.dumps(arr[-2:], ensure_ascii=False)[:300])
        if var == "Data_ACWorthTrend":
            # 看看单位净值与累计净值的形状差异
            pass

    # 交叉：取最后 3 个点的 unit(y) 与 acc
    raw_n = grab(txt, "Data_netWorthTrend")
    raw_a = grab(txt, "Data_ACWorthTrend")
    if raw_n and raw_a:
        n = json.loads(raw_n)
        a = json.loads(raw_a)
        print("  --- tail comparison (unit=netWorthTrend.y, acc=ACWorthTrend[1]) ---")
        amap = {x[0]: x[1] for x in a}
        for pt in n[-4:]:
            ts = pt["x"]
            print("     ts=%s unit=%s acc=%s equityReturn=%s" % (
                ts, pt.get("y"), amap.get(ts), pt.get("equityReturn")))
    print()
