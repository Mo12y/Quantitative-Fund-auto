# -*- coding: utf-8 -*-
"""
参照系的外部独立验证（规则 12 实测对比 / 规则 14 事实来源分级）
=================================================================
问题：本项目的「同侪百分位」是我们自己算的。怎么知道它**不是自说自话**？

机会：全量补采时发现 pingzhongdata 里有 `Data_rateInSimilarPersent` ——
      **天天基金自己发布的同类排名百分位**，与我们完全独立（不同数据、不同算法、不同口径）。
      18,328 只基金有该字段。

做法：把「本项目算出的百分位」与「天天基金的同类百分位」做秩相关。
      · 相关高 → 独立第三方印证了参照系，可信度大幅提升
      · 相关低 → 要么口径不同，要么我们的算法有问题 —— 两者都必须查清楚

⚠️ 口径未知，所以**先做口径识别**：分别用 近3月动量 / 年化收益 / 夏普 百分位去比，
   看哪个相关最高 —— 以此**反推**天天基金这个字段对应的期限。
"""
import io
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date as _d, timedelta as _td

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from calibrate_thresholds import (  # noqa: E402
    DB_PATH, METRICS, TYPE2GROUP, iter_navs, metrics,
)

OUT = os.path.join(ROOT, "docs", "peer_validation.json")

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
peer = {c: (p, r, t, d) for c, p, r, t, d in conn.execute(
    "select fund_code, peer_pct, peer_rank, peer_total, peer_date "
    "from fund_profile_extra where peer_pct is not null")}
print("有同类百分位的基金: %s 只" % f"{len(peer):,}")

# ---------- 1. 流式算每只基金的指标（只存小字典，不存净值序列）----------
print("流式计算指标（流式，内存 O(单只)）…")
met = {}
grp = {}
for code, dates, vals in iter_navs(conn, 756):
    m = metrics(vals, dates)
    if not m:
        continue
    g = TYPE2GROUP.get(fi.get(code, ""))
    if not g:
        continue
    met[code] = m
    grp[code] = g
print("  得到 %s 只可用样本" % f"{len(met):,}")

# ---------- 2. 组内分布 → 每只基金的百分位 ----------
dist = defaultdict(dict)
for g in set(grp.values()):
    codes = [c for c in met if grp[c] == g]
    for k in METRICS:
        arr = np.array([met[c][k] for c in codes], dtype=float)
        dist[g][k] = arr[np.isfinite(arr)]


def pct_of(code, k):
    g = grp[code]
    arr = dist[g][k]
    v = met[code][k]
    if not arr.size or not np.isfinite(v):
        return None
    return float((arr <= v).mean() * 100)


# ---------- 3. 与天天基金同类百分位做秩相关 ----------
def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.size < 10:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d else float("nan")


common = [c for c in met if c in peer]
print("\n与天天基金同类百分位可比的基金: %s 只" % f"{len(common):,}")

print("\n" + "=" * 84)
print("口径识别：哪个指标的本项目百分位，与天天基金的同类百分位最相关？")
print("=" * 84)
print("  %-20s %10s %10s   %s" % ("本项目百分位", "Spearman", "样本数", "解读"))
results = {}
for k in METRICS:
    xs, ys = [], []
    for c in common:
        p = pct_of(c, k)
        if p is None:
            continue
        xs.append(p)
        ys.append(peer[c][0])
    rho = spearman(xs, ys)
    results[k] = {"spearman": rho, "n": len(xs)}
    hint = ""
    if rho == rho and rho > 0.5:
        hint = "★ 高相关：第三方印证成立"
    elif rho == rho and rho > 0.2:
        hint = "中等：口径部分重叠"
    else:
        hint = "低：口径大概率不同"
    print("  %-20s %10.3f %10s   %s" % (k, rho, f"{len(xs):,}", hint))

best = max(results, key=lambda k: (results[k]["spearman"] if results[k]["spearman"] == results[k]["spearman"] else -9))
print("\n  → 最相关的是 **%s**（ρ=%.3f），推测天天基金该字段对应此期限"
      % (best, results[best]["spearman"]))

# ---------- 4. 分组看：是否某些组相关更好 ----------
print("\n" + "=" * 84)
print("分组细看（用最相关的指标 %s）" % best)
print("=" * 84)
print("  %-16s %8s %10s %10s" % ("组", "样本数", "Spearman", "中位差"))
by_group = defaultdict(lambda: ([], []))
for c in common:
    p = pct_of(c, best)
    if p is None:
        continue
    by_group[grp[c]][0].append(p)
    by_group[grp[c]][1].append(peer[c][0])
for g in sorted(by_group):
    xs, ys = by_group[g]
    rho = spearman(xs, ys)
    med = float(np.median(np.abs(np.array(xs) - np.array(ys))))
    print("  %-16s %8s %10.3f %10.1f" % (g, f"{len(xs):,}", rho, med))

# ---------- 5. 排名/总数一致性交叉验证 ----------
print("\n" + "=" * 84)
print("交叉验证：peer_pct 是否 == (1 - rank/total) * 100 ？")
print("=" * 84)
ok = bad = 0
worst = []
for c, (p, r, t, d) in peer.items():
    if r is None or not t:
        continue
    expect = (1 - r / t) * 100
    if abs(expect - p) < 1.0:
        ok += 1
    else:
        bad += 1
        if len(worst) < 5:
            worst.append((c, p, r, t, round(expect, 2)))
print("  一致 %s / 不一致 %s" % (f"{ok:,}", f"{bad:,}"))
if worst:
    print("  不一致样本 (code, peer_pct, rank, total, 期望值):")
    for w in worst:
        print("   ", w)

json.dump({"results": results, "best": best,
           "by_group": {g: {"n": len(v[0]), "spearman": spearman(v[0], v[1])}
                        for g, v in by_group.items()},
           "n_peer": len(peer), "n_met": len(met), "n_common": len(common)},
          open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n结果:", os.path.relpath(OUT, ROOT))
conn.close()
