#!/usr/bin/env python3
"""
参照系稳定性验证（设计方案 §4.5）—— **修正版**

## 为什么不能照搬毕设的判定口径

首版曾直接套用毕设 `g02_stability.py` 的「中位数相对漂移 <15% + MW p>0.05」，
实测后发现**两处根本不适配**：

1. **15% 是毕设自己定的**（原文：「判定（登记册 G02 验证标准）」），并非来自文献。
   把它搬到基金项目，等于又造了一个"拍脑袋阈值" —— 正是本项目反复诊断的病因。

2. **更根本：基金分布漂移是预期行为，不是缺陷。**
   毕设测的是「弹幕节奏」（内容属性，不该随时间变）；
   基金测的是「收益/回撤/夏普」—— 市场环境变了，全体中位数**本来就该变**。
   2023 跌、2024 反弹导致中位数从 9.65%→6.54%，那是**参照系如实记录了市场**，
   判成"不稳定"是误用检验。

## 因此改为测**排名稳定性**

推荐系统用的是**百分位**而非绝对中位数。所以真正该问的是：

    「今天 Top20 的基金，下个月还在 Top20 吗？」

标准工具：**相邻期 Spearman 排名相关** + **Top-K 留存率**。

风控纪律：
    · 只读，不写任何表
    · **严格无前视**：t 时点只用 <= t 的数据算指标

用法:
    python scripts/verify_benchmark_stability.py
    python scripts/verify_benchmark_stability.py --group "A 偏股混合"
    python scripts/verify_benchmark_stability.py --topk 20
"""
import argparse
import os
import sqlite3
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from calibrate_thresholds import (   # noqa: E402
    DB_PATH, LABEL, METRICS, MIN_N_IQR, TRADING_DAYS, TYPE2GROUP,
)

# ── 判定阈值（**明确标注为本项目自定，待实测校准** —— 同 THRESHOLDS 的教训）──
RANK_CORR_MIN = 0.90        # 相邻期 Spearman 排名相关下限
TOPK_RETENTION_MIN = 0.60   # Top-K 留存率下限

# 综合分只取相互独立维度（实测 夏普↔回撤 r=-0.04；夏普↔收益 0.97、回撤↔波动 0.78）
SCORE_W = {"sharpe": 0.6, "max_drawdown_1y": 0.4}
SCORE_DIR = {"sharpe": True, "max_drawdown_1y": False}


def metrics_at(vals, idx, dates=None):
    """只用 vals[:idx] 计算指标（严格无前视）。

    ⚠️ **必须传 dates**：净值序列可能有缺口（实测 576 只里 135 只点数明显少于
    应有交易天数，最严重比值 0.437）。用「点数/252」当年数会把年化**严重高估**
    （实测债券基金最坏高估 135%）。年化与近1年回撤一律用**日期**而非点数。

    ⚠️ `dates` 应是**已经是 Python list**（调用方在流式循环外转换一次）。
    旧版这里写 `list(dates[:idx])`，在 45 个月末 × 1.6 万只 = 72 万次调用下，
    每次都要把 numpy 字符串数组转成 Python list —— 约 14 亿次转换，实测会跑几十分钟。
    """
    import bisect
    from datetime import date as _d, timedelta as _td

    v = vals[:idx]
    n = v.size
    if n < 60:
        return None
    ret = v[-1] / v[0] - 1

    if dates is not None and len(dates) >= idx:
        ds = dates[:idx] if isinstance(dates, list) else list(dates[:idx])
        try:
            years = max((_d.fromisoformat(str(ds[-1])) - _d.fromisoformat(str(ds[0]))).days / 365.25, 1e-6)
        except Exception:
            years = n / TRADING_DAYS
    else:
        ds = None
        years = n / TRADING_DAYS
    ann_ret = (1 + ret) ** (1.0 / years) - 1

    d = np.diff(v) / v[:-1]
    ann_vol = float(d.std(ddof=1) * np.sqrt(TRADING_DAYS)) if d.size > 1 else np.nan

    def _win_start(default_k, days):
        if ds is None:
            return max(0, n - default_k)
        try:
            cut = (_d.fromisoformat(str(ds[-1])) - _td(days=days)).isoformat()
            return bisect.bisect_left(ds, cut)
        except Exception:
            return max(0, n - default_k)

    seg = v[_win_start(TRADING_DAYS, 365):]
    pk = np.maximum.accumulate(seg)
    mdd1y = float(((pk - seg) / pk).max() * 100) if seg.size else np.nan

    j0 = _win_start(63, 91)
    mom = float((v[-1] / v[j0] - 1) * 100) if j0 < n and v[j0] > 0 else np.nan

    sharpe = float((ann_ret - 0.02) / ann_vol) if ann_vol and ann_vol > 0 else np.nan
    return {"annual_return": ann_ret * 100, "ann_vol": ann_vol * 100,
            "max_drawdown_1y": mdd1y, "momentum_3m": mom, "sharpe": sharpe}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=252)
    ap.add_argument("--start", default=None, help="起始月 'YYYY-MM'")
    ap.add_argument("--group", default=None)
    ap.add_argument("--topk", type=int, default=20, help="留存率考察的 Top-K")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
    cur = conn.cursor()

    # ── 全市场月末交易日（一条聚合查询，避免把 2,300 万行净值读进内存）──
    month_end = {m: d for m, d in cur.execute(
        "SELECT substr(nav_date,1,7) AS m, MAX(nav_date) FROM fund_nav GROUP BY m")}
    months = sorted(month_end)
    if args.start:
        months = [m for m in months if m >= args.start]
    print(f"验证期: {months[0]} ~ {months[-1]}（{len(months)} 个月末）")

    # ── 流式逐只算「各月末指标」，只存小字典（内存 O(单只序列)，不 O(全样本)）──
    # 教训：旧版把 11,440 只 × 平均 2,000 点 ≈ 2,300 万行读进 Python list，
    # 需 5GB+，而本机实测空闲内存仅 2.2GB —— 必 OOM。改为流式。
    from calibrate_thresholds import iter_navs
    per_month = {}          # m -> {code: metrics}
    gof = {}                # code -> group
    n_fund = 0
    for code, dates_np, vals in iter_navs(conn, args.min_days):
        g = TYPE2GROUP.get(fi.get(code, ""))
        if not g:
            continue
        n_fund += 1
        gof[code] = g
        dates = [str(x) for x in dates_np]      # 只转一次（见 metrics_at 的性能说明）
        for m in months:
            idx = int(np.searchsorted(dates_np, month_end[m], side="right"))
            if idx < args.min_days:
                continue
            mm = metrics_at(vals, idx, dates)
            if mm:
                per_month.setdefault(m, {})[code] = mm
    print(f"流式载入 {n_fund:,} 只（总历史 >= {args.min_days} 天）")

    # ── 逐月末、逐组：算百分位与综合分（严格无前视）──
    panel = {}       # g -> m -> {code: {"pct":…, "score":…}}
    dist_hist = {}   # (g,k) -> [(m, median)]  仅用于**描述市场状态**
    for m in months:
        by_group = {}
        for code, mm in per_month.get(m, {}).items():
            by_group.setdefault(gof[code], []).append((code, mm))
        for g, rows in by_group.items():
            if len(rows) < MIN_N_IQR:
                continue
            dist = {}
            for k in METRICS:
                arr = np.array([r[k] for _c, r in rows], dtype=float)
                dist[k] = arr[np.isfinite(arr)]
                if dist[k].size:
                    dist_hist.setdefault((g, k), []).append((m, float(np.median(dist[k]))))
            entry = {}
            for code, r in rows:
                ps = {}
                for k in METRICS:
                    arr, v = dist[k], r[k]
                    if arr.size and np.isfinite(v):
                        p = float((arr <= v).mean() * 100)
                        if SCORE_DIR.get(k) is False:
                            p = 100.0 - p
                        ps[k] = p
                entry[code] = {"pct": ps,
                               "score": sum(ps.get(k, 50.0) * w for k, w in SCORE_W.items())}
            panel.setdefault(g, {})[m] = entry

    # ── 市场状态描述（**不作稳定性判定**）──
    print("=" * 100)
    print("① 参照系分布位置的变化（**这是市场状态，不是缺陷** —— 仅供描述）")
    print("=" * 100)
    print(f"  {'组':14} {'指标':16} {'首月中位':>9} {'末月中位':>9} {'变化':>9}")
    for (g, k), rec in sorted(dist_hist.items()):
        if args.group and g != args.group:
            continue
        if len(rec) < 2:
            continue
        print(f"  {g:14} {LABEL[k]:16} {rec[0][1]:>9.2f} {rec[-1][1]:>9.2f} "
              f"{rec[-1][1] - rec[0][1]:>+9.2f}")
    print()

    # ── 核心：排名稳定性 ──
    print("=" * 100)
    print(f"② 排名稳定性（**这才是判定依据**）—— 相邻期 Spearman 排名相关 + Top{args.topk} 留存率")
    print("=" * 100)
    print(f"  判定：相邻期排名相关 >= {RANK_CORR_MIN} 且 Top{args.topk} 留存 >= {TOPK_RETENTION_MIN:.0%}"
          f"  → 参照系排名稳定、可跨期使用")
    print()
    overall = []
    for g in sorted(panel):
        if args.group and g != args.group:
            continue
        ms = sorted(panel[g])
        cors, rets, pairs = [], [], []
        for a, b in zip(ms, ms[1:]):
            ea, eb = panel[g][a], panel[g][b]
            common = sorted(set(ea) & set(eb))
            if len(common) < MIN_N_IQR:
                continue
            xa = np.array([ea[c]["score"] for c in common])
            xb = np.array([eb[c]["score"] for c in common])
            try:
                from scipy.stats import spearmanr
                rho = float(spearmanr(xa, xb).statistic)
            except Exception:
                rho = float("nan")
            K = min(args.topk, len(common))
            ta = {c for c in sorted(common, key=lambda c: -ea[c]["score"])[:K]}
            tb = {c for c in sorted(common, key=lambda c: -eb[c]["score"])[:K]}
            ret = len(ta & tb) / K
            cors.append(rho)
            rets.append(ret)
            pairs.append((a, b, rho, ret, len(common)))
        if not cors:
            continue
        mc, mret = float(np.nanmedian(cors)), float(np.median(rets))
        ok = (mc >= RANK_CORR_MIN) and (mret >= TOPK_RETENTION_MIN)
        print(f"  【{g}】 相邻期对数 {len(cors)}")
        print(f"     排名相关  中位 {mc:.3f}   最小 {np.nanmin(cors):.3f}   最大 {np.nanmax(cors):.3f}")
        print(f"     Top{args.topk} 留存 中位 {mret:.1%}  最小 {np.min(rets):.1%}  最大 {np.max(rets):.1%}")
        print(f"     判定：{'✅ 排名稳定，可跨期使用' if ok else '⚠️ 排名漂移，百分位必须带时点展示'}")
        # 最差的三对
        worst = sorted(pairs, key=lambda x: x[2])[:3]
        if worst and not ok:
            print("     排名最不稳定的相邻期：")
            for a, b, rho, ret, n in worst:
                print(f"       {a} → {b}  ρ={rho:.3f}  留存 {ret:.0%}  (n={n})")
        print()
        overall.append((g, mc, mret, ok))

    print("=" * 100)
    print("结论与正确用法")
    print("=" * 100)
    stable = [g for g, _, _, ok in overall if ok]
    unstable = [g for g, _, _, ok in overall if not ok]
    print(f"  · 排名稳定的组：{stable if stable else '无'}")
    print(f"  · 排名漂移的组：{unstable if unstable else '无'}")
    print()
    print("  ⚠️ 重要：分布位置漂移（①）**不是缺陷** —— 那是市场状态。")
    print("     真正决定参照系能不能用的是②排名稳定性。")
    print("  · 排名稳定 → 百分位可跨期比较")
    print("  · 排名漂移 → 百分位必须**带时点**展示（'2026-09 同类 P72'），")
    print("                且不得声称'长期稳定在同类前 X%'")
    print("  · 阈值 RANK_CORR_MIN / TOPK_RETENTION_MIN **是本项目自定的**，")
    print("    与 THRESHOLDS 当年一样属于拍脑袋 —— 待用实测分布校准（见脚本尾部提示）")
    print("  · 严格无前视：每个月末只用 <= 该月末 的数据算指标")
    print("  · 幸存者偏差：只统计存续基金；早期样本更少")


if __name__ == "__main__":
    main()
