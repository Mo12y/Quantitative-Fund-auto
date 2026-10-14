"""分类口径（SSOT）—— 从 `fund_scorer` 剥出的**保留**口径（2026-10-14，S1'-b）。

为什么单独成模块
----------------
`fund_scorer` 属**已废止的「选基」命题层**（将 `git mv` 至 `legacy/`）；
但其中三样是**全系统复用的口径**，不能随它一起归档：

- `type_bucket` —— **全项目「权益类」的唯一定义（SSOT）**（`reporter` / `web` /
  `historical_recommender` / `strategy_engine` / `rebalance_advisor` 都复用）；
- `format_fee` —— 费率显示口径（缺失显示「无数据」，**不是** 0.00%）；
- 申购状态标记 `_BLOCKED_PURCHASE_MARKERS` / `_LIMITED_PURCHASE_MARKERS`。

剥离后本模块**保留**（归 `features/` 层），命题层即可安全冻结。
见 `docs/05-重构/00_重构蓝图.md` §四。
"""

# 申购状态分类（D2）——
#   `暂停申购` / `封闭期`：**买不进去**，直接排除出推荐池；
#   `限大额`：限额度 ≠ 不能买（每天 ¥10 定投完全合规）→ 保留，但标注单日上限；
#   空串 / NULL / 无法归类的文字：**视为未知**，沿用"无数据(跳过检查)"做法，
#   **不得静默当作可申购**。
_BLOCKED_PURCHASE_MARKERS = ("暂停申购", "封闭期")
_LIMITED_PURCHASE_MARKERS = ("限大额", "限购")


def type_bucket(fund_type: str) -> str:
    """粗分类型桶：权益（equity） / 非权益（bond）—— **全项目「权益类」的唯一定义（SSOT）**。

    匹配方式是**子串**（`kw in t`），因此：
      · "指数型-海外股票"（QDII 联接）→ 命中"指数" → equity ✓
      · "混合型-偏股" / "混合型-灵活配置" → 命中"混合" → equity ✓
      · "债券型-长债" / "货币型" → bond ✓

    ⚠️ 与它名字像、但**语义不同**的不要混用（2026-09-27 §8.4 逐一确认）：
      · `recsys_dataset._bucket` —— 五分类（index/equity/mixed/bond/qdii）；
      · `FundScreener.screen_funds(fund_types=...)` —— 筛选**范围参数**，不是资产类别判断。
    """
    t = str(fund_type or "")
    return "equity" if any(kw in t for kw in ("股票", "混合", "指数", "QDII")) else "bond"


def format_fee(mgt_fee) -> str:
    """费率显示文本：缺失或为 0 时显示「无数据」，而不是 0.00%。

    长历史基金的 `mgt_fee` 大量缺失；直接打印成 0.00% 会被读成"零费率"。
    """
    try:
        v = float(mgt_fee or 0)
    except (TypeError, ValueError):
        return "无数据"
    return f"{v:.2f}%" if v > 0 else "无数据"
