"""
辅助调仓顾问: 对比"你实际持有的"和"你应该持有的"，输出具体的买卖指令。

输入: 当前持仓 + 市场温度 + 质量筛选结果
输出:
  - 你需要调仓吗？
  - 卖什么、卖多少、为什么先卖这个
  - 买什么、买多少
  - 调仓后的预期风险变化
"""

import pandas as pd
from dataclasses import dataclass

from ..data.database import Database
from .thermometer import MarketThermometer
from .fund_scorer import FundScreener


@dataclass
class RebalanceInstruction:
    """一条调仓指令"""
    action: str       # "卖出" | "买入" | "持有"
    fund_code: str
    fund_name: str
    amount: float     # 金额(元)
    current_pct: float  # 当前占总仓位%
    target_pct: float   # 目标占总仓位%
    reason: str
    priority: int     # 1=最优先, 2=其次, 3=最后


class RebalanceAdvisor:
    """辅助调仓顾问"""

    def __init__(self, db: Database):
        self.db = db
        self.thermometer = MarketThermometer(db)
        self.screener = FundScreener(db)

    # =================================================================
    # 主接口
    # =================================================================

    def analyze(self, total_capital: float = None) -> dict:
        """
        分析当前持仓，生成调仓建议。

        Args:
            total_capital: 总资金(含余额宝), 不传则用持仓总市值

        Returns:
            dict: 包含完整的调仓方案
        """
        # 1. 获取数据
        temp = self.thermometer.get_temperature()
        holdings = self.db.get_current_holdings()

        if total_capital is None:
            total_capital = sum(h["buy_amount"] for h in holdings)

        # 2. 计算当前状态
        portfolio_value = self._calc_portfolio_value(holdings)
        cash = total_capital - sum(h["buy_amount"] for h in holdings)

        current_equity_pct = 0
        equity_types = {"股票型", "混合型", "指数型", "QDII"}
        for h in holdings:
            info = self._get_fund_info(h["fund_code"])
            ftype = info.get("fund_type", "") if info else ""
            if any(et in ftype for et in equity_types) or "股票" in ftype or "混合" in ftype:
                current_equity_pct += (h.get("buy_amount", 0) / total_capital * 100) if total_capital > 0 else 0

        target_equity_pct = temp["target_equity_pct"]

        # 3. 生成指令
        instructions = self._generate_instructions(
            holdings, temp, current_equity_pct, target_equity_pct,
            total_capital, portfolio_value, cash
        )

        # 4. 生成摘要
        summary = self._summarize(instructions, current_equity_pct, target_equity_pct,
                                   total_capital, portfolio_value, temp)

        return {
            "current_equity_pct": round(current_equity_pct, 1),
            "target_equity_pct": target_equity_pct,
            "total_capital": total_capital,
            "portfolio_value": portfolio_value,
            "cash_available": cash,
            "need_rebalance": abs(current_equity_pct - target_equity_pct) > 10,
            "gap_pct": round(target_equity_pct - current_equity_pct, 1),
            "temperature": temp,
            "instructions": [self._serialize_instruction(i) for i in instructions],
            "summary": summary,
        }

    # =================================================================
    # 核心逻辑
    # =================================================================

    def _generate_instructions(self, holdings, temp, current_eq, target_eq,
                                total_cap, port_value, cash):
        instructions = []
        if total_cap == 0:
            return instructions

        gap_amount = (target_eq - current_eq) / 100 * total_cap

        # 对每只持仓做风险评估
        fund_risks = []
        for h in holdings:
            info = self._get_fund_info(h["fund_code"])
            result = self.screener._screen_single_fund(h["fund_code"], info) if info else None
            risk = result if result else {"risk_label": "未知", "risk_reasons": [], "metrics": {}}

            # 计算当前市值和盈亏
            shares = h.get("shares", 0)
            bought = h["buy_amount"]
            latest_nav = self._get_latest_nav(h["fund_code"])
            current_value = shares * latest_nav if shares and latest_nav else bought
            pnl_pct = (current_value - bought) / bought * 100 if bought else 0

            fund_risks.append({
                "holding": h,
                "info": info,
                "risk": risk,
                "current_value": current_value,
                "pnl_pct": pnl_pct,
                "days_held": self._days_held(h["buy_date"]),
            })

        # 按风险排序: 高风险+亏损 → 优先卖
        risk_order = {"🔴 高风险": 0, "🟡 注意": 1, "🟢 稳健": 2, "未知": 3}
        fund_risks.sort(key=lambda x: (
            risk_order.get(x["risk"].get("risk_label", "未知"), 3),
            x["pnl_pct"],  # 亏损大的优先处理
        ))

        # === 情况1: 权益过多，需要减仓 ===
        if gap_amount < -5:  # 超过5%偏差
            sell_needed = abs(gap_amount)

            for fr in fund_risks:
                if sell_needed <= 5:  # 少于5元就不调了
                    break

                h = fr["holding"]
                risk_label = fr["risk"].get("risk_label", "未知")
                reasons = fr["risk"].get("risk_reasons", [])

                # 确定卖多少
                if risk_label == "🔴 高风险":
                    sell_ratio = 0.8  # 高风险卖80%
                elif risk_label == "🟡 注意":
                    sell_ratio = 0.5
                elif fr["pnl_pct"] < -10:
                    sell_ratio = 0.4  # 深度亏损也要考虑减
                else:
                    sell_ratio = 0.3  # 稳健的少卖

                sell_amount = min(fr["current_value"] * sell_ratio, sell_needed)
                sell_amount = max(sell_amount, 10)  # 最少10元起卖(手续费考虑)

                reason_parts = []
                if risk_label in ("🔴 高风险", "🟡 注意"):
                    reason_parts.append(f"{risk_label}基金")
                if reasons:
                    reason_parts.append(reasons[0])
                if fr["pnl_pct"] < -5:
                    reason_parts.append(f"已亏损{fr['pnl_pct']:.0f}%, 减仓控制风险")
                if fr["days_held"] < 7:
                    reason_parts.append("持有<7天, 赎回费1.5%——如果不急, 建议等满7天再卖")

                instructions.append(RebalanceInstruction(
                    action="卖出",
                    fund_code=h["fund_code"],
                    fund_name=h.get("fund_name", h["fund_code"]),
                    amount=round(sell_amount, 0),
                    current_pct=round(fr["current_value"] / total_cap * 100, 1),
                    target_pct=round((fr["current_value"] - sell_amount) / total_cap * 100, 1),
                    reason="; ".join(reason_parts) if reason_parts else "降低权益仓位至目标水平",
                    priority=1 if risk_label == "🔴 高风险" else 2,
                ))

                sell_needed -= sell_amount

            if sell_needed > 5:
                instructions.append(RebalanceInstruction(
                    action="卖出",
                    fund_code="—",
                    fund_name="(剩余需卖出)",
                    amount=round(sell_needed, 0),
                    current_pct=round(current_eq, 1),
                    target_pct=target_eq,
                    reason=f"还需减仓约{sell_needed:.0f}元以达到目标权益仓位{target_eq}%",
                    priority=3,
                ))

            # 卖出后钱往哪放
            if sum(i.amount for i in instructions if i.action == "卖出") > 10:
                instructions.append(RebalanceInstruction(
                    action="买入",
                    fund_code="—",
                    fund_name="余额宝 / 货币基金 / 短债基金",
                    amount=round(sum(i.amount for i in instructions if i.action == "卖出"), 0),
                    current_pct=0,
                    target_pct=round(100 - target_eq, 1),
                    reason="卖出权益基金的资金转入固收: 温度60°C偏热, 等待更好的入场时机",
                    priority=3,
                ))

        # === 情况2: 权益不足, 可以加仓 ===
        elif gap_amount > 5:
            buy_amount = gap_amount
            # 从质量筛选池挑🟢稳健的
            pool = self.screener.screen_funds(max_results=10)
            candidates = pool[pool["risk_label"] == "🟢 稳健"] if not pool.empty else pd.DataFrame()

            if not candidates.empty:
                best = candidates.iloc[0]
                instructions.append(RebalanceInstruction(
                    action="买入",
                    fund_code=best["fund_code"],
                    fund_name=best.get("fund_name", best["fund_code"]),
                    amount=round(min(buy_amount, total_cap * 0.3), 0),
                    current_pct=round(current_eq, 1),
                    target_pct=target_eq,
                    reason=f"温度{temp['temperature']}°C, 权益仓位不足, 建议适度加仓。优先选🟢稳健的{best['fund_code']}",
                    priority=1,
                ))

        # === 情况3: 偏差不大, 不动 ===
        else:
            for fr in fund_risks:
                instructions.append(RebalanceInstruction(
                    action="持有",
                    fund_code=fr["holding"]["fund_code"],
                    fund_name=fr["holding"].get("fund_name", fr["holding"]["fund_code"]),
                    amount=fr["current_value"],
                    current_pct=round(fr["current_value"] / total_cap * 100, 1),
                    target_pct=round(fr["current_value"] / total_cap * 100, 1),
                    reason="仓位在合理范围内, 继续持有",
                    priority=3,
                ))

        return instructions

    # =================================================================
    # 摘要
    # =================================================================

    def _summarize(self, instructions, current_eq, target_eq, total_cap, port_value, temp):
        sells = [i for i in instructions if i.action == "卖出"]
        buys = [i for i in instructions if i.action == "买入"]
        holds = [i for i in instructions if i.action == "持有"]

        total_sell = sum(i.amount for i in sells)
        total_buy = sum(i.amount for i in buys)

        if not sells and not buys:
            return {
                "verdict": "✅ 无需调仓",
                "detail": f"当前权益{current_eq:.0f}%, 目标{target_eq}%, 偏差在合理范围内。继续持有。",
            }

        verdict = "🔴 需要大幅减仓" if total_sell > total_cap * 0.3 else \
                  "🟡 建议适度调整" if total_sell > 0 else \
                  "🟢 可以小幅加仓"

        detail_parts = []
        if sells:
            detail_parts.append(f"卖出约{total_sell:.0f}元({len(sells)}笔)")
        if buys:
            detail_parts.append(f"买入约{total_buy:.0f}元({len(buys)}笔)")
        detail_parts.append(f"目标权益{target_eq}%, 当前{current_eq:.0f}%")

        return {
            "verdict": verdict,
            "detail": " | ".join(detail_parts),
            "total_sell": round(total_sell, 0),
            "total_buy": round(total_buy, 0),
        }

    # =================================================================
    # 工具
    # =================================================================

    def _calc_portfolio_value(self, holdings):
        total = 0
        for h in holdings:
            nav = self._get_latest_nav(h["fund_code"])
            shares = h.get("shares", 0)
            total += shares * nav if nav and shares else h["buy_amount"]
        return round(total, 2)

    def _get_fund_info(self, code):
        funds = self.db.get_all_funds()
        for f in funds:
            if f["fund_code"] == code:
                return f
        return {}

    def _get_latest_nav(self, code):
        navs = self.db.get_fund_nav(code)
        return float(navs[-1]["unit_nav"]) if navs else None

    def _days_held(self, buy_date):
        try:
            buy = pd.to_datetime(buy_date)
            return (pd.Timestamp.now() - buy).days
        except:
            return 999

    def _serialize_instruction(self, i: RebalanceInstruction) -> dict:
        return {
            "action": i.action,
            "fund_code": i.fund_code,
            "fund_name": i.fund_name,
            "amount": i.amount,
            "current_pct": i.current_pct,
            "target_pct": i.target_pct,
            "reason": i.reason,
            "priority": i.priority,
        }
