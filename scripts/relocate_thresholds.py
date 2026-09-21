# -*- coding: utf-8 -*-
"""
③ 阈值重定位 + ⑥ 样本分级重标定
==================================
两件事必须一起做：⑥ 决定「多少样本才够建分位」，③ 才能安全地把常数阈值换成分位。

────────────────────────────────────────────────────────────────
⑥ 样本分级：MIN_N_FULL / MID / IQR 原为 100/50/30，依据是 n=191 的 bootstrap。
   全量采集后 n 涨到 11,440（A 组 3,278 / E 组 3,172）→ **必须重做**（规则 8 + 规则 11）。
   方法：从大样本组里**有放回重抽 n 个**，看目标分位数的 90% 置信区间宽度 / 全样本 IQR。
        判据沿用原口径：比值 < 0.5 视为该样本量够用。

────────────────────────────────────────────────────────────────
③ 阈值重定位：把 8 个阈值逐个检查。其中 4 个依赖字段已由本轮补采复活。
   并且**主动挑战「用分位代替常数」这个从未被质疑过的范式**（规则 6）：
   对比「常数阈值」与「分位阈值」选出的基金集合差异有多大。
   若差异不显著 → 范式价值存疑，应诚实说出来。
"""
import io
import json
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from calibrate_thresholds import (  # noqa: E402
    DB_PATH, LABEL, METRICS, TYPE2GROUP, iter_navs, metrics,
)

OUT = os.path.join(ROOT, "docs", "threshold_relocation.json")
RNG = np.random.default_rng(20260921)

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}

print("=" * 88)
print("载入样本（流式）")
print("=" * 88)
met, grp = {}, {}
for code, dates, vals in iter_navs(conn, 756):
    m = metrics(vals, dates)
    if not m:
        continue
    g = TYPE2GROUP.get(fi.get(code, ""))
    if not g:
        continue
    met[code] = m
    grp[code] = g
groups = defaultdict(list)
for c in met:
    groups[grp[c]].append(c)
print("  样本 %s 只，分 %d 组：%s" % (f"{len(met):,}", len(groups),
                                     {g: len(v) for g, v in sorted(groups.items())}))

result = {"groups": {g: len(v) for g, v in groups.items()}}

# ══════════════════════════════════════════════════════════════
# ⑥ 样本分级重标定（bootstrap）
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 88)
print("⑥ 样本分级重标定：bootstrap 求「多少个样本才够建某个分位」")
print("=" * 88)
print("  判据：目标分位数的 90% bootstrap 置信区间宽度 / 全样本 IQR < 0.5")
print("  （沿用原口径，便于与旧的 100/50/30 直接对比）\n")

REF_G = "A 偏股混合"          # 用最大组当参照总体
ref_codes = groups[REF_G]
CAND = [20, 30, 40, 50, 75, 100, 150, 200, 300]
B_REP = 400

boot = {}
for k in ["max_drawdown_1y", "momentum_3m", "sharpe", "annual_return", "ann_vol"]:
    pop = np.array([met[c][k] for c in ref_codes], dtype=float)
    pop = pop[np.isfinite(pop)]
    if pop.size < 500:
        continue
    iqr = float(np.percentile(pop, 75) - np.percentile(pop, 25))
    row = {}
    for n in CAND:
        if n > pop.size:
            continue
        need = {}
        for p in (10, 25, 50, 75, 90):
            est = []
            for _ in range(B_REP):
                s = RNG.choice(pop, size=n, replace=True)
                est.append(np.percentile(s, p))
            lo, hi = np.percentile(est, [5, 95])
            need[p] = float((hi - lo) / iqr) if iqr > 0 else float("nan")
        row[n] = need
    boot[k] = row

print("  指标 / 样本量 → 置信区间宽度比（<0.5 合格）")
print("  %-16s" % "指标" + "".join(f"{'n='+str(n):>9}" for n in CAND))
min_ok = {}
for k, row in boot.items():
    cells = []
    first_ok = None
    for n in CAND:
        if n not in row:
            cells.append(f"{'-':>9}")
            continue
        worst = max(row[n].values())          # 最严：所有分位都要合格
        cells.append(f"{worst:>9.2f}")
        if worst < 0.5 and first_ok is None:
            first_ok = n
    print("  %-16s" % k + "".join(cells))
    min_ok[k] = first_ok
print()
for k, n in min_ok.items():
    print("  %-16s 最小可用样本量 = %s" % (k, n))
worst_all = max([n for n in min_ok.values() if n], default=None)
print("\n  → 全指标都合格的最小样本量 = **%s**" % worst_all)
print("    旧值 MIN_N_FULL=100；实测**%s**"
      % ("与旧值一致，无需改" if worst_all == 100 else "需要改为 %s" % worst_all))
result["bootstrap"] = {"min_ok": min_ok, "worst": worst_all,
                       "grid": {k: {str(n): v for n, v in row.items()} for k, row in boot.items()}}

# ══════════════════════════════════════════════════════════════
# ③ 现有常数阈值的位置 + 分位等价物
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 88)
print("③ 现有常数阈值在各类分布中的位置，及其分位等价物")
print("=" * 88)
CONSTS = [("max_drawdown_1y", 35.0, "<="), ("momentum_3m", 40.0, "<=")]

# 目标分位：以「保留最好的前 X%」为设计意图。旧常数在 A 组约位于 P80~P100，
# 因此给出三档候选供选择，并**明确标注通过率**，不替使用者拍板。
TARGET_PCTS = [75, 80, 90]

reloc = {}
for k, c, op in CONSTS:
    print("\n" + "-" * 88)
    print("阈值 %s（常数 %g，判据 %s）" % (LABEL[k], c, op))
    print("-" * 88)
    print("  %-14s %8s %10s %12s %12s %12s" % ("组", "n", "该常数位于", "常数通过率",
                                               "P75 等价值", "P90 等价值"))
    reloc[k] = {}
    for g in sorted(groups):
        codes = groups[g]
        arr = np.array([met[x][k] for x in codes], dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size < 30:
            continue
        pos = float((arr <= c).mean() * 100)
        p75 = float(np.percentile(arr, 75))
        p90 = float(np.percentile(arr, 90))
        reloc[k][g] = {"n": int(arr.size), "const_pos_pct": pos,
                       "const_pass_rate": pos, "p75": p75, "p90": p90}
        print("  %-14s %8s %9.0f%% %11.1f%% %12.2f %12.2f"
              % (g, f"{arr.size:,}", pos, pos, p75, p90))

# ---------- 规则 6：主动挑战「分位 vs 常数」范式 ----------
print("\n" + "=" * 88)
print("规则 6 挑战：「用分位代替常数」到底有没有实际差别？")
print("=" * 88)
print("  做法：对每组合格样本，比较「常数阈值选出的集合」与「分位阈值选出的集合」。")
print("       若差异很小，说明换分位只是形式变化 —— 那就该诚实说出来，别当卖点。\n")
challenge = {}
for k, c, op in CONSTS:
    print("  【%s】" % LABEL[k])
    print("    %-14s %10s %10s %10s %10s" % ("组", "常数选中", "P75 选中", "P90 选中", "常数∩P75"))
    for g in sorted(reloc[k]):
        codes = groups[g]
        vals = np.array([met[x][k] for x in codes], dtype=float)
        good = np.isfinite(vals)
        arr = vals[good]
        if arr.size < 30:
            continue
        by_const = arr <= c
        thr75 = np.percentile(arr, 75)
        thr90 = np.percentile(arr, 90)
        by75 = arr <= thr75
        by90 = arr <= thr90
        inter = float((by_const & by75).sum() / max(by75.sum(), 1) * 100)
        challenge.setdefault(k, {})[g] = {
            "n": int(arr.size), "sel_const": int(by_const.sum()),
            "sel_p75": int(by75.sum()), "sel_p90": int(by90.sum()),
            "overlap_const_vs_p75": inter}
        print("    %-14s %10s %10s %10s %9.1f%%"
              % (g, f"{by_const.sum():,}", f"{by75.sum():,}", f"{by90.sum():,}", inter))

result["relocation"] = reloc
result["challenge"] = challenge
json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n结果:", os.path.relpath(OUT, ROOT))
conn.close()
