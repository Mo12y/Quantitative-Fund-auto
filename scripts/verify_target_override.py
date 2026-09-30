# -*- coding: utf-8 -*-
"""
验证 target_equity_pct 覆盖真的生效。
⚠️ 会临时改写 config/user_profile.local.yaml，**在 finally 里原样还原**。
"""
import io
import json
import os
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "user_profile.local.yaml")


def get(path):
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen("http://127.0.0.1:5020" + path + sep + "fresh=1",
                                timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def show(tag):
    rb = (get("/api/rebalance").get("data") or {})
    print("  [%s] current=%s target=%s source=%s gap=%s amount=%s need=%s"
          % (tag, rb.get("current_equity_pct"), rb.get("target_equity_pct"),
             rb.get("target_source"), rb.get("gap_pct"), rb.get("gap_amount"),
             rb.get("need_rebalance")))
    return rb


original = open(CFG, encoding="utf-8").read()
print("=" * 76)
print("覆盖功能验证")
print("=" * 76)
try:
    show("覆盖前（应为 temperature / 36.9）")

    print("\n  写入 target_equity_pct: 45 …")
    with open(CFG, "w", encoding="utf-8") as f:
        f.write(original.rstrip() + "\ntarget_equity_pct: 45\n")
    rb = show("覆盖后（应为 user_profile / 45.0）")

    ok1 = rb.get("target_source") == "user_profile" and abs(rb.get("target_equity_pct", 0) - 45.0) < 0.01
    print("  目标与来源:", "✅" if ok1 else "❌")
    exp = round(rb["gap_pct"] / 100.0 * rb["total_capital"], 2)
    print("  gap_amount 自洽: 期望 %.2f 实得 %s %s"
          % (exp, rb.get("gap_amount"), "✅" if abs(exp - rb["gap_amount"]) < 0.02 else "❌"))
    print("  need_rebalance 随目标变化: %s（|%.1f| > 5.0 = %s）"
          % (rb.get("need_rebalance"), rb["gap_pct"], abs(rb["gap_pct"]) > 5.0))
finally:
    with open(CFG, "w", encoding="utf-8") as f:
        f.write(original)
    print("\n  已还原 config/user_profile.local.yaml（字节数 %d）" % len(original))
    rb2 = show("还原后（应回到 temperature / 36.9）")
    print("  还原判定:", "✅" if rb2.get("target_source") == "temperature" else "❌ 未还原干净")
