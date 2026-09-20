"""自查：从毕设搬来的东西，还有哪些没验证就用了？

三个怀疑点：
 ① TYPE2GROUP 是否**静默丢弃**了基金（本项目铁律：缺失必须声明）
 ② 年化/回撤用「点数 n」当年数，但净值序列可能有缺口 → 年化会错
 ③ MIN_N=150/100/30 的样本量分级，依据是"P90 处约 15 个样本" —— 够吗？
"""
import os
import sqlite3
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\DSH\projects\Quantitative-Fund-auto"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from calibrate_thresholds import TYPE2GROUP, DB_PATH  # noqa: E402

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

print("=" * 92)
print("① TYPE2GROUP 是否静默丢弃基金")
print("=" * 92)
rows = list(conn.execute("""
    select f.fund_type, count(distinct f.fund_code)
    from fund_info f
    join (select fund_code from fund_nav group by fund_code having count(*)>=756) d
      on d.fund_code = f.fund_code
    group by f.fund_type order by 2 desc
"""))
mapped = unmapped = 0
print(f"  {'类型':24} {'深历史只数':>10}  是否已映射")
for t, n in rows:
    ok = t in TYPE2GROUP
    if ok:
        mapped += n
    else:
        unmapped += n
    print(f"  {str(t)[:22]:24} {n:>10}  {'✅ ' + TYPE2GROUP[t] if ok else '❌ 未映射，被丢弃'}")
print()
print(f"  已映射 {mapped} 只 / **被静默丢弃 {unmapped} 只**")
print(f"  → {'⚠️ 存在静默丢弃，违反本项目「缺失必须声明」的铁律' if unmapped else '无丢弃'}")

print()
print("=" * 92)
print("② 年化公式用「点数 n」当年数 —— 净值有缺口时会错")
print("=" * 92)
cur = conn.cursor()
cur.execute("""
    select fund_code, nav_date from fund_nav
    where fund_code in (select fund_code from fund_nav group by fund_code having count(*)>=756)
    order by fund_code, nav_date
""")
ser = {}
for c, d in cur.fetchall():
    ser.setdefault(c, []).append(d)
from datetime import date
bad = []
for c, ds in ser.items():
    n = len(ds)
    span_days = (date.fromisoformat(ds[-1]) - date.fromisoformat(ds[0])).days
    span_td = span_days * 252 / 365.25          # 理论交易日
    ratio = n / span_td if span_td else 0
    if ratio < 0.95 or ratio > 1.05:
        bad.append((c, n, ds[0], ds[-1], span_days, ratio))
print(f"  检查 {len(ser)} 只：点数与日期跨度不匹配的有 {len(bad)} 只")
for c, n, d0, d1, sp, r in bad[:8]:
    print(f"    {c}  {n} 点 / {d0}~{d1}（{sp} 天）→ 比值 {r:.3f}")
if bad:
    print()
    print("  ⚠️ 比值 <1 = 净值有缺口（点数少于应有权重），年化会被**高估**")
    print("     我的 metrics_at 用 n 当年数：ann = (1+ret)^(252/n) − 1")
    print("     若真实跨 15 年但只有 3000 点（应有 3780），n/252=11.9 年 ≠ 15 年")

print()
print("=" * 92)
print("③ MIN_N=150 时，P90 的估计稳不稳？（bootstrap）")
print("=" * 92)
cur.execute("""
    select fund_code, nav_date, COALESCE(acc_nav, unit_nav) from fund_nav
    where fund_code in (select fund_code from fund_nav group by fund_code having count(*)>=756)
      and COALESCE(acc_nav, unit_nav) > 0 order by fund_code, nav_date
""")
v = {}
for c, d, x in cur.fetchall():
    v.setdefault(c, []).append(float(x))
fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
A = []
for c, arr in v.items():
    if TYPE2GROUP.get(fi.get(c, "")) != "A 偏股混合":
        continue
    a = np.array(arr)
    w = min(len(a), 252)
    seg = a[-w:]
    pk = np.maximum.accumulate(seg)
    A.append(float(((pk - seg) / pk).max() * 100))
A = np.array(A)
print(f"  A 组回撤指标 n={A.size}")
rng = np.random.default_rng(42)
for n in [30, 65, 100, 150, 191]:
    if n > A.size:
        continue
    p90s, iqrs = [], []
    for _ in range(400):
        s = rng.choice(A, size=n, replace=False)
        p90s.append(np.percentile(s, 90))
        iqrs.append(np.percentile(s, 75) - np.percentile(s, 25))
    p90s, iqrs = np.array(p90s), np.array(iqrs)
    # P90 的 90% 置信区间宽度，以 IQR 为单位
    ci = np.percentile(p90s, 95) - np.percentile(p90s, 5)
    print(f"    n={n:>4}  P90 的 90%CI 宽度 = {ci:6.2f}pp = {ci/np.median(iqrs):.2f}×IQR"
          f"   {'✅ 可用 P90' if ci/np.median(iqrs) < 0.5 else '⚠️ P90 不稳，应降级到 P75'}")
