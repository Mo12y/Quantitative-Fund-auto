# -*- coding: utf-8 -*-
"""同类结构体检（M2）—— 「同类基金」的收益结构是否真有分层？（**只读**）

为什么要这个脚本
----------------
本项目的 `peer_percentile` 整条链路（同类分位 → 质量分 → 筛选池排序）建立在
「同一 `fund_type` = 可比同类」这个**从未被验证过**的假设上。
`docs/审计修复记录.md` 第四批 §8 做过一次深度验证（结论：类型桶粗分类有效，
但**权益类内部细分价值有限**；收益聚类**放开组数**才有明显提升）。

⚠️ 当时那份证据脚本在 `D:\DSH\scratch_hold\`，**scratch 一清就永久丢失** ——
本脚本是它的**固化版**（审计第四批明确列为待办）。

与审计版的两点差别
------------------
1. **零依赖**：用项目自带的 `src/analysis/return_cluster`（numpy 手写 KMeans/PCA），
   审计版用的是 sklearn（开发工具，不进 `requirements.txt`）。
2. **精简**：本脚本只做**两件**最容易复现的事 ——
   ① 真结构 vs 逐列打乱 的轮廓对比；② 候选簇数的轮廓曲线。
   完整的置换零分布 / 分半纪律见审计原文 §8（那部分依赖更重的实验设计）。

用法
----
    python scripts/cluster_peer_check.py                # 默认 3 个组、每组最多 300 只
    python scripts/cluster_peer_check.py --groups A,E --max 500

判读
----
· **真结构轮廓 ≈ 打乱轮廓** → 该组收益空间没有分层，细分无意义（**不要硬分**）。
· 真结构**明显高于**打乱（审计基线：0.142 vs 0.010，14 倍）→ 有结构，可考虑细分。
· `k_scores` 里 k 越大越好且**未被 max 截断** → 说明"细分粒度不够"，值得放开簇数。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from src.data.database import Database  # noqa: E402
from src.analysis.return_cluster import cluster_returns  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")
WEEKS = 152          # 审计口径：近 3 年周收益


def load_returns(conn, fund_type: str, max_n: int):
    """取某类型基金的对齐周收益矩阵 `(n, WEEKS)`。净值不足的直接跳过（不补、不猜）。"""
    codes = [r[0] for r in conn.execute(
        "SELECT fund_code FROM fund_info WHERE fund_type = ?", (fund_type,))]
    if not codes:
        return np.zeros((0, 0))
    ph = ",".join("?" * len(codes))
    frame = {}
    for code, d, nav in conn.execute(
            "SELECT fund_code, nav_date, unit_nav FROM fund_nav "
            "WHERE fund_code IN (%s) ORDER BY nav_date" % ph, codes):
        if nav:
            frame.setdefault(code, []).append(float(nav))
    rows = [np.asarray(v[-WEEKS:], dtype=float) for v in frame.values()
            if len(v) >= WEEKS]
    if len(rows) < 8:
        return np.zeros((0, 0))
    M = np.stack(rows[:max_n])
    R = M[:, 1:] / M[:, :-1] - 1.0
    R = np.where(np.isfinite(R), R, 0.0)
    return R


def shuffle_columns(R, seed=42):
    """逐列打乱（审计 §8 ④ 的零模型）：破坏跨维结构，保留各期边缘分布。"""
    rng = np.random.default_rng(seed)
    S = R.copy()
    for j in range(S.shape[1]):
        rng.shuffle(S[:, j])
    return S


def separation_gap(R, labels):
    """审计 §8 的 **separation gap** = 组内平均相关 − 组间平均相关。

    为什么还要这个（不能只看 silhouette）：两者测的不是一回事 ——
    · silhouette 看"簇内紧凑 vs 簇间分离"（几何）；
    · gap 看"同组是否比跨组更同涨同跌"（相关结构）。
    审计的 k=7 结论是用 **gap** 得出的；只用 silhouette 会得出 k=2~3，
    所以**两个都报**，避免"换个指标结论就翻"却没人发现。
    数据量 O(n²)，故对列抽样（最多 40 周）控制耗时。
    """
    R = np.asarray(R, dtype=float)
    labels = np.asarray(labels)
    n = R.shape[0]
    if n < 4 or R.shape[1] < 3:
        return None
    sub = R[:, :min(40, R.shape[1])]
    # 相关系数矩阵（对基金）
    sd = sub.std(axis=1, keepdims=True)
    sd[sd == 0] = 1e-12
    Z = (sub - sub.mean(axis=1, keepdims=True)) / sd
    C = (Z @ Z.T) / sub.shape[1]
    uniq = np.unique(labels)
    if uniq.size < 2:
        return None
    same = (labels[:, None] == labels[None, :])
    iu = np.triu_indices(n, k=1)
    in_c = C[iu][same[iu]]
    out_c = C[iu][~same[iu]]
    if in_c.size == 0 or out_c.size == 0:
        return None
    return float(in_c.mean() - out_c.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="A,E,D",
                    help="组字母（A 偏股混合 / B 灵活配置 / C 主动股票 / D 指数股票 / E 债券 / F QDII）")
    ap.add_argument("--max", type=int, default=300, help="每组最多取多少只")
    args = ap.parse_args()

    # 组字母 → fund_type（与 calibrate_thresholds.TYPE2GROUP 一致）
    from scripts.calibrate_thresholds import TYPE2GROUP  # noqa: E402
    rev = {}
    for t, g in TYPE2GROUP.items():
        rev.setdefault(g[0] if isinstance(g, tuple) else str(g)[0], t)

    db = Database(DB)
    try:
        conn = db.conn
        print("同类结构体检（近 %d 周周收益，只读）" % WEEKS)
        print()
        print("%-4s %-16s %5s %9s %9s %8s %8s  %s" % (
            "组", "类型", "n", "真结构", "打乱后", "倍数", "gap", "候选 k 轮廓"))
        for letter in [x.strip() for x in args.groups.split(",") if x.strip()]:
            ftype = rev.get(letter)
            if not ftype:
                print("%-4s (未知组字母)" % letter)
                continue
            R = load_returns(conn, ftype, args.max)
            if R.shape[0] < 12:
                print("%-4s %-16s 样本不足（%d）" % (letter, ftype, R.shape[0]))
                continue
            real = cluster_returns(R, k_range=(2, 8), n_components=5)
            shuf = cluster_returns(shuffle_columns(R), k_range=(2, 8), n_components=5)
            a = real.get("silhouette")
            b = shuf.get("silhouette")
            ratio = ("%.1fx" % (a / b)) if (a and b and b > 1e-9) else "n/a"
            gap = separation_gap(R, real["labels"]) if real.get("labels") else None
            shuf_gap = (separation_gap(R, shuf["labels"])
                        if shuf.get("labels") else None)
            ks = ", ".join("k%s:%.3f" % (k, v) for k, v in sorted(real["k_scores"].items())
                           if v is not None)
            print("%-4s %-16s %5d %9s %9s %8s %8s  %s" % (
                letter, ftype[:16], R.shape[0], a, b, ratio,
                ("%.4f" % gap if gap is not None else "n/a"), ks))
            if gap is not None and shuf_gap is not None:
                print("     └ separation gap：聚类 %.3f vs 打乱 %.3f（审计基线：类型桶 0.049、"
                      "聚类 k=7 0.161）" % (gap, shuf_gap))
        print()
        print("判读：真结构 ≈ 打乱 → 该组无分层，**不要硬分**；")
        print("      真结构 >> 打乱（审计基线 14 倍）→ 有结构，可考虑第二层细分；")
        print("      k 越大越好且未被截断 → 细分粒度不够，值得放开簇数。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
