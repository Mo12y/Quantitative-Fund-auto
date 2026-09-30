# -*- coding: utf-8 -*-
"""验证：目标权益覆盖 + 差额金额与百分比同基数"""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def get(path):
    # ⚠️ 必须带 fresh=1：否则可能命中 analysis_snapshot 里的**旧载荷**
    # （实测：改完后端后首次请求 source=snapshot，新字段全为 None，
    #   看起来像"改动没生效"，其实是缓存。这也是前端"重算"按钮的用途。）
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen("http://127.0.0.1:5020" + path + sep + "fresh=1",
                                timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


d = get("/api/rebalance")
rb = d.get("data") or {}
print("ok =", d.get("ok"), " source =", d.get("source"))
if rb.get("error"):
    print("ERROR:", rb["error"])
    sys.exit(1)

print("=" * 76)
print("新增/暴露的字段")
print("=" * 76)
for k in ("current_equity_pct", "target_equity_pct", "target_source",
          "gap_pct", "gap_amount", "rebalance_pp",
          "total_capital", "portfolio_value", "cash_reserve",
          "non_applicable_pct", "need_rebalance"):
    print("  %-22s %r" % (k, rb.get(k)))

print()
print("=" * 76)
print("自洽性校验：gap_amount 必须 = gap_pct%% × total_capital")
print("=" * 76)
gp, ga, tc = rb.get("gap_pct"), rb.get("gap_amount"), rb.get("total_capital")
expect = round(gp / 100.0 * tc, 2) if (gp is not None and tc) else None
print("  gap_pct %.1f%% × total_capital %.2f = %.2f" % (gp, tc, expect))
print("  后端给的 gap_amount                = %s" % ga)
print("  判定:", "✅ 一致" if abs((ga or 0) - (expect or 0)) < 0.02 else "❌ 不一致")

print()
print("=" * 76)
print("对照：旧前端算法的结果（portfolio.total_invested × target%）")
print("=" * 76)
ov = get("/api/overview").get("data") or {}
p_ti = (ov.get("portfolio") or {}).get("total_invested")
tgt = rb.get("target_equity_pct")
if p_ti and tgt:
    print("  portfolio.total_invested = %.2f" % p_ti)
    print("  旧前端算的『权益 ≈』      = %.2f   ← 基数错了（不是百分比的基数）" % (p_ti * tgt / 100))
    print("  按正确基数算的目标金额     = %.2f" % (tc * tgt / 100))
    print("  差额                       = %.2f（%.0f%%）"
          % (tc * tgt / 100 - p_ti * tgt / 100,
             abs(tc * tgt / 100 - p_ti * tgt / 100) / (tc * tgt / 100) * 100))

print()
print("=" * 76)
print("容忍带判定")
print("=" * 76)
print("  rebalance_pp = %s   |gap_pct| = %s   need_rebalance = %s"
      % (rb.get("rebalance_pp"), abs(gp) if gp is not None else None, rb.get("need_rebalance")))
band = rb.get("rebalance_pp")
if band is not None and gp is not None:
    print("  判定:", "✅ 与 |gap| > pp 一致"
          if (abs(gp) > band) == bool(rb.get("need_rebalance")) else "❌ 不一致")
