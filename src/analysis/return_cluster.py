"""收益驱动的同类细分（M2）—— 用收益结构聚类，给出「同类」的第二层细分。

依据（`docs/审计修复记录.md` 第四批 §8 ③④）
------------------------------------------
簇在 train 半段拟合、gap 在 **test 半段**评估（防前视）：

| 分组方式 | test-gap |
|---|---|
| **类型桶**（4 组） | +0.049 |
| KMeans **k=4**（同组数） | +0.074（**仅 +0.025**） |
| KMeans k=2 | +0.135 |
| **KMeans k=7**（最佳） | **+0.161（3.3 倍）** |

逐列打乱零模型：真数据 silhouette **0.1418 ± 0.0067** vs 打乱基线 **0.0104 ± 0.0016**
→ 收益空间**确有结构**（14 倍）。

⚠️ **该节同时推翻了 §2 的排序依据**（原话）：`fund_type` 的**粗分类（债券/权益）是有效的**
（gap 0.413），权益类**内部**细分价值有限（gap 0.054）；收益聚类**同组数下提升有限**（+0.025），
**放开组数**才明显（k=7 → +0.113）。

⇒ 所以本模块的定位是：**「同类」的第二层细分**（在既有类型桶**之内**再分簇），
**不是**整体替换类型分组 —— 后者是本项目明确否掉的设计决策。

方法
----
**PCA 降维 + KMeans**（与审计同法，保证结论可复现）。KMeans 手写（numpy），
因为项目铁律「不新增运行时依赖」，而 sklearn 只为这一处不值得。

簇数怎么选
----------
不自动挑 —— `pick_k()` 在给定范围内按 **silhouette** 选最优并**报出全部候选**，
让调用方看到"选 k=7 比 k=4 好多少"这个事实，而不是替它决定。
"""
from __future__ import annotations

import math

import numpy as np

DEFAULT_K_RANGE = (2, 8)


def _kmeans_pp(X: np.ndarray, k: int, rng) -> np.ndarray:
    """k-means++ 初始化：选彼此尽量远的初始质心（比随机初始化稳得多）。"""
    n = X.shape[0]
    centers = [X[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min(np.stack([((X - c) ** 2).sum(axis=1) for c in centers]), axis=0)
        total = d2.sum()
        if total <= 0:
            centers.append(X[rng.integers(n)])
            continue
        probs = d2 / total
        centers.append(X[rng.choice(n, p=probs)])
    return np.stack(centers)


def kmeans(X, k: int, seed: int = 42, max_iter: int = 200, tol: float = 1e-7) -> tuple:
    """KMeans（numpy 手写，零依赖）。返回 `(labels, centers, inertia)`。"""
    X = np.asarray(X, dtype=float)
    if X.ndim != 2 or X.shape[0] == 0:
        return np.array([], dtype=int), np.zeros((0, 0)), float("nan")
    k = max(1, min(int(k), X.shape[0]))
    rng = np.random.default_rng(seed)
    C = _kmeans_pp(X, k, rng)
    labels = np.zeros(X.shape[0], dtype=int)
    for _ in range(max_iter):
        d = np.stack([((X - c) ** 2).sum(axis=1) for c in C], axis=1)
        new_labels = d.argmin(axis=1)
        if np.array_equal(new_labels, labels):
            labels = new_labels
            break
        labels = new_labels
        for j in range(k):
            m = labels == j
            if m.any():
                C[j] = X[m].mean(axis=0)
    d = np.stack([((X - c) ** 2).sum(axis=1) for c in C], axis=1)
    inertia = float(d.min(axis=1).sum())
    return labels, C, inertia


def pca(X, n_components: int = 5) -> tuple:
    """PCA（SVD 实现，零依赖）。返回 `(得分, 解释方差比)`。中心化在内部完成。"""
    X = np.asarray(X, dtype=float)
    if X.ndim != 2 or X.shape[0] < 2:
        return np.zeros((X.shape[0] if X.ndim == 2 else 0, 0)), np.array([])
    Xc = X - X.mean(axis=0, keepdims=True)
    # 样本数少于特征数时用经济型 SVD，避免生成巨大的 U
    U, S, _ = np.linalg.svd(Xc, full_matrices=False)
    k = max(1, min(int(n_components), S.size))
    scores = U[:, :k] * S[:k]
    var = (S ** 2) / max(X.shape[0] - 1, 1)
    total = var.sum()
    ratio = (var[:k] / total) if total > 0 else np.zeros(k)
    return scores, ratio


def silhouette(X, labels) -> float:
    """平均轮廓系数（-1~1，越大越"分得开"）。用欧氏距离，O(n²) —— 抽样调用。"""
    X = np.asarray(X, dtype=float)
    labels = np.asarray(labels)
    n = X.shape[0]
    uniq = np.unique(labels)
    if n < 2 or uniq.size < 2:
        return float("nan")
    s = []
    for i in range(n):
        same = (labels == labels[i])
        same[i] = False
        if not same.any():
            s.append(0.0)
            continue
        a = float(np.sqrt(((X[i] - X[same]) ** 2).sum(axis=1)).mean())
        b = float("inf")
        for j in uniq:
            if j == labels[i]:
                continue
            m = labels == j
            if m.any():
                b = min(b, float(np.sqrt(((X[i] - X[m]) ** 2).sum(axis=1)).mean()))
        s.append(0.0 if max(a, b) <= 0 else (b - a) / max(a, b))
    return float(np.mean(s))


def cluster_returns(returns_matrix, *, k: int = None, k_range: tuple = DEFAULT_K_RANGE,
                    n_components: int = 5, seed: int = 42, sample_for_silhouette: int = 300):
    """收益矩阵 → 簇标签 + 诊断。

    Args:
        returns_matrix: `(n_funds, n_periods)` 的收益率数组（行=基金，列=期）。
                        调用方负责对齐期数（本项目惯例是**近 3 年周收益**）。
        k: 指定簇数；`None` 时在 `k_range` 内按 silhouette 选最优。
        k_range: 候选簇数范围（含端点）。
        n_components: PCA 保留的主成分数（审计实验用前几个 PC）。
        sample_for_silhouette: silhouette 是 O(n²)，只抽样算（`None` = 全量，慢）。

    Returns:
        `{labels, k, k_scores, explained_var, silhouette, note}`
        — `k_scores` 列出**全部候选**的得分（不只报最优，让调用方看到权衡）。
    """
    X = np.asarray(returns_matrix, dtype=float)
    if X.ndim != 2 or X.shape[0] < 4:
        return {"labels": [], "k": None, "k_scores": {}, "explained_var": None,
                "silhouette": None, "note": "样本不足（<%d 只）" % 4}
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    scores, ratio = pca(X, n_components)
    if scores.size == 0 or scores.shape[1] == 0:
        return {"labels": [], "k": None, "k_scores": {}, "explained_var": None,
                "silhouette": None, "note": "PCA 失败"}
    Z = scores

    rng = np.random.default_rng(seed)
    idx = (np.arange(Z.shape[0]) if not sample_for_silhouette
           or Z.shape[0] <= sample_for_silhouette
           else rng.choice(Z.shape[0], sample_for_silhouette, replace=False))

    k_scores = {}
    chosen_labels = None
    if k is not None:
        chosen_labels, _, _ = kmeans(Z, int(k), seed=seed)
    else:
        best = (-2.0, None, None)
        for kk in range(int(k_range[0]), int(k_range[1]) + 1):
            if kk >= Z.shape[0]:
                break
            lb, _, _ = kmeans(Z, kk, seed=seed)
            sil = silhouette(Z[idx], lb[idx])
            k_scores[kk] = round(float(sil), 4) if math.isfinite(sil) else None
            if math.isfinite(sil) and sil > best[0]:
                best = (sil, kk, lb)
        if best[1] is None:
            return {"labels": [], "k": None, "k_scores": k_scores, "explained_var": None,
                    "silhouette": None, "note": "所有候选簇数都算不出轮廓系数"}
        chosen_labels = best[2]

    sil = silhouette(Z[idx], np.asarray(chosen_labels)[idx])
    kk = int(np.unique(chosen_labels).size)
    note = ("收益聚类细分：k=%d，轮廓 %.3f，PC1~%d 解释 %.1f%% 方差（审计基线：真数据 0.142 / "
            "打乱 0.010）" % (kk, sil, min(n_components, ratio.size), ratio.sum() * 100))
    return {"labels": [int(x) for x in chosen_labels], "k": kk, "k_scores": k_scores,
            "explained_var": [round(float(r), 4) for r in ratio],
            "silhouette": round(float(sil), 4) if math.isfinite(sil) else None,
            "note": note}
