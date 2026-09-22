#!/usr/bin/env python3
"""
同类百分位参照系（设计方案 §4.2/§4.3 的实现）

把「绝对分数」换成「该基金在同类群体分布中的百分位」——
这是本项目对毕设 DanmakuPulse「市场参照系」范式的迁移。

原则（毕设的 SSOT 教训）：**指标计算与分组口径只在一处实现**，
本脚本直接 import `scripts/calibrate_thresholds.py`，不重复实现。
`calibrate_thresholds.py` 是阈值与分位的**单一事实源**。

只读：不写任何表。产出到 stdout 与可选的 JSON。

用法:
    python scripts/fund_percentile.py                      # 各组 Top10
    python scripts/fund_percentile.py --group "A 偏股混合"
    python scripts/fund_percentile.py --json out.json       # 落 JSON 供后续入库
    python scripts/fund_percentile.py --min-days 252        # 放宽到 1 年
"""
import argparse
import json
import os
import sqlite3
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

# ── SSOT：复用校准脚本的口径 ──────────────────────────────
from calibrate_thresholds import (        # noqa: E402
    DB_PATH, LABEL, METRICS, grade, load_navs,
)

# ── SSOT：百分位计算与方向/权重定义已下沉到 src，脚本与 API 共用同一份实现 ──
# （原在本文件的 DIRECTION / WEIGHT / DISPLAY_ONLY / pct_of 已上移，
#   见 src/analysis/peer_percentile.py。不要在这里重新定义。）
from src.analysis.peer_percentile import (  # noqa: E402
    DIRECTION, DISPLAY_ONLY, WEIGHT, build_group_arrays, pct_of,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=756)
    ap.add_argument("--group", default=None, help="只看某一组（如 'A 偏股混合'）")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", default=None, help="把全部结果落成 JSON")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    name = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_name from fund_info")}
    # 分组 + 指标组装走共用实现（src/analysis/peer_percentile.py），脚本不自己来一遍。
    # ⚠️ 不要在这里 conn.close()：navs 是**惰性**的（NavStream），关闭连接会让迭代失败。
    #    连接在 main 结束时才关。
    groups, unmapped, _ = build_group_arrays(args.min_days, conn=conn)
    if unmapped:
        from collections import Counter
        print(f"⚠️ 未映射类型 {sum(unmapped.values())} 只（显式报告，不静默丢弃）："
              f"{dict(unmapped)}\n")

    payload = {}
    for g, rows in sorted(groups.items()):
        if args.group and g != args.group:
            continue
        n = len(rows)
        lvl, _ = grade(n)
        flag = {"full": "全量分位", "mid": "中位+IQR", "iqr_only": "仅中位+IQR",
                "insufficient": "样本不足"}[lvl]
        print("=" * 96)
        print(f"【{g}】 n={n}  {flag}")
        print("=" * 96)
        if lvl == "insufficient":
            print("  样本 <30，不建参照系（退化为「与同类中位数比较」并声明）\n")
            continue

        # 各组分布（参照系本身）
        dist = {}
        for k in METRICS:
            arr = np.array([r[k] for r in rows], dtype=float)
            dist[k] = arr[np.isfinite(arr)]

        # 每只基金 → 各维百分位 + 综合分
        for r in rows:
            ps = {}
            for k in METRICS:
                hb = DIRECTION.get(k)
                if dist[k].size == 0:
                    continue
                # hb=None（如动量）→ 只做**位置**展示，不参与优劣判断
                p = pct_of(dist[k], r[k], True if hb is None else hb)
                if p is not None:
                    ps[k] = p
            score = sum(ps.get(k, 50.0) * w for k, w in WEIGHT.items())
            r["_pct"] = ps
            r["_composite"] = score

        rows.sort(key=lambda x: x["_composite"], reverse=True)

        print(f"  {'基金':30} {'综合':>6} " + " ".join(f"{LABEL[k][:6]:>7}" for k in WEIGHT) +
              "   │ 仅展示 " + " ".join(f"{LABEL[k][:6]:>7}" for k in DISPLAY_ONLY))
        print("  " + "-" * 92)
        for r in rows[:args.top]:
            nm = (r["_name"] or r["_code"])[:28]
            cells = " ".join(f"{r['_pct'].get(k, float('nan')):>7.0f}" for k in WEIGHT)
            extra = " ".join(f"{r['_pct'].get(k, float('nan')):>7.0f}" for k in DISPLAY_ONLY)
            print(f"  {nm:30} {r['_composite']:>6.1f} {cells}   │         {extra}")
        print()

        # 单只基金的完整输出形态（设计方案 §4.3）
        if rows:
            top = rows[0]
            missing = [LABEL.get(k, k) for k in METRICS if k not in top["_pct"]]
            print("  ── 输出形态示例（设计方案 §4.3）──")
            print(f"  {top['_name']}（{top['_code']}）   组别：{g}（同类 {n} 只）")
            for k in ["annual_return", "max_drawdown_1y", "sharpe", "ann_vol", "momentum_3m"]:
                if k not in top["_pct"]:
                    continue
                p = top["_pct"][k]
                arrow = "↑" if p >= 60 else ("↓" if p <= 40 else "→")
                print(f"    {LABEL[k]:14} {top[k]:>9.2f}   同类 P{p:.0f}  {arrow}")
            # ⚠️ 2026-09-21 更正：此处原写「未评估：规模·费率·经理年限（数据缺失）」。
            #    该断言已过期 —— 全量补采（scripts/collect_fund_profile.py）已把
            #    fund_size 填到 66.9%（覆盖池内 99.7%）、manager_tenure 填到 67.0%。
            #    但这些字段目前**尚未进入本脚本的打分链路**（本脚本只做净值维度的百分位），
            #    所以说「未纳入打分」是对的，说「数据缺失」是错的。
            print(f"    ⚠️ 未纳入本脚本打分：规模 · 费率 · 经理年限"
                  f"（数据已具备，见 fund_profile_extra，待接入筛选链路）")
            print(f"    ⚠️ 样本受限：本组 {n} 只，{flag}")
            print(f"    ⚠️ 幸存者偏差：仅统计净值 ≥{args.min_days} 天的存续基金")
            print()

        payload[g] = {
            "n": n, "quality_level": lvl,
            "distribution": {k: {"p10": float(np.percentile(v, 10)),
                                 "p25": float(np.percentile(v, 25)),
                                 "p50": float(np.percentile(v, 50)),
                                 "p75": float(np.percentile(v, 75)),
                                 "p90": float(np.percentile(v, 90))}
                             for k, v in dist.items() if v.size},
            "funds": [{"code": r["_code"], "name": r["_name"], "type": r["_type"],
                       "composite": round(r["_composite"], 1),
                       "pct": {k: round(v, 1) for k, v in r["_pct"].items()},
                       "raw": {k: (None if not np.isfinite(r[k]) else round(float(r[k]), 4))
                               for k in METRICS}}
                      for r in rows],
        }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"已落 JSON: {args.json}")

    print("=" * 96)
    print("口径与限制（必须连同结果一起展示，不得单独引用百分位）")
    print("=" * 96)
    print("  · 净值用 acc_nav（累计净值），避免分红除息污染回撤")
    print("  · 综合分只用**相互独立**的两维：夏普 0.6 + 回撤 0.4（实测 r=-0.04）")
    print("    年化收益与夏普 r=0.97、年化波动与回撤 r=0.78 → 只展示、不计分，避免重复计分")
    print("  · 动量只做**位置**展示（P95 = 涨最多），不代表优劣，用于追涨警示")
    print("  · 幸存者偏差：只统计存续且有足够历史的基金，分布本身偏乐观")
    print("  · 权重 0.6/0.4 属【推测】，待 ML 文档 §3 的 L1 评估台检验")
    print("  · 参照系随时间漂移，正式上线前须做逐月稳定性验证（设计方案 §4.5）")
    print("  · 本输出为客观统计对比，不构成投资建议")
    conn.close()


if __name__ == "__main__":
    main()
