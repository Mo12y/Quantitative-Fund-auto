"""
辅助调仓顾问: 对比"你实际持有的"和"你应该持有的"，输出具体的买卖指令。

输入: 当前持仓 + 市场温度 + 质量筛选结果
输出:
  - 你需要调仓吗？
  - 卖什么、卖多少、为什么先卖这个
  - 买什么、买多少
  - 调仓后的预期风险变化
"""

import math

import pandas as pd
from dataclasses import dataclass

from ..data.database import Database
from .thermometer import MarketThermometer
from .fund_scorer import FundScreener, type_bucket


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

    def __init__(self, db: Database, pool=None):
        """
        Args:
            pool: **可选**的预计算筛选池，用于避免重复跑全市场筛选。
                  允许两种形态：
                    · `screen_funds()` 原样输出的 DataFrame；
                    · 缓存载荷的 `funds` 列表（dict，键为 `code`/`name`/`risk`）。
                  不传则回退到 `self.screener.screen_funds()`（旧行为，向后兼容）。

        为什么要这个参数（2026-09-25）：`/api/all` 的 `funds` worker 已经算过一遍筛选池，
        而调仓顾问为了**挑一只基金**又独立跑了一次 `screen_funds` —— 那是把**全市场 1.8 万只**
        重算一遍（当前数据规模下约 85 秒）。同一个 dashboard 冷启动因此把最贵的计算做了两次。
        """
        self.db = db
        self.thermometer = MarketThermometer(db)
        self.screener = FundScreener(db)
        self._pool = pool                     # None = 自己算（旧路径，保留给 CLI / 测试）

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
        # 口径走 SSOT `type_bucket`：它与 `reporter` 的"当前权益占比"、`strategy_engine`
        # 的权益仓位是**同一件事**，全项目只此一个定义（§8.4，2026-09-27 统一；
        # 原实现内联了 `{"股票型","混合型","指数型","QDII"}` + 冗余的 `or "股票" in ftype`）。
        equity_mv = 0.0
        for h in holdings:
            info = self._get_fund_info(h["fund_code"])
            ftype = info.get("fund_type", "") if info else ""
            if type_bucket(ftype) == "equity":
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
        """逐只**基金**做风险评估并排序：高风险+亏损优先处理 —— **按基金聚合，不按批次**。

        ⚠️ 为什么必须聚合（2026-09-28 实测踩到）：`get_current_holdings()` 返回的是
        **批次**行，不是基金行 —— 用户 7 只基金有 **29 个批次**（016453 一只就有 21 个
        小额定投批次）。旧实现把每个批次当独立持仓，后果是：
          ① 同一只基金输出 21 条「卖出 ¥10」，指令噪声淹没结论；
          ② 叠加起卖下限 `max(sell_amount, 10)` 后**单批次被要求卖出超过自身市值的金额**
             （实测 2 个批次：市值 9.99 / 9.92 → 指令 ¥10.00）；
          ③ 30 条指令合计 **¥1108 > 账户总市值 ¥854.51**（卖出额超过整个账户）。
        聚合后：`current_value` = 该基金各批次市值之和，`pnl_pct` = 基金级盈亏率。

        同时保留每个批次的 `{days_held, value}`（`lots`），供赎回费的**先进先出**判定
        （见 `_sell_reaches_young_lots`）—— 聚合掉批次但**不丢**批次信息。
        """
        by_fund: dict = {}
        for h in holdings:
            code = h["fund_code"]
            v = self._holding_value(h)
            e = by_fund.setdefault(code, {"holding": h, "lots": [],
                                          "current_value": 0.0, "bought": 0.0})
            e["lots"].append({"days_held": self._days_held(h["buy_date"]), "value": v})
            e["current_value"] += v
            e["bought"] += float(h["buy_amount"] or 0)

        fund_risks = []
        for code, e in by_fund.items():
            info = self._get_fund_info(code)
            result = self.screener._screen_single_fund(code, info) if info else None
            risk = result if result else {"risk_label": "未知", "risk_reasons": [], "metrics": {}}
            bought = e["bought"]
            fund_risks.append({
                "holding": e["holding"],
                "info": info,
                "risk": risk,
                "current_value": e["current_value"],
                "pnl_pct": (e["current_value"] - bought) / bought * 100 if bought else 0,
                "lots": e["lots"],
            })

        risk_order = {"🔴 高风险": 0, "🟡 注意": 1, "🟢 稳健": 2, "未知": 3}
        fund_risks.sort(key=lambda x: (
            risk_order.get(x["risk"].get("risk_label", "未知"), 3),
            x["pnl_pct"],
        ))
        return fund_risks

    @staticmethod
    def _sell_reaches_young_lots(lots: list, sell_amount: float, min_days: int = 7) -> bool:
        """这次卖出是否会触及「持有 < `min_days` 天」的批次（决定有没有 1.5% 惩罚性赎回费）。

        判据按公募基金**先进先出**（FIFO）惯例：赎回先卖持有最久的份额 —— 所以只要
        「持有 ≥`min_days` 天的批次市值之和」能覆盖本次卖出额，就不产生惩罚费；
        覆盖不了的部分才落到年轻批次上。

        ⚠️ FIFO 是**行业惯例而非合同条款**（个别产品另有约定）→ 措辞用「将触及」，
        不给用户"一定免费"的错觉。
        """
        old_value = sum(l["value"] for l in lots if l["days_held"] >= min_days)
        return sell_amount > old_value + 0.005

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
            # 起卖下限 ¥10，但**必须同时不超过该基金自身市值** —— 否则会给出
            # "卖出 > 你持有"的指令（旧实现按批次聚合前实测踩到：批次市值 9.99 → 指令 10.00）
            sell_amount = min(max(sell_amount, 10), sell_needed, fr["current_value"])
            # ⚠️ **向下取整到元**（不是 round）：金额在此处还能是 9.99，但序列化时
            # `round(9.99, 0) == 10` 会把"卖超"重新引回来（本用例测试抓到的）。
            # 少卖一点点是保守方向（不会超卖、不会卖到手头没有的份额）。
            sell_amount = math.floor(sell_amount)
            if sell_amount < 1:      # 不足 ¥1 的"卖出"没有意义，跳过该基金（不消耗 sell_needed）
                continue

            reason_parts = []
            if risk_label in ("🔴 高风险", "🟡 注意"):
                reason_parts.append(f"{risk_label}基金")
            if reasons:
                reason_parts.append(reasons[0])
            if fr["pnl_pct"] < -5:
                reason_parts.append(f"已亏损{fr['pnl_pct']:.0f}%, 减仓控制风险")
            # 赎回费提示按 FIFO 判定（不是看单一 buy_date —— 用户是定投型，批次很多）
            if self._sell_reaches_young_lots(fr.get("lots") or [], sell_amount):
                reason_parts.append("本次卖出将触及持有<7天的批次, 有1.5%惩罚性赎回费——如果不急, 建议等满7天再卖")

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
        cands = self._pick_steady_candidates(10)      # 优先复用外部池，避免重复全市场筛选

        if cands:
            best = cands[0]
            instructions.append(RebalanceInstruction(
                action="买入",
                fund_code=best["fund_code"],
                fund_name=best.get("fund_name") or best["fund_code"],
                amount=round(min(buy_amount, total_cap * 0.3), 0),
                current_pct=round(current_eq, 1),
                target_pct=target_eq,
                reason=f"温度{temp['temperature']}°C, 权益仓位不足, 建议适度加仓。优先选🟢稳健的{best['fund_code']}",
                priority=1,
            ))
        return instructions

    def _pick_steady_candidates(self, n: int = 10) -> list:
        """从筛选池里取前 n 个"🟢稳健"候选（保持筛选器原序）→ [{'fund_code','fund_name'}]。

        · 有外部池（`pool=None` 之外）→ 直接用，**不再跑 `screen_funds`**；
          取前 `n` 条再过滤，与旧代码 `screen_funds(max_results=n)` 后过滤**等价**
          （缓存载荷正是 `screen_funds` 的原序输出）。
        · 无外部池 → 回退旧路径（CLI / 单测不受影响）。
        """
        if self._pool is None:
            df = self.screener.screen_funds(max_results=n)
            if df is None or df.empty:
                return []
            rows = df.to_dict("records")
        else:
            rows = self._pool
            if hasattr(rows, "to_dict"):            # DataFrame 也接受
                rows = rows.to_dict("records")
            rows = list(rows)[:n]                   # 与旧路径同一切口
        out = []
        for r in rows:
            label = r.get("risk_label") or r.get("risk") or ""
            if "稳健" not in str(label):            # 只挑 🟢 稳健（"稳健"二字足够稳）
                continue
            code = r.get("fund_code") or r.get("code")
            if not code:
                continue
            out.append({"fund_code": code,
                        "fund_name": r.get("fund_name") or r.get("name") or ""})
        return out

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
        """持仓市值 = 份额 × 最新净值 **+ 现金分红余额**；缺数据退回买入金额。

        ⚠️ 必须加 `cash_balance`（设计稿 §6）：现金分红的钱出了单位净值、进了现金，
        不加它会让**权益占比与调仓金额口径错**。
        """
        nav = self._get_latest_nav(h["fund_code"])
        shares = h.get("shares", 0)
        cash = float(h.get("cash_balance") or 0)
        return (float(shares * nav) if nav and shares else float(h["buy_amount"])) + cash

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
