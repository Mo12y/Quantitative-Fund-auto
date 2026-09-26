"""
持仓跟踪模块: 管理用户持仓，计算收益和风险指标。

功能:
- 添加/更新持仓
- 计算当前市值和浮动盈亏
- 计算组合收益率
- 风险指标（最大回撤、波动率等）
"""

import re
import threading

import pandas as pd
from datetime import date as _date
from typing import Optional

#: 业绩比较基准：沪深300（本地 index_daily 代码 000300，与 thermometer.HS300_CODE 一致）
HS300_CODE = "000300"
HS300_NAME = "沪深300"
from ..data.database import Database
from . import trade_rules

# 串行化写路径：Flask 以 threaded=True 运行，同一笔持仓可能被并发请求同时结算。
# 进程内一把锁 + Database.immediate() 的 BEGIN IMMEDIATE，双重保证"只结算一次"。
_WRITE_LOCK = threading.RLock()

# 监管下限（《流动性风险管理规定》）：持续持有期少于 7 日，赎回费不低于 1.5%
SHORT_HOLD_DAYS = 7
SHORT_HOLD_FEE = 0.015


def parse_redeem_fee(raw) -> Optional[float]:
    """尽力从 fund_info.redeem_fee 文本解析费率：取所有百分比中的最大值（最短持有档，最保守）。"""
    if not raw:
        return None
    try:
        pcts = [float(m) / 100.0 for m in re.findall(r"(\d+(?:\.\d+)?)\s*%", str(raw))]
    except Exception:
        return None
    pcts = [p for p in pcts if 0 < p <= 1]
    return max(pcts) if pcts else None


def redeem_fee_rate(redeem_fee_text, held_days: int) -> float:
    """赎回费率的**单一真源**：只对"持有 < 7 天"这一段计费（唯一明确且惩罚性的一档）。

    ≥7 天一律 0；<7 天取基金自身 redeem_fee 文本中解析出的最大百分比与监管下限 1.5% 孰高。
    其它模块（策略引擎等）必须从这里导入，不得再自建费率表。
    """
    if held_days >= SHORT_HOLD_DAYS:
        return 0.0
    rate = parse_redeem_fee(redeem_fee_text)
    return max(rate, SHORT_HOLD_FEE) if rate else SHORT_HOLD_FEE


# ---------------------------------------------------------------------
# 前端申购费（按份额类别）
# ---------------------------------------------------------------------

# A 类/未知份额的保守默认值。真实 A 类前端费率的采集属《数据源扩展计划书》阶段 1，
# 在此之前一律用这个默认值，宁可保守（高估成本）也不要低估。
DEFAULT_PURCHASE_FEE = 0.0015

# 份额类别字母（基金名称尾部）
_CLASS_LETTERS = frozenset("ABCDEHIORY")
# 这些尾串是**产品类型**不是份额类别，不能被误判（"LOF" 的最后一位是 F，
# 若按"取尾部大写字母"就会把 "LOF" 尾部读成 F/O 而误判）
_NOT_CLASS_SUFFIX = frozenset({"LOF", "ETF", "FOF", "QDII", "REIT", "ABS", "LOFT"})


def share_class(fund_name: str) -> Optional[str]:
    """从基金名称尾部推断份额类别（C/A/E/I...）；推断不出返回 None。

    例：`南方中证500ETF联接发起式C` → `C`；`易方达瑞富混合E` → `E`；`某某LOF` → None。
    """
    s = str(fund_name or "").strip().upper()
    m = re.search(r"([A-Z]+)\)?$", s)
    if not m:
        return None
    tok = m.group(1)
    if tok in _NOT_CLASS_SUFFIX:
        return None
    last = tok[-1]
    return last if last in _CLASS_LETTERS else None


def purchase_fee_rate(fund_name: str, default: float = DEFAULT_PURCHASE_FEE) -> float:
    """前端申购费率的**单一真源**（按份额类别判断）。

    C / E / I 类份额**不收前端申购费**（改收销售服务费，已从每日净值里扣除）→ 0。
    A 类与推断不出类别的 → 返回 `default`（保守默认）。

    依据：`016453` 费率页原文「买入费率（前端申购）: 0≤买入金额 → 0 费率」，
    最高申购费率实测 **0.00%**；用户 7/7 持仓均为 C 类份额。
    """
    if share_class(fund_name) in ("C", "E", "I"):
        return 0.0
    return default


class PortfolioTracker:
    """持仓跟踪器"""

    SHORT_HOLD_DAYS = SHORT_HOLD_DAYS
    SHORT_HOLD_FEE = SHORT_HOLD_FEE

    def __init__(self, db: Database):
        self.db = db
        self._fund_info_cache: dict = {}   # 进程内缓存：同一基金只查一次 fund_info（避免 N+1 全表扫描）
        self._cal = None                   # 交易日历缓存（懒加载）

    # ---------- 交易日历 / 工具 ----------

    def _calendar(self) -> set:
        """交易日历集合（懒加载；为空则 trade_rules 回退“跳过周末”）"""
        if self._cal is None:
            try:
                self._cal = self.db.get_trade_date_set()
            except Exception:
                self._cal = set()
        return self._cal

    @staticmethod
    def _today() -> str:
        return _date.today().isoformat()

    def _backfill_t1(self):
        """老持仓（历史录入、无 T+1 字段）补算一次确认日/起算日，并按确认日修正状态。

        只补**缺失**字段，不覆写已有确认日 —— 历史记录的口径偏差不回填，
        改由 `get_holdings_detail` 的 `legacy_rule_deviation` 在 UI 上标注。
        """
        rows = [dict(r) for r in self.db.conn.execute(
            "SELECT id, buy_date, fund_code FROM holdings "
            "WHERE (confirm_date IS NULL OR confirm_date = '') "
            "AND status IN ('holding','pending_confirm')")]
        for r in rows:
            try:
                info = self._get_fund_info(r["fund_code"]) or {}
                lag = trade_rules.confirm_lag_for(info.get("fund_type"), info.get("fund_name"))
                cd, acc = trade_rules.resolve_apply(
                    r["buy_date"], False, self._calendar(), lag)
                fields = {"apply_date": r["buy_date"], "confirm_date": cd, "accrual_start": acc}
                if trade_rules.holding_status(cd) == "pending_confirm":
                    fields["status"] = "pending_confirm"
                self.db.update_holding(r["id"], **fields)
            except Exception:
                continue
        # 状态对账：确认日仍在未来、却标着 holding 的老记录 → 待确认
        try:
            today = self._today()
            for r in self.db.conn.execute(
                    "SELECT id FROM holdings WHERE status = 'holding' "
                    "AND confirm_date IS NOT NULL AND confirm_date > ?", (today,)):
                self.db.update_holding(r[0], status="pending_confirm")
        except Exception:
            pass

    def reconcile(self, today: str = None) -> dict:
        """幂等对账：结算到期的待确认买入 / 待确认卖出（T+1 到日才生效）。

        设计要点（原 settle_pending 的三个问题一并修掉）：
        1. **不挂在读路径上** —— 只在显式触发（Web `POST /api/reconcile`、
           `/api/holdings/refresh`）或写操作之后调用，GET 不再产生写副作用；
        2. **用生效日（申请日/顺延）的精确净值定价** —— 该日净值没公布就保持
           `pending_confirm`/`sell_pending`，等下次对账，绝不用邻近日近似；
        3. **并发安全** —— 进程内写锁 + `BEGIN IMMEDIATE`，同一笔只会结算一次。

        幂等：重复调用不会重复扣减份额（已确认的流水不再是 pending）。
        """
        today = today or self._today()
        n_buy = n_sell = 0
        with _WRITE_LOCK:
            self._backfill_t1()
            rows = [dict(r) for r in self.db.conn.execute("SELECT * FROM holdings")]
            for h in rows:
                st = h.get("status")
                cd = h.get("confirm_date")
                if not cd or str(cd) > today:
                    continue
                # 定价日 = 生效日（申请日/顺延），不是确认日
                effective = trade_rules.effective_apply_date(
                    h.get("apply_date") or h.get("buy_date"), self._calendar())

                if st == "pending_confirm":
                    nav = self._get_nav_exact(h["fund_code"], effective)
                    if not nav or nav <= 0:
                        continue                      # 净值未公布 → 保持待确认
                    # 申购份额 = 净申购金额 / 净值（与 add_buy_transaction 同口径）。
                    # 费用在申请时已记入 transactions.fee；这里不回填历史流水的 fee（铁律 3）。
                    fee_rate = purchase_fee_rate(h.get("fund_name"))
                    net = float(h.get("buy_amount") or 0) / (1.0 + fee_rate)
                    shares = round(net / nav, 2)
                    with self.db.immediate():
                        self.db.update_holding(h["id"], buy_nav=nav, confirm_nav=nav,
                                               shares=shares, status="holding")
                        for tx in self.db.get_transactions(holding_id=h["id"]):
                            if tx["kind"] == "buy" and tx.get("status") == "pending_confirm":
                                # ⚠️ 必须一起回填 shares：只写 status/confirm_nav 会让买入流水的
                                # shares 永远停在 0.0，日后 get_realized_pnl 会把整只 lot 跳过
                                # → 该 lot 的卖出从「已实现收益」静默消失（实测漏计 ¥145.61）。
                                self.db.update_transaction(tx["id"], status="confirmed",
                                                           confirm_nav=nav, shares=shares)
                    n_buy += 1

                elif st == "sell_pending":
                    for tx in self.db.get_transactions(holding_id=h["id"]):
                        if tx["kind"] != "sell" or tx.get("status") != "pending_confirm":
                            continue
                        if str(tx.get("confirm_date") or "") > today:
                            continue
                        tx_effective = trade_rules.effective_apply_date(
                            tx.get("apply_date") or tx.get("confirm_date"), self._calendar())
                        nav = tx.get("confirm_nav") or self._get_nav_exact(h["fund_code"], tx_effective)
                        if not nav or nav <= 0:
                            continue                      # 净值未公布 → 保持待确认
                        with self.db.immediate():
                            res = self._apply_sell(h, tx, float(nav))
                            if not res:
                                continue
                            self.db.update_transaction(tx["id"], status="confirmed",
                                                       confirm_nav=float(nav), fee=res["fee"])
                        n_sell += 1
                        break
        return {"settled_buys": n_buy, "settled_sells": n_sell}

    def settle_pending(self, today: str = None) -> dict:
        """兼容旧调用名：等价于 reconcile()（幂等，可安全重复调用）。"""
        return self.reconcile(today)

    def _apply_sell(self, holding: dict, tx: dict, nav: float) -> Optional[dict]:
        """落账一笔卖出：更新份额/成本/状态，并算出这次赎回费。

        Returns: {shares, nav, gross, held_days, fee, remain}；份额不足时返回 None。
        """
        held = float(holding.get("shares") or 0)
        sell_shares = float(tx.get("shares") or 0)
        if sell_shares <= 0 and tx.get("amount") and nav:
            sell_shares = round(float(tx["amount"]) / nav, 2)
        if sell_shares <= 0:
            return None
        if held > 0:
            sell_shares = min(sell_shares, held)      # 卖出份额不可能超过持仓

        confirm_date = tx.get("confirm_date") or tx.get("apply_date")
        gross = round(sell_shares * nav, 2)
        held_days = self._sell_held_days(holding, confirm_date)
        fee = round(gross * self._redeem_fee_rate(holding.get("fund_code"), held_days), 2)
        remain = round(held - sell_shares, 2)

        if remain <= 1e-6:
            self.db.update_holding(holding["id"], status="sold", shares=0,
                                   sell_date=confirm_date, sell_amount=gross)
        else:
            # 部分卖出：按比例减少成本，保持盈亏口径正确
            cost = float(holding.get("buy_amount") or 0)
            new_cost = round(cost * (remain / held), 2) if held else cost
            # 还有别的待确认卖单才留在 sell_pending；否则回到 holding。
            # （原先部分卖出后状态永远停在 sell_pending，前端一直显示"卖出待确认"）
            still_pending = self._has_pending_sell(holding["id"], exclude_tx_id=tx.get("id"))
            self.db.update_holding(holding["id"], shares=remain, buy_amount=new_cost,
                                   status="sell_pending" if still_pending else "holding")

        return {"shares": round(sell_shares, 2), "nav": float(nav), "gross": gross,
                "held_days": held_days, "fee": fee, "remain": max(remain, 0.0)}

    def _has_pending_sell(self, holding_id: int, exclude_tx_id=None) -> bool:
        """该持仓是否还有别的待确认卖单"""
        q = ("SELECT COUNT(*) FROM transactions WHERE holding_id = ? "
             "AND kind = 'sell' AND status = 'pending_confirm'")
        params = [holding_id]
        if exclude_tx_id:
            q += " AND id != ?"
            params.append(exclude_tx_id)
        try:
            row = self.db.conn.execute(q, params).fetchone()
        except Exception:
            return False
        return bool(row and row[0])

    @staticmethod
    def _sell_held_days(holding: dict, sell_confirm_date) -> int:
        """持有天数 = 赎回确认日 − 申购确认日（行业口径）。

        缺确认日的老记录退回申请日/买入日；完全算不出时返回一个大数，宁可少收也不误收惩罚费。
        """
        start = holding.get("confirm_date") or holding.get("apply_date") or holding.get("buy_date")
        try:
            s = _date.fromisoformat(str(start)[:10])
            e = _date.fromisoformat(str(sell_confirm_date)[:10])
            return max(0, (e - s).days)
        except Exception:
            return 9999

    def _redeem_fee_rate(self, fund_code: str, held_days: int) -> float:
        """赎回费率（委托给模块级单一真源 redeem_fee_rate）。"""
        info = self._get_fund_info(fund_code) or {}
        return redeem_fee_rate(info.get("redeem_fee"), held_days)

    def get_portfolio_summary(self, reconcile: bool = False) -> dict:
        """
        获取当前组合概况（**默认纯读**）。

        Args:
            reconcile: True 时先做一次幂等对账（结算到期的待确认买卖）。默认 False ——
                GET 请求不应产生写副作用；对账请走显式入口（Web POST /api/reconcile、
                /api/holdings/refresh）或写操作之后。

        Returns:
            dict: {
                'total_invested': float,     # 在持投入金额
                'total_market_value': float, # 总市值（估算）
                'total_pnl': float,          # 未实现盈亏（浮动）
                'total_return_pct': float,   # 未实现收益率(%)
                'realized': dict,            # 已实现盈亏（已了结的卖出）
                'holdings_detail': list,     # 每只基金的详情
                'asset_allocation': dict,    # 资产配置
            }
        """
        if reconcile:
            try:
                self.reconcile()
            except Exception:
                pass
        holdings = self.db.get_current_holdings()
        try:
            realized = self.get_realized_pnl()
        except Exception:
            realized = {"total_pnl": 0.0, "total_gross": 0.0, "total_cost": 0.0,
                        "total_fee": 0.0, "count": 0, "sales": []}

        if not holdings:
            return {
                "total_invested": 0,
                "total_market_value": 0,
                "total_pnl": 0,
                "total_return_pct": 0,
                "holdings_detail": [],
                "asset_allocation": {},
                "has_holdings": False,
                "realized": realized,
            }

        total_invested = 0
        total_market_value = 0
        details = []

        for h in holdings:
            invested = h["buy_amount"]
            shares = float(h.get("shares") or 0)     # shares 列可空：None 不能当数字用
            total_invested += invested

            # 获取最新净值
            latest_nav = self._get_latest_nav(h["fund_code"])
            # 现金分红余额（设计稿 §6）：现金分红的钱**出了净值、进了现金**，
            # 不加上它这笔钱会在总资产里凭空消失 —— 比不记分红更糟。
            cash = float(h.get("cash_balance") or 0)
            if latest_nav is not None and shares > 0:
                current_value = shares * latest_nav + cash
                pnl = current_value - invested
                # pnl_pct = **金额法**：「我赚了多少」的展示口径（前端就显示这个）。
                # 分母 buy_amount 是剩余成本，分子用四舍五入到 2 位的 shares →
                # 与下面的 replay_pct（净值比值法）会差一个"份额舍入楔子"（约 0.1%，
                # ¥10 小额定投最明显）。两者都对，只是回答的问题不同：
                # pnl_pct 答"我的钱涨了多少"，replay_pct 答"这只基金净值涨了多少"。
                pnl_pct = (pnl / invested) * 100
            else:
                current_value = invested  # 无法获取净值时假设不变
                pnl = 0
                pnl_pct = 0

            total_market_value += current_value

            # 获取基金类型
            fund_info = self._get_fund_info(h["fund_code"])
            fund_type = fund_info.get("fund_type", "未知") if fund_info else "未知"

            # 净值曲线（近 60 个净值点，供单基金走势图；从收益起算日开始，保证链完整）
            start = h.get("accrual_start") or h.get("confirm_date") or h.get("buy_date")
            try:
                navs = self.db.get_fund_nav(h["fund_code"], start_date=start)
            except Exception:
                navs = []
            curve = [{"date": str(r.get("nav_date"))[5:], "nav": float(r.get("unit_nav") or 0)}
                     for r in navs[-60:] if r.get("unit_nav")]
            nav_latest_date = str(navs[-1].get("nav_date")) if navs else None

            # 按“确认净值 → 最新净值”重放（净值比值法，断更多天也精确）
            # replay_pct = **净值比值法**：与份额/金额无关，纯"基金净值涨跌"。
            # ⚠️ 口径纪律（F-05）：它**不是**"我赚了多少"，若要露出，必须显式标注为
            #    「基金净值涨跌（不含份额舍入）」，不得与 pnl_pct 并列显示而不加区分。
            cnav = h.get("confirm_nav") or h.get("buy_nav") or latest_nav
            replay_pct = ((latest_nav / cnav - 1) * 100) if (cnav and latest_nav) else 0.0

            # 定价日（生效日）：新流水按这一天的净值成交
            effective_date = trade_rules.effective_apply_date(
                h.get("apply_date") or h["buy_date"], self._calendar())

            # 历史口径偏差：老记录按旧规则（一律 T+1、且周末叠加 15:00 顺延）算出的
            # 确认链，与新规则（QDII T+2 / 非交易日不叠加 cutoff）不一致。**不回填**，
            # 只把偏差标出来让前端提示（铁律 3）。
            legacy_rule_deviation = None
            try:
                exp_confirm = trade_rules.resolve_apply(
                    h.get("apply_date") or h["buy_date"],
                    bool(h.get("apply_after_cutoff")), self._calendar(),
                    trade_rules.confirm_lag_for(fund_type, h.get("fund_name")))[0]
                if h.get("confirm_date") and str(h["confirm_date"]) != exp_confirm:
                    legacy_rule_deviation = {
                        "field": "confirm_date",
                        "stored": str(h["confirm_date"]),
                        "current_rule": exp_confirm,
                    }
            except Exception:
                legacy_rule_deviation = None

            # 待确认期（收益尚未起算）：用生效日净值 → 最新净值 给一个“预估涨跌”
            pending_est_pct = None
            if h.get("status") == "pending_confirm" and latest_nav:
                nav0 = self._get_nav_on_date(h["fund_code"], effective_date)
                if nav0 and nav0 > 0:
                    pending_est_pct = round((latest_nav / nav0 - 1) * 100, 2)

            # 待确认且净值未公布 → 前端提示“净值未公布”，而不是显示成 0 或空白
            nav_missing = False
            if h.get("status") == "pending_confirm":
                nav_missing = bool(str(h.get("confirm_date") or "") <= self._today()
                                   and not self._get_nav_exact(h["fund_code"], effective_date))

            details.append({
                "holding_id": h["id"],
                "fund_code": h["fund_code"],
                "fund_name": h.get("fund_name", h["fund_code"]),
                "fund_type": fund_type,
                "buy_date": h["buy_date"],
                "buy_amount": invested,
                "buy_nav": h.get("buy_nav"),
                "status": h.get("status", "holding"),
                "is_pending": h.get("status") == "pending_confirm",
                "apply_date": h.get("apply_date") or h["buy_date"],
                "confirm_date": h.get("confirm_date"),
                "accrual_start": h.get("accrual_start"),
                "confirm_nav": cnav,
                "effective_date": effective_date,
                "legacy_rule_deviation": legacy_rule_deviation,
                "nav_missing": nav_missing,
                "replay_pct": round(replay_pct, 2),
                "pending_est_pct": pending_est_pct,
                "curve": curve,
                "nav_latest_date": nav_latest_date,
                "current_nav": latest_nav,
                "shares": shares,
                "current_value": round(current_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "days_held": self._calc_days_held(h["buy_date"]),
                # —— 分红（设计稿 §6 前端部分）——
                # `current_value` / `pnl` 已含 `cash_balance`（现金分红的钱在现金里）；
                # 这两个字段透出去是为了让持仓行能**显示**现金余额与策略切换。
                "cash_balance": round(cash, 2),
                "dividend_policy": h.get("dividend_policy") or "reinvest",
            })

        total_pnl = total_market_value - total_invested
        total_return_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0

        # 资产配置
        allocation = self._calc_allocation(details)

        return {
            "total_invested": round(total_invested, 2),
            "total_market_value": round(total_market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "realized": realized,
            "holdings_detail": details,
            "asset_allocation": allocation,
            "has_holdings": True,
        }

    def add_buy_transaction(
        self,
        fund_code: str,
        fund_name: str,
        buy_date: str,
        amount: float,
        notes: str = "",
        after_cutoff: bool = None,
        apply_date: str = None,
    ) -> int:
        """
        记录一次买入，并按公募基金 T+1 规则落账。

        Args:
            buy_date: 买入(申请)日期 YYYY-MM-DD
            after_cutoff: 是否 15:00 后提交（默认 False）
            apply_date: 申请日（默认同 buy_date）

        Returns:
            新增持仓记录的 id
        """
        apply_date = apply_date or buy_date
        ac = bool(after_cutoff)
        cal = self._calendar()
        info = self._get_fund_info(fund_code) or {}
        # QDII/海外 → T+2 确认；其余 T+1。只影响确认日/起算日，不影响成交价。
        lag = trade_rules.confirm_lag_for(info.get("fund_type"), fund_name)
        # 定价日（生效日）= 申请日若是交易日就是它，否则顺延到下一交易日。
        # 公募规则：15:00 前下单按**当日(T日)净值**成交，份额 T+1 日确认（= 本模块开头的规则说明）。
        effective = trade_rules.effective_apply_date(apply_date, cal)
        confirm_date, accrual_start = trade_rules.resolve_apply(apply_date, ac, cal, lag)
        today = self._today()

        confirm_nav = None
        shares = 0.0
        status = "pending_confirm"
        # 前端申购费（单一真源 purchase_fee_rate，按份额类别）—— 权威口径（D1 费率页原文）：
        #   净申购金额 = 申购金额 / (1 + 申购费率)；申购费用 = 申购金额 − 净申购金额；
        #   申购份额   = 净申购金额 / T日基金份额净值。
        # 费用与净值无关，申请时即可入账；C/E/I 类费率 0 → 费用 0、份额与旧口径完全一致。
        fee_rate = purchase_fee_rate(fund_name)
        net_amount = amount / (1.0 + fee_rate)
        fee = round(amount - net_amount, 2)
        # 确认日到了 ⇒ 生效日净值已公布，可以算份额
        if str(confirm_date) <= today:
            confirm_nav = self._get_nav_exact(fund_code, effective)
            if confirm_nav and confirm_nav > 0:
                shares = round(net_amount / confirm_nav, 2)
                status = "holding"
            else:
                status = "pending_confirm"     # 净值未公布，等 reconcile()

        with _WRITE_LOCK:
            with self.db.immediate():        # 持仓与流水必须一起写，避免只落一半
                hid = self.db.add_holding({
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "buy_date": apply_date,
                    "buy_amount": amount,
                    "buy_nav": confirm_nav if status == "holding" else None,
                    "shares": shares,
                    "notes": notes,
                    "apply_date": apply_date,
                    "apply_after_cutoff": int(ac),
                    "confirm_date": confirm_date,
                    "confirm_nav": confirm_nav,
                    "accrual_start": accrual_start,
                    "status": status,
                })
                self.db.add_transaction({
                    "holding_id": hid, "fund_code": fund_code, "kind": "buy",
                    "apply_date": apply_date, "apply_after_cutoff": int(ac),
                    "confirm_date": confirm_date, "confirm_nav": confirm_nav,
                    "accrual_start": accrual_start, "shares": shares, "amount": amount,
                    "fee": fee,
                    "status": "confirmed" if status == "holding" else "pending_confirm",
                    "notes": notes,
                })
        return hid

    def record_sell(
        self,
        holding_id: int,
        sell_date: str,
        sell_amount: float = None,
        shares: float = None,
        after_cutoff: bool = None,
    ) -> bool:
        """
        记录一次卖出（遵守 T+1 确认、T+2 到账）。

        - 传 shares 且小于持仓份额 → 部分卖出；
        - 否则视为全部卖出（与命令行 sell 语义一致）。
        """
        row = self.db.conn.execute("SELECT * FROM holdings WHERE id = ?", (holding_id,)).fetchone()
        if not row:
            return False
        h = dict(row)
        ac = bool(after_cutoff)
        cal = self._calendar()
        # 赎回同样按**申请日(T日)净值**成交（与买入对称）
        effective = trade_rules.effective_apply_date(sell_date, cal)
        confirm_date, payout = trade_rules.resolve_sell(sell_date, ac, cal)
        today = self._today()
        held = float(h.get("shares") or 0)

        pending = str(confirm_date) > today
        confirm_nav = None
        sell_shares = float(shares) if shares else 0.0
        if not pending:
            confirm_nav = self._get_nav_exact(h["fund_code"], effective)
            if sell_shares <= 0 and confirm_nav and sell_amount:
                sell_shares = round(float(sell_amount) / confirm_nav, 2)
            if sell_shares <= 0:
                sell_shares = held          # 兜底：按全部卖出

        tx = {
            "holding_id": holding_id, "fund_code": h["fund_code"], "kind": "sell",
            "apply_date": sell_date, "apply_after_cutoff": int(ac),
            "confirm_date": confirm_date, "confirm_nav": confirm_nav,
            "accrual_start": payout, "shares": sell_shares,
            "amount": sell_amount, "status": "pending_confirm" if pending else "confirmed",
        }
        with _WRITE_LOCK:
            with self.db.immediate():       # 流水 + 持仓 + 状态必须一起落，避免只写一半
                tx_id = self.db.add_transaction(tx)
                if pending:
                    self.db.update_holding(holding_id, status="sell_pending")
                    return True
                # 已确认：立刻落账（含赎回费）
                nav_used = float(confirm_nav) if confirm_nav else (
                    (float(sell_amount) / sell_shares) if (sell_amount and sell_shares) else 0.0)
                if nav_used <= 0:
                    # 生效日净值未公布 → 不猜净值、不落账，留在待确认等对账
                    self.db.update_transaction(tx_id, status="pending_confirm")
                    self.db.update_holding(holding_id, status="sell_pending")
                    return True
                tx["id"] = tx_id
                res = self._apply_sell(h, tx, nav_used)
                if res:
                    self.db.update_transaction(tx_id, status="confirmed",
                                               confirm_nav=nav_used, fee=res["fee"])
                return True

    def get_portfolio_curve(self) -> dict:
        """
        组合整体累计涨跌曲线（第 13 项）。

        口径：
        - 每只持仓从**收益起算日**(accrual_start，缺省用 confirm_date/buy_date)开始计入；
        - 日期取所有持仓净值日期的**并集**，缺失日按“最近可用净值”前向填充；
        - 组合市值_t = Σ 份额_i × 净值_i(t)；成本_t = Σ 已起算持仓的投入金额；
        - 收益_t = 市值_t − 成本_t。

        待确认买入（份额为 0）暂不计入市值，单独统计数量提示。

        **未入仓口径（F-03）**：曲线只画"已经起算"的持仓，因此最终点**不含**
        ① 待确认买入、② 起算日晚于曲线末日的持仓。这两类统一按"**未入仓**"回报
        （`excluded_not_in` 笔 + `excluded_amount` 投入金额），由卡片写明，
        免得用户拿它跟顶部 KPI 的"持仓市值"（含全部持仓）对比时以为数字错了。
        """
        holdings = self.db.get_current_holdings()
        active, pending_rows = [], []
        for h in holdings:
            if h.get("status") == "sold":
                continue
            shares = float(h.get("shares") or 0)
            # 待确认买入：确认净值未定 → 不计入曲线（避免用未确认份额算出假收益）
            if h.get("status") == "pending_confirm" or shares <= 0:
                pending_rows.append(h)
                continue
            start = h.get("accrual_start") or h.get("confirm_date") or h.get("buy_date")
            active.append((h, shares, start))
        pending = len(pending_rows)

        def _not_in(last_date=None):
            """未入仓清单：待确认 + 起算日晚于曲线末日。"""
            extra = [h for h, _s, start in active
                     if last_date and start and last_date < str(start)]
            rows = pending_rows + extra
            return len(rows), round(sum(float(h.get("buy_amount") or 0) for h in rows), 2)

        if not active:
            n, amt = _not_in()
            return {"dates": [], "value": [], "cost": [], "pnl": [], "return_pct": [],
                    "benchmark": [], "benchmark_name": HS300_NAME,
                    "funds_used": 0, "excluded_pending": pending,
                    "excluded_not_in": n, "excluded_amount": amt}

        nav_maps = {}
        for h, _s, start in active:
            rows = self.db.get_fund_nav(h["fund_code"], start_date=start)
            nav_maps[h["id"]] = {str(r["nav_date"]): float(r["unit_nav"]) for r in rows if r.get("unit_nav")}

        all_dates = sorted({d for m in nav_maps.values() for d in m})
        if not all_dates:
            n, amt = _not_in()
            return {"dates": [], "value": [], "cost": [], "pnl": [], "return_pct": [],
                    "benchmark": [], "benchmark_name": HS300_NAME,
                    "funds_used": len(active), "excluded_pending": pending,
                    "excluded_not_in": n, "excluded_amount": amt}

        dates, values, costs, pnls, rets = [], [], [], [], []
        for d in all_dates:
            val, cost = 0.0, 0.0
            for h, shares, start in active:
                if start and d < str(start):
                    continue                       # 还没起算
                m = nav_maps[h["id"]]
                # 前向填充：取 <= d 的最近净值
                nav = None
                for k in m:
                    if k <= d and (nav is None or k > nav[0]):
                        nav = (k, m[k])
                if nav is None:
                    continue
                val += shares * nav[1]
                cost += float(h.get("buy_amount") or 0)
            if cost <= 0:
                continue
            pnl = val - cost
            dates.append(d)
            values.append(round(val, 2))
            costs.append(round(cost, 2))
            pnls.append(round(pnl, 2))
            rets.append(round(pnl / cost * 100, 2))

        n_notin, amt_notin = _not_in(all_dates[-1])
        return {"dates": dates, "value": values, "cost": costs, "pnl": pnls, "return_pct": rets,
                "benchmark": self._hs300_series(dates), "benchmark_name": HS300_NAME,
                "funds_used": len(active), "excluded_pending": pending,
                "excluded_not_in": n_notin, "excluded_amount": amt_notin}

    def _hs300_series(self, dates: list) -> list:
        """沪深300 在给定日期轴上的**收益率序列**（%，以首个可用日为 0）。

        依据（2026-09-25 加）：pyfolio 把 `benchmark_rets` 视为**一等参数**，其 tear sheet
        首图就是「策略 vs 基准 的累计收益对比」，并明确警告 returns 与 benchmark 的
        **日期索引必须对齐**，否则相对指标会被静默扭曲。
        故这里**按日期对齐**（指数收盘价前向填充，不用位置截断），并归一成
        "自曲线首日起的累计收益率 %" —— 与 `return_pct` 同量纲、可直接叠画。
        只读本地 `index_daily`（离线）；取不到返回空列表，前端不画（不编造）。
        """
        out = []
        if not dates:
            return out
        try:
            rows = self.db.get_index_daily(HS300_CODE)
        except Exception:
            return out
        closes = {str(r["trade_date"]): float(r["close"])
                  for r in (rows or []) if r.get("close") is not None}
        if not closes:
            return out
        import bisect
        ks = sorted(closes)
        base = None
        for d in dates:
            i = bisect.bisect_right(ks, str(d)) - 1
            if i < 0:                      # 曲线日期早于指数首个交易日
                out.append(None)
                continue
            c = closes[ks[i]]
            if base is None:
                base = c or None
            out.append(round((c / base - 1) * 100, 4) if base else None)
        return out

    def get_performance_history(self) -> pd.DataFrame:
        """
        计算组合历史绩效（每周一个数据点）。

        Returns:
            DataFrame: 每周组合净值
        """
        holdings = self.db.get_current_holdings()
        if not holdings:
            return pd.DataFrame()

        # 找出最早的买入日期
        all_dates = []
        for h in holdings:
            nav_records = self.db.get_fund_nav(h["fund_code"], start_date=h["buy_date"])
            for r in nav_records:
                all_dates.append(r["nav_date"])

        if not all_dates:
            return pd.DataFrame()

        unique_dates = sorted(set(all_dates))
        weekly_dates = unique_dates[::5]  # 每5个交易日取一个（约一周）

        # 对每个时间点计算组合总市值
        result = []
        for d in weekly_dates:
            total_value = 0
            for h in holdings:
                shares = h.get("shares", 0)
                nav = self._get_nav_on_date(h["fund_code"], d)
                if nav and shares:
                    total_value += shares * nav
                else:
                    total_value += h["buy_amount"]  # fallback

            result.append({
                "date": d,
                "total_value": round(total_value, 2),
            })

        return pd.DataFrame(result)

    # ========== 内部方法 ==========

    def _get_latest_nav(self, fund_code: str) -> Optional[float]:
        """
        获取基金最新净值 —— **只读本地 DB，不联网**。

        联网抓取只发生在显式的“更新净值”路径（Web `/api/nav/update` 或 CLI `nav`），
        读路径联网会在本机（Python 直连外网被重置）白白阻塞数秒。
        """
        try:
            row = self.db.get_latest_fund_nav(fund_code)
        except Exception:
            return None
        if row and row.get("unit_nav") is not None:
            return float(row["unit_nav"])
        return None

    def _get_nav_on_date(self, fund_code: str, target_date: str) -> Optional[float]:
        """取目标日期附近的净值（**只读本地 DB**）：精确日期优先，缺失时取最近一天。"""
        try:
            rec = self.db.closest_fund_nav(fund_code, target_date)
        except Exception:
            return None
        if rec and rec.get("unit_nav") is not None:
            return float(rec["unit_nav"])
        return None

    def _get_nav_exact(self, fund_code: str, nav_date: str) -> Optional[float]:
        """**精确日期**净值 —— 结算与定价专用。

        当天没公布就返回 None，绝不用邻近日近似：否则确认日净值未出时会用一个
        错误日期的净值静默落账，份额永久算错。
        """
        try:
            row = self.db.conn.execute(
                "SELECT unit_nav FROM fund_nav WHERE fund_code = ? AND nav_date = ?",
                (fund_code, str(nav_date)[:10])).fetchone()
        except Exception:
            return None
        return float(row["unit_nav"]) if row and row["unit_nav"] is not None else None

    def _get_fund_info(self, fund_code: str) -> Optional[dict]:
        """获取基金基本信息（单行主键查询 + 进程内缓存，避免全表扫描 N+1）"""
        if fund_code in self._fund_info_cache:
            return self._fund_info_cache[fund_code]
        try:
            info = self.db.get_fund_info(fund_code)
        except Exception:
            info = None
        self._fund_info_cache[fund_code] = info
        return info

    def _calc_days_held(self, buy_date: str) -> int:
        """计算持有了多少天"""
        try:
            buy = pd.to_datetime(buy_date)
            return (pd.Timestamp.now() - buy).days
        except Exception:
            return 0

    def _calc_allocation(self, details: list) -> dict:
        """计算资产配置比例"""
        total = sum(d["current_value"] for d in details)
        if total == 0:
            return {}

        allocation = {}
        for d in details:
            ftype = d["fund_type"]
            pct = d["current_value"] / total * 100
            if ftype not in allocation:
                allocation[ftype] = 0
            allocation[ftype] += round(pct, 1)

        return allocation

    # ========== 已实现盈亏 ==========

    def get_realized_pnl(self) -> dict:
        """已实现盈亏（已了结的卖出）。

        口径：按持仓记录（lot）重放它自己的买卖流水，卖出份额的成本用**比例成本**
        （与 _apply_sell 减成本的方式一致），因此
            单笔已实现 = 卖出毛额 − 卖出份额对应成本 − 赎回费

        这样 Σ已实现 + Σ未实现 = 总收益：未实现只统计仍在持的份额，两者不重不漏。
        """
        holdings = {r["id"]: dict(r) for r in self.db.conn.execute("SELECT * FROM holdings")}
        tx_by_holding = {}
        for t in self.db.conn.execute("SELECT * FROM transactions ORDER BY id"):
            tx_by_holding.setdefault(t["holding_id"], []).append(dict(t))

        def _buy_shares(t: dict) -> float:
            """买入流水代表的份额。

            直接取 `transactions.shares`；**若为 0 则按 SSOT 公式回退** `round(金额/确认净值, 2)`
            （与 `add_buy_transaction` / `reconcile` 的份额算法完全一致）。

            为什么必须回退：`reconcile()` 结算"下单时净值未公布"的买入时，历史实现
            **只回填持仓的 shares，没回填买入流水的 shares**，留下 0.0。
            于是本函数的 `shares <= 0: continue` 会把**整只 lot** 跳过 ——
            该 lot 的卖出就从「已实现收益」里静默消失（实测漏计 ¥145.61）。
            这是读路径自愈，**不改写任何历史记录**（铁律 2）。
            """
            s = float(t.get("shares") or 0)
            if s > 0:
                return s
            amt = float(t.get("amount") or 0)
            nav = float(t.get("confirm_nav") or t.get("buy_nav") or 0)
            if amt > 0 and nav > 0:
                return round(amt / nav, 2)
            return 0.0

        sales = []
        total_gross = total_cost = total_fee = 0.0
        for hid, txs in tx_by_holding.items():
            h = holdings.get(hid) or {}
            # 该 lot 的初始投入（买入流水）
            shares = sum(_buy_shares(t) for t in txs if t["kind"] == "buy")
            cost = sum(float(t.get("amount") or 0) for t in txs if t["kind"] == "buy")
            if shares <= 0:
                continue
            for t in txs:
                if t["kind"] != "sell" or t.get("status") != "confirmed":
                    continue
                ss = float(t.get("shares") or 0)
                nav = float(t.get("confirm_nav") or 0)
                if ss <= 0 or nav <= 0 or shares <= 0:
                    continue
                # 卖出成本 = 按份额比例摊。**上限是该 lot 的总成本** ——
                # 当「卖出份额 > 该 lot 推导份额」时（历史数据里卖出份额与买入流水不一致，
                # 例如 #43 卖出 43.40 份但买入流水净值陈旧只推出 42.68 份），
                # 不设上限会把成本摊到超过实际支付额（50.84 > 50），凭空造出一笔亏损。
                sold_cost = round(min(ss * (cost / shares), cost), 2)
                gross = round(ss * nav, 2)
                fee = round(float(t.get("fee") or 0), 2)
                pnl = round(gross - sold_cost - fee, 2)
                sales.append({
                    "holding_id": hid,
                    "fund_code": t.get("fund_code"),
                    "fund_name": h.get("fund_name") or t.get("fund_code"),
                    "apply_date": t.get("apply_date"),
                    "confirm_date": t.get("confirm_date"),
                    "shares": round(ss, 2), "nav": nav,
                    "gross": gross, "cost": sold_cost, "fee": fee, "pnl": pnl,
                })
                total_gross += gross
                total_cost += sold_cost
                total_fee += fee
                shares -= ss                      # 扣掉已卖份额，继续重放
                cost -= sold_cost

        sales.sort(key=lambda s: str(s.get("confirm_date") or ""), reverse=True)

        # —— 分红收益（设计稿 §6）：与买卖价差**分开统计**，两者相加才是总收益 ——
        # · `div_cash`     → `amount` 即实际收到的现金金额
        # · `div_reinvest` → 金额 = 新增份额 × 再投净值（存量口径 amount 记 0，故需换算）
        div_total, div_count = 0.0, 0
        div_rows = []
        for _hid, _txs in tx_by_holding.items():
            _h = holdings.get(_hid) or {}
            for _t in _txs:
                k = _t.get("kind")
                if k == "div_cash":
                    amt = float(_t.get("amount") or 0)
                    div_total += amt
                    div_count += 1
                    div_rows.append({
                        "date": _t.get("confirm_date") or _t.get("apply_date"),
                        "fund_code": _h.get("fund_code"),
                        "fund_name": _h.get("fund_name"),
                        "mode": "现金",
                        "per_share": _t.get("dividend_per_share"),
                        "shares": None,
                        "nav": None,
                        "amount": round(amt, 2),
                    })
                elif k == "div_reinvest":
                    amt = float(_t.get("shares") or 0) * float(_t.get("confirm_nav") or 0)
                    div_total += amt
                    div_count += 1
                    div_rows.append({
                        "date": _t.get("confirm_date") or _t.get("apply_date"),
                        "fund_code": _h.get("fund_code"),
                        "fund_name": _h.get("fund_name"),
                        "mode": "再投",
                        "per_share": _t.get("dividend_per_share"),
                        "shares": float(_t.get("shares") or 0),
                        "nav": _t.get("confirm_nav"),
                        # 再投流水的 `amount` 存量口径记 0（钱没出净值、直接变份额）→ 必须换算
                        "amount": round(amt, 2),
                    })

        div_rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)

        return {
            "total_gross": round(total_gross, 2),
            "total_cost": round(total_cost, 2),
            "total_fee": round(total_fee, 2),
            "total_pnl": round(total_gross - total_cost - total_fee, 2),
            "count": len(sales),
            "sales": sales,
            # —— 分红收益（设计稿 §6）：与**买卖价差分开列**，两者相加才是总收益 ——
            # `div_cash`    → amount 即收到的现金
            # `div_reinvest`→ 金额 = 新增份额 × 再投净值（存量口径：amount 记 0，故需换算）
            "dividend_total": round(div_total, 2),
            "dividend_count": div_count,
            "dividends": div_rows,
        }
