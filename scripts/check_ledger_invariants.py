# -*- coding: utf-8 -*-
"""
数据契约检查 —— 用**会计（复式记账）+ 数据工程（数据质量约束）**两个交叉学科的视角查库。

覆盖两层：
  · **账本**（A/B/C 段）：`holdings` / `transactions` 必须自洽；
  · **市场数据**（D 段）：`fund_nav` / `index_daily` / `market_temperature` / `fund_metrics`
    的**覆盖率 / 新鲜度 / 单位 / 引用完整性**。

为什么要有这个脚本：
  · 本项目已因"口径/单位"出过两次真事故（`fund_size` 万元混亿元、`classify` 子串误分），
    两次都是**事后人肉发现**的。这类问题不该靠运气。
  · 前端有 `scripts/verify_frontend.py` 兜回归，**数据层一直没有对应的守卫**。
  · 2026-10-01 第一次跑就抓到真缺陷：`transactions.shares` 有 12 条买入没填（占买入额 28%），
    导致复式记账恒等式 7/13 只基金不平 —— 详见 KNOWN 段。
    （该缺陷已于同日修复、KNOWN 基线归零：docs/审计修复记录.md 第四批 §14。）
  · 2026-10-03 按审计 §3 E3 扩到**市场数据**（D 段）—— 依据：
    > `check_ledger_invariants.py` 已验证有效，可扩到市场数据（覆盖率/新鲜度/单位/引用）。
    > **两次真事故都是事后人肉发现的。**
    ⚠️ 文件名保留 `check_ledger_invariants.py`（`AGENTS.md`、交接文档与验收命令都引用它），
    但职责已从"账本"扩为"**数据契约**"。

**只读**：本脚本不含任何 INSERT/UPDATE/DELETE/commit。

用法：
    python scripts/check_ledger_invariants.py            # 有任何失败则退出码 1
    python scripts/check_ledger_invariants.py --explain  # 额外打印每条不变量的学科依据

分类：
    BLOCKER —— 必须成立。失败即数据有问题，会以退出码 1 反映。
    KNOWN   —— **已知缺口**，有明确根因、待决策修复。只报告，但**条数不得增长**
               （增长说明又退化了，同样算失败）。
    INFO    —— 只记录，**不参与判定**（用于"该知道但目前没有可靠阈值"的事实，
               例如覆盖率的具体数值、交易日历的覆盖范围）。
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
    #: 2026-10-03 E3 扩到市场数据时发现：40 行 `fund_nav` 的 unit_nav/acc_nav **同时为空**
    #: （只涉及 2 只基金 000425/000476，且日期含周末 → 是"按日历占位但无数据"的行），
    #: 但同行的 `daily_return` 有值 → **有收益、无净值**，语义矛盾。
    #: 未修（属既有状态、影响面小：所有取净值的地方都过滤 `unit_nav IS NOT NULL`）。
    "fund_nav 净值双列全空的行数": 40,
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


def info(name: str, why: str, detail: str, explain: bool = False) -> None:
    """只记录，**不参与判定**（INFO 不计入 `results`，不影响退出码）。"""
    print("  [INFO] %-40s %s" % (name, detail))
    if explain:
        print("        依据：%s" % why)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--explain", action="store_true", help="打印每条不变量的学科依据")
    ap.add_argument("--db", default=DB, help="库路径（默认 data/fund_quant.db；测试用临时库）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit("找不到库：%s" % args.db)
    db = Database(args.db)
    c = db.conn
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
    ex = args.explain

    def has_table(name: str) -> bool:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                         (name,)).fetchone() is not None

    #: `fund_metrics` 由 `src/analysis/fund_metrics_store.py` 建（不在 `Database` 的建表里）
    #: → 新库/未跑过 E2 的库可能没有这张表，此时**跳过**相关检查而不是崩掉。
    has_metrics = has_table("fund_metrics")

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

    print("=" * 96)
    print("D. 市场数据契约（覆盖率 / 新鲜度 / 单位 / 引用完整性） —— 2026-10-03 E3 新增")
    print("=" * 96)
    print("  依据：审计第四批 §3 E3 —— 本项目两次真事故（万元混亿元、classify 误分）都在市场数据侧，")
    print("        且**都是事后人肉发现**。这一段就是给它们装上红灯。")

    # ── 覆盖率 ────────────────────────────────────────────────────
    n_fund_info = one("SELECT COUNT(*) FROM fund_info")
    n_covered = one("SELECT COUNT(DISTINCT fund_code) FROM fund_nav")
    cov = (n_covered / n_fund_info) if n_fund_info else 0.0
    blocker("第 12 条：净值覆盖率 ≥ 80%",
            "数据工程·覆盖率：一次失败的采集最典型的指纹就是**覆盖率骤降**"
            "（正常约 86%；分母是 fund_info 全部基金，含尚无净值的新基金）",
            cov >= 0.80,
            "覆盖 %d/%d = %.1f%%" % (n_covered, n_fund_info, cov * 100), ex)

    # ── 单位 / 值域 ───────────────────────────────────────────────
    bad_close = one("SELECT COUNT(*) FROM index_daily WHERE close IS NOT NULL"
                    " AND (close < 100 OR close > 20000)")
    blocker("第 13 条：指数收盘 ∈ [100, 20000]（点）",
            "数据工程·单位一致性：A股宽基历史峰值 <12000（中证500 于 2015-06 见 11545）→"
            "上限留 1.7 倍余量；下限 100 用来抓『点数被当成万元/百分比』的单位混装",
            bad_close == 0, "越界 %d 条（当前区间 %.2f ~ %.2f）" % (
                bad_close, one("SELECT MIN(close) FROM index_daily WHERE close IS NOT NULL"),
                one("SELECT MAX(close) FROM index_daily WHERE close IS NOT NULL")), ex)

    bad_nav = one("SELECT COUNT(*) FROM fund_nav WHERE (unit_nav IS NOT NULL AND unit_nav <= 0)"
                  " OR (acc_nav IS NOT NULL AND acc_nav <= 0)")
    blocker("第 14 条：净值非空时必须 > 0",
            "金融数据·值域：净值不可能 ≤ 0（NULL = 缺数据，0 = 占位符/坏数据，两者必须分开）",
            bad_nav == 0, "非正 %d 条" % bad_nav, ex)

    # ── 引用完整性 ────────────────────────────────────────────────
    orphan_nav = one("""SELECT COUNT(DISTINCT n.fund_code) FROM fund_nav n
                        WHERE NOT EXISTS (SELECT 1 FROM fund_info f WHERE f.fund_code = n.fund_code)""")
    blocker("第 15 条：净值代码都在 fund_info",
            "数据工程·引用完整性：孤儿净值会让『有净值但查不到名称/类型』的基金混进筛选池",
            orphan_nav == 0, "孤儿 %d 个" % orphan_nav, ex)

    orphan_temp = one("""SELECT COUNT(DISTINCT m.trade_date) FROM market_temperature m
                         WHERE NOT EXISTS (SELECT 1 FROM index_daily d
                                           WHERE d.trade_date = m.trade_date)""")
    blocker("第 16 条：温度日期都在 index_daily",
            "数据工程·引用完整性 + 血缘：温度由当日指数估值/量能算出，"
            "出现『没有指数数据却有温度』的日期说明血缘断了",
            orphan_temp == 0, "孤儿 %d 天" % orphan_temp, ex)

    orphan_metrics = one("""SELECT COUNT(DISTINCT m.fund_code) FROM fund_metrics m
                            WHERE NOT EXISTS (SELECT 1 FROM fund_info f
                                              WHERE f.fund_code = m.fund_code)""") if has_metrics else 0
    if has_metrics:
        blocker("第 17 条：物化表代码都在 fund_info",
                "数据工程·引用完整性（DWS 层）：物化指标必须指回同一套基金主数据",
                orphan_metrics == 0, "孤儿 %d 个" % orphan_metrics, ex)
    else:
        info("物化表 fund_metrics", "DWS 层：缺失时 `load_or_compute` 会回退实时算，故不是单点故障",
             "不存在 → 跳过第 17 / 19 条", ex)

    # ── 新鲜度 ────────────────────────────────────────────────────
    future = one("SELECT COUNT(*) FROM index_daily WHERE trade_date > date('now')") + \
        one("SELECT COUNT(*) FROM market_temperature WHERE trade_date > date('now')")
    blocker("第 18 条：市场数据没有未来日期",
            "时序·无前视：未来日期的指数/温度会把回测变成作弊（同第 5 条对净值的要求）",
            future == 0, "未来 %d 条" % future, ex)

    # DWS 层新鲜度：物化表不得落后于 ODS
    nav_max = one("SELECT MAX(nav_date) FROM fund_nav")
    fm_max = one("SELECT MAX(asof) FROM fund_metrics") if has_metrics else None
    if has_metrics:
        blocker("第 19 条：物化表 asof 追平最新净值日",
                "数据工程·新鲜度（DWS 不得落后于 ODS）：`fund_metrics` 是筛选/同类分位/回撤预警的"
                "物化来源，一旦落后就说明消费者拿到的是**旧指标** → 修复方式是重跑 "
                "`python scripts/refresh_fund_metrics.py`",
                (fm_max or "") >= (nav_max or ""),
                "fund_metrics %s vs fund_nav %s" % (fm_max, nav_max), ex)

    # 新鲜度一致性：温度与指数不得互相脱节
    temp_max = one("SELECT MAX(trade_date) FROM market_temperature")
    idx_max = one("SELECT MAX(trade_date) FROM index_daily")
    blocker("第 20 条：温度与指数的最新日期一致",
            "数据工程·新鲜度一致性：温度是**由指数数据派生**的，两者最新日不一致 = 派生链断在半路",
            (temp_max or "") == (idx_max or ""),
            "温度 %s vs 指数 %s" % (temp_max, idx_max), ex)

    # 交易日历滞后回归护栏（粗粒度上限，防"日历长期不刷新"）
    cal_max = one("SELECT MAX(trade_date) FROM trade_calendar")
    cal_lag = one("SELECT CAST(julianday(?) - julianday(?) AS INTEGER)", nav_max, cal_max) \
        if (cal_max and nav_max) else -1
    blocker("第 21 条：交易日历滞后最新净值日 ≤ 60 自然日",
            "数据工程·新鲜度：交易日历用于 T+1 确认与『定投跳过节假日』；"
            "长期不刷新会让近期流水无法判断确认日。此处只要**回归护栏**（上限放宽），"
            "具体覆盖范围见下方 INFO",
            0 <= cal_lag <= 60, "滞后 %d 天" % cal_lag, ex)

    # ── 只记录、不判定 ────────────────────────────────────────────
    known("fund_nav 净值双列全空的行数",
          "根因：采集按日历占位却拿不到净值（实测只涉及 2 只基金、日期含周末），"
          "但同行 `daily_return` 有值 → 『有收益、无净值』语义矛盾。"
          "影响面小（取净值处都过滤 `unit_nav IS NOT NULL`），未修，记下来",
          one("SELECT COUNT(*) FROM fund_nav WHERE unit_nav IS NULL AND acc_nav IS NULL"), ex)

    info("交易日历覆盖范围", "数据工程·覆盖率：日历只覆盖一段窗口，超出的日期无法做交易日判断",
         "%s ~ %s（共 %d 天）" % (
             one("SELECT MIN(trade_date) FROM trade_calendar"), cal_max,
             one("SELECT COUNT(*) FROM trade_calendar")), ex)
    info("交易日历未覆盖的持仓批次",
         "数据工程·覆盖率：这些批次的 T+1 确认 / 节假日判断**没有日历可用**（属既有状态）",
         "%d 条（最早未覆盖 %s）" % (
             one("SELECT COUNT(*) FROM holdings WHERE buy_date > ?", cal_max),
             one("SELECT MIN(buy_date) FROM holdings WHERE buy_date > ?", cal_max) or "—"), ex)
    info("各表最新日期", "数据工程·新鲜度：一眼看清五张表是否同步",
         "fund_nav %s｜index_daily %s｜market_temperature %s｜fund_metrics %s｜trade_calendar %s"
         % (nav_max, idx_max, temp_max, fm_max, cal_max), ex)

    db.close()

    bad = [r for r in results if not r[2]]
    print("\n" + "=" * 96)
    print("汇总：%d 项检查，%d 项失败" % (len(results), len(bad)))
    for kind, name, _ok, detail in bad:
        print("  [%s] %s —— %s" % (kind, name, detail))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
