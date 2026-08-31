"""
周报生成器 v3.0: 质量筛选池 + 风险标签 + 仓位建议。

v2→v3 变化:
- 不再展示"Top10排名"（不可靠预测）
- 改为展示"质量筛选池"（排除有坑的，剩下的你自己选）
- 每只基金展示风险标签和具体警告原因
- 动量过高不吹捧、标注追涨风险
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("⚠️ rich 库未安装，使用纯文本输出。安装: pip install rich")


from ..data.database import Database
from ..analysis.fund_scorer import FundScreener
from ..analysis.thermometer import MarketThermometer
from ..analysis.portfolio import PortfolioTracker


class WeeklyReporter:
    """每周报告生成器"""

    def __init__(self, db: Database):
        self.db = db
        self.console = Console() if RICH_AVAILABLE else None

    def generate_full_report(
        self,
        fund_screener: 'FundScreener',
        thermometer: MarketThermometer,
        portfolio: PortfolioTracker,
        sentiment_data: dict = None,
    ) -> str:
        """生成完整的周度报告。温度稳定则输出极简报告。"""
        temp_data = thermometer.get_temperature()
        fund_pool = fund_screener.screen_funds(max_results=30)
        portfolio_data = portfolio.get_portfolio_summary()

        # 极简模式: 温度稳定 + 无持仓 + 无重要信号
        no_holdings = not portfolio_data.get("has_holdings", False)
        no_alerts = not sentiment_data or sentiment_data.get("all_clear", True)

        if no_holdings and no_alerts:
            if RICH_AVAILABLE:
                self._print_minimal_report(temp_data)
            else:
                print(self._generate_minimal_plain(temp_data))
            return ""

        if RICH_AVAILABLE:
            return self._generate_rich_report(temp_data, fund_pool, portfolio_data, fund_screener, sentiment_data)
        else:
            return self._generate_plain_report(temp_data, fund_pool, portfolio_data, sentiment_data)

    def _print_minimal_report(self, temp_data: dict):
        """极简报告: 温度没变，别动。"""
        t = temp_data["temperature"]
        bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
        level = temp_data["level_desc"]

        self.console.print()
        self.console.print(Panel(
            f"[bold white]📊 本周基金简报[/bold white]\n"
            f"[dim]{datetime.now().strftime('%Y-%m-%d')}[/dim]",
            box=box.SIMPLE, style="cyan",
        ))
        self.console.print()
        self.console.print(f"  🌡️ 温度: [{bar}] {t}°C — {level}")
        self.console.print(f"  🎯 建议权益仓位: {temp_data['target_equity_pct']}%")
        self.console.print()
        self.console.print(f"  [bold green]✅ 温度稳定，无需操作。[/bold green]")
        self.console.print()
        self.console.print(f"  [dim]💡 对长期投资者来说，大多数周都应该什么都不做。[/dim]")
        self.console.print(f"  [dim]   频繁操作是散户亏钱的第一原因。[/dim]")
        self.console.print()
        self.console.print(f"  📌 下次建议关注: 当温度突破 40° 或 60° 时重新评估")
        self.console.print()

    @staticmethod
    def _generate_minimal_plain(temp_data: dict) -> str:
        t = temp_data["temperature"]
        lines = [
            "=" * 40,
            f"📊 本周基金简报 ({datetime.now().strftime('%Y-%m-%d')})",
            "=" * 40,
            f"🌡️ 温度: {t}°C — {temp_data['level_desc']}",
            f"🎯 建议权益: {temp_data['target_equity_pct']}%",
            "",
            "✅ 温度稳定，无需操作。",
            "",
            "💡 大多数周都应该什么都不做。",
            "   频繁操作是散户亏钱的第一原因。",
            "=" * 40,
        ]
        return "\n".join(lines)

    def _generate_rich_report(
        self,
        temp_data: dict,
        fund_pool: pd.DataFrame,
        portfolio_data: dict,
        screener: 'FundScreener' = None,
        sentiment_data: dict = None,
    ) -> str:
        """使用 rich 库生成彩色报告"""

        today = datetime.now().strftime("%Y-%m-%d")
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")

        self.console.print()
        title = Panel(
            f"[bold white]📊 每周基金操作建议[/bold white]\n"
            f"[dim]报告周期: {week_start} ~ {today} | v3.0 质量筛选[/dim]",
            box=box.DOUBLE,
            style="cyan",
        )
        self.console.print(title)

        self._print_temperature_section(temp_data)
        self._print_allocation_section(temp_data, portfolio_data)
        if sentiment_data:
            self._print_sentiment_section(sentiment_data)
        self._print_quality_pool_section(fund_pool, screener)
        self._print_holdings_section(portfolio_data)

        self.console.print()
        self.console.print("[dim]───[/dim]")
        self.console.print("[dim]💡 系统不推荐买哪只最好——只帮你排除有坑的。剩下的你自己决定。[/dim]")
        self.console.print("[dim]⚠️ 本报告仅供学习参考，不构成投资建议。投资有风险，入市需谨慎。[/dim]")
        self.console.print("[dim]📌 场外基金T+1确认份额，操作请在交易日下午3点前完成。[/dim]")
        self.console.print()

        return ""

    def _print_temperature_section(self, temp_data: dict):
        """打印市场温度计 v2.0"""
        temp = temp_data["temperature"]
        level_desc = temp_data["level_desc"]
        action = temp_data["action"]

        # 温度条
        filled = int(temp / 5)
        bar = "█" * filled + "░" * (20 - filled)

        if temp <= 20:
            bar_color = "blue"
        elif temp <= 40:
            bar_color = "cyan"
        elif temp <= 60:
            bar_color = "green"
        elif temp <= 80:
            bar_color = "yellow"
        else:
            bar_color = "red"

        self.console.print()
        self.console.print(f"[bold]🌡️ 市场温度计 v2.0[/bold]")
        self.console.print(f"  [{bar_color}]{bar}[/{bar_color}] {temp}°C")
        self.console.print(f"  状态: {level_desc}")
        self.console.print(f"  建议: [bold]{action}[/bold]")

        # 详细分解
        comp = temp_data["components"]
        self.console.print()
        self.console.print(f"  [dim]PE估值分位数:  {comp['pe_score']:.0f}°  (越高越贵)[/dim]")
        self.console.print(f"  [dim]PB估值分位数:  {comp['pb_score']:.0f}°  (越高越贵)[/dim]")
        self.console.print(f"  [dim]股债性价比:    {comp['erp_score']:.0f}°  (越高股票越贵)[/dim]")
        self.console.print(f"  [dim]成交量热度:    {comp['volume_score']:.0f}°  (天量=高温)[/dim]")
        self.console.print(f"  [dim]市场情绪:      {comp['sentiment_score']:.0f}°  (贪婪=高温)[/dim]")

        # 估值分歧
        div = temp_data.get("divergence", {})
        if div:
            div_color = "green" if div.get("level") == "一致" else ("yellow" if "轻微" in str(div.get("level","")) else "red")
            self.console.print()
            self.console.print(f"  🔍 估值分歧度: [{div_color}]{div.get('level', '未知')}[/{div_color}]")
            self.console.print(f"  [dim]{div.get('message', '')}[/dim]")

        # 市场风格
        style = temp_data.get("market_style", {})
        if style and style.get("dominant") != "unknown":
            self.console.print()
            self.console.print(f"  🎨 当前市场风格: [bold cyan]{style.get('dominant', '')}[/bold cyan]")
            self.console.print(f"  [dim]{style.get('detail', '')}[/dim]")
            returns = style.get("returns", {})
            if returns:
                for name, ret in returns.items():
                    arrow = "📈" if ret > 0 else "📉"
                    ret_color = "green" if ret > 0 else "red"
                    self.console.print(f"  [dim]  {arrow} {name}: [{ret_color}]{ret:+.1f}%[/{ret_color}] (20日)[/dim]")

    def _print_allocation_section(self, temp_data: dict, portfolio_data: dict):
        """打印仓位建议"""
        target_equity = temp_data["target_equity_pct"]

        self.console.print()
        self.console.print(f"[bold]🎯 仓位建议[/bold]")
        self.console.print(f"  建议权益仓位: [bold]{target_equity}%[/bold]")
        self.console.print(f"  建议债券/货币仓位: [bold]{100 - target_equity}%[/bold]")

        if portfolio_data.get("has_holdings"):
            total = portfolio_data.get("total_invested", 0)
            current_equity_pct = self._calc_current_equity_pct(portfolio_data)
            self.console.print(f"  当前权益仓位: {current_equity_pct:.0f}%")
            self.console.print(f"  当前总市值: ¥{portfolio_data.get('total_market_value', 0):,.2f}")

            # 仓位偏差提示
            diff = current_equity_pct - target_equity
            if diff > 20:
                self.console.print(f"  [bold red]⚠️ 权益仓位偏高 {diff:.0f}%，建议减仓[/bold red]")
            elif diff < -20:
                self.console.print(f"  [bold green]💡 权益仓位偏低 {abs(diff):.0f}%，可考虑加仓[/bold green]")
            else:
                self.console.print(f"  [green]✅ 仓位在合理范围内[/green]")

    def _print_sentiment_section(self, sentiment_data: dict):
        """打印消息面 — v3.0: 只显示能影响决策的信号"""
        if not sentiment_data:
            return

        summary = sentiment_data.get("signal_summary", "")
        alerts = sentiment_data.get("alerts", [])

        self.console.print()
        self.console.print("[bold]📰 信号[/bold]")

        # 一句话摘要
        if sentiment_data.get("all_clear"):
            self.console.print("  [green]✅ 本周无需要关注的信号[/green]")
        else:
            self.console.print(f"  [dim]{summary}[/dim]")

        # 只在有重要信号时展开详情
        if alerts:
            for alert in alerts[:3]:  # 最多3条
                icon = alert.level
                self.console.print(f"  {icon} [{alert.category}] {alert.title[:55]}")
                if alert.detail:
                    self.console.print(f"     [dim]{alert.detail[:100]}[/dim]")

    def _print_quality_pool_section(self, fund_pool: pd.DataFrame, screener: 'FundScreener' = None):
        """打印质量筛选池（v3.0: 替代Top10排名）"""
        self.console.print()
        self.console.print("[bold]🔍 质量筛选基金池[/bold]")

        if fund_pool.empty:
            self.console.print("  [yellow]暂无通过质量筛选的基金。[/yellow]")
            return

        # 统计摘要
        if screener:
            summary = screener.get_pool_summary(fund_pool)
            parts = []
            for label, count in summary.get("by_risk", {}).items():
                parts.append(f"{label}: {count}只")
            self.console.print(f"  [dim]筛选结果: {summary['total']}只 | {' | '.join(parts)} | 平均费率: {summary['avg_fee']:.2f}%[/dim]")

        self.console.print()

        # 分类展示
        for risk_label, color, icon in [("🟢 稳健", "green", "✅"), ("🟡 注意", "yellow", "⚠️"), ("🔴 高风险", "red", "🔴")]:
            subset = fund_pool[fund_pool["risk_label"] == risk_label]
            if subset.empty:
                continue

            self.console.print(f"  [{color}]{icon} {risk_label} ({len(subset)}只)[/{color}]")

            for _, row in subset.head(8).iterrows():
                code = row["fund_code"]
                name = str(row["fund_name"])[:22]
                fee = row["mgt_fee"]
                reasons = row.get("risk_reasons", [])

                # 风险标签
                if risk_label == "🟢 稳健":
                    tag = "[green]✓[/green]"
                elif risk_label == "🟡 注意":
                    tag = "[yellow]![/yellow]"
                else:
                    tag = "[red]!![/red]"

                # 基金基本信息
                line = f"  {tag} [cyan]{code}[/cyan] {name:<24} 费率{fee:.2f}%"

                # 关键指标(从metrics取)
                metrics = row.get("metrics", {})
                if metrics:
                    mom = metrics.get("momentum_3m")
                    dd = metrics.get("max_drawdown_1y")
                    if mom is not None:
                        mom_str = f"近3月{mom:+.0f}%"
                        if mom > 30:
                            mom_str = f"[red]{mom_str} ⚠️追涨[/red]"
                        line += f"  {mom_str}"
                    if dd is not None:
                        line += f"  回撤{dd:.0f}%"

                self.console.print(line)

                # 风险原因
                if reasons:
                    for reason in reasons[:2]:
                        self.console.print(f"      [dim]↳ {reason}[/dim]")

            self.console.print()

    # Deprecated: kept for backward compat
    def _print_recommendations_section(self, *args, **kwargs):
        self._print_quality_pool_section(*args, **kwargs)

    def _print_holdings_section(self, portfolio_data: dict):
        """打印当前持仓"""
        self.console.print()
        self.console.print("[bold]📋 当前持仓[/bold]")

        if not portfolio_data.get("has_holdings"):
            self.console.print("  [dim]暂无持仓记录。使用 'python src/main.py buy --help' 添加持仓。[/dim]")
            return

        self.console.print(f"  总投入: ¥{portfolio_data['total_invested']:,.2f}")
        self.console.print(f"  总市值: ¥{portfolio_data['total_market_value']:,.2f}")
        pnl = portfolio_data["total_pnl"]
        pnl_pct = portfolio_data["total_return_pct"]
        pnl_color = "green" if pnl >= 0 else "red"
        self.console.print(f"  浮动盈亏: [{pnl_color}]¥{pnl:+,.2f} ({pnl_pct:+.2f}%)[/{pnl_color}]")

        table = Table(box=box.SIMPLE)
        table.add_column("基金名称", width=20)
        table.add_column("买入日期", width=10)
        table.add_column("投入", justify="right", width=8)
        table.add_column("市值", justify="right", width=8)
        table.add_column("盈亏", justify="right", width=10)
        table.add_column("持有时长", justify="right", width=8)

        for d in portfolio_data["holdings_detail"]:
            pnl = d["pnl"]
            pnl_color = "green" if pnl >= 0 else "red"
            table.add_row(
                str(d["fund_name"])[:18],
                str(d["buy_date"]),
                f"¥{d['buy_amount']:,.0f}",
                f"¥{d['current_value']:,.0f}",
                f"[{pnl_color}]¥{pnl:+,.0f} ({d['pnl_pct']:+.1f}%)[/{pnl_color}]",
                f"{d['days_held']}天",
            )

        self.console.print(table)

    def _generate_plain_report(
        self,
        temp_data: dict,
        fund_pool: pd.DataFrame,
        portfolio_data: dict,
        sentiment_data: dict = None,
    ) -> str:
        """生成纯文本报告（无rich依赖，v3.0质量筛选版）"""
        today = datetime.now().strftime("%Y-%m-%d")
        lines = []
        lines.append("=" * 60)
        lines.append(f"📊 每周基金操作建议 ({today})")
        lines.append("=" * 60)

        lines.append(f"\n🌡️ 市场温度: {temp_data['temperature']}°C | {temp_data['level_desc']}")
        lines.append(f"   建议: {temp_data['action']}")
        lines.append(f"   建议权益仓位: {temp_data['target_equity_pct']}%")

        # 质量筛选池
        lines.append(f"\n🔍 质量筛选基金池:")
        lines.append("-" * 50)
        if fund_pool.empty:
            lines.append("  暂无通过筛选的基金")
        else:
            for risk_label in ["🟢 稳健", "🟡 注意", "🔴 高风险"]:
                subset = fund_pool[fund_pool["risk_label"] == risk_label]
                if subset.empty:
                    continue
                lines.append(f"\n  {risk_label}:")
                for _, row in subset.head(5).iterrows():
                    metrics = row.get("metrics", {})
                    mom = metrics.get("momentum_3m", 0) or 0
                    dd = metrics.get("max_drawdown_1y", 0) or 0
                    reasons = row.get("risk_reasons", [])
                    warning = f" ⚠️{reasons[0]}" if reasons else ""
                    lines.append(
                        f"  {row['fund_code']} {str(row['fund_name'])[:22]:<24} "
                        f"费率{row['mgt_fee']:.2f}% 近3月{mom:+.0f}% 回撤{dd:.0f}%{warning}"
                    )

        # 持仓
        lines.append(f"\n📋 当前持仓:")
        lines.append("-" * 50)
        if not portfolio_data.get("has_holdings"):
            lines.append("  暂无持仓")
        else:
            lines.append(f"  总投入: ¥{portfolio_data['total_invested']:,.2f}")
            lines.append(f"  总市值: ¥{portfolio_data['total_market_value']:,.2f}")
            pnl = portfolio_data["total_pnl"]
            lines.append(f"  浮动盈亏: ¥{pnl:+,.2f} ({portfolio_data['total_return_pct']:+.2f}%)")

        lines.append("\n" + "=" * 60)
        lines.append("💡 系统只排除有坑的，不推荐买哪只最好")
        lines.append("⚠️ 仅供学习参考，不构成投资建议。")
        lines.append("=" * 60)

        return "\n".join(lines)

    def _calc_current_equity_pct(self, portfolio_data: dict) -> float:
        """计算当前权益类基金占比"""
        if not portfolio_data.get("has_holdings"):
            return 0

        # 权益类型: 股票型、混合型、指数型
        equity_types = {"股票型", "混合型", "指数型", "混合型-偏股", "混合型-灵活"}
        total = portfolio_data["total_market_value"]
        if total == 0:
            return 0

        equity_value = 0
        for d in portfolio_data["holdings_detail"]:
            ftype = d.get("fund_type", "")
            if any(et in ftype for et in equity_types):
                equity_value += d["current_value"]

        return (equity_value / total) * 100


def quick_report():
    """
    快速生成一份报告（测试用）。
    运行: python -m src.output.reporter
    """
    db = Database("data/fund_quant.db")
    scorer = FundScorer(db)
    thermometer = MarketThermometer(db)
    portfolio = PortfolioTracker(db)
    reporter = WeeklyReporter(db)

    reporter.generate_full_report(scorer, thermometer, portfolio)
    db.close()


if __name__ == "__main__":
    quick_report()
