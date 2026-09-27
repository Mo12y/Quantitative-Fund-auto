"""交易成本口径测试（数据源扩展计划书 §8.5）。

锁三件事：
  1. 优先级：**实测 purchase_fee** > 份额类别推断 > 保守默认（不得静默取默认）；
  2. 口径边界：管理费/托管费/销售服务费**已从净值扣**，不得进交易成本（防双算 —— 那正是阶段 0 T0-0 修掉的 bug）；
  3. 缺失显式声明：解析不出 / '---' → 走兜底且依据说明里写明。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.trading_cost import (DEFAULT_PURCHASE_FEE_PCT, assumption_note,
                                      one_way_cost_pct, purchase_cost_pct,
                                      redeem_cost_pct)


# ── 1. 买入成本优先级 ─────────────────────────────────────────────

def test_real_purchase_fee_wins():
    pct, why = purchase_cost_pct({"fund_name": "华夏成长混合A", "purchase_fee": 0.15})
    assert pct == pytest.approx(0.15)
    assert "实测" in why


def test_string_purchase_fee_is_tolerated():
    pct, _ = purchase_cost_pct({"fund_name": "X", "purchase_fee": "0.12%"})
    assert pct == pytest.approx(0.12)


def test_real_zero_is_not_treated_as_missing():
    """C 类实测 0.0 是**真值 0**，不能被当成"没采到"再退回默认。"""
    pct, why = purchase_cost_pct({"fund_name": "华夏纯债债券C", "purchase_fee": 0.0})
    assert pct == 0.0 and "实测" in why


def test_missing_falls_back_by_share_class():
    pct, why = purchase_cost_pct({"fund_name": "南方中证500ETF联接发起式C"})
    assert pct == 0.0 and "C" in why


def test_unparsable_marker_falls_back_conservatively():
    pct, why = purchase_cost_pct({"fund_name": "某某混合A", "purchase_fee": "---"})
    assert pct == pytest.approx(DEFAULT_PURCHASE_FEE_PCT)
    assert "保守默认" in why, "用了兜底必须写明依据"


def test_unknown_share_class_uses_default():
    pct, why = purchase_cost_pct({"fund_name": "某某LOF"})
    assert pct == pytest.approx(DEFAULT_PURCHASE_FEE_PCT)
    assert "份额类别" in why


# ── 2. 口径边界：不得双算持续费用 ─────────────────────────────────

def test_purchase_cost_ignores_ongoing_fees():
    """管理费/托管费/销售服务费已从每日净值扣 → 再进交易成本就是双算。"""
    base = {"fund_name": "华夏成长混合A", "purchase_fee": 0.15}
    fat = dict(base, mgt_fee=1.5, custodian_fee=0.25, sales_service_fee=0.6)
    assert purchase_cost_pct(base)[0] == purchase_cost_pct(fat)[0]
    assert one_way_cost_pct(base, 365)[0] == one_way_cost_pct(fat, 365)[0]


# ── 3. 赎回费（真源委托 portfolio.redeem_fee_rate） ────────────────

def test_short_hold_uses_penalty_rate():
    pct, why = redeem_cost_pct({"redeem_fee": None}, 3)
    assert pct >= 1.5, "持有 <7 天有监管下限 1.5% 的惩罚档"
    assert "<7" in why


def test_long_hold_is_free_when_no_rate():
    pct, _ = redeem_cost_pct({"redeem_fee": None}, 400)
    assert pct == 0.0


# ── 4. 单边成本 = 往返 ÷ 2 ────────────────────────────────────────

def test_one_way_is_half_round_trip():
    info = {"fund_name": "华夏成长混合A", "purchase_fee": 0.15, "redeem_fee": None}
    buy, _ = purchase_cost_pct(info)
    sell, _ = redeem_cost_pct(info, 400)
    got, why = one_way_cost_pct(info, 400)
    assert got == pytest.approx((buy + sell) / 2)
    assert "purchase_fee" in why or "实测" in why


# ── 5. 报告口径说明必须带警示（防"0.5% 被当成实测成本"） ──────────

def test_assumption_note_warns_it_is_not_measured():
    note = assumption_note()
    assert "保守" in note and "低估" in note
    assert "0.03" in note or "0.15" in note, "应给出实测中位做对照"
