# -*- coding: utf-8 -*-
"""⑤-前置2：探测对象型字段的真实结构（Data_fluctuationScale 是 fund_size 的唯一来源）"""
import io
import re
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
       "Referer": "https://fund.eastmoney.com/"}
CODES = ["110022", "003376", "160641", "007868", "050025"]
OBJ_FIELDS = ["Data_fluctuationScale", "Data_holderStructure", "Data_assetAllocation",
              "Data_performanceEvaluation", "Data_buySedemption"]


def balanced(txt, start):
    """从 start 处的 '[' 或 '{' 开始，按括号配对取出完整字面量"""
    open_ch = txt[start]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    i = start
    in_str = False
    esc = False
    while i < len(txt):
        c = txt[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return txt[start:i + 1]
        i += 1
    return None


for code in CODES:
    try:
        req = urllib.request.Request("https://fund.eastmoney.com/pingzhongdata/%s.js" % code, headers=HDR)
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(code, "FAIL", e)
        continue
    print("=" * 88)
    print("FUND", code)
    for f in OBJ_FIELDS:
        m = re.search(r"var\s+%s\s*=\s*" % re.escape(f), txt)
        if not m:
            print("  %-30s : 不存在" % f)
            continue
        start = m.end()
        lit = balanced(txt, start)
        if lit is None:
            print("  %-30s : 无法配对" % f)
            continue
        print("  %-30s : len=%-6d %s" % (f, len(lit), lit[:230].replace("\n", " ")))
    # 基金经理 workTime 解析验证
    wt = re.search(r'"workTime":"([^"]*)"', txt)
    print("  %-30s : %s" % ("manager workTime", wt.group(1) if wt else "-"))
    fr = re.search(r'var\s+fund_Rate\s*=\s*"?([0-9.]*)"?', txt)
    fsr = re.search(r'var\s+fund_sourceRate\s*=\s*"?([0-9.]*)"?', txt)
    print("  %-30s : fund_Rate=%s  fund_sourceRate=%s" % ("费率", fr.group(1) if fr else "-",
                                                          fsr.group(1) if fsr else "-"))
