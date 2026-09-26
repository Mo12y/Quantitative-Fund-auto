"""§4.6 可解释性（推荐三问）的前端渲染契约 —— app.js / app.css 静态哨兵。

背景：本区块此前有一个**真 bug** —— 判据写成 `p.sharpe!==undefined`（读顶层字段，
而百分位在 `p.percentiles` 里）→ 该区块的百分位永远显示「同类 P—」。本文件同时锁住：
  1. 修复后的判据（`P.sharpe!=null`）；
  2. 三问明细块（为什么落选 / 缺什么没评估 / 约束计数）确实渲染；
  3. 样式类存在（.qh / .rec3row），避免"有 HTML 没样式"。
"""
import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "src" / "web" / "static"
APP_JS = (BASE / "app.js").read_text(encoding="utf-8")
APP_CSS = (BASE / "app.css").read_text(encoding="utf-8")


def _fn_body(name):
    """截取指定函数的源码（到下一个顶层 function/async function 为止）。"""
    m = re.search(rf"function {name}\(", APP_JS)
    assert m, f"function {name} not found in app.js"
    nxt = re.search(r"\n(?:async )?function ", APP_JS[m.end():])
    return APP_JS[m.start(): m.end() + nxt.start()] if nxt else APP_JS[m.start():]


def test_review_block_renders_rejected_and_unevaluated():
    body = _fn_body("constraintReviewHTML")
    assert "为什么落选" in body, "三问之二：为什么落选"
    assert "缺什么没评估" in body, "三问之三：缺什么没评估"
    assert "cr.dropped" in body and "cr.skipped" in body
    assert "约束前" in body and "未评估" in body, "计数必须展示（约束前 → 通过/落选/未评估）"
    assert "cr.error" in body, "约束未应用时必须显式声明，不得静默"


def test_reason_rows_escape_reasons():
    body = _fn_body("recReasonList")
    assert "esc(r)" in body, "reason 含基金名/板块名 → 必须转义"


def test_loadrec_wires_review_and_fixes_peer_tag():
    body = _fn_body("loadRec")
    assert "constraintReviewHTML(d.constraint_review)" in body, "/api/recommend 的审查块必须上屏"
    assert "P.sharpe!=null" in body, "判据改为 percentiles 块本身"
    assert "p.sharpe!==undefined" not in body, \
        "旧判据读的是不存在的顶层字段 → 百分位永远显示「同类 P—」（已修的 bug，不得回退）"
    assert "qChips(p)" in body, "三问之一：为什么入选须展示维度百分位"
    assert 'class="fund-row recrec"' in body, "候选行用 .recrec 布局（保底宽度，防被 meta 挤成 0 宽）"
    assert "没有候选通过全部约束" in body, "空结果要指向「落选/未评估」明细，而不是无解释的空"


def test_review_css_classes_exist():
    for cls in (".qh{", ".rec3row{", ".recrec{", ".recrec .fund-meta{"):
        assert cls in APP_CSS, f"缺少样式类 {cls}（有 HTML 无样式）"


# ── 主路径接入（§4.4 第 3 层）：筛选池标注 / 调仓候选过滤 ──────────────

def test_pool_marks_constraint_without_removing_rows():
    """池子是浏览面：被约束剔除的行要打标，但**不剔除**（行数不变由后端保证）。"""
    assert "constraintTag(f)" in _fn_body("poolRow"), "池内每行须带约束标注"
    tag = _fn_body("constraintTag")
    assert "u-pillwarn" in tag, "被约束剔除 → 警示 pill"
    assert "st==='skipped'" not in tag, \
        "未评估不逐行打标（实测 38/40 是噪声），改由汇总行计数 —— 见 poolConstraintLine"


def test_pool_header_shows_constraint_summary():
    assert "poolConstraintLine(F.constraint_review)" in _fn_body("poolHTML")
    assert "poolConstraintLine(d.constraint_review)" in _fn_body("boardPoolHTML"), \
        "按板块视图与按类型视图必须同一口径"
    line = _fn_body("poolConstraintLine")
    assert "只标注不剔除" in line and "被约束剔除" in line
    assert "无法判定重叠≠通过" in line, "未评估的语义必须写清（不得被读成通过）"


def test_rebalance_states_blocked_buy_explicitly():
    body = _fn_body("rebalanceHTML")
    assert "constraint_blocked_buy" in body, \
        "候选被用户约束挡住时**不得**显示『当前无需调仓』（会把'没算'说成'不用做'）"
    assert "rbConstraintBlock(RB)" in body, "买入候选的约束明细要可见（可折叠）"
    blk = _fn_body("rbConstraintBlock")
    assert "constraintReviewHTML(cr)" in blk, "复用同一套三问渲染（口径一致）"