# -*- coding: utf-8 -*-
"""
涨跌色回归守卫（旧前端）
=========================
用途：确保「方向」取色只用 --rise/--fall，且「状态」色没有被误改成 --rise/--fall。

背景（2026-09-30 修复）：app.css 原先只有 up/down 一对色，同时承担两种冲突语义：
  (a) 方向：P&L / 收益率 / 累计收益 —— 中国 A 股惯例 **涨红跌绿**
  (b) 状态：危险 / 成功 / 预警 / 风险等级 —— 红色=危险是普适的，不能反
up/down 取的是欧美惯例（绿=涨/红=跌），于是 (a) 全反：**盈利显示绿、亏损显示红**。
详见 docs/前端重构计划书.md §1.2（新前端因此改用 rise/fall 命名）。

判定启发式：
  · 出现 `var(--up|--down|--green|--red)`，且同一行的条件表达式里含
    pnl / ret / pct / mom / last / grand / estPct 这类**收益类变量** + 比较 0
    → 判为**方向点**，必须用 --rise/--fall。命中即 FAIL。
  · 其余为状态点，列出供人工复核（不断言失败）。

用法：python scripts/check_rise_fall.py     （退出码 0 = 通过）
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "src" / "web" / "static" / "app.css"
JS = ROOT / "src" / "web" / "static" / "app.js"

# 收益/方向类变量名（出现这些 + 与 0 比较 → 是方向点）
DIRECTION_VARS = re.compile(
    r"\b(pnl|ret|ret_1m|ret_3m|pct|mom|last|lastPnl|grand|estPct|total_pnl|return_pct)\b",
    re.I)
OLD_TOKENS = re.compile(r"var\(--(up|down|green|red)\)")
CMP_ZERO = re.compile(r"(>=|<=|>|<)\s*0\b")

# ── 已知方向选择器清单（硬断言）──
# 教训：第一版只靠「变量名 + 与 0 比较」的启发式，漏掉了 .u-bgup/.u-bgdown ——
# 它们没有条件表达式，但注释明写「零轴上方（盈利区间）」，是纯方向语义。
# 曲线渐变改了而图例没改会造成「图例与曲线自相矛盾」，故这里改为**硬清单**。
DIRECTION_SELECTORS = (
    ".pnl-pos", ".pnl-neg", ".pos", ".neg",
    ".u-trise", ".u-tfall", ".u-tgreen", ".u-tred",
    ".u-bgup", ".u-bgdown",
)
# 方向语义关键词（出现在同一行 → 该行若是旧 token 定义即为漏改）
DIRECTION_WORDS = re.compile(r"盈利|亏损|涨|跌|收益")


def scan(path: Path):
    direction_bad, status_ok = [], []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not OLD_TOKENS.search(line):
            continue
        reasons = []
        # 判据 1：条件表达式里含收益类变量并与 0 比较
        if CMP_ZERO.search(line) and DIRECTION_VARS.search(line):
            reasons.append("收益变量与0比较")
        # 判据 2：行内命中了已知方向选择器
        sel = [s for s in DIRECTION_SELECTORS if s in line]
        if sel:
            reasons.append("方向选择器 " + "/".join(sel))
        # 判据 3：行内含方向语义关键词
        #   注意：判据 3 只对 CSS 生效 —— JS 里 "收益" 常出现在无关的标签文案中，
        #   误报会淹没真问题（宁可把 JS 的这类交给人工复核清单）。
        if path.suffix == ".css" and DIRECTION_WORDS.search(line):
            reasons.append("含方向关键词")
        (direction_bad if reasons else status_ok).append((i, line.strip(), "; ".join(reasons)))
    return direction_bad, status_ok


def main():
    print("=" * 78)
    print("涨跌色回归守卫：方向点必须用 --rise/--fall")
    print("=" * 78)

    ok = True
    for path in (CSS, JS):
        rel = path.relative_to(ROOT)
        bad, good = scan(path)
        print("\n【%s】方向点残留 %d 处 | 状态点 %d 处" % (rel, len(bad), len(good)))
        if bad:
            ok = False
            print("  ❌ 以下位置仍是旧 token（应为 --rise/--fall）：")
            for i, l, why in bad:
                print("     %5d| [%s] %s" % (i, why, l[:100]))
        else:
            print("  ✅ 无方向点残留")
        if good:
            print("  ── 状态点（红色=危险，语义正确，仅供复核）──")
            for i, l, _why in good:
                print("     %5d| %s" % (i, l[:112]))

    # 正向检查：--rise/--fall 是否真的被定义了
    print("\n【token 定义】")
    css = CSS.read_text(encoding="utf-8")
    for tok in ("--rise", "--fall"):
        m = re.search(re.escape(tok) + r"\s*:\s*(#[0-9a-fA-F]{3,8})", css)
        if m:
            print("  ✅ %s = %s" % (tok, m.group(1)))
        else:
            print("  ❌ %s 未定义" % tok)
            ok = False
    # 语义校验：rise 必须是红（R 分量最大），fall 必须是绿（G 分量最大）
    def rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    r = re.search(r"--rise\s*:\s*(#[0-9a-fA-F]{6})", css)
    f = re.search(r"--fall\s*:\s*(#[0-9a-fA-F]{6})", css)
    if r and f:
        rr, rg, rb = rgb(r.group(1))
        fr, fg, fb = rgb(f.group(1))
        if rr > rg and rr > rb:
            print("  ✅ --rise 是红色系（涨=红，符合中国惯例）")
        else:
            print("  ❌ --rise 不是红色系 —— 涨跌色又反了")
            ok = False
        if fg > fr and fg > fb:
            print("  ✅ --fall 是绿色系（跌=绿，符合中国惯例）")
        else:
            print("  ❌ --fall 不是绿色系 —— 涨跌色又反了")
            ok = False

    print("\n" + "=" * 78)
    print("结论：" + ("✅ 通过" if ok else "❌ 未通过"))
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
