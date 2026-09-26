"""用户约束层（B 层）接口契约测试 —— 设计稿 §4.4「只留接口不实现」。

锁定的不是业务算法（B 层才落地），而是**接口契约**本身：
  1. 空约束 = pass-through（不假装筛过）；
  2. 已注册约束：通过→kept、不通过→dropped（带 reason）；
  3. 未注册/非法 kind → **skipped（显式声明）**，绝不静默丢弃、也不当成通过；
  4. dropped 优先于 skipped（"明确不通过"不因"另有约束无法评估"而消失）；
  5. 纯函数：不修改入参；
  6. 架构纪律：第 2 层不反向依赖第 1 层（peer_percentile / fund_scorer）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import user_constraint as uc
from src.analysis.user_constraint import (
    Constraint, apply_constraints, build_constraints_from_user, register_constraint,
    HOLDING_OVERLAP, TYPE_PREFERENCE, RISK_PREFERENCE,
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


def test_build_constraints_is_interface_stub():
    """B 层未实现 → 返回空约束 + 显式 note；传了用户数据也不编造约束（铁律 5）。"""
    r0 = build_constraints_from_user()
    assert r0.constraints == [] and "仅接口" in r0.note

    r1 = build_constraints_from_user(user_profile={"risk_pref": "稳健"}, holdings=[{"code": "X"}])
    assert r1.constraints == []
    assert "忽略" in r1.note, "传了用户数据必须显式说明被忽略，不得静默"


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
