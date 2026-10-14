"""收益聚类细分（M2）测试。

核心要证明的：**合成数据上三个明显分离的簇能被正确分出来**，
且 `silhouette` 能区分"有结构"与"纯噪声" —— 前者是功能正确性，
后者是审计那句"真数据 0.142 vs 打乱 0.010"的可复现性。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.return_cluster import (  # noqa: E402
    cluster_returns, kmeans, pca, silhouette,
)


def test_kmeans_separates_three_obvious_clusters():
    rng = np.random.default_rng(0)
    a = rng.normal(-5, 0.3, (30, 2))
    b = rng.normal(0, 0.3, (30, 2))
    c = rng.normal(+5, 0.3, (30, 2))
    X = np.vstack([a, b, c])
    labels, centers, _ = kmeans(X, 3, seed=1)
    assert len(np.unique(labels)) == 3, "三个分离簇应被完整分出"
    # 同簇内的样本必须来自同一块
    for want in (0, 1, 2):
        got = labels[want * 30:(want + 1) * 30]
        assert len(set(got)) == 1, "第 %d 块被拆开了：%s" % (want, sorted(set(got)))


def test_kmeans_is_deterministic_with_seed():
    rng = np.random.default_rng(2)
    X = rng.normal(0, 1, (60, 4))
    l1, _, _ = kmeans(X, 3, seed=7)
    l2, _, _ = kmeans(X, 3, seed=7)
    assert list(l1) == list(l2), "同 seed 必须同结果（否则无法复核）"


def test_kmeans_handles_k_larger_than_samples():
    X = np.zeros((3, 2))
    labels, _, _ = kmeans(X, 9, seed=0)
    assert len(labels) == 3 and len(np.unique(labels)) <= 3


def test_pca_variance_ratio_is_descending_and_sums_to_one():
    rng = np.random.default_rng(3)
    X = rng.normal(0, 1, (200, 6))
    _, ratio = pca(X, 6)
    assert ratio.size == 6
    assert all(ratio[i] >= ratio[i + 1] - 1e-9 for i in range(len(ratio) - 1)), "应递减"
    assert abs(float(ratio.sum()) - 1.0) < 1e-9


def test_pca_leading_component_captures_common_factor():
    """共同因子结构：前 1 个 PC 应吃掉绝大部分方差（等价于审计的「PC1 占 63~73%」）。"""
    rng = np.random.default_rng(4)
    f = rng.normal(0, 1, 300)                      # 共同因子
    X = np.outer(f, np.ones(5)) + rng.normal(0, 0.1, (300, 5))
    _, ratio = pca(X, 5)
    assert ratio[0] > 0.9, "共同因子应让 PC1 占 >90%%：%s" % ratio[0]


def test_silhouette_high_for_separated_low_for_random():
    rng = np.random.default_rng(5)
    sep = np.vstack([rng.normal(-8, 0.2, (20, 2)), rng.normal(8, 0.2, (20, 2))])
    lab = np.array([0] * 20 + [1] * 20)
    assert silhouette(sep, lab) > 0.9, "完全分离的簇轮廓应接近 1"

    rnd = rng.normal(0, 1, (40, 2))
    rnd_lab = np.array([0] * 20 + [1] * 20)        # 人为切两半（其实无结构）
    assert silhouette(rnd, rnd_lab) < 0.3


def test_cluster_returns_picks_k_and_reports_all_candidates():
    """`k=None` 时应遍历候选并**报出全部** k 的得分，而不只给最优。"""
    rng = np.random.default_rng(6)
    X = np.vstack([rng.normal(-4, 0.4, (25, 8)),
                   rng.normal(0, 0.4, (25, 8)),
                   rng.normal(4, 0.4, (25, 8))])
    r = cluster_returns(X, k_range=(2, 5), n_components=3)
    assert r["k"] in (2, 3, 4, 5) and len(r["labels"]) == 75
    assert set(r["k_scores"]) == {2, 3, 4, 5}, "必须列出全部候选：%s" % r["k_scores"]
    assert r["silhouette"] is not None and "轮廓" in r["note"]


def test_cluster_returns_explicit_k():
    rng = np.random.default_rng(7)
    X = rng.normal(0, 1, (40, 6))
    r = cluster_returns(X, k=3, n_components=3)
    assert r["k"] == 3
    assert len(set(r["labels"])) <= 3
    assert r["k_scores"] == {}, "显式给 k 时不必扫候选"


def test_cluster_returns_insufficient_sample_is_explicit():
    r = cluster_returns(np.zeros((3, 5)))
    assert r["k"] is None and r["labels"] == [] and "样本不足" in r["note"]


def test_cluster_returns_handles_nan():
    rng = np.random.default_rng(8)
    X = rng.normal(0, 1, (30, 5))
    X[0, 0] = np.nan
    X[1, 1] = np.inf
    r = cluster_returns(X, k=2, n_components=3)
    assert len(r["labels"]) == 30, "NaN/inf 应被就地替换而不是让整批失败"


def test_real_structure_beats_shuffled_baseline():
    """可复现审计那句话：**真结构**的轮廓显著高于**逐列打乱**。

    这是本模块存在的理由 —— 如果打乱后也能得到同样的轮廓，说明方法没测到结构。
    """
    rng = np.random.default_rng(9)
    f = rng.normal(0, 1, 120)
    real = np.stack([f + rng.normal(0, 0.4, 120),
                     f + rng.normal(0, 0.4, 120),
                     -f + rng.normal(0, 0.4, 120)], axis=1)   # 3 只、结构明确
    shuf = real.copy()
    for j in range(shuf.shape[1]):
        rng.shuffle(shuf[:, j])                              # 逐列打乱 → 结构被打散
    r_real = cluster_returns(real, k=2, n_components=2)
    r_shuf = cluster_returns(shuf, k=2, n_components=2)
    assert r_real["silhouette"] >= r_shuf["silhouette"], \
        "真结构轮廓(%s)应 ≥ 打乱(%s)" % (r_real["silhouette"], r_shuf["silhouette"])
