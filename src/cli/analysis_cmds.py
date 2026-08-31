"""
CLI 命令模块（由 main.py 拆分而来）。
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser

import akshare as ak
import numpy as np
import pandas as pd

from src.data.database import Database
from src.data.collector import DataCollector, quick_test
from src.data.hithink_collector import HiThinkCollector, quick_test as hithink_quick_test
from src.analysis.backtest import RigorousBacktest
from src.analysis.fund_scorer import FundScorer, FundScreener
from src.analysis.historical_recommender import HistoricalRecommender
from src.analysis.investment_plan import get_plan, get_progress
from src.analysis.portfolio import PortfolioTracker
from src.analysis.rebalance_advisor import RebalanceAdvisor
from src.analysis.sector_analyzer import SectorAnalyzer
from src.analysis.sentiment_monitor import SentimentMonitor, quick_scan as sentiment_quick_scan
from src.analysis.strategy_engine import StrategyEngine
from src.analysis.thermometer import MarketThermometer
from src.output.reporter import WeeklyReporter


# 同花顺 API Key（从 .env 读取）
def _load_api_key():
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith("HITHINK_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


HITHINK_KEY = _load_api_key()


def cmd_score():
    """基金质量筛选（v3.0: 替代评分排名）"""
    db = Database("data/fund_quant.db")
    screener = FundScreener(db)
    thermometer = MarketThermometer(db)

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

                print(f"  {row['fund_code']} {str(row['fund_name'])[:28]:<30} 费率{row['mgt_fee']:.2f}%  近3月{mom:+.0f}%  回撤{dd:.0f}%")

                reasons = row.get("risk_reasons", [])
                for r in reasons:
                    print(f"    ↳ {r}")

    db.close()


def cmd_temp():
    """查看市场温度 (v2.0: 含真实成交量+风格判断+分歧检测)"""
    db = Database("data/fund_quant.db")
    thermometer = MarketThermometer(db)

    temp = thermometer.get_temperature()

    # 温度条
    t = temp['temperature']
    bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
    comp = temp["components"]

    print("🌡️ 市场温度计 v2.0")
    print("=" * 50)
    print(f"  [{bar}] {t}°C")
    print(f"  状态: {temp['level_desc']}")
    print(f"  建议: {temp['action']}")
    print(f"  建议权益仓位: {temp['target_equity_pct']}%")
    print()
    print("  📊 五维分解:")
    print(f"    PE估值分位数:  {comp['pe_score']:.0f}°  (越高越贵)")
    print(f"    PB估值分位数:  {comp['pb_score']:.0f}°  (越高越贵)")
    print(f"    股债性价比:    {comp['erp_score']:.0f}°  (越高股票越贵)")
    print(f"    成交量热度:    {comp['volume_score']:.0f}°  (天量=高温)")
    print(f"    市场情绪:      {comp['sentiment_score']:.0f}°  (贪婪=高温)")

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

    summary = tracker.get_portfolio_summary()

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

    # 计算总资金(持仓+估计的现金)
    holdings = db.get_current_holdings()
    total_invested = sum(h["buy_amount"] for h in holdings)

    if not holdings:
        print("📋 暂无持仓。先录入:")
        print("   python src/main.py buy")
        db.close()
        return

    # 假设总资金 = 已投入 + 10%现金缓冲, 或提示用户输入
    total_cap = total_invested * 1.1  # 留10%现金缓冲

    result = advisor.analyze(total_capital=total_cap)

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
    """历史验证推荐: 基于3年回测, 找出持续被选中且真的赚了钱的基金"""
    db = Database("data/fund_quant.db")
    hr = HistoricalRecommender(db)

    print("🔄 历史回测推荐引擎运行中...")
    print("   (80只基金 · 每2月打分 · 约1.5年数据)")
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
    print(f"  候选基金: {stats['total_candidates']}只")
    print(f"  被选中过的: {stats['funds_ever_picked']}只")
    print(f"  历史验证: {stats['funds_with_proven_record']}只有效数据")
    print(f"    正收益{stats['proven_good']}只 / 负收益{stats['proven_bad']}只")
    print(f"  Top10平均3月后收益: {stats['top10_avg_3m_return']:+.1f}%")

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

