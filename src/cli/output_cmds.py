"""
输出与操作命令：report / buy / sell / web / schedule。
"""

import threading
import time
import webbrowser

from src.analysis.fund_scorer import FundScreener
from src.analysis.dca import DcaManager
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
    holdings = portfolio.get_portfolio_summary(reconcile=True)   # 周报先对账，反映最新结算
    holding_codes = [h["fund_code"] for h in holdings.get("holdings_detail", [])]

    sentiment_data = None
    try:
        monitor = SentimentMonitor()
        sentiment_data = monitor.full_scan(holding_codes=holding_codes)
    except Exception:
        pass

    reporter.generate_full_report(screener, thermometer, portfolio, sentiment_data)

    # 定投状态
    _print_dca_status(db)
    db.close()


def _print_dca_status(db):
    """在周报末尾输出定投计划状态"""
    try:
        mgr = DcaManager(db)
        plans = mgr.get_status()
    except Exception:
        return
    if not plans:
        return

    print("\n📅 定投计划")
    print("-" * 56)
    any_due = False
    for p in plans:
        due_label = "🔔 今日到期" if p["frequency"] == "daily" else "🔔 本周到期"
        due_flag = due_label if p["due"] else ""
        any_due = any_due or p["due"]
        print(f"  {p['fund_name'][:20]:<22} | 每期¥{p['amount_per_period']:.0f}/{p['frequency']} | "
              f"已投{p['total_periods']}期 累计¥{p['total_amount']:.0f} | 下期 {p['next_run_date']} {due_flag}")
    if any_due:
        print("  💡 执行定投: python src/main.py dca run")


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
    summary = tracker.get_portfolio_summary(reconcile=True)   # 显式动作：先对账再列持仓
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
        if not row:
            print("❌ 未找到 ID=", holding_id)
            db.close()
            return
        buy_nav = row["buy_nav"]
        if buy_nav and buy_nav > 0:
            fields["shares"] = round(fields["buy_amount"] / buy_nav, 2)
        else:
            # 买入净值未知(录入时可能缺净值)：不能可靠重算份额，避免把份额清成 0
            print("⚠️ 该持仓买入净值未知，无法重算份额；已只更新金额")

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
    """启动本地仪表盘（默认 http://localhost:5020；端口可用 QFA_PORT / 参数覆盖）"""
    import webbrowser
    from src.web import app as webapp
    # 端口解析统一走 app._resolve_port（E-07）：优先 --port=N > 位置参数 > QFA_PORT > 5020。
    # 这里不再硬编码 5020，否则第二个实例的横幅会指向错误端口（踩过）。
    port = webapp._resolve_port()
    url = "http://localhost:%d" % port
    print("🚀 启动仪表盘...")
    print("   " + url)
    print("   按 Ctrl+C 停止")
    print()

    # Open browser after a short delay
    import threading
    def _open():
        import time
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()
    # 单一启动入口：app.main() 负责预计算板块(后台线程)+ 线程化 Flask(threaded=True)，
    # 避免这里重复启动板块预计算、并用单线程模式阻塞异步卡片
    webapp.main()


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


def cmd_dca():
    """定投管理: dca list / dca add / dca run / dca pause / dca resume"""
    import sys as _sys

    sub = _sys.argv[2] if len(_sys.argv) > 2 else "list"
    db = Database("data/fund_quant.db")
    mgr = DcaManager(db)

    if sub == "add":
        _dca_add(db)
    elif sub == "run":
        _dca_run(mgr)
    elif sub == "pause":
        _dca_set_status(mgr, paused=True)
    elif sub == "resume":
        _dca_set_status(mgr, paused=False)
    else:
        _dca_list(mgr)

    db.close()


def _dca_list(mgr: DcaManager):
    """列出定投计划及到期状态"""
    plans = mgr.get_status()
    if not plans:
        print("📋 暂无定投计划。添加: python src/main.py dca add")
        return

    print("📋 定投计划")
    print("-" * 56)
    for p in plans:
        due_label = "🔔 今日到期" if p["frequency"] == "daily" else "🔔 本周到期"
        due_flag = due_label if p["due"] else ""
        print(f"  ID:{p['id']} {p['fund_name']} | 每期¥{p['amount_per_period']:.0f}/{p['frequency']} | "
              f"已投{p['total_periods']}期 累计¥{p['total_amount']:.0f} | 下期 {p['next_run_date']} {due_flag}")
    print()
    print("  操作: dca run(执行到期期数) / dca pause|resume(暂停/恢复) / dca add(新增)")


def _dca_add(db: Database):
    """交互式新增定投计划"""
    print("📝 新增定投计划")
    print("-" * 40)
    fund_code = input("基金代码: ").strip()
    fund_name = input("基金名称: ").strip()
    try:
        amount = float(input("每期金额 (元): ").strip())
    except ValueError:
        print("❌ 金额格式错误")
        return
    freq = input("频率 (daily日/weekly周/biweekly双周/monthly月, 默认weekly): ").strip() or "weekly"
    start = input("开始日期 (YYYY-MM-DD, 默认今天): ").strip()
    if not start:
        from datetime import date
        start = date.today().isoformat()

    db.add_dca_plan({
        "fund_code": fund_code,
        "fund_name": fund_name,
        "amount_per_period": amount,
        "frequency": freq,
        "start_date": start,
        "next_run_date": DcaManager.next_run_date(freq, start),
    })
    print(f"\n✅ 已添加定投: {fund_name}({fund_code}) 每期¥{amount:.0f}/{freq}，下期 {DcaManager.next_run_date(freq, start)}")


def _dca_run(mgr: DcaManager):
    """执行到期（或指定）定投期数：记录买入 + 推进下一期"""
    plans = mgr.get_status()
    due_plans = [p for p in plans if p["due"]]
    if not due_plans:
        print("✅ 当前没有到期的定投计划。")
        _dca_list(mgr)
        return

    print(f"🔔 有 {len(due_plans)} 个定投计划到期:")
    for p in due_plans:
        print(f"  ID:{p['id']} {p['fund_name']} | 每期¥{p['amount_per_period']:.0f} | 下期日 {p['next_run_date']}")

    try:
        choice = input("\n执行哪些？(输入ID执行单期，输入 all 全部执行，回车跳过): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return

    targets = due_plans if choice == "all" else [p for p in due_plans if str(p["id"]) == choice]
    for p in targets:
        result = mgr.execute_installment(p["id"])
        if result.get("ok"):
            print(f"✅ 已执行 {p['fund_name']} 第{result['period']}期 ¥{result['amount']:.0f}，下期 {result['next_run_date']}")
        else:
            print(f"❌ {p['fund_name']}: {result.get('error')}")


def _dca_set_status(mgr: DcaManager, paused: bool):
    """暂停/恢复定投计划"""
    plans = mgr.get_status()
    if not plans:
        print("📋 暂无定投计划。")
        return
    print("📋 当前定投计划:")
    for p in plans:
        print(f"  ID:{p['id']} {p['fund_name']} | 每期¥{p['amount_per_period']:.0f}")
    try:
        plan_id = int(input(f"\n要{'暂停' if paused else '恢复'}的 ID: ").strip())
    except ValueError:
        print("❌ ID 格式错误")
        return
    ok = mgr.db.update_dca_plan(plan_id, status="paused" if paused else "active")
    print(f"\n✅ 已{'暂停' if paused else '恢复'} ID={plan_id}" if ok else f"❌ 未找到 ID={plan_id}")

