# -*- coding: utf-8 -*-
"""
① 覆盖率复算 + 盲区归因  v2
============================
v1 → v2 修订（2026-09-21）：
  ✗ v1 把「可买池」定义成 `purchase_status like '%开放%' OR 为空/NULL`（22,413 只），
    与深采脚本 `collect_deep_nav.py:129` 的窄口径（`like '%开放%'`，18,677 只）不一致，
    于是把 **3,736 只数据源根本不覆盖的对象**算成了「采集盲区」。

  【实测】抽样 40 只空状态基金直接打源头 pingzhongdata：**39 只返回不了任何数据**；
    整个 3,736 只组里仅 62 只有任何净值。它们是：
      · 190 只「(后端)」虚拟份额 —— 不单独披露净值，与前端份额同一只基金
      · 967 只普通货币基金 —— 走「每万份收益」，根本不披露单位净值
      ·  94 只 REITs —— 同源但格式不同
      · 其余为已终止/未成立的联接份额
  ✓ 结论：**窄口径才是正确的分母**（数据源覆盖 = 可采集 = 可建参照系的前提）。
    把它们放进分母只会把覆盖率算低，且**没有任何可采取的措施**（源头就没数据）。

  ✓ 另一处修正：门槛一直是「**点数** ≥756」，但定开基金（定期开放）每周/每月才披露一次净值，
    9 年只有 ~590 个点，被**错误排除**。本版增加「**日历跨度**」维度分别统计。
"""
import io
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.analysis.nav_series import valuation_nav_sql

DB = os.path.join(ROOT, "data", "fund_quant.db")
OUT = os.path.join(ROOT, "docs", "coverage_attribution.json")
V = valuation_nav_sql("n")

MIN_DEEP = 756          # 3 年交易日
MIN_SPAN_DAYS = 1095    # 3 年日历日
SOURCED = "i.purchase_status like '%开放%'"     # 数据源覆盖池（= 深采目标）
OUT_OF_SCOPE = "(i.purchase_status is null or i.purchase_status = '')"

conn = sqlite3.connect(DB)
conn.execute("PRAGMA busy_timeout=60000")
cur = conn.cursor()
out = {}

print("=" * 84)
print("① 覆盖率复算 + 盲区归因  v2（可买池 = 数据源覆盖池）")
print("=" * 84)

# ---------- 1. 三种"池"的界定 ----------
n_all = cur.execute("select count(*) from fund_info").fetchone()[0]
n_src = cur.execute(f"select count(*) from fund_info i where {SOURCED}").fetchone()[0]
n_oos = cur.execute(f"select count(*) from fund_info i where {OUT_OF_SCOPE}").fetchone()[0]
print("\n[1] 池的界定")
print("    名录总数                        %8s" % f"{n_all:,}")
print("    数据源覆盖池（=深采目标）        %8s  ← **正确分母**" % f"{n_src:,}")
print("    源不覆盖（状态为空，实测无数据）  %8s  ← 出局，不进分母" % f"{n_oos:,}")
out["pools"] = {"all": n_all, "sourced": n_src, "out_of_scope": n_oos}

# ---------- 2. 每只基金的有效点数与日历跨度 ----------
print("\n[2] 逐只计算有效序列（SSOT 口径）…")
stat = {}
for code, pts, first_d, last_d in cur.execute(f"""
        select n.fund_code, count(*), min(n.nav_date), max(n.nav_date)
        from fund_nav n where {V} > 0 group by n.fund_code"""):
    stat[code] = {"pts": pts, "first": first_d, "last": last_d}
print("    有有效净值 %s 只" % f"{len(stat):,}")

meta = {c: {"name": n, "type": t or "(空)", "est": e, "status": s or "(空)"}
        for c, n, t, e, s in cur.execute(
            "select fund_code, fund_name, fund_type, establish_date, purchase_status from fund_info")}
GLOBAL_LAST = cur.execute("select max(nav_date) from fund_nav").fetchone()[0]
print("    全库最新净值日 %s" % GLOBAL_LAST)


def span_days(s):
    if not s:
        return 0
    import datetime
    a = datetime.date(*map(int, s["first"].split("-")))
    b = datetime.date(*map(int, s["last"].split("-")))
    return (b - a).days


# ---------- 3. 覆盖率（点数与跨度两种口径） ----------
src_codes = [r[0] for r in cur.execute(f"select i.fund_code from fund_info i where {SOURCED}")]
pts_deep = [c for c in src_codes if stat.get(c, {}).get("pts", 0) >= MIN_DEEP]
span_deep = [c for c in src_codes if span_days(stat.get(c)) >= MIN_SPAN_DAYS]
# 交集：点够 且 跨度够（最严）
both = [c for c in src_codes
        if stat.get(c, {}).get("pts", 0) >= MIN_DEEP and span_days(stat.get(c)) >= MIN_SPAN_DAYS]

print("\n[3] 覆盖率（分母 = 数据源覆盖池 %s）" % f"{n_src:,}")
print("    口径① 点数  >= %d 交易日 : %8s 只 → **%.1f%%**" % (MIN_DEEP, f"{len(pts_deep):,}", len(pts_deep) / n_src * 100))
print("    口径② 跨度  >= 3 年      : %8s 只 → **%.1f%%**" % (f"{len(span_deep):,}", len(span_deep) / n_src * 100))
print("    口径③ 两者都满足        : %8s 只 → **%.1f%%**" % (f"{len(both):,}", len(both) / n_src * 100))
extra = set(span_deep) - set(pts_deep)
print("    ⚠️ 仅跨度够、点数不够（定开基金被误排）: %s 只" % f"{len(extra):,}")
out["coverage"] = {"sourced": n_src, "pts_deep": len(pts_deep), "span_deep": len(span_deep),
                   "both": len(both), "span_only": len(extra)}

# ---------- 4. 盲区归因（只对被覆盖池） ----------
blind = [c for c in src_codes if c not in set(pts_deep)]
print("\n[4] 盲区归因：数据源覆盖池内 %s 只无深历史" % f"{len(blind):,}")
reasons = Counter()
samples = defaultdict(list)
SPAN3 = 1095
for c in blind:
    s = stat.get(c)
    m = meta.get(c, {})
    if not s or s["pts"] == 0:
        r = "R1 有申购状态但一条净值都没有"
    elif s["last"] <= "2026-06-30":
        r = "R2 已停止更新（疑似清盘/终止）"
    elif span_days(s) < SPAN3:
        r = "R3 存续不足 3 年（产品边界）"
    elif s["pts"] < MIN_DEEP:
        r = "R4 存续够 3 年但净值稀疏（定开/低频披露）"
    else:
        r = "R5 其他"
    reasons[r] += 1
    if len(samples[r]) < 5:
        samples[r].append((c, m.get("name", "?"), m.get("type", "?"),
                           s["pts"] if s else 0, span_days(s), s["last"] if s else "-"))
for r, n in reasons.most_common():
    print("  %-38s %7s 只 %6.1f%%" % (r, f"{n:,}", n / len(blind) * 100))
    for c, nm, t, p, sp, ld in samples[r]:
        print("        %-8s %-20s %-13s pts=%-5d span=%-5d last=%s"
              % (c, (nm or "?")[:18], (t or "?")[:11], p, sp, ld))
out["reasons"] = dict(reasons)

# ---------- 5. 判定 ----------
print("\n[5] 判定")
r1 = reasons.get("R1 有申购状态但一条净值都没有", 0)
r2 = reasons.get("R2 已停止更新（疑似清盘/终止）", 0)
r3 = reasons.get("R3 存续不足 3 年（产品边界）", 0)
r4 = reasons.get("R4 存续够 3 年但净值稀疏（定开/低频披露）", 0)
print("    R1 状态开放但无净值   : %6s 只 ← **唯一需要排查的**（状态与数据矛盾）" % f"{r1:,}")
print("    R2 已停止更新         : %6s 只 ← 可接受，不该推荐；且是幸存者偏差的解药" % f"{r2:,}")
print("    R3 存续不足 3 年      : %6s 只 ← 产品边界，前端须显式声明「样本不足，不给百分位」" % f"{r3:,}")
print("    R4 定开/低频披露      : %6s 只 ← **口径缺陷：门槛按点数算，把它们误排除了**" % f"{r4:,}")
out["verdict"] = {"R1": r1, "R2": r2, "R3": r3, "R4": r4}

json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n明细:", os.path.relpath(OUT, ROOT))
