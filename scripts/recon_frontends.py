# -*- coding: utf-8 -*-
"""
阶段 2 对账：新前端 /v2 与旧前端 / 的逐项数字比对
=================================================
做法：拉三个 API，把「规范数字」抽出来；再对照两个前端实际渲染出来的文本，
      找出「后端有但新前端没渲染」的字段。这类缺失最难自己发现 ——
      因为界面看起来是正常的，只是少了披露。

用法：python scripts/recon_frontends.py
"""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:5020"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def walk(obj, prefix="", out=None, depth=0):
    """把嵌套 payload 摊平成 路径 -> 值，便于逐项核对"""
    if out is None:
        out = {}
    if depth > 4:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{prefix}.{k}" if prefix else k, out, depth + 1)
    elif isinstance(obj, list):
        out[prefix] = f"<list len={len(obj)}>"
    else:
        out[prefix] = obj
    return out


ov = get("/api/overview").get("data") or {}
rb = get("/api/rebalance").get("data") or {}
ex = get("/api/explain").get("data") or {}

print("=" * 84)
print("① 行情/新鲜度与温度（旧前端有明确告警，新前端要核对）")
print("=" * 84)
t = ov.get("temp") or {}
flat_t = walk(t, "temp")
for k in sorted(flat_t):
    if any(s in k for s in ("divergence", "temperature", "target", "components",
                            "asof", "date", "stale", "lag", "level", "insufficient",
                            "intraday", "notice", "warning", "error")):
        print("  %-46s %r" % (k, flat_t[k]))

print()
print("=" * 84)
print("② 曲线卡：旧前端展示「当前市值/累计收益/未入仓披露」，新前端核对")
print("=" * 84)
c = ov.get("curve") or {}
for k in ("value", "cost", "pnl", "return_pct", "benchmark", "dates",
          "excluded_not_in", "excluded_pending", "excluded_amount", "funds_used",
          "benchmark_name"):
    v = c.get(k)
    if isinstance(v, list):
        print("  %-20s list len=%d  末值=%s" % (k, len(v), v[-1] if v else None))
    else:
        print("  %-20s %r" % (k, v))

print()
print("=" * 84)
print("③ /api/explain 五格（新前端已渲染；核对是否有格缺失）")
print("=" * 84)
if isinstance(ex, dict):
    for k, v in ex.items():
        if isinstance(v, list):
            print("  %-16s list len=%d" % (k, len(v)))
            for i, cell in enumerate(v):
                if isinstance(cell, dict):
                    name = cell.get("name") or cell.get("key") or cell.get("title")
                    nrows = len(cell.get("rows") or [])
                    nart = len(cell.get("artifacts") or [])
                    print("      [%d] %-14s rows=%d artifacts=%d"
                          % (i, name, nrows, nart))
        else:
            print("  %-16s %r" % (k, v))

print()
print("=" * 84)
print("④ 全量字段清单（供人工核对「后端给了但界面没显示」）")
print("=" * 84)
for label, data in (("overview", ov), ("rebalance", rb)):
    flat = walk(data, label)
    print("\n  [%s] 共 %d 个叶子字段" % (label, len(flat)))
    for k in sorted(flat):
        v = flat[k]
        s = repr(v)
        print("    %-52s %s" % (k, s[:70]))
