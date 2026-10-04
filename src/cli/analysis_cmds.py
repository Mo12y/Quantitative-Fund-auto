"""
分析命令：score / temp / sentiment / portfolio / rebalance / sector / recommend / plan。
"""

from src.analysis.fund_scorer import FundScreener, format_fee
from src.analysis.historical_recommender import HistoricalRecommender
from src.analysis.investment_plan import get_plan, get_progress
from src.analysis.portfolio import PortfolioTracker
from src.analysis.rebalance_advisor import RebalanceAdvisor
from src.analysis.sector_analyzer import SectorAnalyzer
from src.analysis.sentiment_monitor import quick_scan as sentiment_quick_scan
from src.analysis.thermometer import MarketThermometer
from src.data.database import Database


def cmd_score():
    """基金质量筛选（v3.0: 替代评分排名）"""
    db = Database("data/fund_quant.db")
    screener = FundScreener(db)
    thermometer = MarketThermometer.from_profile(db)

    print("🔍 基金质量筛选 v3.0")
    print()

    temp = thermometer.get_temperature()
    print(f"🌡️ 当前市场温度: {temp['temperature']}°C — {temp['level_desc']}")
    print()

    df = screener.screen_funds()
    if df.empty:
        print("❌ 暂无通过质量筛选的基金。")
    else:
        summary = screener.get_pool_summary(df)
        print(f"📊 筛选结果: {summary['total']}只通过质量筛选")
        for label, count in summary.get("by_risk", {}).items():
            print(f"   {label}: {count}只")
        print()
        print("─" * 70)

        # 分组展示
        for risk_label in ["🟢 稳健", "🟡 注意", "🔴 高风险"]:
            subset = df[df["risk_label"] == risk_label]
            if subset.empty:
                continue
            print(f"\n{risk_label}:")
            for _, row in subset.iterrows():
                checks = row.get("quality_checks", {})
                metrics = row.get("metrics", {})
                mom = metrics.get("momentum_3m", 0) or 0
                dd = metrics.get("max_drawdown_1y", 0) or 0
                # 综合评分（批次 4.1）：类型桶内归一化，同风险等级内的排序依据
                score = row.get("quality_score")
                score_txt = f"{score:.0f}" if score is not None and score == score else "--"

                # mgt_fee 缺失时（长历史基金很常见）必须显示"无数据"而不是 0.00%，
                # 否则会被读成"零费率"
                _fee_txt = format_fee(row.get("mgt_fee"))
                print(f"  {row['fund_code']} {str(row['fund_name'])[:28]:<30} "
                      f"评分{score_txt:>4} 费率{_fee_txt:<8} 近3月{mom:+.0f}%  回撤{dd:.0f}%")

                reasons = row.get("risk_reasons", [])
                for r in reasons:
                    print(f"    ↳ {r}")

    db.close()


def cmd_temp():
    """查看市场温度 (v2.0: 含真实成交量+风格判断+分歧检测)"""
    db = Database("data/fund_quant.db")
    thermometer = MarketThermometer.from_profile(db)

    temp = thermometer.get_temperature()

    # 温度条
    t = temp['temperature']
    comp = temp["components"]

    print("🌡️ 市场温度计 v2.0")
    print("=" * 50)
    if t is None:
        # 全维度缺失：不给温度读数（F-02）
        print("  [数据不足] —— PE/PB/ERP/量能/情绪五个维度都没有数据，无法给出温度")
        print(f"  状态: {temp['level_desc']}")
        print(f"  建议: {temp['action']}")
    else:
        bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
        print(f"  [{bar}] {t}°C")
        print(f"  状态: {temp['level_desc']}")
        print(f"  建议: {temp['action']}")
        print(f"  建议权益仓位: {temp['target_equity_pct']}%")
    print()
    def _d(v):
        return f"{v:.0f}°" if v is not None else "缺失"

    print("  📊 五维分解:")
    print(f"    PE估值分位数:  {_d(comp['pe_score'])}  (越高越贵)")
    print(f"    PB估值分位数:  {_d(comp['pb_score'])}  (越高越贵)")
    print(f"    股债性价比:    {_d(comp['erp_score'])}  (越高股票越贵)")
    print(f"    成交量热度:    {_d(comp['volume_score'])}  (天量=高温)")
    print(f"    市场情绪:      {_d(comp['sentiment_score'])}  (贪婪=高温)")
    if temp.get("degraded_dimensions"):
        print(f"    ⚠️ 数据缺失维度已剔除并按剩余权重归一化: {', '.join(temp['degraded_dimensions'])}")

    # 估值分歧
    div = temp.get("divergence", {})
    print()
    print(f"  🔍 估值分歧度: {div.get('level', '未知')}")
    print(f"     {div.get('message', '')}")

    # 市场风格
    style = temp.get("market_style", {})
    if style.get("dominant") != "unknown":
        print()
        print(f"  🎨 市场风格: {style.get('dominant', '')}")
        print(f"     {style.get('detail', '')}")
        returns = style.get("returns", {})
        if returns:
            for name, ret in returns.items():
                arrow = "📈" if ret > 0 else "📉"
                print(f"     {arrow} {name}: {ret:+.1f}% (20日)")

    db.close()


def cmd_sentiment():
    """消息面扫描: 市场新闻 + 基金公告 + 情绪打分"""
    sentiment_quick_scan()


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


def cmd_rebalance():
    """辅助调仓: 对比实际持仓 vs 目标仓位, 输出具体买卖指令"""
    db = Database("data/fund_quant.db")
    advisor = RebalanceAdvisor(db)

    holdings = db.get_current_holdings()

    if not holdings:
        print("📋 暂无持仓。先录入:")
        print("   python src/main.py buy")
        db.close()
        return

    # 总资金口径 = 持仓市值 + 投资计划的现金弹药（cash_reserve）。
    # 旧版用 total_invested * 1.1 拍脑袋估算，与计划卡数字互相打架。
    try:
        plan = get_plan(db) or {}
        cash_reserve = float(plan.get("cash_reserve") or 0)
    except Exception:
        cash_reserve = 0.0

    result = advisor.analyze(cash_reserve=cash_reserve)

    # === 输出 ===
    temp = result["temperature"]
    summary = result["summary"]
    instructions = result["instructions"]

    print()
    print("=" * 55)
    print("🔄 辅助调仓建议")
    print("=" * 55)

    # 状态
    t = temp["temperature"]
    if t is None:
        print(f"\n🌡️ 温度: [数据不足] — {temp['level_desc']}")
        print(f"📊 当前权益: {result['current_equity_pct']:.0f}% → 目标: 数据不足")
    else:
        bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
        print(f"\n🌡️ 温度: [{bar}] {t}°C — {temp['level_desc']}")
        print(f"📊 当前权益: {result['current_equity_pct']:.0f}% → 目标: {result['target_equity_pct']}%")
    print(f"💰 总资产约: ¥{result['total_capital']:.0f} (含现金)")

    # 调仓建议
    print(f"\n📋 {summary['verdict']}")
    print(f"   {summary['detail']}")

    if instructions:
        print()
        for inst in instructions:
            icon = {"卖出": "🔴", "买入": "🟢", "持有": "⚪"}
            print(f"  {icon.get(inst['action'], '•')} [{inst['action']}] {inst['fund_code']} {inst['fund_name'][:28]:<30}")
            print(f"     金额: ¥{inst['amount']:.0f} | 仓位: {inst['current_pct']:.1f}%→{inst['target_pct']:.1f}%")
            print(f"     原因: {inst['reason']}")
            print()

    # 提醒
    print("⚠️ 操作提醒:")
    print("   1. 以上为系统建议, 最终决策由你做出")
    print("   2. 场外基金持有<7天赎回费1.5%, 请确认持有天数")
    print("   3. 在支付宝手动操作后, 用 python src/main.py buy/sell 记录")
    print("=" * 55)

    db.close()


def cmd_sector():
    """板块分析: 31个行业动量排名 + 超跌候选 + 针对你持仓的建议"""
    db = Database("data/fund_quant.db")

    # 获取用户持仓的行业暴露
    holdings = db.get_current_holdings()
    held_sectors = set()
    if holdings:
        for h in holdings:
            info = None
            for f in db.get_all_funds():
                if f["fund_code"] == h["fund_code"]:
                    info = f; break
            if info:
                ftype = info.get("fund_type", "")
                name = info.get("fund_name", "")
                # 映射基金名→行业
                for kw, sector in [("半导体","电子"),("芯片","电子"),("科创","电子"),
                                     ("电力","电力设备"),("电网","电力设备"),("新能源","电力设备"),
                                     ("计算机","计算机"),("AI","计算机"),("纳斯达克","QDII"),
                                     ("通信","通信"),("机器人","机械设备"),
                                     ("医药","医药生物"),("消费","食品饮料"),("食品","食品饮料"),
                                     ("银行","银行"),("红利","银行")]:
                    if kw in name or kw in ftype:
                        held_sectors.add(sector)

    print("📊 31个申万一级行业分析中...")
    analyzer = SectorAnalyzer()
    result = analyzer.analyze()

    if "error" in result:
        print(f"  ❌ {result['error']}")
        db.close()
        return

    print()
    print("=" * 60)
    print("📊 申万31行业综合排名 Top 15")
    print("=" * 60)
    print(f"  {'排名':<4} {'行业':<10} {'综合分':>6} {'近1月':>7} {'近3月':>7} {'波动':>6}")
    print("  " + "-" * 42)
    for s in result["rankings"]:
        marker = " ← 你已持有" if s["name"] in held_sectors else ""
        print(f"  {s['rank']:<4} {s['name']:<10} {s['score']:>6.2f} {s['ret_1m']:>+6.1f}% {s['ret_3m']:>+6.1f}% {s['volatility']:>5.0f}%{marker}")

    print()
    print("=" * 60)
    print("🔥 动量领涨 (顺势)        💧 超跌候选 (逆向)")
    print("=" * 60)
    ml = result.get("momentum_leaders", [])
    vc = result.get("value_candidates", [])
    for i in range(max(len(ml), len(vc))):
        left = f"  {ml[i]['name']:<10} 近1月{ml[i]['ret_1m']:+.1f}%" if i < len(ml) else " " * 30
        right = f"  {vc[i]['name']:<10} 近1月{vc[i]['ret_1m']:+.1f}%" if i < len(vc) else ""
        print(f"{left}    {right}")

    if held_sectors:
        print()
        print("=" * 60)
        print("🎯 针对你的持仓")
        print("=" * 60)
        rec = analyzer.recommend_for_portfolio(list(held_sectors))
        print(f"  你已持有: {', '.join(held_sectors)}")
        print()
        if rec.get("complements"):
            print("  互补方向 (与你持仓低相关, 分散风险):")
            for s in rec["complements"]:
                print(f"    🟢 {s['name']}: 近1月{s['ret_1m']:+.1f}%  近3月{s['ret_3m']:+.1f}%")
        if rec.get("value_non_tech"):
            print()
            print("  超跌非科技 (值得关注):")
            for s in rec["value_non_tech"]:
                print(f"    💧 {s['name']}: 近1月{s['ret_1m']:+.1f}%")
        print()
        print(f"  ⚠️ {rec.get('temperature_note','')}")

    db.close()


def cmd_recommend():
    """历史回测验证（辅助参考路径）：基于回测找出持续被选中且真的赚了钱的基金"""
    db = Database("data/fund_quant.db")
    hr = HistoricalRecommender(db)

    print("🔄 历史回测验证引擎运行中...")
    print("   (候选池按类型分层随机抽样 · 每月末打分 · 约1.5年数据)")
    print()
    print("⚠️ 口径说明（批次 4.3/4.9）：")
    print("   - 这是**历史回测验证**，不是主推荐；主推荐 = 温度驱动的实时筛选（score 命令）。")
    print("   - 下列结果为**样本内**历史表现：用已实现的前向收益筛选“赢家”存在")
    print("     同义反复，不构成样本外的选基能力证据，仅作参考。")
    print("   - 【2026-09-27 实测】批次 4.3 已做**滚动样本外验证**（`oos` 命令，51 个不重叠窗口）：")
    print("     该口径超额中位仅 +0.11pp、逐窗胜率 51.0% —— **看不出优于对照的选基能力**。")
    print("     故 `proven_winners` 宜读作「打分法偏好的类型分布」，不要当成「验证过的赢家」。")
    print()
    result = hr.recommend(lookback_years=1.5)

    if "error" in result:
        print(f"❌ {result['error']}")
        db.close()
        return

    stats = result["stats"]
    print("=" * 60)
    print("📊 历史回测统计")
    print("=" * 60)
    print(f"  回测周期: {stats['date_range']} ({stats['total_months']}个月)")
    print(f"  候选基金: {stats['total_candidates']}只"
          f"（权益 {stats.get('candidates_equity', '?')} / 非权益 {stats.get('candidates_bond', '?')}，"
          f"分层随机抽样）")
    print(f"  被选中过的: {stats['funds_ever_picked']}只")
    print(f"  历史验证: {stats['funds_with_proven_record']}只有效数据")
    print(f"    正收益{stats['proven_good']}只 / 负收益{stats['proven_bad']}只")
    print(f"  Top10平均3月后收益: {stats['top10_avg_3m_return']:+.1f}%")
    pw_dist = stats.get("proven_type_dist") or {}
    cp_dist = stats.get("current_type_dist") or {}
    if pw_dist:
        print(f"  历史最强类型分布: 权益 {pw_dist.get('equity', 0)} / 非权益 {pw_dist.get('bond', 0)}"
              f"（按类型分组评分，批次 4.4）")
    if cp_dist:
        print(f"  当前推荐类型分布: 权益 {cp_dist.get('equity', 0)} / 非权益 {cp_dist.get('bond', 0)}")

    print()
    print("=" * 60)
    print("🏆 历史验证最强 (高选中率 + 实际正收益)")
    print("=" * 60)
    print(f"  {'基金':<10} {'名称':<28} {'综合':>5} {'选中率':>6} {'均1月':>6} {'均3月':>6} {'胜率':>5}")
    print("  " + "-" * 68)
    for p in result["proven_winners"][:15]:
        print(f"  {p['code']:<10} {str(p['name'])[:26]:<28} {p['composite_score']:>5.0f} {p['pick_rate']:>5.0f}% {p['avg_return_1m']:>+5.1f}% {p['avg_return_3m']:>+5.1f}% {int(p['win_rate_3m']):>4}%")

    print()
    print("=" * 60)
    print("🎯 当前推荐 (最新评分 + 历史验证)")
    print("=" * 60)
    for p in result["current_picks"][:10]:
        hist = ""
        if p.get("hist_avg_3m") is not None:
            hist = f" | 历史选中{p['times_picked']}次, 均3月+{p['hist_avg_3m']:.1f}%"
        print(f"  {p['code']} {str(p['name'])[:30]:<32} 评分{p['score']:.0f}{hist}")

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



def cmd_oos():
    """滚动样本外验证（批次 4.3）——「打分法到底有没有选基能力」。

    与 `recommend` 的区别：`recommend` 是**样本内**（用同一批已实现收益选赢家，
    同义反复）；本命令把选择期与验证期**切开**，并给出三组对照。
    结论可能是负的 —— 那也是有价值的结果，本命令不提供让结果好看的开关。
    """
    from src.analysis.oos_validate import run as run_oos
    from src.data.database import Database

    print("🔄 滚动样本外验证（walk-forward）")
    print("   选择期 18 个月 → 验证期 6 个月，逐窗滚动；两段**不重叠**（选择期看不到验证期）。")
    print("   对照：① 全池中位数  ② 随机等量  ③ 选择期末打分 TopK")
    print()
    db = Database()
    try:
        out = run_oos(db)
    finally:
        db.close()

    if out.get("error"):
        print("❌ %s" % out["error"])
        return

    meta = out.get("meta", {})
    print("候选池：%s · 载入净值 %d 只" % (meta.get("pool"), meta.get("nav_loaded", 0)))
    print("持有期：%d 个交易日（≈%d 个月）" % (
        (out["windows"][0]["hold_days"] if out["windows"] else 0), meta.get("step_months", 6)))
    print("每期持有 %s 只（top_pct=%s = 十分位）· 随机对照重复 %s 次 · 市场序列：%s"
          % (meta.get("top_k"), meta.get("top_pct"), meta.get("c2_repeats"), meta.get("market")))
    print()

    print("%-12s %-16s %8s %8s %8s %8s %9s" % (
        "验证起点", "选择期", "S1高频", "S2打分", "全池中位", "随机", "S1-中位"))
    for w in out["windows"]:
        f = lambda v: ("%+7.2f%%" % v) if v is not None else "     n/a"   # noqa: E731
        print("%-12s %-16s %8s %8s %8s %8s %9s" % (
            w["as_of"], "%s起%d月" % (w["sel_start"], w["sel_months"]),
            f(w["s1"]), f(w["s2"]), f(w["c1_pool"]), f(w["c2_random"]), f(w["s1_minus_c1"])))

    s = out["summary"]
    print("\n=== 汇总（%d 个互不重叠的验证窗口，%s）===" % (s["n_windows"], s.get("span")))
    for k, label in (("s1_frequent_pick", "S1 选择期高频选中"),
                     ("s2_top_score", "S2 选择期末打分TopK"),
                     ("c1_pool", "C1 全池等权（=所有候选等权持有）"),
                     ("c2_random", "C2 随机等量")):
        d = s.get(k)
        if d:
            print("  %-22s 中位 %+6.2f%%  均值 %+6.2f%%  （n=%d）" % (
                label, d["median"], d["mean"], d["n"]))
    for k, label in (("s1_minus_c1", "S1 − 全池中位"), ("s1_minus_c2", "S1 − 随机")):
        d = s.get(k)
        if d:
            wr = s.get("win_rate_vs_c1" if k.endswith("c1") else "win_rate_vs_c2")
            print("  %-22s 中位 %+6.2f pp  逐窗胜率 %.1f%%" % (label, d["median"], wr or 0))

    print("\n=== 显著性（4 个比较，Bonferroni-Hochberg 校正）===")
    labels = s.get("p_labels") or []
    for i, lb in enumerate(labels):
        wc = (s.get("win_counts") or {}).get(lb, {})
        print("  %-10s 胜 %2d/%-2d  p=%.4f → 校正后 p=%.4f %s" % (
            lb, wc.get("win", 0), wc.get("n", 0),
            (s.get("p_raw") or [1])[i], (s.get("p_adjusted") or [1])[i],
            "✅ 显著" if (s.get("p_adjusted") or [1])[i] < s.get("bh_alpha", 0.05) else ""))

    a = s.get("alpha")
    print("\n=== 因子中性 α（y = S1 − 全池等权 的**相对**超额 ~ 沪深300 同期收益）===")
    if a:
        t = ("%.2f" % a["t_alpha"]) if a["t_alpha"] is not None else "n/a"
        print("  α = %+.3f%%  β = %.3f  t(α) = %s  R² = %.3f  n = %d"
              % (a["alpha"], a["beta"], t, a["r2"], a["n"]))
        print("  读法：α 是**剥离市场 beta 后仍剩下的相对超额**；|t(α)| < ~2 即与 0 无法区分。")
        print("        （若 y 用绝对收益，测的只是『基金组合 vs 沪深300』，与选基能力无关。）")
    else:
        print("  未计算（%s）" % meta.get("market", "缺市场序列"))

    print("\n判定：%s" % out["verdict"])
    print("\n⚠️ 口径：本结果是**样本外**（选择期与验证期不重叠）；"
          "但候选池仍是当前存续基金 → 存在**幸存者偏差**，绝对收益偏高，"
          "看的是 S1 与对照的**差**，不是绝对数。")


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
    """预计算并落 SQLite 快照（温度/筛选池/调仓/聚合总览/板块总榜）。

    作用：Web 端点是「内存缓存 → SQLite 快照 → 现算」。跑一次本命令把快照填好，
    之后即使重启服务，首屏与筛选池也是纯 SELECT（毫秒级），不必再等 3~8 秒冷算。
    """
    import time
    from src.web import app as webapp

    jobs = [
        ("温度+持仓 总览", "overview", webapp._overview_compute),
        ("基金质量筛选池", "funds", webapp._all_funds),
        ("调仓建议", "rebalance", webapp._all_rebalance),
        ("聚合总览", "__dash__", webapp._compute_dashboard),
        ("板块总榜(默认)", "funds_board_20_300",
         lambda: webapp._compute_board_pool(20, 300)),
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
            n = len(data.get("funds", [])) if key == "funds" else ""
            print(f"  ✅ {label}: {dt:.1f}s {'('+str(n)+' 只)' if n != '' else ''}")
    print("-" * 60)
    print(f"合计 {total:.1f}s。快照有效期 6 小时，或在任何写操作（记买入/更新净值…）时自动失效。")
    print("=" * 60)
