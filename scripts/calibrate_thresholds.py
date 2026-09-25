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

# 估值口径的单一事实来源（SSOT）。**禁止在本文件重写这个表达式** ——
# 历史上这里用过朴素 COALESCE(acc_nav, unit_nav)，被采集器的 0.0 哨兵骗过，
# 导致参照系与线上推荐用两套净值口径。详见 nav_series.py 与 load_navs 的说明。
from src.analysis.nav_series import VALUATION_NAV_SQL as _VALUATION_SQL  # noqa: E402

# ---- SSOT：指标实现统一在 src/analysis/nav_metrics.py（2026-09-25）----
# 本文件在 scripts/ 下，nav_metrics 在 src/analysis/ 下；跨模块调用时不能假定
# 对方已把该目录塞进 sys.path，所以在**导入时**一次性解析好（原写成函数内 import，
# 被 src.analysis.peer_percentile 调用时 path 不稳 → ModuleNotFoundError，已踩）。
import os as _os
import sys as _sys
_ANALYSIS_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src", "analysis")
if _ANALYSIS_DIR not in _sys.path:
    _sys.path.insert(0, _ANALYSIS_DIR)
from nav_metrics import compute as _nav_compute          # noqa: E402

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
                "债券型-利率债", "债券型-信用债", "债券型-可转债",
                # ⚠️ 2026-09-21 补：全量采集后暴露 993 只「混合型-偏债」未映射，
                # 被排除在参照系之外（占新样本 8.7%）。归属由**实测分位网格**决定，
                # 不靠直觉（scripts/_diag_group_placement.py）：
                #   与 E 的平均绝对差 1.597 vs 与 B 的 7.985（差 5.00 倍）→ 归 E。
                #   决定性证据：年化收益中位 2.89%（E 2.92% / B 5.79%，几乎与 E 重合）、
                #   年化波动中位 4.82%（E 1.62% / B 19.03%）、回撤中位 3.78%（E 0.50% / B 18.78%）。
                "混合型-偏债",
                # ⚠️ 2026-09-25 补：`指数型-固收`（**债券指数基金**）原先归在 F QDII/其他，
                # 于是这些纯债指数基金被打上 F 组「同类为大类口径、可比性弱」（ρ 仅 0.52）的标签。
                # 归属由**同一套实测分位网格**决定（scripts/_diag_group_placement_bond_index.py）：
                #   与 E 的平均绝对差 1.858 vs 与 F 的 6.777（差 **3.65 倍**）vs 与 D 的 12.273
                # 决定性证据（分位网格中位）：年化波动 0.88%（E 3.17% / F 10.81% / D 22.10%）、
                #   近1年最大回撤 0.22%（E 1.70% / F 8.94% / D 20.68%）、年化收益 2.77%（E 2.92% / F 2.67%）。
                #   —— 波动/回撤比 E 还低（因 E 含可转债与二级债基），明显是纯债画像，绝非 F。
                "指数型-固收"]),
    ("F QDII/其他", ["QDII-混合偏股", "QDII-普通股票", "QDII-混合灵活", "QDII-纯债",
                     "QDII-FOF", "QDII-混合债", "QDII-混合平衡", "QDII-商品", "QDII-REITs",
                     "指数型-海外股票",
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

MIN_N_FULL, MIN_N_MID, MIN_N_IQR = 150, 50, 30

# 参照系入门门槛的「稀疏分支」（批次 B3）——
# 定开/低频披露基金：点数少但**日历跨度**够，见 iter_navs 的说明。
# 实测被误排 387 只，典型 000792（跨度 3,374 天 / 591 点）。
MIN_DAYS_SPARSE = 250
MIN_SPAN_YEARS = 3.0
# ⚠️ 三次修订记录（教训：改口径必须重测，且**自己的改动也要被质疑**）
#   v1  150/100/30 —— 口算，无实测依据
#   v2  100/50/30  —— 基于 n=191 的 bootstrap，当时判定 150 过保守
#   v3  150/50/30  —— **全量采集后 n=3,278 的参照总体上重测（scripts/relocate_thresholds.py）**
# 全量采集后样本从 576 → 11,440（A 组 3,278），bootstrap 重做（400 次重抽，
# 判据：目标分位 90% 置信区间宽度 / 全样本 IQR < 0.5）：
#     回撤 需 n≥40 | 动量 需 n≥50 | 夏普 需 n≥75 | **年化收益/波动 需 n≥150**
# → 全指标合格的门槛是 150，**v2 的 100 对收益/波动两指标是过松的**。
#   注意：打分只用 sharpe+drawdown（需 75），但 annual_return/ann_vol 仍会展示，
#   展示值同样需要 n≥150 才稳定，故取全指标最严值。
# v2 的原始实测记录（n=191 真值池，已作废，保留供对照）：
#   P90：回撤 n=50→0.42×  夏普 n=50→0.46×  年化收益 n=100→0.50×（收益最难估）
#   P25：n=30→0.32~0.66×   n=50→0.22~0.47×
#   → 当时结论：P10/P90 需 n≥100；P25/P75 需 n≥50；n<50 只用中位+IQR



class NavStream:
    """惰性净值序列集合：接口像 `{code: (dates, vals)}`，但**不把全部序列留在内存**。

    ⚠️ 为什么必须惰性（2026-09-21 实测）：
    全量采集后深历史基金从 576 只涨到 11,423 只，对应约 **2,300 万行**净值。
    一次性读进 Python 列表约需 5 GB+，而本机总内存 15.2 GB、**实测空闲仅 2.2 GB** —— 会 OOM。
    本类改为每次只物化一只基金的序列，内存 O(单只)。

    兼容性：`len()` 走一次计数查询（走主键索引，快）；`.items()` / `.keys()` /
    `navs[code]` 都支持，所以既有三个调用方**无需修改**。
    """

    def __init__(self, conn, min_days):
        self._conn = conn
        self._min_days = min_days
        self._n = None

    def _count(self):
        if self._n is None:
            cur = self._conn.cursor()
            row = cur.execute(
                "SELECT COUNT(*) FROM (SELECT fund_code FROM fund_nav GROUP BY fund_code "
                "HAVING COUNT(*) >= ?)", (self._min_days,)).fetchone()
            # 注意：这里只统计"点数够"的基金，与实际 yield 的条件（有效点数够）可能差几只，
            # 故只用于打印进度，不作为正确性依据。
            self._n = row[0] if row else 0
        return self._n

    def __len__(self):
        return self._count()

    def items(self):
        # 保持与旧 dict 接口完全一致：产出 (code, (dates, vals)) 二元组
        for code, dates, vals in iter_navs(self._conn, self._min_days):
            yield code, (dates, vals)

    def keys(self):
        for code, _d, _v in iter_navs(self._conn, self._min_days):
            yield code

    def __iter__(self):
        return self.keys()

    def __getitem__(self, code):
        cur = self._conn.cursor()
        cur.execute(f"""
            SELECT n.nav_date, {_VALUATION_SQL} AS v FROM fund_nav n
            WHERE n.fund_code = ? AND {_VALUATION_SQL} > 0 ORDER BY n.nav_date
        """, (code,))
        rows = cur.fetchall()
        if not rows:
            raise KeyError(code)
        return (np.asarray([r[0] for r in rows]),
                np.asarray([r[1] for r in rows], dtype=float))


def iter_navs(conn, min_days, sparse_ok=True):
    """流式逐只产出 (fund_code, dates_ndarray, vals_ndarray)。内存 O(单只基金)。

    依赖 fund_nav 主键索引 (fund_code, nav_date) 有序扫描，不做额外排序。

    ⚠️ **入门门槛（批次 B3 修正）**：原口径只看**点数** ≥ min_days(756)，
    把「定开 / 低频披露」基金误排 —— 典型 `000792 招商定期宝六个月期理财债券`：
    **日历跨度 3,374 天（≈9.2 年）却只有 591 个净值点**（每半年才披露一次），
    于是被当成"历史不足"踢出参照系。实测被误排 **387 只**。

    新口径（sparse_ok=True，默认）：
        `点数 ≥ min_days`  **或**  （`日历跨度 ≥ MIN_SPAN_YEARS(3年)` **且** `点数 ≥ MIN_DAYS_SPARSE(250)`）

    为什么对定开基金用稀疏净值是**正确的**：持有人本来也只能在**开放日**申赎，
    两次开放之间的净值波动他赚不到也躲不开；用披露出来的点算区间收益/回撤，
    与持有人的真实体验一致。这不是"放宽质量"，是**修正被误排的合法样本**。
    """
    import bisect as _bisect                      # noqa: F401  (保持与本文件风格一致)
    from datetime import date as _date

    def _accept(dates, vals):
        n = len(vals)
        if n >= min_days:
            return True
        if not sparse_ok or n < MIN_DAYS_SPARSE:
            return False
        try:
            span = (_date.fromisoformat(str(dates[-1])) - _date.fromisoformat(str(dates[0]))).days / 365.25
        except Exception:
            return False
        return span >= MIN_SPAN_YEARS

    # SQL 预筛放宽到两分支的下界，真正的判定在 Python（要同时看日期跨度）
    sql_min = min(min_days, MIN_DAYS_SPARSE) if sparse_ok else min_days
    cur = conn.cursor()
    cur.execute(f"""
        SELECT n.fund_code, n.nav_date, {_VALUATION_SQL} AS v
        FROM fund_nav n
        JOIN (SELECT fund_code FROM fund_nav GROUP BY fund_code HAVING COUNT(*) >= ?) d
          ON d.fund_code = n.fund_code
        WHERE {_VALUATION_SQL} > 0
        ORDER BY n.fund_code, n.nav_date
    """, (sql_min,))

    code0, dates, vals = None, [], []
    while True:
        batch = cur.fetchmany(20000)
        if not batch:
            break
        for code, dt, v in batch:
            if code != code0:
                if code0 is not None and _accept(dates, vals):
                    yield code0, np.asarray(dates), np.asarray(vals, dtype=float)
                code0, dates, vals = code, [], []
            dates.append(str(dt))
            vals.append(float(v))
    if code0 is not None and _accept(dates, vals):
        yield code0, np.asarray(dates), np.asarray(vals, dtype=float)


def load_navs(conn, min_days):
    """返回惰性集合 `{fund_code: (dates_ndarray, vals_ndarray)}`（见 NavStream）。

    ⚠️ **同时返回日期**：净值序列可能有缺口，年化必须用日期跨度算（见 metrics 的说明）。
    早先只返回净值，导致调用方无法用日期口径 —— 这个接口缺陷曾让我把债券基金
    的年化高估了 135%。

    ⚠️ **估值口径必须用 SSOT `nav_series.VALUATION_NAV_SQL`**（2026-09-21 修复）：
    早先这里写的是朴素 `COALESCE(acc_nav, unit_nav)`，而采集器把「缺失/异常的累计净值」
    写成 **0.0 而不是 NULL**（见 src/data/collector.py）。0.0 不是 NULL，COALESCE 会原样
    返回 0.0，紧接着的 `WHERE ... > 0` 就把**整行丢掉**，而正确行为是**逐行回退 unit_nav**。
    实测命中 30 只基金 / 124 行。这会造成参照系与线上推荐**用两套净值口径**，
    是本项目「单一事实来源」铁律的直接违反。
    """
    return NavStream(conn, min_days)


def metrics(vals, dates=None):
    """单只基金的指标 —— **委托给 SSOT `src/analysis/nav_metrics.compute`**。

    ⚠️ 2026-09-25 口径统一：本函数改为**固定 3 年窗口**（原为"基金全历史"）。
    依据见 `nav_metrics` 模块 docstring：晨星按 3/5/10 年固定窗口评级、
    天天基金只给 近1/2/3 年 —— **没有任何一家用全历史**，因为只有固定窗口
    才能让不同年龄的基金**可比**。
    同时统一了年化口径（几何 + 日期跨度）与年化波动的交易日数（244，非 252）。

    vals: 累计净值序列（升序）；dates: 对应 'YYYY-MM-DD'（强烈建议传 —— 序列有缺口，
          按点数当年数会把年化严重高估，实测最坏约 2.3 倍）。
    """
    return _nav_compute(vals, dates)


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

    # ---- 全局类型覆盖断言（无条件打印）----
    # 教训：漏一个类型 = 整类基金被静默排除在参照系外。首版漏「混合型-股债平衡」
    # 丢 5 只；全量采集后又漏「混合型-偏债」，一次丢掉 993 只（占样本 8.7%）。
    # 因此这里对**全库**类型做一次核对，而不是只看本次样本命中的那些。
    from collections import Counter as _C
    _tc = _C(v for v in fi.values() if v)
    _mapped, _unmapped = audit_type_coverage(_tc.items())
    _tot = sum(_tc.values())
    print(f"类型覆盖断言：全库 {_tot:,} 只 / {len(_tc)} 种类型；"
          f"已映射 {_mapped:,} 只（{_mapped/_tot*100:.1f}%）")
    if _unmapped:
        print(f"  ⚠️ **未映射类型 {sum(n for _, n in _unmapped):,} 只，这些基金拿不到参照系**：")
        for t, n in sorted(_unmapped, key=lambda x: -x[1]):
            print(f"        {t!r:24} {n:>7,} 只")
    else:
        print("  ✅ 全库类型 100% 已映射，无基金被静默排除")
    print()

    navs = load_navs(conn, args.min_days)
    # ⚠️ 不要在这里 conn.close()：navs 现在是**惰性**的（见 NavStream），
    #    关闭连接会让后续迭代失败。连接在 main 结束时才关。
    print(f"载入 {len(navs)} 只（净值 >= {args.min_days} 天，惰性流式）\n")

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
    conn.close()


if __name__ == "__main__":
    main()
