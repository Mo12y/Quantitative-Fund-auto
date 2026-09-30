# -*- coding: utf-8 -*-
"""
同类基金的结构体检 —— 同类组里到底有多少**独立**的东西？

为什么要有这个脚本：2026-10-01 的实测发现，本项目两个关键设计建立在一个未经验证的假设上 ——
  · `peer_percentile` 用**同名同类组的点分位**排序（隐含假设：组内可比、差异是真信号）；
  · `corr_overlap` 用**绝对阈值**（0.80）判"同涨同跌"（隐含假设：0.8 算高）。
实测（300 只/组，近 3 年 152 周）：

    同类组         平均相关 ρ   PC1 占比   名义 N    有效独立 N
    混合型-偏股       0.61        63%      5,735     3,426 (60%)
    指数型-股票       0.67        69%      5,588     2,904 (52%)
    债券型-长债       0.70        73%      2,785     1,314 (47%)

→ 结论：**同类基线相关就有 0.61~0.70**，绝对阈值 0.8 对同组内几乎形同虚设；
  且"同类组里有几千只可选"是幻觉（三分之二横截面波动来自同一个因子）。

另附 Lo (2002) 的夏普标准误：`SE ≈ sqrt((1 + SR²/2) / T)`，T=3 年 → **SE≈0.58**
（单只基金 3 年夏普的 95% 区间约 ±1.13 —— 与真值 0 无法区分）。这是"用 3 年夏普精细排序"
这件事本身的统计上限，与本脚本的横截面结果互相印证。

**只读**。用法：
    python scripts/analyze_peer_structure.py
    python scripts/analyze_peer_structure.py --sample 500 --weeks 156
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np          # noqa: E402
import pandas as pd         # noqa: E402
from src.data.database import Database  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")

#: 体检的同类组（覆盖股/债/指数三类，均为大样本组）
GROUPS = ["混合型-偏股", "指数型-股票", "债券型-长债"]


def load_weekly(c, fund_type: str, k: int, lookback_years: int = 3):
    """取某同类组的**周频**净值矩阵（周频降低噪音与停牌错位），返回周收益率 DataFrame。"""
    codes = [r[0] for r in c.execute(
        "SELECT fund_code FROM fund_info WHERE fund_type=? LIMIT 20000", (fund_type,))]
    if len(codes) < 50:
        return None
    ph = ",".join("?" * len(codes))
    df = pd.read_sql_query(
        "SELECT fund_code, nav_date, acc_nav FROM fund_nav"
        " WHERE acc_nav IS NOT NULL AND nav_date >= date('now', ?)"
        " AND fund_code IN (%s) ORDER BY fund_code, nav_date" % ph,
        c, params=[f"-{lookback_years} years"] + codes)
    if df.empty:
        return None
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    df["wk"] = df["nav_date"].dt.to_period("W")
    wk = df.sort_values("nav_date").groupby(["fund_code", "wk"], as_index=False).last()
    wide = wk.pivot(index="wk", columns="fund_code", values="acc_nav")
    wide = wide.dropna(axis=1, thresh=int(len(wide) * 0.95)).ffill().dropna(axis=1)
    if wide.shape[1] < 30 or wide.shape[0] < 40:
        return None
    rng = np.random.default_rng(42)                 # 固定种子 → 结果可复现
    pick = rng.choice(wide.shape[1], size=min(k, wide.shape[1]), replace=False)
    ret = wide.iloc[:, sorted(pick)].pct_change().dropna()
    sd = ret.std(ddof=1)
    # 掉零/近零波动序列（短债常见），否则相关阵出现 NaN、特征值不收敛
    return ret.loc[:, sd.notna() & (sd > 1e-12)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=300, help="每组抽样只数（默认 300）")
    ap.add_argument("--years", type=int, default=3, help="回看年数（默认 3）")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("找不到账本库：%s" % DB)
    db = Database(DB)
    c = db.conn

    print("=" * 92)
    print("同类基金结构体检（近 %d 年周收益，每组抽样 %d 只）" % (args.years, args.sample))
    print("=" * 92)
    print("%-14s %5s %6s %9s %9s %9s %9s" %
          ("同类组", "样本", "周数", "平均相关ρ", "PC1占比", "名义N", "有效N"))
    for ft in GROUPS:
        ret = load_weekly(c, ft, args.sample, args.years)
        if ret is None:
            print("%-14s  — 样本不足，跳过" % ft)
            continue
        corr = ret.corr().values
        n = corr.shape[0]
        rho = float(corr[np.triu_indices(n, 1)].mean())
        vals = np.linalg.eigvalsh(corr)[::-1]
        pc1 = float(vals[0] / vals.sum())
        # Cheverud–Nyholt：M_eff = 1 + (M−1)(1 − Var(λ)/M)
        m_eff = 1.0 + (n - 1) * (1.0 - float(vals.var(ddof=1)) / n)
        grp_n = c.execute("SELECT COUNT(*) FROM fund_info WHERE fund_type=?", (ft,)).fetchone()[0]
        print("%-14s %5d %6d %9.2f %8.0f%% %9d %9.0f" %
              (ft, n, ret.shape[0], rho, pc1 * 100, grp_n, m_eff / n * grp_n))

    db.close()
    print()
    print("判据提示：")
    print("  · 平均相关 ρ 是**同类基线** —— `corr_overlap` 的阈值应相对它定，而不是绝对值 0.8；")
    print("  · 有效 N 是**多重检验的真实分母**（BH 校正目前用的是名义 N）；")
    print("  · PC1 占比高 = 组内大部分是「同一个东西」，排序的边际信息少。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
