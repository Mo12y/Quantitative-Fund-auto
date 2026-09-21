# -*- coding: utf-8 -*-
"""
②-补：把 993 只「混合型-偏债」归到哪一组？—— 用数据决定，不靠直觉（规则 12）
对比其指标分布与 B 灵活配置 / E 债券 的接近程度。
判据：对每个指标算「混合型-偏债 的分位网格」与 B、E 分位网格的平均绝对差，
      差值小者即为归属组。
"""
import io
import os
import sqlite3
import sys

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from calibrate_thresholds import DB_PATH, TYPE_GROUPS, load_navs, metrics  # noqa: E402

GRID = [5, 10, 25, 50, 75, 90, 95]
KEYS = ["annual_return", "ann_vol", "max_drawdown_1y", "momentum_3m", "sharpe"]
TARGET = "混合型-偏债"

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
navs = load_navs(conn, 756)

# 我关心三组：目标类型 / B 灵活配置 / E 债券
WANT = {
    TARGET: lambda t: t == TARGET,
    "B 灵活配置": lambda t: t in dict((g, ts) for g, ts in TYPE_GROUPS)["B 灵活配置"],
    "E 债券": lambda t: t in dict((g, ts) for g, ts in TYPE_GROUPS)["E 债券"],
}
acc = {k: [] for k in WANT}
n_seen = 0
for code, (d, v) in navs.items():
    n_seen += 1
    t = fi.get(code, "")
    for label, pred in WANT.items():
        if pred(t):
            m = metrics(v, d)
            if m:
                acc[label].append(m)
            break

print("扫描 %s 只\n" % f"{n_seen:,}")
grid = {}
for label, rows in acc.items():
    if not rows:
        print("%-14s 无样本" % label)
        continue
    print("=" * 78)
    print("【%s】 n=%s" % (label, f"{len(rows):,}"))
    print("=" * 78)
    print("  %-16s" % "指标" + "".join(f"{'P'+str(p):>9}" for p in GRID))
    g = {}
    for k in KEYS:
        a = np.array([r[k] for r in rows], dtype=float)
        a = a[np.isfinite(a)]
        if not a.size:
            continue
        g[k] = [float(np.percentile(a, p)) for p in GRID]
        print("  %-16s" % k + "".join(f"{x:>9.3f}" for x in g[k]))
    grid[label] = g
    print()

print("=" * 78)
print("归属判定：与哪一组的分布最接近（分位网格平均绝对差，越小越像）")
print("=" * 78)
if TARGET in grid:
    print("  %-16s %12s %12s" % ("指标", "vs B 灵活配置", "vs E 债券"))
    tot = {"B 灵活配置": [], "E 债券": []}
    for k in KEYS:
        if k not in grid[TARGET]:
            continue
        row = []
        for other in ["B 灵活配置", "E 债券"]:
            if k in grid.get(other, {}):
                d_ = float(np.mean(np.abs(np.array(grid[TARGET][k]) - np.array(grid[other][k]))))
                tot[other].append(d_)
                row.append(d_)
            else:
                row.append(float("nan"))
        print("  %-16s %12.3f %12.3f" % (k, row[0], row[1]))
    print("  " + "-" * 44)
    mA = float(np.mean(tot["B 灵活配置"])) if tot["B 灵活配置"] else float("nan")
    mE = float(np.mean(tot["E 债券"])) if tot["E 债券"] else float("nan")
    print("  %-16s %12.3f %12.3f  ← 平均" % ("合计", mA, mE))
    print()
    print("  → 结论：混合型-偏债 更接近 **%s**"
          % ("B 灵活配置" if mA < mE else "E 债券"))
    print("    （平均绝对差 %.3f vs %.3f，比值 %.2f）"
          % (min(mA, mE), max(mA, mE), max(mA, mE) / min(mA, mE) if min(mA, mE) else float('inf')))
conn.close()
