# -*- coding: utf-8 -*-
"""`指数型-固收`（债券指数基金）该归哪一组？—— 用数据决定，不靠直觉（沿用规则 12）。

背景：`TYPE_GROUPS` 目前把 `指数型-固收` 放在 **F QDII/其他**，导致这些**纯债指数基金**
被打上 F 组的「同类为大类口径、可比性弱」标签（ρ 仅 0.52）。看着别扭，但不实测不该动。

方法（与 `_diag_group_placement.py` 一致，即当初定「混合型-偏债」用的同一套）：
  对每个指标，算「目标类型的分位网格」与各候选组网格的**平均绝对差**，最小者即归属组。
候选：E 债券（直觉上最像）/ F QDII/其他（当前归属）/ D 指数股票（同为"指数型"字样）。

只读。用法：`python scripts/_diag_group_placement_bond_index.py`
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
TARGET = "指数型-固收"
B = dict(TYPE_GROUPS)
CAND = {
    TARGET: lambda t: t == TARGET,
    "E 债券": lambda t: t in B["E 债券"],
    "F QDII/其他": lambda t: t in B["F QDII/其他"],
    "D 指数股票": lambda t: t in B["D 指数股票"],
}

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
navs = load_navs(conn, 756)

acc = {k: [] for k in CAND}
seen = 0
for code, (d, v) in navs.items():
    seen += 1
    t = fi.get(code, "")
    for label, pred in CAND.items():
        if pred(t):
            m = metrics(v, d)
            if m:
                acc[label].append(m)
            break

print("扫描 %s 只（净值 >= 756 天）\n" % f"{seen:,}")
grids = {}
for label, rows in acc.items():
    if len(rows) < 10:
        print("【%s】样本 %d 只 —— 太少，不参与比较\n" % (label, len(rows)))
        continue
    g = {}
    for k in KEYS:
        # ⚠️ 两个坑一起防：① None（未算出） ② **浮点 nan**（算出但不是数）。
        #    只滤 None 不够 —— nan 会顺着 np.percentile 传播，最后得出"nan 无法比较"（踩过）。
        vals = [r[k] for r in rows if r.get(k) is not None and np.isfinite(r[k])]
        if len(vals) >= 10:
            g[k] = np.percentile(vals, GRID)
    grids[label] = g
    print("【%s】n=%s  可用指标: %s" % (label, f"{len(rows):,}", "/".join(g.keys())))
    print("   " + " | ".join("%s: " % k + " ".join("%.2f" % x for x in g[k]) for k in list(g)[:3]))
    print()

print("=" * 86)
print("平均绝对差（越小越像）—— 目标: %s" % TARGET)
print("=" * 86)
tgt = grids.get(TARGET)
if tgt is None:
    print("目标样本不足，无法判定")
else:
    res = []
    for label, g in grids.items():
        if label == TARGET:
            continue
        common = [k for k in tgt if k in g]          # 只比两边都有数据的指标
        if not common:
            continue
        diffs = {k: float(np.mean(np.abs(tgt[k] - g[k]))) for k in common}
        res.append((float(np.mean(list(diffs.values()))), label, diffs, common))
    for score, label, diffs, common in sorted(res):
        print("  %-14s 平均绝对差 %8.4f  (比了 %d 个指标)  " % (label, score, len(common))
              + " ".join("%s=%.3f" % (k[:6], v) for k, v in diffs.items()))
    print()
    if res:
        best = sorted(res)[0]
        cur = next((s for s, l, _, _ in res if l == "F QDII/其他"), None)
        print("→ 最接近的是 **%s**（%.4f）" % (best[1], best[0]))
        if cur:
            print("  当前归属 F QDII/其他 = %.4f → 差 %.2f 倍" % (cur, cur / best[0] if best[0] else float("inf")))
        print()
        print("  判据：平均绝对差小者即归属组。若 E 明显更小 → 把 `指数型-固收` 从 F 移到 E。")
