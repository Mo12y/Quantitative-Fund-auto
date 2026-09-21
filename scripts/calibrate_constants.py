"""批次 1：数值口径校准（只读）

把项目里**还没验证就用了**的数值逐个用实测校准：
  1.1 TRADING_DAYS = 252 —— A 股实际年均交易日是多少？
  1.2 样本量分级 MIN_N_FULL/MID/IQR = 150/100/30 —— bootstrap 说该是多少？
  1.3 参照系判定阈值 RANK_CORR_MIN/TOPK_RETENTION_MIN —— 用实测分布定
  1.4 类型聚合 44 种 → 6 组 —— 聚合依据成立吗（组内同质性）
"""
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\DSH\projects\Quantitative-Fund-auto"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from calibrate_thresholds import DB_PATH, TYPE2GROUP, load_navs, metrics  # noqa: E402

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

print("=" * 94)
print("1.1 TRADING_DAYS：A 股实际年均交易日（现用 252）")
print("=" * 94)
# 用全体基金的净值日期并集近似"市场交易日"
days = sorted({r[0] for r in conn.execute("select distinct nav_date from fund_nav")})
by_year = defaultdict(int)
for d in days:
    by_year[d[:4]] += 1
full = {y: n for y, n in by_year.items() if n > 200}   # 只看完整年份
vals = np.array(list(full.values()), dtype=float)
print(f"  按净值日期并集统计（完整年份 {len(full)} 个）：")
for y in sorted(full):
    print(f"    {y}: {full[y]} 天")
print(f"  均值 {vals.mean():.1f}  中位 {np.median(vals):.0f}  "
      f"最小 {vals.min():.0f}  最大 {vals.max():.0f}")
print(f"  → 现用 252 相对实测中位数的偏差：{(252 / np.median(vals) - 1) * 100:+.2f}%")
print(f"     年化波动会被**高估**约 {(252 / np.median(vals) - 1) * 100:.2f}%")

print()
print("=" * 94)
print("1.2 样本量分级：P90 / P25 / P75 在多大 n 下才稳？（bootstrap，400 次）")
print("=" * 94)
navs = load_navs(conn, 756)
fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
grp = defaultdict(list)
for code, (d, v) in navs.items():
    g = TYPE2GROUP.get(fi.get(code, ""))
    if g:
        m = metrics(v, d)
        if m:
            grp[g].append(m)
A = grp.get("A 偏股混合", [])
print(f"  A 组 n={len(A)}（用作 bootstrap 的真值池）")
rng = np.random.default_rng(42)
print(f"  {'指标':16} {'统计量':>8} " + " ".join(f"{'n='+str(n):>9}" for n in [30, 50, 65, 100, 150]))
for k in ["max_drawdown_1y", "sharpe", "annual_return"]:
    arr = np.array([r[k] for r in A], dtype=float)
    arr = arr[np.isfinite(arr)]
    for stat, fn in [("P90", lambda s: np.percentile(s, 90)),
                     ("P25", lambda s: np.percentile(s, 25))]:
        cells = []
        for n in [30, 50, 65, 100, 150]:
            if n > arr.size:
                cells.append(f"{'-':>9}")
                continue
            est = [fn(rng.choice(arr, size=n, replace=False)) for _ in range(400)]
            ci = np.percentile(est, 95) - np.percentile(est, 5)
            iqr = np.percentile(arr, 75) - np.percentile(arr, 25)
            v = ci / iqr if iqr > 0 else np.inf
            cells.append(f"{v:>8.2f}×")
        print(f"  {k[:14]:16} {stat:>8} " + " ".join(cells))
print("  （数值 = 该统计量的 90% 置信区间宽度 / 全样本 IQR；<0.5 视为可用）")

print()
print("=" * 94)
print("1.4 类型聚合依据：A~E 组内各原始类型的指标是否同质？")
print("=" * 94)
print("  若组内某个原始类型与组内其他类型的指标中位数差异很大，说明聚合不当")
for g in ["A 偏股混合", "B 灵活配置", "C 主动股票", "D 指数股票", "E 债券"]:
    rows = grp.get(g, [])
    if len(rows) < 20:
        continue
    # 按原始类型拆
    sub = defaultdict(list)
    for code, (d, v) in navs.items():
        if TYPE2GROUP.get(fi.get(code, "")) != g:
            continue
        m = metrics(v, d)
        if m:
            sub[fi.get(code, "")].append(m)
    gmed_sharpe = np.median([r["sharpe"] for r in rows if np.isfinite(r["sharpe"])])
    gmed_dd = np.median([r["max_drawdown_1y"] for r in rows if np.isfinite(r["max_drawdown_1y"])])
    print(f"\n  【{g}】组中位 夏普 {gmed_sharpe:.2f} / 回撤 {gmed_dd:.1f}%")
    if len(sub) < 2:
        print("      （组内仅 1 个原始类型，无聚合问题）")
        continue
    iqr_sharpe = np.percentile([r["sharpe"] for r in rows if np.isfinite(r["sharpe"])], 75) - \
                 np.percentile([r["sharpe"] for r in rows if np.isfinite(r["sharpe"])], 25)
    for t, rs in sorted(sub.items(), key=lambda x: -len(x[1])):
        if len(rs) < 4:
            continue
        s = np.median([r["sharpe"] for r in rs if np.isfinite(r["sharpe"])]) if rs else np.nan
        dd = np.median([r["max_drawdown_1y"] for r in rs if np.isfinite(r["max_drawdown_1y"])])
        dev = abs(s - gmed_sharpe) / iqr_sharpe if iqr_sharpe > 0 else 0
        flag = "⚠️ 偏离组内 >0.5×IQR" if dev > 0.5 else "✅ 同质"
        print(f"     {t[:22]:24} n={len(rs):>4}  夏普中位 {s:>6.2f}  回撤中位 {dd:>6.2f}%  "
              f"偏离 {dev:.2f}×IQR  {flag}")
