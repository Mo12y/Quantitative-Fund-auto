"""用户约束层（B 层）—— 接口契约 + 内置业务实现（设计稿 §4.4）。

三层架构（见 `docs/基金推荐系统设计方案.md` §4.4）
------------------------------------------------
    第 1 层  客观参照系      `peer_percentile.py`     纯客观、零用户依赖
    第 2 层  用户约束        本模块                   user_profile + user_holdings + 持仓重叠度
    第 3 层  推荐 = 第1层 ∩ 第2层约束（`apply_constraints`）

本模块两部分（同一文件，契约与实现不分离 —— 改契约必须同时改实现）
------------------------------------------------------------------
1. **接口契约**（2026-09-26 落地）：`Constraint` / `apply_constraints` / `REGISTRY` /
   `build_constraints_from_user` 的输入输出语义，让 B/C 层实现时无需改动第 1 层。
2. **内置业务实现**（2026-09-26 落地）：三种约束的 evaluator（`eval_*`），import 本模块
   即自动注册；`build_constraints_from_user` 把用户画像/持仓翻译为约束（不猜语义）。

业务口径（每条都有来源，见各函数 docstring）
------------------------------------------
- `type_preference`：候选的参照系组别（A/B/C/D/E/F）白名单/黑名单；
- `risk_preference`：同类百分位下限/上限（读 `item["percentiles"]`，已按"越大越好"翻转）
  或绝对阈值（读 `item["metrics"]`，如回撤 ≤ 25%）；
- `holding_overlap`：候选板块（`fund_boards` 关键词映射）在现有持仓中的金额占比 ≥ 上限
  → 剔除；已持有同一只 → 剔除。**"其他"板块（关键词未命中）不猜重叠**→ skipped。

架构纪律（必须遵守，违反即破坏叠加性）
------------------------------------
1. **第 1 层不得引用任何用户信息**：`peer_percentile.py` 不 import 本模块、不 import
   任何 user/profile/holding 概念；本模块**只消费第 1 层的输出 dict**，绝不反向调用第 1 层。
2. 本模块是**纯函数**：`apply_constraints` 不修改入参 `picks`（返回新结构），
   也不触碰数据库（需要运行时数据时由 `ctx` 传入，谁的数据谁负责注入）。

口径纪律（与项目铁律一致）
--------------------------
- **缺失/未实现必须显式声明，不得静默丢弃**：evaluator 未注册、评估时缺数据、
  约束参数不完整 → 相关基金进 `skipped`（带 reason），**不是** dropped、也不是"当它通过"。
  evaluator 用 `missing("...")` 返回 `(False, "数据缺失: ...")`，由 `_classify` 归类为 skipped。
- dropped 语义 = "明确不通过某条约束"；skipped 语义 = "这条约束无法评估"。
  两者必须分开，否则"没算"会被当成"被淘汰"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import fund_boards

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

#: 数据缺失标记：evaluator 返回 (False, "数据缺失: ...") → apply_constraints 归类为 skipped。
#: 用它把"算不了"与"不通过"分开 —— 见模块 docstring 的口径纪律。
DATA_MISSING = "数据缺失"

#: 持仓重叠度的**默认**单板块占比上限（%，用户偏好值，不是统计阈值）。
#: 依据：项目背景里用户自述"喜好全仓科技/半导体、需要分散化提醒"，
#: 这里取 40% 作为"单板块视为集中"的默认线；用户可在画像里用
#: `overlap_max_board_pct` 覆盖。⚠️ 属**自定偏好值、待用户按实际风险承受力确认**，
#: 不是从数据分布校准出来的阈值（本项目吃过"拍脑袋阈值"的亏，故此处显式声明性质）。
DEFAULT_MAX_BOARD_PCT = 40.0

#: 用户画像（dict）中会被翻译为约束的键；**其余键一律显式声明"未识别、已忽略"**（不猜语义）。
#: - preferred_groups / excluded_groups : 参照系组别白名单 / 黑名单 → type_preference
#: - min_percentiles / max_percentiles  : 同类百分位下限 / 上限（越大越好口径）→ risk_preference
#: - max_values / min_values            : 指标绝对上限 / 下限（需 item["metrics"]）→ risk_preference
#: - overlap_max_board_pct              : 单板块占比上限（%）→ holding_overlap
#: - overlap_exclude_held               : 已持有的同一只基金是否剔除（默认 True）→ holding_overlap
PROFILE_KEYS = (
    "preferred_groups", "excluded_groups",
    "min_percentiles", "max_percentiles", "max_values", "min_values",
    "overlap_max_board_pct", "overlap_exclude_held",
)


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
# (False, "数据缺失: ...")（推荐用 `missing()` 构造），由 apply_constraints 的
# `_classify` 归类为 skipped。

REGISTRY: dict[str, Callable] = {}


def missing(reason: str) -> tuple:
    """evaluator 声明「这条约束**无法评估**」（→ skipped），区别「不通过」（→ dropped）。

    用法：`return missing("ctx 未提供持仓数据（holdings）")`
    """
    return False, "%s: %s" % (DATA_MISSING, reason)


def _classify(why: str) -> str:
    """把 evaluator 的未通过原因归类："skipped"（数据缺失/无法评估）或 "dropped"。"""
    return "skipped" if str(why or "").startswith(DATA_MISSING) else "dropped"


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
                # 接口已定义但实现未注册 → skipped，显式声明（不猜成通过、也不当淘汰）
                skipped_by.append("「%s」约束未实现（未注册 evaluator，见 user_constraint.REGISTRY）"
                                  % (c.description or c.kind))
                continue
            try:
                ok, why = ev(dict(p), dict(c.params or {}), ctx)
            except Exception as e:  # evaluator 自身异常 → 也算"无法评估"，不崩整条管线
                skipped_by.append("约束「%s」评估异常：%s" % (c.description or c.kind, e))
                continue
            if not ok:
                label = "「%s」" % (c.description or c.kind)
                if _classify(why) == "skipped":
                    # 数据缺失 / 参数不全 → "算不了"，不是"不通过"（口径纪律）
                    skipped_by.append("%s：%s" % (label, why))
                else:
                    dropped_by.append("%s：%s" % (label, why or "未通过"))

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


# =================================================================
# 内置业务实现（B 层，2026-09-26）
# =================================================================
# 口径来源：设计稿 §4.4「第 2 层 = user_profile + user_holdings + 持仓重叠度」。
# 三条纪律贯穿全部 evaluator：
#   ① 只读入参（item/params/ctx），不查库、不联网、无副作用；
#   ② 算不了就用 missing(...) → skipped，绝不当成通过、也不当成淘汰；
#   ③ 每条 reason 都带**具体数字或缺失原因**，供 §4.6 可解释性直接展示。

def eval_type_preference(item: dict, params: dict, ctx: dict) -> tuple:
    """组别偏好：候选的参照系组别过白名单 `groups` / 黑名单 `exclude_groups`。

    数据来源：`item["group"]`（第 1 层 `_peer_block` 给的 A/B/C/D/E/F 组别）。
    组别缺失（类型未映射 / 样本不足）→ 算不了 → skipped，不猜。
    """
    allow = params.get("groups")
    exclude = params.get("exclude_groups")
    if allow is None and exclude is None:
        return missing("约束未提供 groups / exclude_groups")
    group = item.get("group")
    if not group:
        return missing("候选无参照系组别（%s）" % (item.get("reason") or "未映射到组"))
    if allow is not None and group not in allow:
        return False, "组别 %s 不在偏好组 %s 内" % (group, "、".join(map(str, allow)))
    if exclude and group in exclude:
        return False, "组别 %s 属于排除组 %s" % (group, "、".join(map(str, exclude)))
    return True, ""


def eval_risk_preference(item: dict, params: dict, ctx: dict) -> tuple:
    """风险偏好：候选指标过个性化上下限。两套口径可同时使用。

    - **同类百分位**（推荐）：`min_percentiles` / `max_percentiles`，读 `item["percentiles"]`
      —— 该块已按"越大越好"翻转（见 `web.app._peer_block`），所以"回撤百分位 70"= 回撤控制
      优于组内 70% 的基金；缺某个被要求的指标 → skipped。
    - **绝对阈值**：`max_values` / `min_values`，读 `item["metrics"]`（原始指标，
      如 `max_drawdown_1y: 25.0` = 近 1 年最大回撤 25%，见 `fund_scorer`/`nav_metrics`）。
      候选没带 `metrics`（当前 `/api/recommend` 未注入）→ 算不了 → skipped，
      **不会**因为"看不见"就当成通过。
    """
    floors = params.get("min_percentiles") or {}
    caps = params.get("max_percentiles") or {}
    hi_vals = params.get("max_values") or {}
    lo_vals = params.get("min_values") or {}
    if not (floors or caps or hi_vals or lo_vals):
        return missing("约束未提供任何阈值（min_percentiles/max_percentiles/max_values/min_values）")

    if floors or caps:
        pcts = item.get("percentiles")
        if not pcts:
            return missing("候选无同侪百分位（%s）" % (item.get("reason") or "参照系未覆盖"))
        for name, lo in floors.items():
            if name not in pcts:
                return missing("缺 %s 的同侪百分位" % name)
            if pcts[name] < lo:
                return False, "%s 同类百分位 %.1f < 下限 %.1f" % (name, pcts[name], lo)
        for name, hi in caps.items():
            if name not in pcts:
                return missing("缺 %s 的同侪百分位" % name)
            if pcts[name] > hi:
                return False, "%s 同类百分位 %.1f > 上限 %.1f" % (name, pcts[name], hi)

    if hi_vals or lo_vals:
        metrics = item.get("metrics")
        if not metrics:
            return missing("候选无原始指标（item[metrics]），无法做绝对阈值判断")
        for name, hi in hi_vals.items():
            if metrics.get(name) is None:
                return missing("缺 %s 的原始指标值" % name)
            if metrics[name] > hi:
                return False, "%s=%.2f 超过上限 %.2f" % (name, metrics[name], hi)
        for name, lo in lo_vals.items():
            if metrics.get(name) is None:
                return missing("缺 %s 的原始指标值" % name)
            if metrics[name] < lo:
                return False, "%s=%.2f 低于下限 %.2f" % (name, metrics[name], lo)
    return True, ""


def eval_holding_overlap(item: dict, params: dict, ctx: dict) -> tuple:
    """持仓重叠度：候选 vs `ctx["holdings"]`（现有持仓，形状同 `SELECT * FROM holdings`）。

    两条判定（任一命中即剔除）：
    1. **同一只**：候选 `code` 已在持仓里（`exclude_held`，默认 True）；
    2. **同一板块**：候选板块在持仓中的金额占比 ≥ `max_board_pct`（%）。
       板块用 `fund_boards.classify`（基金名称关键词，项目既有口径）。

    口径声明：候选板块归入"其他"（关键词未命中）→ **不猜重叠** → skipped；
    持仓为空 / ctx 未注入 → skipped（数据缺失显式声明）。
    """
    holdings = ctx.get("holdings")
    if not holdings:
        return missing("ctx 未提供持仓数据（holdings）")
    cap = params.get("max_board_pct")
    if cap is None:
        return missing("约束未提供 max_board_pct（单板块占比上限）")

    code = str(item.get("code") or "")
    if params.get("exclude_held", True) and code:
        held = {str(h.get("fund_code")) for h in holdings if h.get("fund_code")}
        if code in held:
            return False, "已持有该基金（%s）" % code

    board = fund_boards.classify(item.get("name") or item.get("fund_name") or "")
    if board == fund_boards._OTHER:
        return missing("候选板块未归类（名称关键词未命中，不猜重叠）")
    weight = fund_boards.board_allocation(holdings).get(board, 0.0)
    if weight >= float(cap):
        return False, "板块「%s」已占持仓 %.1f%%（≥上限 %.1f%%）" % (board, weight, float(cap))
    return True, ""


def register_builtin_constraints() -> None:
    """注册三种内置约束（幂等）。**import 本模块时自动调用**。

    测试若清空 `REGISTRY` 换桩实现，可再次调用本函数恢复。
    """
    register_constraint(TYPE_PREFERENCE, eval_type_preference)
    register_constraint(RISK_PREFERENCE, eval_risk_preference)
    register_constraint(HOLDING_OVERLAP, eval_holding_overlap)


register_builtin_constraints()


@dataclass
class BuildResult:
    """build_constraints_from_user 的返回。"""
    constraints: list = field(default_factory=list)
    note: str = ""


def build_constraints_from_user(user_profile: Optional[dict] = None,
                                holdings: Optional[list] = None) -> BuildResult:
    """把用户画像 / 持仓翻译成约束列表（B 层业务实现，2026-09-26）。

    识别的画像键见 `PROFILE_KEYS`（模块顶部，含每个键的口径）。翻译规则：
    - `preferred_groups` / `excluded_groups` → 一条 `type_preference`（source=user_profile）；
    - `min/max_percentiles`、`max/min_values` → 一条 `risk_preference`（source=user_profile）；
    - **有持仓** → 一条 `holding_overlap`（source=user_holdings，阈值取
      `overlap_max_board_pct`，默认 `DEFAULT_MAX_BOARD_PCT`）；
      **无持仓** → 不生成（note 显式声明，不猜）。

    不猜语义的三种情况（都写进 note，不静默）：
    ① 无任何可用数据 → 空约束 + pass-through 声明；
    ② 未识别的画像键 → 列出键名并声明"已忽略"（尤其 `risk_pref` 这类自由文本，
       本项目没有经校准的"文字→阈值"映射表，**不编造**）；
    ③ 有持仓但 ctx 未注入 → 评估时由 `eval_holding_overlap` 归 skipped。

    参数
    ----
    user_profile : 用户画像 dict，如 {"preferred_groups": ["A","B"], "max_values": {"max_drawdown_1y": 25}}
    holdings     : 用户当前持仓 list（如 `SELECT * FROM holdings` 的行；空/None 视为无持仓）

    返回
    ----
    BuildResult：constraints + note（note 永远说明"生成了什么/忽略了什么/为什么没有"）。
    """
    profile = dict(user_profile or {})
    holdings = list(holdings or [])
    constraints, notes = [], []

    preferred = profile.get("preferred_groups")
    if preferred:
        constraints.append(Constraint(
            TYPE_PREFERENCE, {"groups": list(preferred)}, SOURCE_USER_PROFILE,
            "只保留偏好组：%s" % "、".join(map(str, preferred))))
    excluded = profile.get("excluded_groups")
    if excluded:
        constraints.append(Constraint(
            TYPE_PREFERENCE, {"exclude_groups": list(excluded)}, SOURCE_USER_PROFILE,
            "排除组：%s" % "、".join(map(str, excluded))))

    risk_params = {k: profile[k] for k in ("min_percentiles", "max_percentiles",
                                           "max_values", "min_values") if profile.get(k)}
    if risk_params:
        desc = "风险偏好：" + "；".join("%s=%s" % (k, v) for k, v in risk_params.items())
        constraints.append(Constraint(RISK_PREFERENCE, risk_params, SOURCE_USER_PROFILE, desc))

    if holdings:
        cap = float(profile.get("overlap_max_board_pct") or DEFAULT_MAX_BOARD_PCT)
        constraints.append(Constraint(
            HOLDING_OVERLAP,
            {"max_board_pct": cap,
             "exclude_held": bool(profile.get("overlap_exclude_held", True))},
            SOURCE_USER_HOLDINGS,
            "持仓重叠：单板块 ≤ %.0f%%（%d 只持仓）" % (cap, len(holdings))))
    else:
        notes.append("无持仓数据 → 未生成持仓重叠约束（不猜）")

    unknown = sorted(k for k in profile if k not in PROFILE_KEYS)
    if unknown:
        notes.append("未识别的画像键已忽略：%s（不猜语义）" % "、".join(unknown))
    if profile.get("risk_pref") and not risk_params:
        notes.append("risk_pref=%r 未自动映射为阈值（本项目无经校准的映射表，不编造）"
                     % profile["risk_pref"])

    if constraints:
        srcs = "、".join(sorted({c.source for c in constraints}))
        notes.insert(0, "已生成 %d 条约束（来源：%s）" % (len(constraints), srcs))
    else:
        notes.insert(0, "无用户画像/持仓数据 → 未生成任何约束（pass-through，不假装筛过）")
    return BuildResult(constraints=constraints, note="；".join(notes))
