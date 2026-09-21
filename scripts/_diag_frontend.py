# -*- coding: utf-8 -*-
"""前端现状探测：页签、API 端点、是否已有百分位展示"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

t = open("src/web/static/app.js", encoding="utf-8", errors="replace").read()
h = open("src/web/templates/dashboard.html", encoding="utf-8", errors="replace").read()
ap = open("src/web/app.py", encoding="utf-8", errors="replace").read()

print("=== dashboard.html 导航项 ===")
for m in re.finditer(r'data-(?:tab|view|page)\s*=\s*"([^"]+)"', h):
    print("   ", m.group(1))
print("   (html 内 nav 关键字)", re.findall(r'<nav[^>]*>', h)[:2])

print("\n=== app.js 中的页签标识 ===")
cands = set(re.findall(r"(?:tab|view|page)\s*[:=]\s*['\"]([a-zA-Z_]+)['\"]", t))
print("   ", sorted(cands)[:40])

print("\n=== 前端调用的 API ===")
for e in sorted(set(re.findall(r"['\"`](/api/[A-Za-z0-9_/\-{}$]+)", t))):
    print("   ", e)

print("\n=== app.py 暴露的路由 ===")
for m in re.finditer(r'@app\.route\(\s*[\'"]([^\'"]+)[\'"]', ap):
    print("   ", m.group(1))

print("\n=== 百分位 / 同类 / peer 相关字样 ===")
hit = 0
for name, src in [("app.js", t), ("app.py", ap), ("dashboard.html", h)]:
    for i, l in enumerate(src.splitlines(), 1):
        if re.search(r"百分位|同类|peer|percentile|分位|参照系", l):
            print("   %-14s %5d| %s" % (name, i, l.strip()[:118]))
            hit += 1
print("   命中", hit, "行")

print("\n=== 推荐相关端点实现（app.py）===")
for i, l in enumerate(ap.splitlines(), 1):
    if re.search(r"def api_|recommend|screen|FundScreener|historical_rec", l):
        print("   %5d| %s" % (i, l.rstrip()[:118]))
