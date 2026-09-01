"""
输出与操作命令：report / buy / sell / web / schedule。
"""

import threading
import time
import webbrowser

from src.analysis.fund_scorer import FundScreener
from src.analysis.portfolio import PortfolioTracker
from src.analysis.sentiment_monitor import SentimentMonitor
from src.analysis.thermometer import MarketThermometer
from src.data.database import Database
from src.output.reporter import WeeklyReporter


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


def cmd_update():
    """修改持仓记录：投入金额 / 买入日期（更正录入错误）"""
    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)

    summary = tracker.get_portfolio_summary()
    if not summary.get("has_holdings"):
        print("📋 暂无持仓记录。")
        db.close()
        return

    print("📋 当前持仓（选择要修改的 ID）:")
    for d in summary["holdings_detail"]:
        print(f"  ID:{d['holding_id']} | {d['fund_name'][:25]} | 买入{d['buy_date']} | ¥{d['buy_amount']:,.0f}")

    try:
        holding_id = int(input("\n要修改的持仓 ID: ").strip())
    except ValueError:
        print("❌ ID 格式错误")
        db.close()
        return

    new_amount = input("新的投入金额（元，直接回车跳过）: ").strip()
    new_date = input("新的买入日期（YYYY-MM-DD，直接回车跳过）: ").strip()

    fields = {}
    if new_amount:
        try:
            fields["buy_amount"] = float(new_amount)
        except ValueError:
            print("❌ 金额格式错误")
            db.close()
            return
    if new_date:
        fields["buy_date"] = new_date

    if not fields:
        print("ℹ️ 未输入任何修改，已取消")
        db.close()
        return

    # 修改金额时，按记录的买入净值重算份额
    if "buy_amount" in fields:
        row = db.conn.cursor().execute("SELECT buy_nav FROM holdings WHERE id = ?", (holding_id,)).fetchone()
        buy_nav = row["buy_nav"] if row else None
        fields["shares"] = round(fields["buy_amount"] / buy_nav, 2) if buy_nav and buy_nav > 0 else 0

    ok = db.update_holding(holding_id, **fields)
    db.close()
    print(f"\n✅ 已更新持仓 ID={holding_id}: {fields}" if ok else f"❌ 未找到 ID={holding_id}")


def cmd_delete():
    """删除持仓记录（处理重复录入等）"""
    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)

    summary = tracker.get_portfolio_summary()
    if not summary.get("has_holdings"):
        print("📋 暂无持仓记录。")
        db.close()
        return

    print("📋 当前持仓（选择要删除的 ID）:")
    for d in summary["holdings_detail"]:
        print(f"  ID:{d['holding_id']} | {d['fund_name'][:25]} | 买入{d['buy_date']} | ¥{d['buy_amount']:,.0f}")

    try:
        holding_id = int(input("\n要删除的持仓 ID: ").strip())
    except ValueError:
        print("❌ ID 格式错误")
        db.close()
        return

    confirm = input(f"确认删除 ID={holding_id}？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消")
        db.close()
        return

    ok = db.delete_holding(holding_id)
    db.close()
    print(f"\n✅ 已删除持仓 ID={holding_id}" if ok else f"❌ 未找到 ID={holding_id}")


def cmd_web():
    """启动本地仪表盘 (http://localhost:5020)"""
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

