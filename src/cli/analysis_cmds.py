"""
分析命令：portfolio / plan / counterfactual / sell_rules / breakeven / behavior / drift / precompute。

⚠️ 2026-10-14 S1'-c2：`score` / `temp` / `rebalance` / `sentiment` / `sector` / `recommend` / `oos`
已随其命题层模块一并归档（`legacy/`），不再注册。
"""

from src.analysis.investment_plan import get_plan, get_progress
from src.analysis.portfolio import PortfolioTracker
from src.data.database import Database


def cmd_portfolio():
    """查看持仓"""
    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)

    summary = tracker.get_portfolio_summary(reconcile=True)   # CLI 是显式动作，先对账再展示

    if not summary.get("has_holdings"):
        print("📋 暂无持仓记录。")
        print("   添加买入记录: python src/main.py buy")
    else:
        print("📋 当前持仓")
        print("=" * 60)
        print(f"  总投入: ¥{summary['total_invested']:,.2f}")
        print(f"  总市值: ¥{summary['total_market_value']:,.2f}")
        print(f"  浮动盈亏: ¥{summary['total_pnl']:+,.2f} ({summary['total_return_pct']:+.2f}%)")
        print()
        for d in summary["holdings_detail"]:
            print(
                f"  {d['fund_name'][:25]:<25} | "
                f"买入: {d['buy_date']} | "
                f"投入¥{d['buy_amount']:,.0f} | "
                f"市值¥{d['current_value']:,.0f} | "
                f"盈亏¥{d['pnl']:+,.0f} ({d['pnl_pct']:+.1f}%) | "
                f"持有{d['days_held']}天"
            )
        print()
        print("  资产配置:")
        for ftype, pct in summary["asset_allocation"].items():
            print(f"    {ftype}: {pct}%")

    db.close()


def cmd_plan():
    """查看投资计划 + 当前进度"""
    db = Database("data/fund_quant.db")
    plan = get_plan()
    progress = get_progress(db)

    print()
    print("=" * 60)
    print(f"📋 {plan['name']}")
    print(f"   起始日期: {plan['start_date']} · 总资金: ¥{plan['total_capital']:.0f}")
    print("=" * 60)

    # 计划总览表
    print(f"\n{'基金':<22}{'角色':<10}{'目标':>8}{'已投':>8}{'还差':>8}{'进度':>8}")
    print("-" * 60)
    for f in progress["funds"]:
        bar_len = int(f["progress_pct"] / 100 * 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        print(
            f"{f['name'][:20]:<22}{f['role']:<10}"
            f"{f['target']:>6.0f}¥{f['invested']:>7.0f}¥{f['remaining']:>7.0f}¥"
            f"{bar:>11} {f['progress_pct']:.0f}%"
        )

    # 现金 + 汇总
    equity_target = plan['total_capital'] - plan['cash_reserve']
    print(f"\n💰 现金底仓(弹药): ¥{plan['cash_reserve']:.0f}")
    print(f"📊 已投权益: ¥{progress['total_invested']:.0f} / 目标 ¥{equity_target:.0f}")
    if equity_target > 0:
        print(f"   总进度: {progress['total_invested'] / equity_target * 100:.0f}%")

    # 下一笔建议
    print("\n" + "-" * 60)
    print("🎯 下一笔该买什么:")
    has_next = False
    for f in progress["funds"]:
        n = f["next"]
        if n and n["amount"] > 0:
            has_next = True
            if n.get("note"):
                print(f"   {f['name'][:20]:<22} {n['note']}")
            else:
                print(f"   {f['name'][:20]:<22} 买 ¥{n['amount']:.0f}  (建议 {n['date']})")
    if not has_next:
        print("   ✅ 所有基金已买满目标，等待温度信号再加仓")

    # 现金信号
    print(f"\n💡 现金 ¥{plan['cash_reserve']:.0f} 的使用时机:")
    for level, rule in plan["temp_rules"].items():
        print(f"   {rule}")

    print()
    print("⚠️ 操作提醒:")
    print("   1. 每次买入后: python src/main.py buy 录入")
    print("   2. 查看进度: python src/main.py plan")
    print("   3. 不满7天别赎回(1.5%惩罚费)")
    print("=" * 60)

    db.close()



def cmd_counterfactual():
    """反事实归因（P1）——「如果我当时不动，会怎样」。

    用你**自己的**流水回答三个反事实（不是验证策略，是验证操作）：
      ① 完全不动（买了就不卖）  ② 只定投（只留 10 元笔）  ③ 照温度信号动（温度低多投）
    四个情形**投入总额不同**，所以只能比 XIRR（资金加权年化），不能比盈亏额。
    """
    from src.analysis.counterfactual import compare
    from src.data.database import Database

    print("🔍 反事实归因：如果我当时不动，会怎样")
    print("   （只读你的流水与净值，不改任何东西）")
    print()
    db = Database()
    try:
        r = compare(db)
    finally:
        db.close()

    if r.get("error"):
        print("❌ %s" % r["error"])
        return

    print("区间 %s｜买入 %d 笔（定投 %d 笔）" % (r["span"], r["n_buys_total"], r["n_buys_dca"]))
    print("定投规则：%s" % r["dca_rule"])
    bench = r.get("benchmark_pct")
    if bench is not None:
        tone = "跌" if bench < 0 else "涨"
        print("同期 %s：%+.2f%%（%s）" % (r.get("benchmark_name", "基准"), bench, tone))
    print()

    # 「别被短期数据迷惑」—— 放在表格**之前**，因为它决定表格该怎么读
    smp = r.get("sample") or {}
    if smp.get("warning"):
        print(smp["warning"])
        print()
    if bench is not None and bench < -1:
        print("📌 区间内基准是**下跌**的 —— 组合的绝对亏损里，先有相当一部分是**市场（beta）**，")
        print("   不是你的操作（alpha）。请对照上表『实际 vs 完全不动』看**相对差**。")
        print()
    print("%-24s %5s %10s %11s %10s %11s" % ("情形", "笔数", "投入", "期末市值", "盈亏", "XIRR"))
    print("-" * 78)
    order = [("actual", "实际（你自己操作的）"), ("hold_all", "① 完全不动（买了就不卖）"),
             ("dca_only", "② 只定投（只留 10 元笔）"), ("temp_tilt", "③ 照温度信号动")]
    for key, label in order:
        c = r["cases"].get(key) or {}
        x = ("%+.2f%%" % c["xirr_pct"]) if c.get("xirr_pct") is not None else "n/a"
        print("%-24s %5s %10s %11s %10s %11s" % (
            label, c.get("n_buys"), c.get("invested"), c.get("end_value"),
            c.get("pnl"), x))
    print()
    print("读法：")
    for n in r["notes"]:
        print("  · %s" % n)
    print("  · ⚠️ 各情形的**标的构成不同**（定投笔偏纳斯达克、主动笔偏 A 股/黄金）——")
    print("    差异里混着「策略」与「标的」两个因素，别把全部差距都算成操作能力。")

    # ── 同标的反事实：把「标的」这个混淆项去掉（B9）────────────────────
    from src.analysis.counterfactual import same_fund_counterfactual
    db2 = Database()
    try:
        sf = same_fund_counterfactual(db2)
    finally:
        db2.close()
    if sf.get("error") or not sf.get("funds"):
        return
    print()
    print("=" * 78)
    print("🎯 同标的反事实：锁死**同一只基金 + 同一笔投入**，只变**买入时机**")
    print("   （上面几个情形标的构成不同；这一节把「标的」这个混淆项去掉）")
    print("=" * 78)
    print("%-8s %-16s %4s %8s %9s %9s %10s %8s %9s" %
          ("代码", "名称", "笔数", "投入", "实际%", "一次性%", "等额定投%", "vs一次", "vs定投"))
    print("-" * 96)
    for f in sf["funds"]:
        ed = f.get("even_dca") or {}
        print("%-8s %-16s %4d %8.0f %9.2f %9.2f %10s %8s %9s" % (
            f["code"], (f["name"] or "")[:14], f["n_buys"], f["invested"],
            f["actual"].get("pnl_pct") or 0, f["lump"].get("pnl_pct") or 0,
            ("%.2f" % ed["pnl_pct"]) if ed.get("pnl_pct") is not None else "—",
            f.get("vs_lump"), f.get("vs_even_dca")))
    print()
    for key, nm in (("vs_lump", "实际 − 一次性"), ("vs_even_dca", "实际 − 等额定投")):
        a = sf["summary"].get(key)
        if a:
            print("  %s：中位 %+.2fpp ｜ 均值 %+.2fpp ｜ 实际更差 %d / 更好 %d（n=%d）" %
                  (nm, a["median"], a["mean"], a["worse"], a["better"], a["n"]))
    print()
    for n in sf["notes"]:
        print("  · %s" % n)


def cmd_sell_rules():
    """同标的卖出规则对比（P1 扩展）—— **冻结买入，只变卖出规则**。

    与 `counterfactual` 的关键区别：那四个情形**标的构成不同**（定投偏纳斯达克、
    主动偏 A 股/黄金），差异里混着「策略」与「标的」。本命令把**买入完全冻结**
    （同一批基金/日期/金额），只让卖出规则变化 → 差异**纯粹来自卖出时机**。
    """
    from src.analysis.counterfactual import simulate_sell_rules
    from src.data.database import Database

    print("🔬 同标的卖出规则对比（买入已冻结，只变卖出规则）")
    print("   （只读你的流水与净值，不改任何东西）")
    print()
    db = Database()
    try:
        r = simulate_sell_rules(db)
    finally:
        db.close()

    if r.get("error"):
        print("❌ %s" % r["error"])
        return

    print("区间 %s｜冻结买入 %d 笔（各规则投入完全相同）" % (r["span"], r["n_buys"]))
    print()
    print("%-22s %10s %11s %10s %10s %8s" % (
        "卖出规则", "投入", "回收总额", "盈亏", "XIRR", "卖出笔数"))
    print("-" * 78)
    best = None
    for x in r["results"]:
        print("%-22s %10s %11s %10s %10s %8s" % (
            x["label"][:20], x["invested"], x.get("total_value"), x["pnl"],
            ("%+.2f%%" % x["xirr_pct"]) if x["xirr_pct"] is not None else "n/a",
            "%d/%d" % (x["n_sold"], x["n_buys"])))
        if x["xirr_pct"] is not None and (best is None or x["xirr_pct"] > best[1]):
            best = (x["label"], x["xirr_pct"])
    if best:
        print()
        print("这段区间里 XIRR 最好的规则：**%s**（%+.2f%%）" % best)
    print()
    for n in r["notes"]:
        print("  · %s" % n)
    print()
    print("  ⚠️ **样本仍然很短** —— 结论只能读成『这段区间里哪条规则没吃亏』，")
    print("     不能读成『这条规则长期有效』（参见 counterfactual 的样本充分性声明）。")
    print("  ⚠️ 某条规则若『0 笔卖出』，那是**没机会触发**（如温度从没到过阈值），不是『无效』。")


def cmd_breakeven():
    """回本门槛（P2）—— 「**这笔操作要涨多少才不亏**」。

    费率在报告里通常只作为一个**数字**出现（"费率 1.5%"），
    而真正影响决策的形式是**门槛**：
      · "现在还亏 0.4%，但赎回费 1.5% → 现在卖是双重亏损"；
      · "再持有 3 天赎回费就归零 → 别急着卖"。
    费率**不重写**，一律委托 `portfolio` 的单一真源。
    """
    from src.analysis.breakeven import analyze
    from src.data.database import Database

    print("🧾 回本门槛：这笔操作要涨多少才不亏")
    print("   （只读你的持仓与净值，不改任何东西）")
    print()
    db = Database()
    try:
        r = analyze(db)
    finally:
        db.close()
    if r.get("error"):
        print("❌ %s" % r["error"])
        return

    print("基准日 %s｜持仓批次 %d｜总市值 %s" % (r["today"], r["n_lots"], r["total_market_value"]))
    if r["n_in_penalty"]:
        print("⚠️ **惩罚期内 %d 笔**（市值 %s，现在卖要多付赎回费 %s）"
              % (r["n_in_penalty"], r["penalty_value"], r["penalty_cost"]))
    else:
        print("✅ 没有持仓处在 7 天惩罚期内")
    print()
    print("%-8s %-18s %6s %8s %9s %10s %11s" % (
        "代码", "基金", "持有天", "申购费%", "赎回费%", "门槛%", "扣费后收益%"))
    print("-" * 76)
    for x in sorted(r["lots"], key=lambda y: y["held_days"])[:12]:
        print("%-8s %-18s %6s %8s %9s %10s %11s" % (
            x["fund_code"], str(x["fund_name"])[:16], x["held_days"],
            x["buy_fee_pct"], x["redeem_fee_pct"], x["threshold_pct"],
            x["net_if_sell_pct"] if x["net_if_sell_pct"] is not None else "n/a"))
    if r.get("next_free"):
        print()
        print("⏳ 最近会「免费」的批次：")
        for n in r["next_free"]:
            print("   %s %s → %s（还有 %d 天）"
                  % (n["fund_code"], str(n["fund_name"])[:14], n["free_date"], n["days_to_free"]))
    print()
    for n in r["notes"]:
        print("  · %s" % n)


def cmd_behavior():
    """行为画像（P3）—— 「**这几笔操作，我到底是怎么做的**」。

    与 `counterfactual`（P1，比**结果**）互补：本命令量的是**行为本身** ——
      · 买入纪律：有没有追高（**带同基金同期对照**，不是只看绝对涨幅）；
      · 卖出纪律：割肉（亏损卖出）与卖飞（卖后又涨）分开量；
      · 频率 ↔ 持有期 ↔ 该档实现收益。
    ⚠️ 样本不足时**明确不出行为结论**（区间只有数月时，读的是"发生了什么"）。
    """
    from src.analysis.behavior_profile import analyze
    from src.data.database import Database

    print("🧭 行为画像：这几笔操作，我到底是怎么做的")
    print("   （只读你的流水与净值，不改任何东西）")
    print()
    db = Database()
    try:
        r = analyze(db)
    finally:
        db.close()

    if r.get("error"):
        print("❌ %s" % r["error"])
        return

    bench = r.get("benchmark_pct")
    bench_txt = ("%+.2f%%" % bench) if bench is not None else "n/a"
    print("区间 %s｜买入 %d 笔｜已卖批次 %d 个｜同期 %s %s"
          % (r["span"], r["buy"]["n_buys"], r["sell"].get("n_lots", 0),
             r.get("benchmark_name", "基准"), bench_txt))
    print()

    smp = r.get("sample") or {}
    if smp.get("warning"):
        print(smp["warning"])
        print()

    b = r["buy"]
    print("─" * 66)
    print("【买入纪律 · 追高】")
    print("  买入前 20 个交易日涨幅（中位）：%s%%" % b.get("median_trail20"))
    print("  对照 · 同基金同期**全部交易日**的中位：%s%%" % b.get("median_trail20_baseline"))
    cs = b.get("chase_spread")
    print("  → 追高差值 chase_spread = %s pp   %s" % (
        ("%+.2f" % cs) if cs is not None else "n/a",
        "（**正数 = 你买得比平常更「涨过」**）" if cs is not None else ""))
    print("  买入价在自身历史中的分位（中位）：%s（对照 %s）"
          % (b.get("median_pct"), b.get("median_pct_baseline")))
    ps = b.get("pct_spread")
    print("  → 分位差值 pct_spread = %s pp" % (("%+.2f" % ps) if ps is not None else "n/a"))
    print("     ⚠️ 分位普遍高通常只是「这些基金本来就处在历史高位」——**差值**才说明你的行为。")
    if b.get("trail_range"):
        print("  极差：买入前 20 日涨幅从 %+.1f%% 到 %+.1f%%（买点差异很大，不是均匀买入）"
              % (b["trail_range"][0], b["trail_range"][1]))
    print()

    s = r["sell"]
    print("─" * 66)
    print("【卖出纪律 · 割肉 / 卖飞】")
    if not s.get("n_lots"):
        print("  无已卖批次。")
    else:
        print("  已卖批次 %d 个（分布在 %d 个**卖出决策日**）｜实现收益中位 %+.2f%%｜亏损卖出 %d 笔（%.1f%%）"
              % (s["n_lots"], s.get("n_distinct_sell_dates", 0),
                 s["median_ret_pct"], s["n_loss"], s["frac_loss"] * 100))
        print("  极差：最差 %s %+.1f%%（%s 买 → %s 卖）｜最好 %s %+.1f%%"
              % (s["worst_code"], s["worst_pct"], s["worst_buy_date"], s["worst_sell_date"],
                 s["best_code"], s["best_pct"]))
        cov = "%d/%d" % (s["n_with_followup"], s["n_lots"])
        if s.get("frac_sold_too_early") is not None:
            print("  卖后 20 个交易日：可算 %s 笔｜其中 %d 笔（%.1f%%）**卖后又涨**（卖飞）｜按笔中位 %+.2f%%"
                  % (cov, s["n_sold_too_early"], s["frac_sold_too_early"] * 100,
                     s["median_after20_pct"]))
            md = s.get("median_after20_pct_by_date")
            if md is not None:
                print("  ⚠️ 30 个批次其实只对应 %d 个**卖出决策日** → 按笔的中位会被某一天绑架。"
                      % s.get("n_distinct_sell_dates", 0))
                print("     按决策日中位 = %+.2f%%（下面的『决策日』一栏才是该读的口径）" % md)
                print("     %-12s %5s %12s %12s" % ("卖出日", "批次", "实现收益中位", "卖后 20 日"))
                for d in s.get("by_sell_date", []):
                    a = ("%+.2f%%" % d["after20_pct"]) if d["after20_pct"] is not None else "n/a"
                    print("     %-12s %5d %12s %12s"
                          % (d["date"], d["n_lots"], "%+.2f%%" % d["median_ret_pct"], a))
        else:
            print("  卖后 20 个交易日：可算 %s 笔（不足，未推断）" % cov)
    print()

    f = r["frequency"]
    print("─" * 66)
    print("【交易频率 ↔ 持有期】")
    print("  %.2f 个月：买 %.1f 笔/月、卖 %.1f 笔/月｜中位持有 %s 天"
          % (f.get("months") or 0, f.get("buys_per_month") or 0,
             f.get("sells_per_month") or 0, f.get("median_held_days")))
    if f.get("n_under_7d"):
        print("  ⚠️ %d 笔在 **7 天惩罚期内** 卖出（赎回费 1.5%%）" % f["n_under_7d"])
    print("  持有期分档实现收益：")
    for bk in f.get("hold_buckets", []):
        med = ("%+.2f%%" % bk["median_ret_pct"]) if bk["median_ret_pct"] is not None else "n/a"
        print("    %-22s %3d 笔   中位 %s" % (bk["label"], bk["n"], med))
    print()

    print("─" * 66)
    print("【结论】%s" % r["verdict"])
    print()
    for n in r["notes"]:
        print("  · %s" % n)


def cmd_drift():
    """风格漂移检测（M5）—— CUSUM-of-squares + PELT，查「名称与类型不变、风险特征变了」。

    用法：`python src/main.py drift [基金代码 ...]`（不给代码 = 扫描当前持仓）。
    ⭐ 判据一律带**市场对照**（同日期窗的沪深300 / β 用中证500）—— 只报**超额**变化。
    """
    import sys as _sys
    from src.analysis.changepoint import analyze, scan, MIN_HISTORY
    from src.data.database import Database

    codes = [a for a in _sys.argv[2:] if not a.startswith("-")]
    print("🔍 风格漂移检测（CUSUM-of-squares + PELT）")
    print("   （只读净值与指数，不改任何东西；判据带市场对照）")
    print()
    db = Database()
    try:
        if codes:
            rows = [analyze(db, c) for c in codes]
            out = {"n": len(rows), "alerts": sum(1 for x in rows if x.get("alerts")),
                   "results": rows}
        else:
            out = scan(db)
    finally:
        db.close()

    print("范围：%s｜检查 %d 只｜告警 **%d** 只"
          % ("指定代码" if codes else "当前持仓", out["n"], out["alerts"]))
    print()
    fired = [x for x in out["results"] if x.get("alerts")]
    quiet = [x for x in out["results"] if not x.get("alerts")]

    for r in fired:
        _print_drift(r)
    if quiet:
        print("─" * 70)
        print("未告警 / 不适用：")
        for r in quiet:
            v = r.get("vol") or {}
            exc = v.get("excess_rel_change")
            print("  %-8s %-22s %-12s %s"
                  % (r["code"], str(r.get("name") or "")[:20], r.get("flag"),
                     ("超额波动变化 %+.0f%%" % (exc * 100)) if exc is not None else
                     (r.get("reason") or "")))
    print()
    print("读法（详见 `src/analysis/changepoint.py` module docstring）：")
    for n in (out.get("notes") or []):
        print("  · %s" % n)
    print("  · 变点**不是结论**，是怀疑的入口；判据门槛已写死在模块常量（事前写死，不随数据挪）。")
    print("  · 历史不足 %d 个收益点（约一年）的基金**不给判断**。" % MIN_HISTORY)


def _print_drift(r):
    """打印单只基金的漂移报告。"""
    print("=" * 70)
    print("%s %s｜%s" % (r["code"], str(r.get("name") or "")[:28], r.get("flag")))
    if not r.get("ok"):
        print("  %s" % (r.get("reason") or ""))
        print()
        return
    print("  区间 %s（%d 个收益点）" % (r.get("span"), r.get("n_returns")))
    pel = r.get("pelt_change_points") or []
    print("  变点：%s（CUSUM-of-squares）" % r.get("cp_date"))
    print("        PELT 在滚动波动上的变点：%s"
          % ("、".join(pel[-3:]) if pel else "无"))
    v = r.get("vol") or {}
    if v.get("delta_pp") is not None:
        line = "  波动  %.2f%% → %.2f%%  (%+.2fpp)" % (v["ref_pct"], v["test_pct"], v["delta_pp"])
        if v.get("excess_rel_change") is not None:
            line += "｜相对 %+.0f%%，沪深300 同期 %+.0f%% → **超额 %+.0f%%**" % (
                v["rel_change"] * 100, v["bench_rel_change"] * 100, v["excess_rel_change"] * 100)
        print(line)
    b = r.get("beta") or {}
    if b.get("delta") is not None:
        line = "  β     %.2f → %.2f  (%+.2f)" % (b["ref"], b["test"], b["delta"])
        if b.get("excess_delta") is not None:
            line += "｜对照 %s 对沪深300 %+.2f → **超额 %+.2f**" % (
                b.get("control_name", ""), b.get("control_delta", 0), b["excess_delta"])
        print(line)
    for a in r.get("alerts") or []:
        print("  ⚠️ %s" % a)
    if not r.get("control_available") and r.get("bench_available"):
        print("  ⚠️ 中证500 对照序列缺失 → β 无市场对照")
    print()


def cmd_precompute():
    """预计算并落 SQLite 快照（T2.2 后只剩「聚合总览」= 投资计划 + 持仓）。

    作用：Web 端点是「内存缓存 → SQLite 快照 → 现算」。跑一次本命令把快照填好，
    之后即使重启服务，首屏也是纯 SELECT（毫秒级），不必等冷算。
    历史：原含 温度 / 筛选池 / 调仓 / 板块总榜 —— 均已随旧命题删除（T2.2）。
    """
    import time
    from src.web import app as webapp

    jobs = [
        ("聚合总览（计划 + 持仓）", "overview", webapp._overview_compute),
    ]
    print("=" * 60)
    print("预计算快照（写入 data/fund_quant.db 的 analysis_snapshot 表）")
    print("=" * 60)
    total = 0.0
    for label, key, fn in jobs:
        s = time.perf_counter()
        try:
            data, _src = webapp._cached_get(key, fn, fresh=True)
            bad = (not data) or (isinstance(data, dict) and data.get("error"))
        except Exception as e:
            data, bad = None, True
            print(f"  ❌ {label}: {e}")
            continue
        dt = time.perf_counter() - s
        total += dt
        if bad:
            print(f"  ⚠️ {label}: 计算返回空/错误，未落快照 ({dt:.1f}s)")
        else:
            print(f"  ✅ {label}: {dt:.1f}s")
    print("-" * 60)
    print(f"合计 {total:.1f}s。快照有效期 6 小时，或在任何写操作（记买入/更新净值…）时自动失效。")
    print("=" * 60)
