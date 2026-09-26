"""用户约束层（B 层）测试 —— 接口契约（2026-09-26 落地）+ 内置业务实现（同日落地）。

契约用例锁定的不是业务算法，而是**接口契约**本身：
  1. 空约束 = pass-through（不假装筛过）；
  2. 已注册约束：通过→kept、不通过→dropped（带 reason）；
  3. 未注册/非法 kind → **skipped（显式声明）**，绝不静默丢弃、也不当成通过；
  4. dropped 优先于 skipped（"明确不通过"不因"另有约束无法评估"而消失）；
  5. 纯函数：不修改入参；
  6. 架构纪律：第 2 层不反向依赖第 1 层（peer_percentile / fund_scorer）；
  7. 数据缺失（`missing()`）→ skipped，与 dropped 分开。

业务用例（Test*Builtin / Test*FromUser / end-to-end）锁定三条内置约束的口径：
组别白名单/黑名单、风险阈值（同类百分位 / 绝对）、持仓重叠（同板块占比 / 已持有）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import user_constraint as uc
from src.analysis.user_constraint import (
    Constraint, apply_constraints, build_constraints_from_user, register_constraint,
    HOLDING_OVERLAP, TYPE_PREFERENCE, RISK_PREFERENCE,
    SOURCE_USER_HOLDINGS, SOURCE_USER_PROFILE,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """测试里注册的 evaluator 不得泄漏到其他用例。"""
    saved = dict(uc.REGISTRY)
    uc.REGISTRY.clear()
    yield
    uc.REGISTRY.clear()
    uc.REGISTRY.update(saved)


PICKS = [
    {"code": "000001", "name": "甲", "group": "A", "percentiles": {"sharpe": 70.0}},
    {"code": "000002", "name": "乙", "group": "B", "percentiles": {"sharpe": 55.0}},
    {"code": "000003", "name": "丙", "group": "A", "percentiles": {"sharpe": 30.0}},
]


def _kept_codes(r):
    return [p["code"] for p in r.kept]


def test_empty_constraints_is_passthrough():
    """空约束 → 全部 kept，且**返回新结构、不修改入参**（纯函数契约）。"""
    src = [dict(p) for p in PICKS]
    r = apply_constraints(PICKS, [])
    assert _kept_codes(r) == ["000001", "000002", "000003"]
    assert r.dropped == [] and r.skipped == []
    assert PICKS == src, "不得修改入参"


def test_registered_constraint_drops_with_reason():
    """已注册 evaluator：不通过 → dropped，reason 含约束描述（可解释性，§4.6）。"""
    register_constraint(TYPE_PREFERENCE,
                        lambda item, params, ctx: (item.get("group") in params.get("groups", []), "不在偏好组"))

    c = Constraint(kind=TYPE_PREFERENCE, params={"groups": ["A"]}, description="只保留 A 组")
    r = apply_constraints(PICKS, [c])

    assert _kept_codes(r) == ["000001", "000003"], "A 组两只应保留"
    dropped = {p["code"]: p for p in r.dropped}
    assert list(dropped) == ["000002"]
    assert "只保留 A 组" in dropped["000002"]["dropped_reasons"][0]


def test_unregistered_constraint_skips_not_drops():
    """接口已定义但实现未注册 → skipped（显式声明），不是 dropped、不是 kept。"""
    c = Constraint(kind=HOLDING_OVERLAP, params={}, description="持仓重叠度")
    r = apply_constraints(PICKS, [c])
    assert r.kept == [] and r.dropped == []
    assert _kept_codes(r) == []
    assert len(r.skipped) == 3
    for p in r.skipped:
        assert any("未实现" in s for s in p["skipped_reasons"])


def test_unknown_kind_is_explicitly_skipped():
    """非法 kind（typo）→ 全部 skipped + 明确 reason，不静默吞掉。"""
    c = Constraint(kind="type_prefernce", params={})   # 拼错：preference → prefernce
    r = apply_constraints(PICKS, [c])
    assert r.kept == [] and r.dropped == []
    assert len(r.skipped) == 3
    assert any("未定义" in s for s in r.skipped[0]["skipped_reasons"])


def test_drop_priority_over_skip():
    """一条 drop + 一条 skip 同时命中 → 进 dropped（不因"另有无法评估"而降级为 skipped）。

    同时验证保守语义：只有"无法评估"约束的基金进 skipped（不能声称通过全部约束）。
    """
    register_constraint(TYPE_PREFERENCE,
                        lambda item, params, ctx: (item.get("group") == "A", "非 A 组"))
    cs = [
        Constraint(kind=TYPE_PREFERENCE, params={}, description="偏好 A 组"),
        Constraint(kind=HOLDING_OVERLAP, params={}, description="持仓重叠度"),  # 未注册 → skipped
    ]
    r = apply_constraints(PICKS, cs)
    dropped = {p["code"] for p in r.dropped}
    skipped = {p["code"] for p in r.skipped}
    assert dropped == {"000002"}, "B 组：既有 drop 又有 skip，必须进 dropped（drop 优先）"
    assert skipped == {"000001", "000003"}, "A 组：重叠度无法评估，不得假装通过 → skipped"
    assert r.kept == []


def test_evaluator_exception_is_skipped_not_crash():
    """evaluator 抛异常 → 该约束对这只基金算 skipped，不崩整条管线。"""
    def boom(item, params, ctx):
        raise RuntimeError("模拟实现 bug")
    register_constraint(RISK_PREFERENCE, boom)
    r = apply_constraints(PICKS, [Constraint(kind=RISK_PREFERENCE, description="风险偏好")])
    assert r.kept == [] and r.dropped == []
    assert len(r.skipped) == 3
    assert any("评估异常" in s for s in r.skipped[0]["skipped_reasons"])


def test_register_constraint_rejects_unknown_kind():
    with pytest.raises(ValueError):
        register_constraint("nope", lambda *a: (True, ""))


def test_build_constraints_no_data_is_explicit_pass_through():
    """无任何用户数据 → 空约束（pass-through）+ 显式 note；不假装筛过、不编造约束。"""
    r0 = build_constraints_from_user()
    assert r0.constraints == []
    assert "未生成任何约束" in r0.note and "pass-through" in r0.note

    r1 = build_constraints_from_user(user_profile={}, holdings=[])
    assert r1.constraints == []


def test_data_missing_reason_is_skipped_not_dropped():
    """evaluator 用 missing() 声明"算不了" → skipped（不是 dropped、不是 kept）。"""
    register_constraint(RISK_PREFERENCE, lambda i, p, c: uc.missing("没有指标"))
    r = apply_constraints(PICKS, [Constraint(kind=RISK_PREFERENCE, description="风险偏好")])
    assert r.kept == [] and r.dropped == []
    assert len(r.skipped) == 3
    assert any("数据缺失" in s for s in r.skipped[0]["skipped_reasons"])


def _import_lines(relpath: str) -> list:
    """返回源文件里的 import 行（from/import 开头），供纪律断言用。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, relpath), encoding="utf-8").read()
    return [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith(("import ", "from "))]


def test_layer2_does_not_import_layer1():
    """架构纪律：第 2 层不反向依赖第 1 层（peer_percentile / fund_scorer）。

    只查真实的 import 行（不查文档注释里的文件名提及）。违反 = 破坏 A→B→C 叠加性。
    """
    for banned in ("peer_percentile", "fund_scorer", "historical_recommender"):
        for ln in _import_lines("src/analysis/user_constraint.py"):
            assert banned not in ln, f"第 2 层不得 import 第 1 层组件：{banned}（{ln}）"


def test_layer1_does_not_import_layer2():
    """架构纪律：第 1 层（peer_percentile）不得引用用户约束/用户信息。"""
    for banned in ("user_constraint", "user_profile", "user_holdings"):
        for ln in _import_lines("src/analysis/peer_percentile.py"):
            assert banned not in ln, f"第 1 层不得引用用户信息：{banned}（{ln}）"


# =================================================================
# B 层内置业务实现（2026-09-26 落地）
# =================================================================

@pytest.fixture
def builtins():
    """业务用例显式注册三种内置约束。

    为什么需要：`_clean_registry`（autouse）会清空 REGISTRY，
    本 fixture 把内置实现装回来；契约用例则继续在"空注册表"下测。
    """
    uc.register_builtin_constraints()


#: 真实持仓的形状（同 `SELECT * FROM holdings`；此处取 4 只代表性持仓，金额=剩余成本）
#: 板块占比：半导体芯片 29.3% / 人工智能 29.3% / 黄金对冲 23.1% / 宽基指数 18.3%
REAL_LIKE_HOLDINGS = [
    {"fund_code": "017470", "fund_name": "嘉实上证科创板芯片ETF发起联接C", "buy_amount": 200.0},
    {"fund_code": "024663", "fund_name": "富国创业板人工智能ETF发起式联接C", "buy_amount": 200.0},
    {"fund_code": "018392", "fund_name": "南方上海金ETF联接C", "buy_amount": 157.29},
    {"fund_code": "007029", "fund_name": "易方达中证500ETF联接发起式C", "buy_amount": 125.0},
]


class TestHoldingOverlap:
    """持仓重叠度：同板块占比超限 / 已持有 → dropped；无法判定 → skipped。"""

    def test_same_board_over_cap_is_dropped(self, builtins):
        c = Constraint(HOLDING_OVERLAP, {"max_board_pct": 25.0}, description="持仓重叠")
        picks = [{"code": "X1", "name": "某某半导体芯片指数C"},
                 {"code": "X2", "name": "某某医药医疗混合C"}]
        r = apply_constraints(picks, [c], {"holdings": REAL_LIKE_HOLDINGS})
        assert _kept_codes(r) == ["X2"], "医药板块未持有 → 保留"
        assert [p["code"] for p in r.dropped] == ["X1"]
        assert "半导体芯片" in r.dropped[0]["dropped_reasons"][0]
        assert "29.3" in r.dropped[0]["dropped_reasons"][0], "reason 必须带实测占比"

    def test_held_code_is_dropped(self, builtins):
        c = Constraint(HOLDING_OVERLAP, {"max_board_pct": 100.0}, description="持仓重叠")
        r = apply_constraints([{"code": "017470", "name": "嘉实上证科创板芯片ETF发起联接C"}],
                              [c], {"holdings": REAL_LIKE_HOLDINGS})
        assert [p["code"] for p in r.dropped] == ["017470"]
        assert "已持有" in r.dropped[0]["dropped_reasons"][0]

    def test_exclude_held_false_keeps_it(self, builtins):
        """exclude_held=False：加仓场景允许候选就是已持有的那只。"""
        c = Constraint(HOLDING_OVERLAP, {"max_board_pct": 100.0, "exclude_held": False},
                       description="持仓重叠")
        r = apply_constraints([{"code": "017470", "name": "嘉实上证科创板芯片ETF发起联接C"}],
                              [c], {"holdings": REAL_LIKE_HOLDINGS})
        assert _kept_codes(r) == ["017470"]

    def test_no_holdings_in_ctx_is_skipped(self, builtins):
        """ctx 未注入持仓 → 算不了 → skipped（不是 dropped、不是 kept）。"""
        c = Constraint(HOLDING_OVERLAP, {"max_board_pct": 40.0}, description="持仓重叠")
        r = apply_constraints(PICKS, [c])
        assert r.kept == [] and r.dropped == []
        assert len(r.skipped) == 3
        assert any("数据缺失" in s for s in r.skipped[0]["skipped_reasons"])

    def test_unclassified_board_is_skipped(self, builtins):
        """板块归"其他"（关键词未命中）→ 不猜重叠 → skipped。"""
        c = Constraint(HOLDING_OVERLAP, {"max_board_pct": 40.0}, description="持仓重叠")
        r = apply_constraints([{"code": "Y1", "name": "某某灵活配置混合"}], [c],
                              {"holdings": REAL_LIKE_HOLDINGS})
        assert r.kept == [] and r.dropped == []
        assert "未归类" in r.skipped[0]["skipped_reasons"][0]

    def test_missing_cap_param_is_skipped(self, builtins):
        c = Constraint(HOLDING_OVERLAP, {}, description="持仓重叠")
        r = apply_constraints(PICKS, [c], {"holdings": REAL_LIKE_HOLDINGS})
        assert len(r.skipped) == 3


class TestTypePreference:
    """组别偏好：白名单 / 黑名单；组别缺失 → skipped。"""

    def test_allow_list(self, builtins):
        c = Constraint(TYPE_PREFERENCE, {"groups": ["A"]}, description="偏好 A 组")
        r = apply_constraints(PICKS, [c])
        assert _kept_codes(r) == ["000001", "000003"]
        assert [p["code"] for p in r.dropped] == ["000002"]
        assert "不在偏好组" in r.dropped[0]["dropped_reasons"][0]

    def test_exclude_list(self, builtins):
        c = Constraint(TYPE_PREFERENCE, {"exclude_groups": ["B"]}, description="排除 B 组")
        r = apply_constraints(PICKS, [c])
        assert _kept_codes(r) == ["000001", "000003"]
        assert "属于排除组" in r.dropped[0]["dropped_reasons"][0]

    def test_missing_group_is_skipped(self, builtins):
        c = Constraint(TYPE_PREFERENCE, {"groups": ["A"]}, description="偏好 A 组")
        r = apply_constraints([{"code": "Z1", "name": "无组别候选"}], [c])
        assert r.kept == [] and r.dropped == []
        assert "数据缺失" in r.skipped[0]["skipped_reasons"][0]


class TestRiskPreference:
    """风险偏好：同类百分位（item[percentiles]）/ 绝对阈值（item[metrics]）。"""

    def test_percentile_floor(self, builtins):
        c = Constraint(RISK_PREFERENCE, {"min_percentiles": {"sharpe": 60.0}},
                       description="夏普不差于 P60")
        r = apply_constraints(PICKS, [c])       # PICKS 的 sharpe 百分位：70 / 55 / 30
        assert _kept_codes(r) == ["000001"]
        assert "sharpe 同类百分位 55.0 < 下限 60.0" in r.dropped[0]["dropped_reasons"][0]

    def test_percentile_cap(self, builtins):
        c = Constraint(RISK_PREFERENCE, {"max_percentiles": {"sharpe": 60.0}}, description="夏普 ≤ P60")
        r = apply_constraints(PICKS, [c])
        assert _kept_codes(r) == ["000002", "000003"]

    def test_missing_metric_is_skipped(self, builtins):
        c = Constraint(RISK_PREFERENCE, {"min_percentiles": {"max_drawdown_1y": 50.0}},
                       description="回撤不差于 P50")
        r = apply_constraints(PICKS, [c])       # PICKS 只有 sharpe 百分位
        assert r.kept == [] and r.dropped == []
        assert all("数据缺失" in s for p in r.skipped for s in p["skipped_reasons"])

    def test_absolute_value_uses_metrics(self, builtins):
        c = Constraint(RISK_PREFERENCE, {"max_values": {"max_drawdown_1y": 25.0}},
                       description="回撤 ≤ 25%")
        picks = [{"code": "M1", "name": "甲", "metrics": {"max_drawdown_1y": 18.0}},
                 {"code": "M2", "name": "乙", "metrics": {"max_drawdown_1y": 32.0}}]
        r = apply_constraints(picks, [c])
        assert _kept_codes(r) == ["M1"]
        assert "32.00 超过上限 25.00" in r.dropped[0]["dropped_reasons"][0]

    def test_absolute_without_metrics_is_skipped(self, builtins):
        """候选没带 metrics → 算不了 → skipped，**不得**因为"看不见"就当成通过。"""
        c = Constraint(RISK_PREFERENCE, {"max_values": {"max_drawdown_1y": 25.0}},
                       description="回撤 ≤ 25%")
        r = apply_constraints(PICKS, [c])
        assert r.kept == [] and r.dropped == []
        assert len(r.skipped) == 3

    def test_no_thresholds_is_skipped(self, builtins):
        r = apply_constraints(PICKS, [Constraint(RISK_PREFERENCE, {}, description="空阈值")])
        assert r.kept == [] and len(r.skipped) == 3


class TestBuildConstraintsFromUser:
    """用户数据 → 约束的翻译（不猜语义，未识别键显式声明）。"""

    def test_profile_and_holdings_map_to_constraints(self, builtins):
        r = build_constraints_from_user(
            {"preferred_groups": ["A", "B"], "excluded_groups": ["F"],
             "min_percentiles": {"max_drawdown_1y": 50.0},
             "overlap_max_board_pct": 30},
            REAL_LIKE_HOLDINGS)
        kinds = [c.kind for c in r.constraints]
        assert kinds.count(TYPE_PREFERENCE) == 2
        assert RISK_PREFERENCE in kinds and HOLDING_OVERLAP in kinds
        assert "已生成 4 条约束" in r.note
        overlap = [c for c in r.constraints if c.kind == HOLDING_OVERLAP][0]
        assert overlap.params["max_board_pct"] == 30.0 and overlap.params["exclude_held"] is True
        assert overlap.source == SOURCE_USER_HOLDINGS
        assert [c for c in r.constraints if c.kind == TYPE_PREFERENCE][0].source == SOURCE_USER_PROFILE

    def test_no_holdings_no_overlap_constraint(self, builtins):
        """无持仓 → 不生成重叠约束（不猜），note 显式声明。"""
        r = build_constraints_from_user({"preferred_groups": ["A"]})
        assert [c.kind for c in r.constraints] == [TYPE_PREFERENCE]
        assert "未生成持仓重叠约束" in r.note

    def test_overlap_cap_defaults_and_overrides(self, builtins):
        d = build_constraints_from_user(holdings=REAL_LIKE_HOLDINGS)
        cap = [c for c in d.constraints if c.kind == HOLDING_OVERLAP][0].params["max_board_pct"]
        assert cap == uc.DEFAULT_MAX_BOARD_PCT
        o = build_constraints_from_user({"overlap_max_board_pct": 25}, REAL_LIKE_HOLDINGS)
        cap2 = [c for c in o.constraints if c.kind == HOLDING_OVERLAP][0].params["max_board_pct"]
        assert cap2 == 25.0

    def test_unknown_keys_and_risk_pref_text_are_declared(self, builtins):
        """未识别的画像键（含自由文本 risk_pref）→ 明确声明"已忽略/未映射"，不编造。"""
        r = build_constraints_from_user({"risk_pref": "稳健偏平衡", "type_pref": ["A"]})
        assert r.constraints == []
        assert "未识别的画像键已忽略：risk_pref、type_pref" in r.note
        assert "不编造" in r.note

    def test_end_to_end_user_data_to_decision(self, builtins):
        """完整链路：用户数据 → 约束 → 应用到候选（三条约束同时生效）。"""
        built = build_constraints_from_user(
            {"preferred_groups": ["A"], "max_values": {"max_drawdown_1y": 30.0},
             "overlap_max_board_pct": 25},
            REAL_LIKE_HOLDINGS)
        picks = [
            {"code": "P1", "name": "某半导体ETF联接C", "group": "A",
             "metrics": {"max_drawdown_1y": 20.0}},        # 板块重叠（29.3% ≥ 25%）
            {"code": "P2", "name": "某医药ETF联接C", "group": "A",
             "metrics": {"max_drawdown_1y": 20.0}},        # 全通过
            {"code": "P3", "name": "某消费ETF联接C", "group": "B",
             "metrics": {"max_drawdown_1y": 20.0}},        # 组别不符
            {"code": "P4", "name": "某医药ETF联接A", "group": "A",
             "metrics": {"max_drawdown_1y": 45.0}},        # 回撤超限
        ]
        r = apply_constraints(picks, built.constraints, {"holdings": REAL_LIKE_HOLDINGS})
        assert _kept_codes(r) == ["P2"]
        assert {p["code"] for p in r.dropped} == {"P1", "P3", "P4"}
        assert r.skipped == []
