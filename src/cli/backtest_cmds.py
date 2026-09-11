"""
回测命令：backtest / backtest2 / backtest3 / backtest4 / strategy。
"""

import numpy as np
import pandas as pd

import akshare as ak

from src.analysis.backtest import RigorousBacktest
from src.analysis.strategy_engine import StrategyEngine
from src.data.database import Database


def cmd_backtest():
    """
    ⚠️ LEGACY 回测 v1: 每周换仓 Top3。

    已被严谨回测 v3（backtest3）取代（v1 存在未来函数且交易成本模拟粗糙），
    保留此命令仅用于兼容。
    """
    import pandas as pd
    import numpy as np
    from datetime import timedelta

    db = Database("data/fund_quant.db")

    print("📊 回测验证: 多因子评分策略 vs 沪深300基准")
    print("=" * 60)

    # 1. 获取有净值数据的基金
    cur = db.conn.cursor()
    all_codes = sorted(db.get_all_fund_codes())
    print(f"   候选基金池: {len(all_codes)} 只")

    if len(all_codes) < 10:
        print("   ❌ 样本太少，无法回测。请先运行: python src/main.py nav")
        db.close()
        return

    # 2. 确定回测时间范围
    cur.execute("SELECT MIN(nav_date), MAX(nav_date) FROM fund_nav")
    min_date, max_date = cur.fetchone()
    print(f"   数据时间范围: {min_date} ~ {max_date}")

    weeks = []
    d = pd.to_datetime(max_date)
    start = d - timedelta(days=365 * 3)
    while d > start:
        weeks.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=7)
    weeks = sorted(weeks)
    print(f"   回测周期: {weeks[0]} ~ {weeks[-1]} ({len(weeks)} 周)")

    # 3. 获取沪深300基准
    try:
        import akshare as ak
        hs300 = ak.stock_zh_index_daily(symbol="sh000300")
        hs300["date"] = pd.to_datetime(hs300["date"])
        hs300 = hs300.set_index("date")["close"]
    except Exception:
        hs300 = None

    strategy_returns = []
    benchmark_returns = []
    print()
    print("   正在回测...")

    for i, week_date in enumerate(weeks[:-1]):
        next_week = weeks[i + 1]
        try:
            # 对每只基金做简化评分
            scores = []
            for code in all_codes:
                navs = db.get_fund_nav(code, end_date=week_date)
                if len(navs) < 60:
                    continue
                df_nav = pd.DataFrame(navs).sort_values("nav_date")
                df_nav["daily_return"] = df_nav["unit_nav"].pct_change() * 100
                returns_arr = df_nav["daily_return"].dropna().values
                if len(returns_arr) < 20:
                    continue
                try:
                    nav = df_nav.set_index("nav_date")["unit_nav"]
                    mom = (nav.iloc[-1] / nav.iloc[-min(63, len(nav))] - 1) * 100 if len(nav) >= 21 else 0
                    mom_score = max(5, min(95, (mom + 30) / 80 * 100))

                    ann_ret = np.mean(returns_arr) * 252
                    ann_vol = np.std(returns_arr, ddof=1) * np.sqrt(252)
                    sv = (ann_ret / 100 - 0.02) / (ann_vol / 100) if ann_vol > 0 else 0
                    sharpe_score = max(5, min(95, (sv + 1) / 3.5 * 100))

                    peak = df_nav["unit_nav"].iloc[0]
                    max_dd = 0
                    for p in df_nav["unit_nav"].values:
                        if p > peak:
                            peak = p
                        dd = (peak - p) / peak * 100
                        if dd > max_dd:
                            max_dd = dd
                    dd_score = max(5, min(95, (50 - max_dd) / 50 * 100))

                    total = mom_score * 0.4 + sharpe_score * 0.3 + dd_score * 0.3
                    scores.append((code, total))
                except Exception:
                    continue

            scores.sort(key=lambda x: x[1], reverse=True)
            top3 = [s[0] for s in scores[:3] if s[1] > 0]
            if not top3:
                continue

            weekly_returns = []
            for code in top3:
                nb = db.get_fund_nav(code, end_date=week_date)
                na = db.get_fund_nav(code, end_date=next_week)
                if nb and na:
                    try:
                        b_val = float(nb[-1]["unit_nav"])
                        a_val = float(na[-1]["unit_nav"])
                        if b_val > 0:
                            weekly_returns.append((a_val / b_val - 1) * 100)
                    except Exception:
                        pass

            if weekly_returns:
                strategy_returns.append(np.mean(weekly_returns))

            if hs300 is not None:
                try:
                    b_idx = hs300.loc[hs300.index <= pd.to_datetime(week_date)]
                    a_idx = hs300.loc[hs300.index <= pd.to_datetime(next_week)]
                    if len(b_idx) > 0 and len(a_idx) > 0:
                        b_hs = b_idx.iloc[-1]
                        a_hs = a_idx.iloc[-1]
                        benchmark_returns.append((a_hs / b_hs - 1) * 100)
                except Exception:
                    pass

            if (i + 1) % 50 == 0:
                print(f"   进度: {i+1}/{len(weeks)-1} 周已完成")

        except Exception:
            continue

    db.close()

    if not strategy_returns:
        print("\n❌ 回测数据不足，无法生成结果。")
        return

    sr = np.array(strategy_returns)
    br = np.array(benchmark_returns) if benchmark_returns else None

    total_ret = np.prod(1 + sr / 100) - 1
    ann_ret = (1 + total_ret) ** (52 / len(sr)) - 1
    ann_vol = np.std(sr, ddof=1) * np.sqrt(52)
    sv = (ann_ret - 0.02) / (ann_vol / 100) if ann_vol > 0 else 0
    cum = np.cumprod(1 + sr / 100)
    peak = cum[0]
    max_dd = 0
    for c in cum:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100
        if dd > max_dd:
            max_dd = dd
    win_rate = (sr > 0).sum() / len(sr) * 100

    print()
    print("=" * 60)
    print("📊 回测结果 (近3年)")
    print("=" * 60)
    print(f"\n🎯 多因子选基策略:")
    print(f"   总收益率:    {total_ret*100:+.1f}%")
    print(f"   年化收益率:  {ann_ret*100:+.1f}%")
    print(f"   年化波动率:  {ann_vol:.1f}%")
    print(f"   夏普比率:    {sv:.2f}")
    print(f"   最大回撤:    {max_dd:.1f}%")
    print(f"   周胜率:      {win_rate:.0f}%")
    print(f"   回测周数:    {len(sr)}")

    if br is not None and len(br) > 0:
        b_total = np.prod(1 + br / 100) - 1
        b_ann = (1 + b_total) ** (52 / len(br)) - 1
        b_vol = np.std(br, ddof=1) * np.sqrt(52)
        b_sv = (b_ann - 0.02) / (b_vol / 100) if b_vol > 0 else 0
        b_cum = np.cumprod(1 + br / 100)
        b_peak = b_cum[0]
        b_max_dd = 0
        for c in b_cum:
            if c > b_peak:
                b_peak = c
            dd = (b_peak - c) / b_peak * 100
            if dd > b_max_dd:
                b_max_dd = dd

        print(f"\n📉 沪深300基准:")
        print(f"   总收益率:    {b_total*100:+.1f}%")
        print(f"   年化收益率:  {b_ann*100:+.1f}%")
        print(f"   年化波动率:  {b_vol:.1f}%")
        print(f"   夏普比率:    {b_sv:.2f}")
        print(f"   最大回撤:    {b_max_dd:.1f}%")

        alpha = ann_ret - b_ann
        print(f"\n⚖️ 超额收益 (Alpha): {alpha*100:+.1f}%/年")
        if alpha > 0:
            print(f"   ✅ 策略跑赢基准 {alpha*100:.1f}% 每年")
        else:
            print(f"   ⚠️ 策略跑输基准 {abs(alpha)*100:.1f}% 每年")

    print()
    print("⚠️ 回测局限性:")
    print("   1. 未考虑申购/赎回费 (场外约0.5-1.5%)")
    print("   2. 未考虑T+1确认延迟")
    print("   3. 过去表现不代表未来收益")
    print("   4. 只覆盖了有净值数据的基金子集")
    print("=" * 60)


def cmd_backtest2():
    """
    回测 v2（LEGACY）: 月频评估 + 温度阈值触发。

    已统一委托给 RigorousBacktest（v3.0）——v2 原实现用全历史 PE 分位数
    （存在未来函数），已被严谨回测引擎取代。此命令保留仅用于兼容输出。
    """
    db = Database("data/fund_quant.db")
    engine = RigorousBacktest(db)

    print("📊 回测 v2（LEGACY → 已委托严谨回测 v3 引擎）")
    print("=" * 60)
    print("   规则: 月频评估 + 温度变化 ≥15° 才调仓 + 完整交易成本")
    print()

    result = engine.run(lookback_years=3)

    if "error" in result:
        print(f"   ❌ {result['error']}")
        db.close()
        return

    s = result["strategy"]
    print("=" * 60)
    print("📊 回测结果 (近3年, 月频)")
    print("=" * 60)
    print(f"\n🎯 策略 (月频+阈值触发):")
    print(f"   总收益率:    {s['total_return']:+.1f}%")
    print(f"   年化收益率:  {s['annual_return']:+.1f}%")
    print(f"   年化波动率:  {s['annual_volatility']:.1f}%")
    print(f"   夏普比率:    {s['sharpe']:.2f}")
    print(f"   最大回撤:    {s['max_drawdown']:.1f}%")
    print(f"   月胜率:      {s['win_rate']:.0f}%")
    print(f"   回测月数:    {s['months']}")

    for sym in ["sh000300", "sh000905"]:
        bkey, akey = f"benchmark_{sym}", f"alpha_vs_{sym}"
        if bkey in result:
            b = result[bkey]
            print(f"\n📉 基准 {sym}:")
            print(f"   年化收益率: {b['annual_return']:+.1f}%")
            a = result.get(akey, {})
            if a.get("t_stat") is not None:
                sig = "✅显著" if a["significant"] else "❌不显著"
                print(f"   Alpha: {a['mean_alpha']:+.2f}%/月 | p={a['p_value']} | {sig}")

    print()
    print("⚠️ 注意: v2 原实现已弃用，本输出来自严谨回测 v3 引擎")
    print("=" * 60)
    db.close()


def cmd_backtest3():
    """严谨回测 v3.0: 防未来函数 + 完整交易成本 + 多基准 + 显著性检验"""
    db = Database("data/fund_quant.db")
    engine = RigorousBacktest(db)

    print("📊 严谨回测 v3.0")
    print("=" * 60)
    print("  改进: 扩展窗口分位数(无未来函数) / 交易成本已进指标(净收益) / 多基准按日期对齐 / t检验")
    print("  局限: 存续基金池(幸存者偏差)")
    print()

    result = engine.run(lookback_years=5)

    if "error" in result:
        print(f"❌ {result['error']}")
        db.close()
        return

    s = result["strategy"]
    print("🎯 策略表现 (月频 · 温度阈值调仓 · Top3等权 · **净收益**: 已扣申购费/赎回费/管理费):")
    print(f"   总收益率:    {s['total_return']:+.1f}%")
    print(f"   年化收益率:  {s['annual_return']:+.1f}%")
    print(f"   年化波动率:  {s['annual_volatility']:.1f}%")
    print(f"   夏普比率:    {s['sharpe']:.2f}")
    print(f"   最大回撤:    {s['max_drawdown']:.1f}%")
    print(f"   Calmar:      {s['calmar']:.2f}")
    print(f"   月胜率:      {s['win_rate']:.0f}%")
    print(f"   回测月数:    {s['months']}")

    for sym in ["sh000300", "sh000905"]:
        bkey, akey, rkey = f"benchmark_{sym}", f"alpha_vs_{sym}", f"regression_vs_{sym}"
        if bkey not in result:
            continue
        b = result[bkey]
        print(f"\n📉 基准 {sym}:")
        print(f"   年化: {b['annual_return']:+.1f}%  夏普: {b['sharpe']:.2f}  回撤: {b['max_drawdown']:.1f}%"
              f"  (与策略对齐 {b.get('aligned_months', '-')} 个月)")
        a = result.get(akey, {})
        if a.get("t_stat") is not None:
            sig = "✅ 显著" if a["significant"] else "❌ 不显著"
            print(f"   Alpha: {a['mean_alpha']:+.2f}%/月 | t={a['t_stat']} | p={a['p_value']} | {sig}")
        r = result.get(rkey, {})
        if r.get("beta") is not None:
            print(f"   Beta: {r['beta']} | 年化Alpha: {r['alpha_annual']:+.2f}%")

    print("\n📅 逐年一致性 (按自然年分组):")
    for y in result.get("yearly", []):
        parts = [f"策略 {y['strategy_return']:+.1f}%"]
        for sym in ["sh000300", "sh000905"]:
            if sym in y:
                parts.append(f"{sym} {y[sym]:+.1f}%")
        print(f"   {y['period']} ({y.get('months', '?')}个月): {' | '.join(parts)}")

    cm = result.get("cost_model") or {}
    if cm:
        print(f"\n💸 成本模型: 申购 {cm['purchase_fee']*100:.2f}% · "
              f"赎回 <7天 {cm['redemption_fee_lt7d']*100:.2f}% / ≥7天 {cm['redemption_fee_ge7d']*100:.2f}% · "
              f"管理费年化 {cm['management_fee_annual']*100:.2f}%")
        print(f"   费率真源: {cm.get('source', '')}")

    print(f"\n⚠️ 调仓次数: {len(result['trades'])} 次")
    print("⚠️ 局限: 存续基金池存在幸存者偏差; 未模拟 T+1 确认延迟")
    print("=" * 60)
    db.close()


def cmd_backtest4():
    """策略对比研究: 买入持有 vs 现状 vs 温度自适应选基（验证改进假设）"""
    db = Database("data/fund_quant.db")
    engine = RigorousBacktest(db)

    print("🔬 策略对比研究")
    print("=" * 60)
    print("  假设: 现状策略牛市跑输，源于选基权重过度防御")
    print("  对照: 买入持有(基线) | 温度阈值调仓(现状) | 温度自适应选基(改进)")
    print()

    result = engine.compare_strategies(lookback_years=5)

    for name, r in result.items():
        if "error" in r:
            print(f"❌ {name}: {r['error']}")
            continue
        s = r["strategy"]
        a300 = r.get("alpha_vs_sh000300", {})
        a905 = r.get("alpha_vs_sh000905", {})
        print(f"\n📌 {name}（调仓 {r['trades']} 次）:")
        print(f"   年化 {s['annual_return']:+.1f}% | 夏普 {s['sharpe']:.2f} | "
              f"回撤 {s['max_drawdown']:.1f}% | 月胜率 {s['win_rate']:.0f}%")
        for label, a in [("沪深300", a300), ("中证500", a905)]:
            if a.get("t_stat") is not None:
                sig = "✅显著" if a["significant"] else "❌不显著"
                print(f"   vs {label}: alpha {a['mean_alpha']:+.2f}%/月 | p={a['p_value']} | {sig}")

    print("\n📊 结论: 对比三组年化收益/alpha 显著性，判断改进假设是否成立")
    print("=" * 60)
    db.close()


def cmd_strategy():
    """v2策略建议: 月频评估，判断是否应调仓"""
    db = Database("data/fund_quant.db")
    engine = StrategyEngine(db)

    suggestion = engine.get_weekly_suggestion()
    temp = suggestion["temp_data"]

    print("🎯 策略引擎 v2.0 (月频+温度阈值)")
    print("=" * 50)

    # 温度
    t = temp["temperature"]
    bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
    print(f"\n🌡️ 当前温度: [{bar}] {t}°C — {temp['level_desc']}")
    print(f"   建议权益仓位: {suggestion['target_equity_pct']}%")

    # 调仓判断
    print(f"\n📋 调仓判断:")
    if suggestion["should_rebalance"]:
        print(f"   🔄 {suggestion['reason']}")
    else:
        print(f"   ✋ {suggestion['reason']}")

    # 交易成本
    cost = suggestion.get("cost_estimate")
    if cost:
        print(f"\n💰 预估交易成本:")
        print(f"   总成本约: ¥{cost['total_cost']:.2f}")
        for bd in cost.get("breakdown", []):
            held = f"持{bd['days_held']}天, " if bd.get("days_held") is not None else ""
            print(f"   - {bd['fund_name'][:20]}: {bd.get('action','')}{held}"
                  f"{bd['fee_rate']} × ¥{bd.get('traded', 0):.2f} ≈ ¥{bd.get('fee', 0):.2f}")
        if cost.get("note"):
            print(f"   {cost['note']}")

    # 推荐基金
    df = suggestion.get("recommended_funds")
    if df is not None and not df.empty:
        print(f"\n📈 当前市场状态推荐:")
        for _, row in df.head(5).iterrows():
            print(f"   {row['rank']:>2}. {row['fund_code']} {str(row['fund_name'])[:25]:<27} 总分:{row['total_score']:.0f}")

    db.close()

