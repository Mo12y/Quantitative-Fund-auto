"""用户约束层（B 层）—— **接口契约**（设计稿 §4.4，本次只留接口不实现）。

三层架构（见 `docs/基金推荐系统设计方案.md` §4.4）
------------------------------------------------
    第 1 层  客观参照系      `peer_percentile.py`     纯客观、零用户依赖
    第 2 层  用户约束        本模块（接口）           user_profile + user_holdings + 持仓重叠度
    第 3 层  推荐 = 第1层 ∩ 第2层约束

为什么"只留接口"
----------------
A（自用）→ B（个性化）→ C（上线）应当是**叠加，不是重写**。为此必须先钉死
第 2 层的输入/输出契约，让 B/C 实现时无需改动第 1 层。本模块只定义契约 + 空实现，
不写真实业务逻辑（持仓重叠度、类型偏好、风险偏好的具体算法在 B 层落地）。

架构纪律（必须遵守，违反即破坏叠加性）
------------------------------------
1. **第 1 层不得引用任何用户信息**：`peer_percentile.py` 不 import 本模块、不 import
   任何 user/profile/holding 概念；本模块**只消费第 1 层的输出 dict**，绝不反向调用第 1 层。
2. 本模块是**纯函数**：`apply_constraints` 不修改入参 `picks`（返回新结构），
   也不触碰数据库（需要运行时数据时由 `ctx` 传入，谁的数据谁负责注入）。

口径纪律（与项目铁律一致）
--------------------------
- **缺失/未实现必须显式声明，不得静默丢弃**：某个约束的 evaluator 未注册，或评估时
  缺数据 → 相关基金进 `skipped`（带 reason），**不是** dropped、也不是"当它通过"。
- dropped 语义 = "明确不通过某条约束"；skipped 语义 = "这条约束无法评估"。
  两者必须分开，否则"没算"会被当成"被淘汰"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# ── 约束种类（B 层要实现的三种，与设计稿 §4.4 一一对应）────────────────
HOLDING_OVERLAP = "holding_overlap"   # 持仓重叠度：候选 vs 已持，避免重复买同类/同赛道
TYPE_PREFERENCE = "type_preference"   # 组别/类型偏好：只保留用户偏好的参照系组（A/B/C/D...）
RISK_PREFERENCE = "risk_preference"   # 风险偏好：对指标上限/下限的个性化约束（如回撤 ≤ 某值）

# 全部合法 kind（校验用；将来新增约束在此登记）
ALL_KINDS = (HOLDING_OVERLAP, TYPE_PREFERENCE, RISK_PREFERENCE)

# ── 约束来源（谁提供的这条约束，决定"谁的数据谁负责注入"）────────────────
SOURCE_USER_PROFILE = "user_profile"
SOURCE_USER_HOLDINGS = "user_holdings"
SOURCE_MANUAL = "manual"


@dataclass(frozen=True)
class Constraint:
    """一条用户约束。

    kind        : 见上 ALL_KINDS
    params      : evaluator 所需的参数（如 type_preference 的 {"groups": ["A"]}）
    source      : 约束来源（user_profile / user_holdings / manual）
    description : 人类可读的一句话，用于 `dropped`/`skipped` 的 reason 前缀（可解释性，设计稿 §4.6）
    """
    kind: str
    params: dict = field(default_factory=dict)
    source: str = SOURCE_MANUAL
    description: str = ""


# ── evaluator 协议（B 层实现时按此签名注册）──────────────────────────────
# evaluator(item: dict, params: dict, ctx: dict) -> tuple[bool, str]
#   入参：item   第 1 层输出的一只基金（至少含 code；可能含 group/percentiles/type 等）
#         params 该条约束的参数
#         ctx    运行时上下文（由调用方注入：{"holdings": [...], "profile": {...}}）
#   返回：(通过?, 未通过时的 reason)。未通过才需要给 reason；通过时第二项可为空串。
#
# 约定：evaluator 必须**无副作用**、**可重入**；评估所需数据缺失时**抛 KeyError/返回 False
# 之外的第三种情况不允许** —— 数据缺失由调用方在注册前拦截，或 evaluator 自行返回
# (False, "数据缺失: ...")，由 apply_constraints 归类为 skipped（见 _classify）。

REGISTRY: dict[str, Callable] = {}


def register_constraint(kind: str, evaluator: Callable) -> None:
    """注册一条约束的实现（B 层落地时调用）。

    幂等：同名覆盖。kind 必须合法，否则 ValueError —— 提前暴露拼写错误，
    不让一个 typo 静默变成"这条约束永远 skipped"。
    """
    if kind not in ALL_KINDS:
        raise ValueError("未知约束类型 %r（合法值：%s）" % (kind, ", ".join(ALL_KINDS)))
    REGISTRY[kind] = evaluator


@dataclass
class ApplyResult:
    """apply_constraints 的返回：三层拆分。

    kept    = 通过全部已评估约束
    dropped = 明确不通过某条约束（含 reason）
    skipped = 至少一条约束无法评估（未实现 / 缺数据），未进 kept 也未进 dropped
    """
    kept: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kept": self.kept,
            "dropped": self.dropped,
            "skipped": self.skipped,
            "counts": {"kept": len(self.kept), "dropped": len(self.dropped),
                       "skipped": len(self.skipped)},
        }


def apply_constraints(picks: list[dict], constraints: list[Constraint],
                      ctx: Optional[dict] = None) -> ApplyResult:
    """第 3 层组合：推荐 = 第1层输出 ∩ 第2层约束。

    参数
    ----
    picks       : 第 1 层（peer_percentile / 推荐器）的输出，list[dict]，每项至少含 `code`
    constraints : 约束列表；**空列表 = 无约束 → 全部 kept**（pass-through，诚实，不猜）
    ctx         : 运行时上下文（holdings / profile），谁的数据谁注入；不传则约束需数据时
                  会被归类为 skipped（数据缺失显式声明，铁律 5）

    返回
    ----
    ApplyResult：kept / dropped / skipped 三分，**不修改入参 picks**（返回新 list）。
    """
    constraints = list(constraints or [])
    ctx = ctx or {}
    kept, dropped, skipped = [], [], []

    # 空约束 → 全保留（接口诚实语义：没给约束就别假装筛过）
    if not constraints:
        return ApplyResult(kept=list(picks), dropped=[], skipped=[])

    # 校验 kind 是否全部合法 + 是否已注册。未注册 → 该约束整体"无法评估"，
    # 影响的基金进 skipped（不得静默丢弃、也不得当成通过）。
    for c in constraints:
        if c.kind not in ALL_KINDS:
            # 非法 kind：直接对全部 pick 声明 skipped（不猜、不吞）
            reason = "约束类型 %r 未定义（合法值：%s）" % (c.kind, ", ".join(ALL_KINDS))
            skipped = [dict(p, skipped_reasons=[reason]) for p in picks]
            return ApplyResult(kept=[], dropped=[], skipped=skipped)

    for p in picks:
        dropped_by, skipped_by = [], []
        for c in constraints:
            ev = REGISTRY.get(c.kind)
            if ev is None:
                # 接口已定义但实现未注册（B 层待落地）→ skipped，显式声明
                skipped_by.append("「%s」约束未实现（仅接口，见 user_constraint.REGISTRY）"
                                  % (c.description or c.kind))
                continue
            try:
                ok, why = ev(dict(p), dict(c.params or {}), ctx)
            except Exception as e:  # evaluator 自身异常 → 也算"无法评估"，不崩整条管线
                skipped_by.append("约束「%s」评估异常：%s" % (c.description or c.kind, e))
                continue
            if not ok:
                dropped_by.append("「%s」：%s" % (c.description or c.kind, why or "未通过"))

        item = dict(p)
        if dropped_by:
            item["dropped_reasons"] = dropped_by
            dropped.append(item)
        elif skipped_by:
            item["skipped_reasons"] = skipped_by
            skipped.append(item)
        else:
            kept.append(item)

    return ApplyResult(kept=kept, dropped=dropped, skipped=skipped)


@dataclass
class BuildResult:
    """build_constraints_from_user 的返回。"""
    constraints: list = field(default_factory=list)
    note: str = ""


def build_constraints_from_user(user_profile: Optional[dict] = None,
                                holdings: Optional[list] = None) -> BuildResult:
    """把用户画像 / 持仓翻译成约束列表 —— **接口占位，B 层实现**。

    当前实现：**不翻译任何用户数据为约束**（返回空列表 + 显式 note）。
    为什么不静默翻译：用户约束的具体算法（持仓重叠度阈值、类型偏好映射、风险偏好
    到指标上下限的换算）属 B 层范围，本模块只钉住"输入→约束"的契约，不编造实现。

    参数
    ----
    user_profile : 用户画像 dict（如 {"risk_pref": "稳健", "type_pref": ["A"]}）
    holdings     : 用户当前持仓 list（用于持仓重叠度约束）

    返回
    ----
    BuildResult：constraints 为空 + note 说明"仅接口、未翻译"。
    B 层落地后：按 SOURCE_USER_PROFILE / SOURCE_USER_HOLDINGS 填充 constraints，
    并把已接/未接的数据源写进 note（缺失仍显式声明）。
    """
    note = "用户约束层（B）尚未实现：user_profile / holdings 未翻译为约束（仅接口）"
    if user_profile or holdings:
        note += "；本次调用传入了用户数据，但已被接口层忽略（不静默编造约束）"
    return BuildResult(constraints=[], note=note)
