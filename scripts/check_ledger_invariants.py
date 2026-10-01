# -*- coding: utf-8 -*-
"""
账本不变量检查 —— 用**会计（复式记账）+ 数据工程（数据质量约束）**两个交叉学科的视角查库。

为什么要有这个脚本：
  · 本项目已因"口径/单位"出过两次真事故（`fund_size` 万元混亿元、`classify` 子串误分），
    两次都是**事后人肉发现**的。这类问题不该靠运气。
  · 前端有 `scripts/verify_frontend.py` 兜回归，**数据层一直没有对应的守卫**。
  · 2026-10-01 第一次跑就抓到真缺陷：`transactions.shares` 有 12 条买入没填（占买入额 28%），
    导致复式记账恒等式 7/13 只基金不平 —— 详见 KNOWN 段。
    （该缺陷已于同日修复、KNOWN 基线归零：docs/审计修复记录.md 第四批 §14。）

**只读**：本脚本不含任何 INSERT/UPDATE/DELETE/commit。

用法：
    python scripts/check_ledger_invariants.py            # 有 BLOCKER 失败则退出码 1
    python scripts/check_ledger_invariants.py --explain  # 额外打印每条不变量的学科依据

分类：
    BLOCKER —— 必须成立。失败即数据有问题，会以退出码 1 反映。
    KNOWN   —— **已知缺口**，有明确根因、待决策修复。只报告，但**条数不得增长**
               （增长说明又退化了，同样算失败）。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.data.database import Database  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")

#: KNOWN 段的基线（超过即视为退化）。**2026-10-01 三项已全部修复 → 基线归零**（再出现即回归）。
#: 修复过程与回滚点见 docs/审计修复记录.md 第四批 §14；修复脚本 scripts/backfill_transaction_shares.py。
KNOWN_BASELINE = {
    "复式记账恒等式不平的基金数": 0,
    "买入流水 shares 缺失条数": 0,
    "sold 批次缺 sell_amount 条数": 0,
}

results: list[tuple[str, str, bool, str]] = []


def blocker(name: str, why: str, ok: bool, detail: str = "", explain: bool = False) -> None:
    results.append(("BLOCKER", name, ok, detail))
    print(("  [OK]  " if ok else "  [BAD] ") + "%-40s %s" % (name, detail))
    if explain:
        print("        依据：%s" % why)


def known(name: str, why: str, n: int, explain: bool = False) -> None:
    cap = KNOWN_BASELINE.get(name, 0)
    ok = n <= cap
    results.append(("KNOWN", name, ok, "实测 %d，基线 %d" % (n, cap)))
    print(("  [OK]  " if ok else "  [BAD] ") + "%-40s 实测 %d，基线 %d" % (name, n, cap))
    if explain:
        print("        依据：%s" % why)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--explain", action="store_true", help="打印每条不变量的学科依据")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("找不到账本库：%s" % DB)
    db = Database(DB)
    c = db.conn
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
    ex = args.explain

    print("=" * 96)
    print("A. 会计不变量（复式记账：流水 ↔ 持仓 必须自洽）")
    print("=" * 96)

    tx = defaultdict(lambda: [0.0, 0.0])
    for r in c.execute("SELECT fund_code, kind, shares FROM transactions WHERE shares > 0"):
        tx[r[0]][0 if r[1] == "buy" else 1] += float(r[2])
    hold = defaultdict(float)
    for r in c.execute("SELECT fund_code, SUM(shares) FROM holdings"
                       " WHERE status='holding' AND shares IS NOT NULL GROUP BY fund_code"):
        hold[r[0]] += float(r[1])
    unbalanced = [fc for fc in set(list(tx) + list(hold))
                  if abs(tx.get(fc, [0, 0])[0] - tx.get(fc, [0, 0])[1] - hold.get(fc, 0.0)) > 0.05]
    known("复式记账恒等式不平的基金数",
          "会计·复式记账：Σ买入份额 − Σ卖出份额 必须等于在持份额（容差 0.05 份为份额两位小数舍入）",
          len(unbalanced), ex)
    if unbalanced:
        print("        不平基金：%s" % ", ".join(sorted(unbalanced)))

    blocker("第 2 条：每条流水都有 confirm_nav / confirm_date",
            "会计·凭证完整性：没有确认净值就无法定价，没有确认日就算不出持有期",
            one("SELECT COUNT(*) FROM transactions WHERE confirm_nav IS NULL OR confirm_date IS NULL") == 0,
            "缺 %d 条" % one("SELECT COUNT(*) FROM transactions WHERE confirm_nav IS NULL OR confirm_date IS NULL"), ex)

    m2 = one("SELECT COUNT(*) FROM transactions WHERE kind='buy' AND amount IS NULL")
    m3 = one("SELECT COUNT(*) FROM transactions WHERE kind='sell' AND amount IS NOT NULL")
    blocker("第 3 条：买入必有 amount、卖出必无 amount",
            "会计·语义一致性：本项目规定卖出金额由 shares×confirm_nav 换算，amount 字段只对买入有值",
            m2 == 0 and m3 == 0, "买入缺 %d / 卖出多 %d" % (m2, m3), ex)

    print("=" * 96)
    print("B. 数据质量约束（数据工程：值域 / 引用完整性 / 时序）")
    print("=" * 96)

    blocker("第 4 条：净值恒为正", "金融数据·值域：净值不可能 ≤ 0",
            one("SELECT COUNT(*) FROM fund_nav WHERE acc_nav IS NOT NULL AND acc_nav <= 0") == 0,
            "非正 %d 条" % one("SELECT COUNT(*) FROM fund_nav WHERE acc_nav IS NOT NULL AND acc_nav <= 0"), ex)

    blocker("第 5 条：净值日期不在未来", "时序·无前视：未来净值会把回测变成作弊",
            one("SELECT COUNT(*) FROM fund_nav WHERE nav_date > date('now')") == 0,
            "未来 %d 条" % one("SELECT COUNT(*) FROM fund_nav WHERE nav_date > date('now')"), ex)

    blocker("第 6 条：申购费率 ∈ [0,5]%（百分数口径）", "数据工程·值域 + 单位约定",
            one("SELECT COUNT(*) FROM fund_info WHERE purchase_fee IS NOT NULL"
                " AND (purchase_fee < 0 OR purchase_fee > 5)") == 0,
            "越界 %d 条" % one("SELECT COUNT(*) FROM fund_info WHERE purchase_fee IS NOT NULL"
                              " AND (purchase_fee < 0 OR purchase_fee > 5)"), ex)

    blocker("第 7 条：基金规模 ≤ 500 亿", "数据工程·单位一致性：>500 亿即 2026-09-27 那起万元/亿元混装事故的指纹",
            one("SELECT COUNT(*) FROM fund_info WHERE fund_size IS NOT NULL AND fund_size > 500") == 0,
            ">500 亿 %d 条" % one("SELECT COUNT(*) FROM fund_info WHERE fund_size IS NOT NULL"
                                " AND fund_size > 500"), ex)

    blocker("第 8 条：持仓 fund_code 都在 fund_info", "数据工程·引用完整性",
            one("""SELECT COUNT(DISTINCT h.fund_code) FROM holdings h
                   WHERE NOT EXISTS (SELECT 1 FROM fund_info f WHERE f.fund_code = h.fund_code)""") == 0,
            "孤儿 %d 个" % one("""SELECT COUNT(DISTINCT h.fund_code) FROM holdings h
                                  WHERE NOT EXISTS (SELECT 1 FROM fund_info f
                                                    WHERE f.fund_code = h.fund_code)"""), ex)

    blocker("第 9 条：持仓份额 ≥ 0", "会计·值域",
            one("SELECT COUNT(*) FROM holdings WHERE shares < 0") == 0,
            "负 %d 条" % one("SELECT COUNT(*) FROM holdings WHERE shares < 0"), ex)

    blocker("第 10 条：同一基金同一净值日唯一", "时序·唯一性：重复日会让收益序列出现 0 收益",
            one("""SELECT COUNT(*) FROM (SELECT fund_code, nav_date FROM fund_nav
                   GROUP BY fund_code, nav_date HAVING COUNT(*) > 1)""") == 0,
            "重复 %d 组" % one("""SELECT COUNT(*) FROM (SELECT fund_code, nav_date FROM fund_nav
                                  GROUP BY fund_code, nav_date HAVING COUNT(*) > 1)"""), ex)

    ncost = one("""SELECT COUNT(*) FROM holdings h
                   WHERE h.confirm_nav IS NOT NULL AND h.shares IS NOT NULL AND h.buy_amount IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM transactions t
                                     WHERE t.holding_id = h.id AND t.kind='sell')
                     AND ABS(h.buy_amount - h.shares * h.confirm_nav) > 0.02""")
    blocker("第 11 条：无卖出批次 cost == 份额 × 确认净值",
            "会计·计价自洽（C 类零费率）。⚠️ 对**有部分卖出**的批次不适用 —— "
            "buy_amount 是卖出后剩余的摊薄成本，不是原始投入", ncost == 0, "偏差 %d 条" % ncost, ex)

    print("=" * 96)
    print("C. 已知缺口（有明确根因、待决策修复；条数不得增长）")
    print("=" * 96)

    known("买入流水 shares 缺失条数",
          "根因：补录/导入流水时只写了金额没写份额。对应 holdings 批次是**对的**，"
          "所以可回填；但回填要动账本，须先整库快照（见 docs/前端重构计划书.md 的铁律）",
          one("SELECT COUNT(*) FROM transactions WHERE kind='buy'"
              " AND (shares IS NULL OR shares=0) AND COALESCE(amount,0)>0"), ex)

    known("sold 批次缺 sell_amount 条数", "根因：一笔清仓未记录卖出金额（份额已归 0）",
          one("SELECT COUNT(*) FROM holdings WHERE status='sold' AND sell_amount IS NULL"), ex)

    db.close()

    bad = [r for r in results if not r[2]]
    print("\n" + "=" * 96)
    print("汇总：%d 项检查，%d 项失败" % (len(results), len(bad)))
    for kind, name, _ok, detail in bad:
        print("  [%s] %s —— %s" % (kind, name, detail))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
