# -*- coding: utf-8 -*-
"""
⑤-前置：探测 pingzhongdata 里到底有哪些字段可用（规则 1 数据源验证前置）
不写任何代码逻辑，先看清源头有什么，再决定解析什么、存什么。
"""
import io
import json
import re
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
       "Referer": "https://fund.eastmoney.com/"}

# 混合型 / 债券型 / 指数型 / QDII 各取一只，看字段是否因类型而不同
CODES = ["110022", "000001", "003376", "160641", "007868", "050025"]

for code in CODES:
    try:
        req = urllib.request.Request("https://fund.eastmoney.com/pingzhongdata/%s.js" % code, headers=HDR)
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(code, "FAIL", e)
        continue

    nm = re.search(r'fS_name\s*=\s*"([^"]*)"', txt)
    print("=" * 88)
    print("FUND %s  %s   js=%d bytes" % (code, nm.group(1) if nm else "?", len(txt)))
    print("=" * 88)

    # 所有 var 声明
    names = re.findall(r'var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=', txt)
    print("var 列表(%d): %s" % (len(names), ", ".join(names)))

    # 标量字符串字段
    print("\n-- 标量字符串字段 --")
    for m in re.finditer(r'var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]{0,80})"', txt):
        print("   %-32s = %s" % (m.group(1), m.group(2)))

    # 数值字段
    print("\n-- 数值字段 --")
    for m in re.finditer(r'var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?[0-9.]+)\s*;', txt):
        print("   %-32s = %s" % (m.group(1), m.group(2)))

    # 数组字段：报长度 + 前 2 项形状
    print("\n-- 数组/对象字段（长度 + 头部样本）--")
    for m in re.finditer(r'var\s+(Data_[A-Za-z0-9_]+)\s*=\s*(\[.*?\])\s*;', txt, re.S):
        var, raw = m.group(1), m.group(2)
        try:
            arr = json.loads(raw)
        except Exception:
            print("   %-32s len=? 解析失败 头80字: %s" % (var, raw[:80].replace("\n", " ")))
            continue
        head = json.dumps(arr[:2], ensure_ascii=False)[:190]
        print("   %-32s len=%-6d %s" % (var, len(arr), head))
    print()
