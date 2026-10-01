# -*- coding: utf-8 -*-
"""构建 `corr_overlap`「相对同类基线」的**类对底数** → `data/overlap_baselines.json`（**只读账本**）。

为什么要有它：绝对阈值 0.8 对不同类对没有意义 —— 实测（1 年窗、与线上同一估计器）：

    类对                               μ      σ
    指数型-股票 × 指数型-股票（类内）      0.46   0.35
    混合型-偏股 × 混合型-偏股（类内）      0.40   0.37
    指数型-股票 × 指数型-海外股票        0.31   0.21
    指数型-股票 × 债券型-长债           −0.05   0.12

跨类差异比"0.8 还是 0.7"大得多 → 判定改为**相对该类对基线偏高**：
门限 = `max(μ + k·σ, 绝对底线)`（k / 底线是政策，在 `user_constraint`，默认 k=1.0、底线 0.60）。

产物是**运行时候选**（由 `user_constraint` 的相对模式消费）；缺失时该约束对相关候选
声明「未评估」（skipped），**不会**猜成通过。

用法：
    python scripts/build_overlap_baselines.py                      # 默认 data/overlap_baselines.json
    python scripts/build_overlap_baselines.py --sample 60 --window-days 500
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import portfolio_overlap as po          # noqa: E402
from src.analysis.user_constraint import MIN_BASELINE_PAIRS  # noqa: E402
from src.data.database import Database                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=po.DEFAULT_SAMPLE_K, help="每类抽样只数（默认 50）")
    ap.add_argument("--seed", type=int, default=po.DEFAULT_SEED, help="抽样随机种子（默认 42，可复现）")
    ap.add_argument("--window-days", type=int, default=po.DEFAULT_WINDOW_DAYS,
                    help="周收益回看窗（默认 400 天，与线上估计器一致）")
    ap.add_argument("--out", default=po.BASELINE_PATH,
                    help="输出 JSON（默认 data/overlap_baselines.json）")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("找不到账本库：%s" % DB)
    db = Database(DB)
    try:
        data = po.build_baselines(db.conn, sample_k=args.sample, seed=args.seed,
                                  window_days=args.window_days)
    finally:
        db.close()
    path = po.save_baselines(data, args.out)

    print("=" * 92)
    print("类对基线已生成：%s" % path)
    print("as_of=%s ｜ 窗 %d 天（since %s）｜ 每类抽样 %d ｜ seed=%d"
          % (data["as_of"], data["window_days"], data["since"], data["sample_k"], data["seed"]))
    print("=" * 92)
    print("%-36s %6s %7s %7s %7s %7s" % ("类对", "对数", "mu", "sigma", "P90", "P95"))
    for key in sorted(data["pairs"]):
        p = data["pairs"][key]
        flag = "" if p["n_pairs"] >= MIN_BASELINE_PAIRS else \
            "  ⚠ 样本不足(<%d)，评估时会声明未评估" % MIN_BASELINE_PAIRS
        print("%-36s %6d %7.3f %7.3f %7.3f %7.3f%s" % (
            key, p["n_pairs"], p["mu"], p["sigma"], p["p90"], p["p95"], flag))
    print()
    print("类别覆盖（存活 = 近 %d 天有净值）:" % data["active_days"])
    for ft, m in sorted(data["classes"].items()):
        print("  %-22s 存活 %5d / 抽样 %3d / 有效序列 %3d"
              % (ft, m["n_universe_active"], m["n_sample"], m["n_series"]))
    print()
    print("阈值政策（user_constraint）：门限 = max(mu + k*sigma, 底线)，默认 k=1.0、底线 0.60；")
    print("可在画像 config/user_profile.local.yaml 用 corr_relative: {k, floor} 覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())