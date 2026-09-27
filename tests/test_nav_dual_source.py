"""净值双源校验测试（数据源扩展计划书 阶段 2）。

验收条款："给出比对结果；并**故意注入一个日期错位**，证明校验器能抓到。"
另有两条本项目铁律要钉住：
  · **绝不覆盖主源** —— 校验模块不得包含任何写操作；
  · 差异**分级不静默** —— 舍入/轻微/严重/缺失分开计数，不揉成一个"不一致率"。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.nav_dual_source import (SEV_MAJOR, SEV_MINOR, SEV_MISSING_LOCAL,
                                          SEV_MISSING_REMOTE, SEV_OK, SEV_ROUNDING,
                                          classify, compare_series, detect_shift)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 验收核心：日期错位必须被识别 ──────────────────────────────────

def test_injected_date_shift_is_caught():
    """故意注入日期错位（远端整体比本地早一天）→ 校验器必须抓到并标出偏移。"""
    local = {"2026-01-05": {"unit_nav": 1.00},
             "2026-01-06": {"unit_nav": 1.01},
             "2026-01-07": {"unit_nav": 1.02}}
    remote = {"2026-01-05": {"unit_nav": 1.01},     # ← 把 01-06 的值放到了 01-05
              "2026-01-06": {"unit_nav": 1.02},     # ← 把 01-07 的值放到了 01-06
              "2026-01-07": {"unit_nav": 1.02}}

    r = compare_series(local, remote)

    assert r["n_shifted"] >= 1, "必须识别出日期错位（否则本校验器等于没用）"
    assert r["counts"][SEV_MAJOR] >= 2, "同日比对不上账应计为严重差"
    shifted = [a for a in r["alerts"] if a.get("shift_days")]
    assert shifted, r["alerts"]
    assert any(a["date"] == "2026-01-06" and a["shift_days"] == -1 for a in shifted), \
        "本地 01-06 的值等于远端 -1 天（01-05）的值 → 偏移应为 -1"
    assert "日期错位" in r["verdict"]


def test_shift_detector_returns_none_when_aligned():
    local = {"2026-01-06": {"unit_nav": 1.01}}
    remote = {"2026-01-06": {"unit_nav": 1.01}, "2026-01-05": {"unit_nav": 1.00}}
    assert detect_shift(local, remote, "2026-01-06", "unit_nav") is None


# ── 分级 ─────────────────────────────────────────────────────────

def test_classify_levels():
    assert classify(1.00000, 1.00004)[0] == SEV_OK            # 绝对差 ≤1e-4 → 舍入内
    assert classify(1.0000, 1.0005)[0] == SEV_ROUNDING        # 绝对差 5e-4 → 精度差异
    assert classify(1.000, 1.002)[0] == SEV_MINOR             # 0.2% ≤ 0.5%
    assert classify(1.000, 1.100)[0] == SEV_MAJOR             # 10% > 0.5%
    assert classify(1.0, None)[0] == SEV_MISSING_REMOTE
    assert classify(None, 1.0)[0] == SEV_MISSING_LOCAL


def test_counts_are_not_collapsed():
    """分级必须分开计数 —— 不能只给一个"不一致率"糊过去。"""
    local = {"d1": {"unit_nav": 1.0}, "d2": {"unit_nav": 1.0}, "d3": {"unit_nav": 1.0}}
    remote = {"d1": {"unit_nav": 1.0}, "d2": {"unit_nav": 1.002}, "d3": {"unit_nav": 2.0}}
    r = compare_series(local, remote)
    assert r["counts"][SEV_OK] == 1
    assert r["counts"][SEV_MINOR] == 1
    assert r["counts"][SEV_MAJOR] == 1


def test_missing_on_one_side_is_reported_not_ignored():
    local = {"d1": {"unit_nav": 1.0}}
    remote = {"d1": {"unit_nav": 1.0}, "d2": {"unit_nav": 1.0}}
    r = compare_series(local, remote)
    assert r["counts"][SEV_MISSING_LOCAL] == 1, "远端多出的日期 → 本地缺失"
    assert "缺失" in r["verdict"]
    # 反向：本地多出的日期 → 远端缺失
    r2 = compare_series(remote, local)
    assert r2["counts"][SEV_MISSING_REMOTE] == 1


def test_identical_series_is_clean():
    s = {"2026-01-05": {"unit_nav": 1.0, "acc_nav": 1.2},
         "2026-01-06": {"unit_nav": 1.1, "acc_nav": 1.3}}
    r = compare_series(dict(s), dict(s))
    assert r["counts"][SEV_MAJOR] == 0 and r["n_shifted"] == 0
    assert "一致" in r["verdict"]


def test_both_sides_missing_field_is_skipped():
    """两侧都没该字段 → 不是"差异"，不该计入（本项目的 acc_nav 常缺失）。"""
    r = compare_series({"d1": {"unit_nav": 1.0}}, {"d1": {"unit_nav": 1.0}})
    assert r["counts"][SEV_MISSING_REMOTE] == 0
    assert r["counts"][SEV_OK] == 1


# ── 铁律：绝不覆盖主源 ────────────────────────────────────────────

def test_module_never_writes():
    """校验模块**不得**包含任何写操作 —— 自动覆盖等于把"发现错误"换成"传播错误"。"""
    text = open(os.path.join(ROOT, "src/analysis/nav_dual_source.py"), encoding="utf-8").read()
    for banned in (r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bcommit\b", "Database"):
        assert not re.search(banned, text), f"校验模块不得出现 {banned}"
