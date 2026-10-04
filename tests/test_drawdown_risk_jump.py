# -*- coding: utf-8 -*-
"""回撤预警的「风险跃迁」加层（M5 变点检测接进 `drawdown_warning`）的守卫。

两条断言各钉住一件事：
  ① **库路径不存在时必须返回 None 且不建库** —— `Database(path)` 在路径不存在时会
     **静默新建空库**（铁律·绝对路径记的就是这个坑）；不挡住的话路径写错不报错，
     只会得到"全部历史不足"的假结果。2026-10-04 实测踩到过（在 `D:/nonexistent/`
     建了一个 135KB 空库，已清理）。
  ② **加层不进模型** —— 报告里必须有一节说明它是"并排佐证"，且明确写出
     "不进模型 / 属研究级变更"。防止后人顺手把它塞进 `FEATURE_COLS` 而没重跑验证。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.drawdown_warning import scan_risk_jumps  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")


def test_missing_db_returns_none_and_does_not_create(tmp_path):
    """① 路径不存在 → 返回 None 且**不建库**。"""
    p = str(tmp_path / "definitely_not_here.db")
    assert scan_risk_jumps(p, ["000001"], 1) is None
    assert not os.path.exists(p), "不能静默新建空库（铁律·绝对路径）"


def test_bad_path_does_not_raise(tmp_path):
    """加层失败**不许弄挂主流程** —— 它是"加一层"，不是主结论。"""
    assert scan_risk_jumps(str(tmp_path / "x" / "y.db")) is None


@pytest.mark.skipif(not os.path.exists(DB), reason="需要真实库")
def test_scans_holdings_and_dedupes():
    """② 缺省扫当前持仓；同一只基金的多个批次**必须去重**（`holdings` 是批次表）。"""
    r = scan_risk_jumps(DB, None, max_funds=8)
    assert r is not None and r["n"] >= 1
    codes = [x["code"] for x in r["results"]]
    assert len(codes) == len(set(codes)), "结果里有重复基金代码 → 没去重"
    assert all("alerts" in x and "flag" in x for x in r["results"])


@pytest.mark.skipif(not os.path.exists(DB), reason="需要真实库")
def test_not_a_model_feature():
    """② 变点**只能当报告里的并排佐证**，不许进模型特征列。"""
    import src.analysis.drawdown_warning as dw
    feats = getattr(dw, "FEATURE_COLS", [])
    for banned in ("changepoint", "cp_", "risk_jump", "regime"):
        assert not any(banned in str(f).lower() for f in feats), (
            "`%s` 类特征出现在 FEATURE_COLS 里 —— 把变点当模型输入是**研究级变更**，"
            "必须重跑 walk-forward + 置换检验并重新解释结论，不能顺手加。" % banned)
