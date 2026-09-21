#!/usr/bin/env python3
"""
阈值校准（参考毕设 analysis/benchmark_threshold.py 的范式）

把 fund_scorer.THRESHOLDS 里那些**拍脑袋常数**，换成**同类基金群体分布的分位数**，
并回答一个关键问题：**现有常数阈值在同类分布里到底位于第几分位？通过率多少？**

只读脚本：不写任何表、不改任何业务代码。

用法:
    python scripts/calibrate_thresholds.py
    python scripts/calibrate_thresholds.py --min-days 756     # 只算深历史基金

口径:
    - 估值序列一律用 acc_nav（累计净值），见 src/analysis/nav_series.py
      —— 分红除息日 unit_nav 向下跳，会把回撤凭空放大
    - 样本量分级（见 docs/基金推荐系统设计方案.md §3.4）：
        n>=150 全量分位 / 100<=n<150 降到 P25-P75 / 30<=n<100 仅中位+IQR / n<30 不建
"""
import argparse
import os
import sqlite3
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台兜底

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB_PATH = os.path.join(ROOT, "data", "fund_quant.db")

TRADING_DAYS = 244
# ⚠️ 经实测校准（原为 252 的国际惯例值）：
# 统计 2014~2025 共 12 个完整年份的 A 股实际交易日（全体基金净值日期并集）：
#   均值 244.7 / 中位 244 / 范围 243~247
# 用 252 会把**年化波动高估 3.07%**。校准脚本：scripts/calibrate_constants.py §1.1

# --- 类型聚合：把 44 种 fund_type 归成 6 组（见设计文档 §3.5）---
TYPE_GROUPS = [
    ("A 偏股混合", ["混合型-偏股"]),
    ("B 灵活配置", ["混合型-灵活配置", "混合型-灵活", "混合型-平衡", "混合型-股债平衡"]),
    ("C 主动股票", ["股票型", "股票型-普通"]),
    ("D 指数股票", ["股票型-标准指数", "股票型-增强指数", "指数型-股票", "指数型-其他"]),
    ("E 债券", ["债券型-长债", "债券型-普通债券", "债券型-长期纯债", "债券型-中短债",
                "债券型-短期纯债", "债券型-混合一级", "债券型-混合二级",
                "债券型-利率债", "债券型-信用债", "债券型-可转债"]),
    ("F QDII/其他", ["QDII-混合偏股", "QDII-普通股票", "QDII-混合灵活", "QDII-纯债",
                     "QDII-FOF", "QDII-混合债", "QDII-混合平衡", "QDII-商品", "QDII-REITs",
                     "指数型-海外股票", "指数型-固收",
                     "FOF-稳健型", "FOF-均衡型", "FOF-进取型",
                     "混合型-绝对收益", "货币型-普通货币", "货币型-浮动净值",
                     "商品", "Reits"]),
]
TYPE2GROUP = {t: g for g, ts in TYPE_GROUPS for t in ts}

# ⚠️ 覆盖性断言（2026-09-18 自查发现的问题）：
# 首版映射表漏了「混合型-股债平衡」「指数型-其他」，导致 5 只基金被**静默丢弃** ——
# 违反本项目铁律「数据缺失必须声明，不得静默填充/丢弃」。
# 现在改为：调用方必须显式报告未映射的类型（见 audit_type_coverage()）。
def audit_type_coverage(types_with_count):
    """返回 (已映射数, 未映射清单)。调用方**必须把未映射清单打印出来**，不得静默。"""
    mapped, unmapped = 0, []
    for t, n in types_with_count:
        if t in TYPE2GROUP:
            mapped += n
        else:
            unmapped.append((t, n))
    return mapped, unmapped


# --- 现有拍脑袋阈值（fund_scorer.THRESHOLDS）---
CURRENT = {
    "max_drawdown_1y": 35.0,     # 检查：<=35% 通过
    "momentum_3m": 40.0,         # 检查：<=40% 通过（追涨警告）
    "ann_vol": None,
    "annual_return": None,
    "sharpe": None,
}

# --- 参与校准的指标（模块级，供 fund_percentile.py 复用，保证 SSOT）---
METRICS = ["annual_return", "ann_vol", "max_drawdown_1y", "momentum_3m", "sharpe"]
LABEL = {"annual_return": "年化收益%", "ann_vol": "年化波动%",
         "max_drawdown_1y": "近1年最大回撤%", "momentum_3m": "近3月动量%", "sharpe": "夏普"}

MIN_N_FULL, MIN_N_MID, MIN_N_IQR = 100, 50, 30
# ⚠️ 经 bootstrap 实测校准（原为 150/100/30，依据是口算而非实测）：
# 判定标准 = 统计量的 90% 置信区间宽度 / 全样本 IQR，< 0.5 视为可用。
# 实测（A 组 n=191 作真值池，400 次重采样，见 calibrate_constants.py §1.2）：
#   P90：回撤 n=50→0.42×  夏普 n=50→0.46×  年化收益 n=100→0.50×（收益最难估）
#   P25：n=30→0.32~0.66×   n=50→0.22~0.47×
# → P10/P90 需 n≥100；P25/P75 需 n≥50；n<50 只用中位+IQR



def load_navs(conn, min_days):
    """返回 {fund_code: (dates_ndarray, vals_ndarray)}，只取 >= min_days 天的基金。

    ⚠️ **同时返回日期**：净值序列可能有缺口，年化必须用日期跨度算（见 metrics 的说明）。
    早先只返回净值，导致调用方无法用日期口径 —— 这个接口缺陷曾让我把债券基金
    的年化高估了 135%。
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT n.fund_code, n.nav_date, COALESCE(n.acc_nav, n.unit_nav)
        FROM fund_nav n
        JOIN (SELECT fund_code FROM fund_nav GROUP BY fund_code HAVING COUNT(*) >= ?) d
          ON d.fund_code = n.fund_code
        WHERE COALESCE(n.acc_nav, n.unit_nav) > 0
        ORDER BY n.fund_code, n.nav_date
    """, (min_days,))
    out = {}
    for code, dt, v in cur.fetchall():
        out.setdefault(code, ([], []))
        out[code][0].append(str(dt))
        out[code][1].append(float(v))
    return {k: (np.asarray(d), np.asarray(v, dtype=float))
            for k, (d, v) in out.items() if len(v) >= min_days}


def metrics(vals, dates=None):
    """单只基金的指标。

    ⚠️ **必须传 dates**：净值序列**可能有缺口**（实测 576 只里 135 只点数明显少于
    应有的交易天数，最严重的比值仅 0.437）。用「点数 / 252」当年数会把年化收益
    **严重高估**（实测最坏情形约 2.3 倍）。
    正确做法：年化用**日期跨度**，近 1 年回撤用**日期窗口**，而不是点数窗口。

    vals: 累计净值序列（升序）；dates: 对应的 'YYYY-MM-DD' 列表（可选，但强烈建议传）
    """
    n = len(vals)
    if n < 60:
        return None
    vals = np.asarray(vals, dtype=float)
    ret = vals[-1] / vals[0] - 1

    # ---- 年化：优先用日期跨度 ----
    if dates is not None and len(dates) == n:
        from datetime import date as _d
        try:
            y0 = _d.fromisoformat(str(dates[0]))
            y1 = _d.fromisoformat(str(dates[-1]))
            years = max((y1 - y0).days / 365.25, 1e-6)
        except Exception:
            years = n / TRADING_DAYS
    else:
        years = n / TRADING_DAYS
    ann_ret = (1 + ret) ** (1.0 / years) - 1 if years > 0 else np.nan

    daily = np.diff(vals) / vals[:-1]
    ann_vol = float(daily.std(ddof=1) * np.sqrt(TRADING_DAYS)) if daily.size > 1 else np.nan

    peak = np.maximum.accumulate(vals)
    mdd = float(((peak - vals) / peak).max() * 100)

    # ---- 近 1 年回撤：优先用日期窗口 ----
    if dates is not None and len(dates) == n:
        import bisect
        i0 = bisect.bisect_left(list(dates), str(dates[-1])[:4] + "-" + str(dates[-1])[5:7] + "-" + str(dates[-1])[8:])
        # 用 dates[-1] 往前 365 天的位置
        from datetime import date as _d, timedelta as _td
        try:
            cut = (_d.fromisoformat(str(dates[-1])) - _td(days=365)).isoformat()
            i0 = bisect.bisect_left(list(dates), cut)
        except Exception:
            i0 = max(0, n - TRADING_DAYS)
    else:
        i0 = max(0, n - TRADING_DAYS)
    seg = vals[i0:]
    pk = np.maximum.accumulate(seg)
    mdd1y = float(((pk - seg) / pk).max() * 100) if seg.size else np.nan

    # ---- 近 3 月动量：日期窗口 ----
    if dates is not None and len(dates) == n:
        import bisect
        from datetime import date as _d, timedelta as _td
        try:
            cut3 = (_d.fromisoformat(str(dates[-1])) - _td(days=91)).isoformat()
            j0 = bisect.bisect_left(list(dates), cut3)
        except Exception:
            j0 = max(0, n - 63)
    else:
        j0 = max(0, n - 63)
    mom3m = float((vals[-1] / vals[j0] - 1) * 100) if j0 < n and vals[j0] > 0 else np.nan

    sharpe = float((ann_ret - 0.02) / ann_vol) if ann_vol and ann_vol > 0 else np.nan
    return {"annual_return": ann_ret * 100, "ann_vol": ann_vol * 100,
            "max_drawdown_1y": mdd1y, "max_drawdown_all": mdd,
            "momentum_3m": mom3m, "sharpe": sharpe}


def grade(n):
    if n >= MIN_N_FULL:
        return "full", [1, 5, 10, 25, 50, 75, 90, 95, 99]
    if n >= MIN_N_MID:
        return "mid", [10, 25, 50, 75, 90]
    if n >= MIN_N_IQR:
        return "iqr_only", [25, 50, 75]
    return "insufficient", [50]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=756,
                    help="最少净值天数（默认 756 ≈ 3 年）")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
    navs = load_navs(conn, args.min_days)
    conn.close()
    print(f"载入 {len(navs)} 只（净值 >= {args.min_days} 天）\n")

    # 分组
    groups = {}
    dropped = []
    for code, (d, v) in navs.items():
        g = TYPE2GROUP.get(fi.get(code, ""))
        if not g:
            dropped.append(fi.get(code, "") or "(未知类型)")
            continue
        m = metrics(v, d)
        if m:
            groups.setdefault(g, []).append(m)
    if dropped:
        from collections import Counter
        print(f"  ⚠️ 未映射类型 {len(dropped)} 只（**显式报告，不静默丢弃**）：{dict(Counter(dropped))}\n")

    METRICS_LOCAL = METRICS  # 模块级已定义，此处仅为可读性
    LABEL_LOCAL = LABEL

    for g, rows in sorted(groups.items()):
        n = len(rows)
        lvl, pcts = grade(n)
        flag = {"full": "✅ 全量", "mid": "⚠️ 中位+IQR", "iqr_only": "⚠️ 仅中位+IQR",
                "insufficient": "❌ 样本不足，不建参照系"}[lvl]
        print("=" * 92)
        print(f"【{g}】 n={n}  {flag}")
        print("=" * 92)
        if lvl == "insufficient":
            print("  样本 <30，跳过\n")
            continue
        print(f"  {'指标':16} " + " ".join(f"{'P'+str(p):>9}" for p in pcts) + f" {'均值':>9}")
        for k in METRICS:
            arr = np.array([r[k] for r in rows], dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                continue
            cells = " ".join(f"{np.percentile(arr, p):>9.2f}" for p in pcts)
            print(f"  {LABEL[k]:16} {cells} {arr.mean():>9.2f}")
        # ★ 现有常数阈值在该组分布中的位置
        print()
        for k, c in CURRENT.items():
            if c is None:
                continue
            arr = np.array([r[k] for r in rows], dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                continue
            pct = float((arr <= c).mean() * 100)
            print(f"  ★ 现有阈值 {LABEL[k]} <= {c:g}  →  位于同类 P{pct:.0f}，通过率 {pct:.1f}%")
        print()

    print("=" * 92)
    print("怎么读这个结果")
    print("=" * 92)
    print("  · 通过率 ≈100%  → 阈值太松，等于没设（当前 max_drawdown_1y=35 大概率如此）")
    print("  · 通过率 ≈50%   → 阈值在中位，属'一半淘汰'")
    print("  · 通过率 <20%   → 阈值很严，要确认是否有依据")
    print("  · 建议把阈值改为同类分位（如'回撤优于同类 P75'），市场波动变化时自动跟随")


if __name__ == "__main__":
    main()
