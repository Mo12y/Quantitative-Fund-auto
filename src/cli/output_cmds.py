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


def cmd_report():
    """生成完整周报 (v3.0 质量筛选 + 消息面)"""
    db = Database("data/fund_quant.db")
    screener = FundScreener(db)
    thermometer = MarketThermometer(db)
    portfolio = PortfolioTracker(db)
    reporter = WeeklyReporter(db)

    # 消息面扫描
    holdings = portfolio.get_portfolio_summary()
    holding_codes = [h["fund_code"] for h in holdings.get("holdings_detail", [])]

    sentiment_data = None
    try:
        monitor = SentimentMonitor()
        sentiment_data = monitor.full_scan(holding_codes=holding_codes)
    except Exception:
        pass

    reporter.generate_full_report(screener, thermometer, portfolio, sentiment_data)
    db.close()


def cmd_buy():
    """交互式添加买入记录"""
    print("📝 添加买入记录")
    print("-" * 40)

    fund_code = input("基金代码 (如 000011): ").strip()
    fund_name = input("基金名称 (如 华夏大盘精选): ").strip()
    buy_date = input("买入日期 (YYYY-MM-DD, 默认今天): ").strip()
    if not buy_date:
        from datetime import date
        buy_date = date.today().isoformat()

    try:
        amount = float(input("买入金额 (元): ").strip())
    except ValueError:
        print("❌ 金额格式错误")
        return

    notes = input("备注 (可选): ").strip()

    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)
    tracker.add_buy_transaction(fund_code, fund_name, buy_date, amount, notes)
    db.close()

    print(f"\n✅ 已记录: {buy_date} 买入 {fund_name}({fund_code}) ¥{amount:,.2f}")


def cmd_sell():
    """交互式添加卖出记录"""
    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)

    # 先显示当前持仓
    summary = tracker.get_portfolio_summary()
    if not summary.get("has_holdings"):
        print("📋 暂无持仓记录，无需卖出。")
        db.close()
        return

    print("📋 当前持仓:")
    for d in summary["holdings_detail"]:
        print(f"  ID:{d['holding_id']} | {d['fund_name'][:25]} | 市值¥{d['current_value']:,.0f}")

    print()
    try:
        holding_id = int(input("要卖出的持仓ID: ").strip())
    except ValueError:
        print("❌ ID格式错误")
        db.close()
        return

    sell_date = input("卖出日期 (YYYY-MM-DD, 默认今天): ").strip()
    if not sell_date:
        from datetime import date
        sell_date = date.today().isoformat()

    try:
        sell_amount = float(input("卖出金额 (元): ").strip())
    except ValueError:
        print("❌ 金额格式错误")
        db.close()
        return

    tracker.record_sell(holding_id, sell_date, sell_amount)
    db.close()

    print(f"\n✅ 已记录: {sell_date} 卖出 持仓ID={holding_id}, ¥{sell_amount:,.2f}")


def cmd_web():
    """启动本地仪表盘 (http://localhost:5020)"""
    import subprocess
    import webbrowser
    print("🚀 启动仪表盘...")
    print("   http://localhost:5020")
    print("   按 Ctrl+C 停止")
    print()
    # Open browser after a short delay
    import threading
    def _open():
        import time
        time.sleep(1.5)
        webbrowser.open("http://localhost:5020")
    threading.Thread(target=_open, daemon=True).start()
    # 预计算板块数据（后台线程，不阻塞启动）
    from src.web.app import app, _precompute_sectors
    threading.Thread(target=_precompute_sectors, daemon=True).start()
    # Run Flask
    app.run(host="0.0.0.0", port=5020, debug=False)


def cmd_schedule():
    """定时调度: 每周日 20:00 自动生成周报（依赖 schedule 库）"""
    try:
        import schedule
        import time
    except ImportError:
        print("❌ 未安装 schedule 库，请先执行: pip install schedule")
        return

    print("⏰ 定时调度已启动")
    print("   每周日 20:00 自动生成周报")
    print("   按 Ctrl+C 停止")
    print()

    schedule.every().sunday.at("20:00").do(cmd_report)

    while True:
        schedule.run_pending()
        time.sleep(60)

