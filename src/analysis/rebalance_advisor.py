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


# 触发调仓的权益仓位偏差阈值(百分点)。used by 指令生成 与 need_rebalance，二者一致，
# 避免“指令建议调仓 但 need_rebalance=False”的矛盾。
REBALANCE_PP = 5.0


class RebalanceAdvisor:
    """辅助调仓顾问"""

    def __init__(self, db: Database):
        self.db = db
        self.thermometer = MarketThermometer(db)
        self.screener = FundScreener(db)

    # =================================================================
    # 主接口
    # =================================================================

    def analyze(self, total_capital: float = None, cash_reserve: float = 0.0) -> dict:
        """
        分析当前持仓，生成调仓建议。

        Args:
            total_capital: 总资金；不传则 = 持仓市值 + cash_reserve（投资计划的现金弹药）
            cash_reserve: 计划里的现金底仓，作为未投资部分的资金

        Returns:
            dict: 包含完整的调仓方案
        """
        # 1. 获取数据
        temp = self.thermometer.get_temperature()
        holdings = self.db.get_current_holdings()

        # 2. 计算当前状态（总资金按"市值 + 现金弹药"口径，不再拍脑袋乘系数）
        portfolio_value = self._calc_portfolio_value(holdings)
        if total_capital is None:
            total_capital = portfolio_value + float(cash_reserve or 0)
        cash = max(0.0, total_capital - portfolio_value)

        # 权益占比用**市值**（与持仓卡口径统一；旧版用成本 buy_amount，浮盈浮亏不进判断）
        equity_types = {"股票型", "混合型", "指数型", "QDII"}
        equity_mv = 0.0
        for h in holdings:
            info = self._get_fund_info(h["fund_code"])
            ftype = info.get("fund_type", "") if info else ""
            if any(et in ftype for et in equity_types) or "股票" in ftype or "混合" in ftype:
                equity_mv += self._holding_value(h)
        current_equity_pct = (equity_mv / total_capital * 100) if total_capital > 0 else 0

        target_equity_pct = temp["target_equity_pct"]

        # 温度数据不足（全维度缺失 → target_equity_pct 为 None）→ 目标仓位无法确定，
        # **不给任何调仓指令**（黑箱审计 F-02 的连带：旧实现会拿兜底 50.0 硬算出
        # "建议权益 35%" 并据此给出大额买卖指令）。
        if target_equity_pct is None:
            return {
                "current_equity_pct": round(current_equity_pct, 1),
                "target_equity_pct": None,
                "total_capital": round(total_capital, 2),
                "portfolio_value": round(portfolio_value, 2),
                "cash_available": round(cash, 2),
                "need_rebalance": False,
                "gap_pct": None,
                "degraded": ["temperature"],
                "temperature": temp,
                "instructions": [],
                "summary": {
                    "verdict": "温度数据不足，暂不给出调仓建议",
                    "detail": "PE/PB/ERP/量能/情绪五个维度都没有数据 → 目标仓位无法确定。"
                              "请先完成数据采集（python src/main.py collect / index）。",
                },
            }

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
            "need_rebalance": abs(current_equity_pct - target_equity_pct) > REBALANCE_PP,
            "gap_pct": round(target_equity_pct - current_equity_pct, 1),
            "temperature": temp,
            "instructions": [self._serialize_instruction(i) for i in instructions],
            "summary": summary,
        }

    # =================================================================
    # 核心逻辑
    # =================================================================

    def _assess_holdings(self, holdings: list) -> list:
        """逐只持仓做风险评估并排序：高风险+亏损优先处理"""
        fund_risks = []
        for h in holdings:
            info = self._get_fund_info(h["fund_code"])
            result = self.screener._screen_single_fund(h["fund_code"], info) if info else None
            risk = result if result else {"risk_label": "未知", "risk_reasons": [], "metrics": {}}

            bought = h["buy_amount"]
            current_value = self._holding_value(h)
            pnl_pct = (current_value - bought) / bought * 100 if bought else 0

            fund_risks.append({
                "holding": h,
                "info": info,
                "risk": risk,
                "current_value": current_value,
                "pnl_pct": pnl_pct,
                "days_held": self._days_held(h["buy_date"]),
            })

        risk_order = {"🔴 高风险": 0, "🟡 注意": 1, "🟢 稳健": 2, "未知": 3}
        fund_risks.sort(key=lambda x: (
            risk_order.get(x["risk"].get("risk_label", "未知"), 3),
            x["pnl_pct"],
        ))
        return fund_risks

    def _build_reduce_instructions(self, fund_risks: list, gap_amount: float,
                                   total_cap: float, current_eq: float, target_eq: float) -> list:
        """减仓指令：权益过多时按风险排序卖出，多余资金转入固收"""
        instructions = []
        sell_needed = abs(gap_amount)

        for fr in fund_risks:
            if sell_needed <= 5:  # 少于5元就不调了
                break

            h = fr["holding"]
            risk_label = fr["risk"].get("risk_label", "未知")
            reasons = fr["risk"].get("risk_reasons", [])

            if risk_label == "🔴 高风险":
                sell_ratio = 0.8
            elif risk_label == "🟡 注意":
                sell_ratio = 0.5
            elif fr["pnl_pct"] < -10:
                sell_ratio = 0.4
            else:
                sell_ratio = 0.3

            sell_amount = min(fr["current_value"] * sell_ratio, sell_needed)
            sell_amount = min(max(sell_amount, 10), sell_needed)  # ≥¥10起卖但不超过仍需卖出额

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

        return instructions

    def _build_increase_instructions(self, gap_amount: float, total_cap: float,
                                     temp: dict, current_eq: float, target_eq: float) -> list:
        """加仓指令：权益不足时从🟢稳健筛选池挑一只买入"""
        instructions = []
        buy_amount = gap_amount
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
        return instructions

    def _build_hold_instructions(self, fund_risks: list, total_cap: float) -> list:
        """持有指令：仓位偏差不大时全部继续持有"""
        instructions = []
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

    def _generate_instructions(self, holdings, temp, current_eq, target_eq,
                                total_cap, port_value, cash):
        """根据目标仓位与当前仓位的偏差生成买卖/持有指令。

        触发条件基于权益仓位偏差(百分点)而非金额：|偏差|>REBALANCE_PP 才调仓，与 need_rebalance 一致。
        否则持有。
        """
        if total_cap == 0:
            return []

        gap_pp = target_eq - current_eq                # 百分点偏差（正=权益不足需加仓）
        gap_amount = gap_pp / 100.0 * total_cap        # 折算成金额(元)，供买卖量用
        fund_risks = self._assess_holdings(holdings)

        if gap_pp < -REBALANCE_PP:   # 权益过多，需要减仓
            return self._build_reduce_instructions(fund_risks, gap_amount, total_cap, current_eq, target_eq)
        if gap_pp > REBALANCE_PP:    # 权益不足，可以加仓
            return self._build_increase_instructions(gap_amount, total_cap, temp, current_eq, target_eq)
        return self._build_hold_instructions(fund_risks, total_cap)

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

    def _holding_value(self, h: dict) -> float:
        """持仓市值 = 份额 × 最新净值；缺数据退回买入金额。"""
        nav = self._get_latest_nav(h["fund_code"])
        shares = h.get("shares", 0)
        return float(shares * nav) if nav and shares else float(h["buy_amount"])

    def _calc_portfolio_value(self, holdings):
        return round(sum(self._holding_value(h) for h in holdings), 2)

    def _get_fund_info(self, code):
        """单行主键查询（原先每次调 get_all_funds() 全表扫 27,852 行，46 次 ≈ 9s）"""
        try:
            return self.db.get_fund_info(code) or {}
        except Exception:
            return {}

    def _get_latest_nav(self, code):
        """LIMIT 1 取最新净值，不再把整段历史读进内存"""
        try:
            row = self.db.get_latest_fund_nav(code)
        except Exception:
            return None
        return float(row["unit_nav"]) if row and row.get("unit_nav") is not None else None

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
