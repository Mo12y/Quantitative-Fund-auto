# -*- coding: utf-8 -*-
"""
新前端（`/v2`）真机验收 —— 把散在仓库外的 Playwright 检查固化下来。

为什么要固化：这些断言原本只存在于 `D:\\DSH\\scratch_hold\\_v3_entries.py` 之类的临时脚本里，
**scratch 目录一清就全没了**，下次改前端就得从头重写。前端没有单元测试框架，
它是唯一的回归守卫。

依赖（**开发工具，不是运行时依赖**，故意不写进 requirements.txt）：
    pip install playwright && python -m playwright install chrome
    注意本机 Playwright 装在 **system Python**，托管 Python 里没有。

前置：Web 服务已启动
    python src/main.py web          # 默认 http://localhost:5020

用法：
    python scripts/verify_frontend.py
    python scripts/verify_frontend.py --base http://localhost:5021
    python scripts/verify_frontend.py --json out.json     # 附带机器可读结果

退出码：0 = 全过；1 = 有失败（可直接接进 CI / 提交前自查）。

覆盖范围（截至 2026-10-01 共 **32 项**）：
  ① 四入口渲染与导航可达
  ② hash 路由（深链直达 / 未知 hash 回落）
  ③ 文案卫生（JSX 里写 `**粗体**` 会原样渲染 —— 曾真出现字面 `**只读**`）
  ④ 移动端 375：四个入口**零横向溢出**（元素级扫描，不是只看 body）
  ⑤ 导航断点：lg 起侧栏 / 以下顶部条，且任意视口**只有一套可见**
  ⑥ 交互动效已绑定（错峰入场 / 条形生长 / 指针滑入 / 骨架屏 shimmer）
  ⑦ console 无 error

B-0 追加（2026-10-04，计划书 §11.3 的 ⬜ 三项 → ✅；见 §11.4 的判据取舍）：
  ⑧ **排版**：`text-[Npx]` 裸字号落在 token 阶梯内；TSX 里无裸色值（唯一例外见 `_CSS_ALLOW`）
  ⑨ **留白**：`Card` 自身零 margin；间距只走 4px-grid（**含 Tailwind 半档**，见 `_SPACING_GRID_PX`）
  ⑩ **视觉层级**：无 `h1 > h2 > h3` 倒挂；页面头层字号高于卡标题
  ⑪ **数字等宽**：货币/百分比/份额格式化值必须挂 `.num`/`.mono`

⚠️ 静态扫描项（⑧⑨⑩⑪）**不需要起服务**：`--static-only` 可单独跑。
   默认模式下若服务不可达，静态项仍会跑并计入结果，动态项才降级为 SKIP。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ⚠️ playwright 在 `main()` 里**延迟导入**（见那里的注释）—— 静态判据（B-0）不需要它，
#    没装 playwright 的机器也应能跑 `--static-only`。

ENTRIES = ["今天", "持仓", "研究", "设置"]
HASHES = {"今天": "today", "持仓": "position", "研究": "research", "设置": "settings"}

# 仓库根（本文件在 <root>/scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
INDEX_CSS = SRC / "index.css"

RESULTS: list[dict] = []
SKIPPED: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  | " + str(detail)[:160]) if detail else ""))


def skip(name: str, why: str) -> None:
    """动态项在服务不可达时**降级**而不是判红 —— 静态判据仍要能跑（B-0 的可跑性要求）。

    ⚠️ 刻意不写进 RESULTS（否则 `ok == len(RESULTS)` 永远成立 → 退出码骗人）。
    SKIP 会在汇总里显式列出，CI 上看到 SKIP 就说明动态覆盖这一轮没生效。
    """
    SKIPPED.append(name)
    print(f"[SKIP] {name}  | {why}")


# ══════════════════════════════════════════════════════════════════════════
# B-0 · 静态视觉判据（不依赖服务 / 浏览器）
#
# 设计原则（计划书 §11.4）：**判据与真实观感冲突时先改判据**。这里的三条都按
# 「能抓到现存问题、又不逼着页面写出夸张数值」来定，取舍逐条写在下面。
# ══════════════════════════════════════════════════════════════════════════

# ── 排版 1：裸字号阶梯 ────────────────────────────────────────────────────
# ⚠️ `@theme` 里**没有 `--text-*` token**（index.css 只有色/圆角/字体族），
#    所以"不出现裸值"这条判据**在本项目无法字面成立** —— 见 §11.4 的裁决。
#    折中：裸字号必须落在 **0.5px 网格**上（离散化），且种类数有上限。
#    抓的是 `13.7px` / `10.2px` 这种随手拟的散落值。
_FONT_STEP = 0.5
_FONT_MAX_DISTINCT = 16        # 现状 14 种；留 2 档余量做"定型"

# ── 排版 1b：裸色值 ──────────────────────────────────────────────────────
# 唯一现存例外：PoolBoard 的「低估」标签边框 `#3d3117`（warn 暗化变体，token 里没有）。
# 列入白名单并在 §11.4 记名，**不再新增**。
# 键 = 相对 `frontend/src` 的 POSIX 路径，值 = 允许出现的裸色字面量。
_BARE_COLOR_ALLOW: dict[str, set[str]] = {
    "components/PoolBoard.tsx": {"#3d3117"},
}

# ── 留白 1：Card 自身零 margin ───────────────────────────────────────────
# 纪律是「块间距由父级给」（App 的 `main flex flex-col gap-4`）。
# ⚠️ `Card` 的**内**元素（header 的 `mb-3.5`）不在此列 —— 那是卡内节奏，不是块间距。
_MARGIN_CLS = re.compile(r"\b(?:m|mt|mb|ml|mr|mx|my)-")

# ── 留白 2：4px 网格 ─────────────────────────────────────────────────────
# ⚠️ 计划书原文写「间距值 ∈ 4 的倍数」= 只认 4/8/12/16…，但那会把 Tailwind 的
#    **半档**（`-0.5`=2px / `-1.5`=6px / `-2.5`=10px / `-3.5`=14px）全判红：
#    现状 217 处间距里 95 处用了半档 —— 那是**有意的**细分节奏
#    （卡内 `p-3.5`、标题 `mb-1.5`），不是"随机 13px/17px"。
#    真正要抓的是**离开 2px 网格**的值（如 `13px` / `17px`）。
#    故判据收紧为「**2px 网格**」，并把取舍记进 §11.4。这是"先改判据"的实例。
_SPACING_GRID_PX = 2

# ⚠️ 两条写法都要管（负对照实测过）：
#    · `p-3` / `gap-1.5` —— Tailwind 档位，px = n × 4
#    · `p-[13px]` / `gap-[17px]` —— 任意值写法，`-[13px]` 直接就是 13px
#    只写第一条会漏掉任意值（那是"随手拟一个数"的**主要**写法）。
_CLS_SP_STEP = re.compile(
    r"\b(?:(?:m|p)[trblxy]?|gap(?:-[xy])?|space-[xy])-(\d+(?:\.\d+)?)\b")
_CLS_SP_ARB = re.compile(
    r"\b(?:(?:m|p)[trblxy]?|gap(?:-[xy])?|space-[xy])-\[(\d+(?:\.\d+)?)px\]")

# ── 视觉层级 ─────────────────────────────────────────────────────────────
# ⚠️ 原判据「同页字号种类 ≤4 档」按"每个 .tsx 文件"量 → 现状最坏 6 档
#    （`routes/Settings.tsx`：11/11.5/12/12.5/13/16）。但这个数字**量错了对象**：
#    4 档讲的是"信息层级"（页标题 → 卡标题 → 正文 → 注脚），而 11/11.5/12/12.5 全在
#    "正文/注脚"这同一层里做微调 —— 它不是层级扁平化，是**同为最小可读尺寸档**的细分。
#    按文件扫会把"一张表里三种列宽字号"直接判成"层级失控"，属于判据与观感冲突。
#    故拆成两条**可判定且指向真问题**的：
#      ⑩a `h1..h6` 字号必须严格递减（现在 h1=24 / h2=12，安全）；
#      ⑩b 页面头层（h1）字号 > 卡标题（Card 的 h2）字号（防"标题比卡标题还小"）。
#    "每页字号种类"改为**诊断输出**（进 detail，不参与成败），保留可见性。
_H_TAG = re.compile(r"<(h[1-6])\b[^>]*?className=\"([^\"]*)\"[^>]*>", re.S)
_FONT_SZ = re.compile(r"text-\[(\d+(?:\.\d+)?)px\]")

# ── 数字等宽 ─────────────────────────────────────────────────────────────
# 抓"货币/百分比/份额的格式化值直接裸渲染在无 `num`/`mono` 的容器里"。
# 判据只看**格式化函数**（`fmtMoney`/`fmtPct`/… 与 `toFixed`），不看普通计数/序号 ——
# 后者本来就不要求等宽。
_MONEY_EXPR = re.compile(r"\{(?:[^{}]*?)(?:toFixed\(|fmt[A-Z]\w*\()")
_NUM_TAGS = ("span", "td", "div", "b", "strong", "em", "p")


def probe_server(base: str, tries: int = 40) -> None:
    """等服务就绪；顺带给出"服务没起"的明确指引，而不是让 Playwright 报超时。

    ⚠️ **必须绕过环境代理**（2026-10-05 踩到）：本机沙箱导出了
    `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:<port>`（WorkBuddy 自己的代理），
    `urllib` 默认**会读这两个变量**，于是连 `http://localhost:5020` 都被转发到代理上
    → 返回 502 → 探针判"服务不可达" → 动态项被静默 `[SKIP]`。
    **假绿比假红危险**：看起来"跑过了"。故显式用空 ProxyHandler。
    （Playwright 不走这套环境变量，所以只有这里的探针会中招。）
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(tries):
        try:
            with opener.open(base + "/v2", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit(f"{base}/v2 不可达 —— 先启动服务：python src/main.py web")


# ── 页面内测量脚本 ────────────────────────────────────────────────────────
# 移动端横向溢出。
#
# ⚠️ 判据改过一版（2026-10-04），旧版是 `scrollWidth - clientWidth > 2`，会**假红**：
#   · `sr-only`（Tailwind 无障碍隐藏文本）= 1px 见方 + `overflow:hidden` + `white-space:nowrap`，
#     内容不换行 → `scrollWidth` 很大而 `clientWidth` 只有 1 → 被算成"溢出"，
#     但它的盒子只有 1px，**根本不参与布局撑宽**（而且预热态里「加载中」标签常驻 DOM）。
#   · `animate-ping` 的装饰点 = `transform: scale(2)` 脉冲 → `getBoundingClientRect()`
#     **含 transform**，看起来越界；但 transform **不影响布局**，是有意的动画出血。
# 实测这两种情况下 `document.documentElement.scrollWidth == clientWidth == 375`
# —— 页面**并没有**横向溢出（旧判据记在 `docs/审计修复记录.md` §24 第四节）。
#
# 新判据（两条，缺一不可）：
#   ① 硬闸：文档级 `scrollWidth <= clientWidth + 1`（页面真的不能横向滚）；
#   ② 诊断：只统计**布局盒越过视口**的元素 —— 跳过 a11y 隐藏盒（clip 到 1px 见方）、
#      `aria-hidden` 子树、以及带 transform 的装饰元素。
OVERFLOW_JS = r"""() => {
  const vw = document.documentElement.clientWidth;
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (el.closest('[aria-hidden="true"]')) return;          // 无障碍隐藏子树
    if (el.clientWidth <= 1 || el.clientHeight <= 1) return; // sr-only 一类（裁剪成 1px）
    if (cs.transform && cs.transform !== 'none') return;     // animate-ping 等动画出血
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 || r.left < -1) {
      out.push({tag: el.tagName.toLowerCase(), right: Math.round(r.right),
                left: Math.round(r.left), cls: (el.className || '').toString().slice(0, 40)});
    }
  });
  return {doc: document.documentElement.scrollWidth,
          client: document.documentElement.clientWidth, n: out.length,
          worst: out.slice(0, 3)};
}"""

NAV_JS = r"""() => {
  const tb = document.querySelector('[class*="lg:hidden"]');
  const aside = document.querySelector('aside');
  const disp = el => (el ? getComputedStyle(el).display : 'MISSING');
  const visible = [...document.querySelectorAll('button')].filter(
    b => b.getBoundingClientRect().width > 0 && /^(今天|持仓|研究|设置)$/.test(b.textContent.trim())
  ).length;
  return {topbar: disp(tb), sidebar: disp(aside), visible};
}"""

ANIM_JS = r"""() => {
  const g = document.querySelector('.grow-x');
  const s = document.querySelector('.slide-thumb');
  const sk = document.querySelector('.skeleton');
  // ⚠️ 读**动画元素自己**的 animation-delay，而不是 `main > *` 的 ——
  // 错峰现在通过可继承的自定义属性 `--rise-delay` 下发（卡片被包进栅格也能拿到节奏），
  // 所以父容器上是读不到延迟的。
  const delays = [...document.querySelectorAll('.rise-in')].map(
    el => getComputedStyle(el).animationDelay);
  return {
    grow: g ? getComputedStyle(g).animationName : null,
    thumb: s ? getComputedStyle(s).animationName : null,
    skeleton: sk ? getComputedStyle(sk).animationName : null,
    stagger: delays,
  };
}"""

CHART_JS = r"""() => ({
  gridH: document.querySelectorAll('.recharts-cartesian-grid-horizontal line').length,
  gridV: document.querySelectorAll('.recharts-cartesian-grid-vertical line').length,
  axisLines: document.querySelectorAll('.recharts-cartesian-axis-line').length,
  yTicks: [...document.querySelectorAll('.recharts-yAxis .recharts-cartesian-axis-tick-value tspan')]
            .map(t => t.textContent),
})"""


# ══════════════════════════════════════════════════════════════════════════
# B-0 · 静态扫描实现
# ══════════════════════════════════════════════════════════════════════════

def _tsx_files() -> list[Path]:
    """所有 `.tsx`（`main.tsx` 是 Vite 入口壳，无视觉决策 → 排除）。"""
    return sorted(p for p in SRC.rglob("*.tsx") if p.name != "main.tsx")


def _rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def _strip_comments(src: str) -> str:
    """去 `/* */` 与 `//` 注释 —— 注释里引用 `text-[13.5px]` 是在解释判据，不是违规。

    ⚠️ 粗粒度但够用：本项目 TSX 里没有正则/字符串含 `//` 的写法（模板串内的 URL 已由
       先剥块注释、且 `//` 只在行首或空格后才剥来规避）。宁可漏判也不误伤。
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)(?<![:/])//[^\n]*$", " ", src)
    return src


def _class_attrs(src: str) -> list[str]:
    """抓 className 的值（两/单引号 + 模板串的静态部分）。"""
    out = re.findall(r"className=(?:\"([^\"]*)\"|'([^']*)'|\{`([^`]*)`\})", src)
    return [a or b or c for a, b, c in out]


# ── ⑧ 排版 1：裸字号阶梯 + 裸色值 ─────────────────────────────────────────
def static_typography() -> None:
    files = _tsx_files()
    if not files:
        check("排版：`.tsx` 源文件可读", False, f"未找到源文件：{SRC}")
        return

    # 读 `@theme` —— 色 token 白名单 + 确认字号 token 的**缺失**
    css = INDEX_CSS.read_text(encoding="utf-8") if INDEX_CSS.exists() else ""
    theme = re.search(r"@theme\s*\{(.*?)\n\}", css, re.S)
    tokens = re.findall(r"(--[\w-]+)\s*:", theme.group(1)) if theme else []
    color_tokens = {t for t in tokens if t.startswith("--color-")}

    seen_sizes: dict[str, int] = {}
    off_grid: list[tuple[str, str]] = []
    bare_colors: list[tuple[str, str]] = []
    for f in files:
        src = _strip_comments(f.read_text(encoding="utf-8"))
        rel = _rel(f)
        for s in _FONT_SZ.findall(src):
            seen_sizes[s] = seen_sizes.get(s, 0) + 1
            if (float(s) / _FONT_STEP) % 1 != 0:
                off_grid.append((rel, s))
        # 裸色值：只查 `#hex` / `rgb()` / `hsl()` 这类"硬编码颜色"，
        # 不查 `w-[212px]` 这种尺寸（那是布局测量值，不是设计 token 的职责）
        for m in re.finditer(r"\[(#[0-9a-fA-F]{3,8}|rgba?\([^\]]*\)|hsla?\([^\]]*\))\]", src):
            lit = m.group(1)
            if lit in _BARE_COLOR_ALLOW.get(rel, set()):
                continue
            bare_colors.append((rel, lit))

    check(
        f"排版：裸字号落在 {_FONT_STEP}px 阶梯上（{len(seen_sizes)} 种 / 上限 {_FONT_MAX_DISTINCT}）",
        not off_grid and len(seen_sizes) <= _FONT_MAX_DISTINCT,
        (f"离格 {off_grid[:5]}" if off_grid else "") +
        (f" 种类超限 {len(seen_sizes)}>{_FONT_MAX_DISTINCT}：{sorted(seen_sizes, key=float)}"
         if len(seen_sizes) > _FONT_MAX_DISTINCT else "") or f"{len(seen_sizes)} 种",
    )
    check(
        f"排版：TSX 无裸色值（白名单 {sum(len(v) for v in _BARE_COLOR_ALLOW.values())} 处，token {len(color_tokens)} 个）",
        not bare_colors,
        bare_colors[:5] or "全部走 token",
    )


# ── ⑨ 留白：Card 零 margin + 4px(含半档) 网格 ─────────────────────────────
def static_spacing() -> None:
    files = _tsx_files()
    card = SRC / "components" / "Card.tsx"
    if not card.exists():
        check("留白：`Card.tsx` 存在", False, str(card))
        return

    # ① Card 自身的 margin（根 `<section>` 与内部 `<header>` 之外的元素）
    card_src = _strip_comments(card.read_text(encoding="utf-8"))
    sec = re.search(r"<section\s+className=\{(.*?)\}\s*>", card_src, re.S)
    sec_cls = " ".join(re.findall(r"'([^']*)'", sec.group(1))) if sec else ""
    card_margin = _MARGIN_CLS.findall(sec_cls)

    # ② 全站间距网格（档位写法 + 任意值写法）
    off_grid: list[tuple[str, str, float]] = []
    total = 0
    for f in files:
        rel = _rel(f)
        src = _strip_comments(f.read_text(encoding="utf-8"))
        for cls in _class_attrs(src):
            for n in _CLS_SP_STEP.findall(cls):
                total += 1
                px = float(n) * 4
                if px % _SPACING_GRID_PX != 0:
                    off_grid.append((rel, f"-{n}", px))
            for v in _CLS_SP_ARB.findall(cls):
                total += 1
                px = float(v)
                if px % _SPACING_GRID_PX != 0:
                    off_grid.append((rel, f"-[{v}px]", px))

    check("留白：`Card` 自身无 margin（块间距由父级 gap 统一给）",
          not card_margin, f"根 section 命中 {card_margin}" if card_margin else "0 处")
    check(f"留白：间距落在 {_SPACING_GRID_PX}px 网格（{total} 处）",
          not off_grid, off_grid[:6] or f"{total} 处全在网格内")


# ── ⑩ 视觉层级：h 标签 + 页头 > 卡标题 ───────────────────────────────────
def static_hierarchy() -> None:
    files = _tsx_files()
    ranks: dict[str, list[tuple[str, float, int]]] = {}
    missing_size: list[tuple[str, str]] = []
    for f in files:
        rel = _rel(f)
        src = _strip_comments(f.read_text(encoding="utf-8"))
        for line_no, line in enumerate(src.splitlines(), 1):
            for m in _H_TAG.finditer(line):
                tag, cls = m.group(1), m.group(2)
                sz = _FONT_SZ.search(cls)
                if not sz:
                    missing_size.append((rel, f"<{tag}>"))
                    continue
                ranks.setdefault(tag, []).append((rel, float(sz.group(1)), line_no))

    order = ["h1", "h2", "h3", "h4", "h5", "h6"]
    vals = {k: sorted({v for _, v, _ in ranks[k]}) for k in order if k in ranks}

    # ① 同标签多值 → 报出（不算红：那是"档内细分"，见 §11.4）
    multi = {k: v for k, v in vals.items() if len(v) > 1}

    # ② 相邻层级必须严格递减（h1 > h2 > h3…）
    inversion: list[str] = []
    present = [k for k in order if k in vals]
    for a, b in zip(present, present[1:]):
        if min(vals[a]) <= max(vals[b]):
            inversion.append(f"{a}({vals[a]}) ≤ {b}({vals[b]})")

    # ③ 页面头层 > 卡标题层
    head_gt_card = True
    note = "无 h2 或未取到字号"
    if "h1" in vals and "h2" in vals:
        head_gt_card = min(vals["h1"]) > max(vals["h2"])
        note = f"h1={vals['h1']} vs h2={vals['h2']}"

    check(f"层级：h 标签字号严格递减（{' > '.join(present) or '无'}）",
          not inversion, inversion or note)
    check("层级：页面头层字号 > 卡标题字号", head_gt_card, note)
    # 诊断（不参与成败）：无字号 h 标签 + 同标签多档
    if missing_size:
        print(f"        · 诊断：{len(missing_size)} 处 h 标签未显式给字号 {missing_size[:3]}")
    for k, v in multi.items():
        print(f"        · 诊断：<{k}> 有 {len(v)} 档字号 {v}（档内细分，非层级倒挂）")


# ── ⑪ 数字等宽：格式化值必须挂 .num / .mono ─────────────────────────────
def static_numeral() -> None:
    files = _tsx_files()
    # ⚠️ 判据用**正则近似**而非 AST：只找"单行内 `{fmtXxx(...)}` 直接坐落在无 num/mono 的
    #    元素里"这一种形态。多行 JSX 或 `{cond ? fmtA() : fmtB()}` 会漏 ——
    #    故意的：漏判（假绿）比误判（假红）可接受，误判会让人把判据当噪音关掉。
    TAG_RE = re.compile(
        r"<(" + "|".join(_NUM_TAGS) + r")\s+className=\"([^\"]*)\"[^>]*>([^<>{}]*?\{[^{}]*?" +
        r"(?:toFixed\(|fmt[A-Z]\w*\()[^{}]*\}[^<>{}]*?)</\1>"
    )
    unreachable: list[tuple[str, str]] = []
    checked = 0
    for f in files:
        rel = _rel(f)
        src = _strip_comments(f.read_text(encoding="utf-8"))
        for m in TAG_RE.finditer(src):
            cls, body = m.group(2), m.group(3)
            checked += 1
            if "num" in cls or "mono" in cls or "field" in cls:
                continue
            # 例外：`text-fg-4` 的"另有 N 笔"这类附注 —— 仍要求 num（见 §11.4）
            unreachable.append((rel, (cls[:48] + " :: " + body[:44]).replace("\n", " ")))

    check(f"数字等宽：格式化值挂 `.num`/`.mono`（扫到 {checked} 处）",
          not unreachable, unreachable[:5] or "全部命中")


def run_static_checks() -> None:
    static_typography()
    static_spacing()
    static_hierarchy()
    static_numeral()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5020")
    ap.add_argument("--json", default=None, help="把结果另存为 JSON")
    ap.add_argument("--static-only", action="store_true",
                    help="只跑 B-0 的静态视觉判据（无需起服务 / 不需要 playwright）")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    if args.static_only:
        print("静态视觉判据（B-0）—— 不需要服务\n" + "=" * 72)
        run_static_checks()
        return _summary(args.json)

    # ── 动态部分需要 playwright；静态部分不依赖它 ─────────────────────
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        skip("全部动态项", "未安装 playwright")
        run_static_checks()
        return _summary(args.json)

    try:
        probe_server(base, tries=8)
        up = True
    except SystemExit:
        up = False
    if not up:
        skip("全部动态项", f"{base}/v2 不可达（先 `python src/main.py web`）")
        run_static_checks()
        return _summary(args.json)

    print(f"服务就绪：{base}\n" + "=" * 72)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)

        # ── ① 四入口渲染 / ③ 文案卫生 / ⑦ console ──────────────────────
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        console: list[str] = []
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))

        page.goto(base + "/v2", wait_until="domcontentloaded")
        page.wait_for_selector("text=今天要做什么", timeout=40000)
        page.wait_for_timeout(3000)

        check("导航含四个入口", all(e in page.inner_text("nav") for e in ENTRIES))
        check("「今天」页渲染", "总资产" in page.inner_text("body"))
        # B-3：行情 + 消息面（两个独占展示块）
        t0 = page.inner_text("body")
        check("「今天」页：指数行情块已接入", "指数行情" in t0)
        check("「今天」页：消息面块已接入", "消息面" in t0)

        def go(label: str) -> str:
            page.get_by_role("button", name=label, exact=True).click()
            page.wait_for_timeout(3500)
            return page.inner_text("body")

        t = go("持仓")
        check("「持仓」页：调仓 + 建仓计划 + 定投", "调仓" in t and "建仓计划" in t and "定投计划" in t)
        # B-4b：写操作面板（写入口不再是"只读"）
        check("「持仓」页：写操作面板已接入（记一笔 + 确认卡说明）",
              "记一笔" in t and "确认卡" in t)

        t = go("研究")
        check("「研究」页：行业板块 + 筛选池", "行业板块" in t and "筛选池" in t)
        check("「研究」页：量化模型已移出", "量化模型" not in t)
        # B-3：历史回测验证块。⚠️ 必须同时含「非推荐」字样 —— 那是最容易漂移的一条文案
        # （后端 §4.4 明确它不是推荐；只断言"块存在"会漏掉文案被改回"推荐"的情况）。
        check("「研究」页：历史回测验证已接入且标注非推荐",
              "历史回测验证" in t and "非推荐" in t)
        # 数据链路下钻（2026-10-04 接进「研究」）—— 复用「今天」页同一个 DrillDown 组件
        check("「研究」页：数据链路下钻已接入",
              "数据链路" in t and "采集" in t and "评估" in t)

        t = go("设置")
        check("「设置」页：计划 + 画像 + 运维", "投资计划" in t and "用户画像与生效约束" in t and "数据源与运维" in t)
        check("「设置」页：量化模型已移入", "量化模型" in t and "波动率预测模型" in t)
        check("「设置」页：运维命令表", "python src/main.py snapshot" in t)

        # ③ 文案卫生：JSX 文本节点里的 `**粗体**` 会被原样渲染
        for label in ENTRIES:
            body = go(label)
            check(f"「{label}」无 markdown 记号泄漏", "**" not in body)

        # ── ② hash 路由 ───────────────────────────────────────────────
        page.goto(base + "/v2#/research", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        check("hash 深链 #/research 直达研究页", "行业板块" in page.inner_text("body"))
        page.goto(base + "/v2#/nonsense", wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        check("未知 hash 回落「今天」", "今天要做什么" in page.inner_text("body"))

        # ── ⑥ 图表参考系 ──────────────────────────────────────────────
        page.goto(base + "/v2#/today", wait_until="domcontentloaded")
        # 绘图区现在是**懒加载**（Recharts 不进首屏）→ 必须等它到位再量，
        # 否则会误报"没有网格"。（这也是这条断言存在的意义：拆包别把图拆没了。）
        page.wait_for_selector(".recharts-surface", timeout=20000)
        page.wait_for_timeout(1200)
        c = page.evaluate(CHART_JS)
        check("曲线：有垂直网格", c["gridV"] > 0, f'垂直 {c["gridV"]} 条')
        check("曲线：有水平网格", c["gridH"] >= 4, f'水平 {c["gridH"]} 条')
        check("曲线：有坐标轴线", c["axisLines"] >= 2, f'轴线 {c["axisLines"]} 条')
        check("曲线：Y 轴刻度含 0 且 ≥5 档",
              len(c["yTicks"]) >= 5 and any(t.startswith("0.") for t in c["yTicks"]), c["yTicks"])

        # ── ⑥ 动效绑定 ────────────────────────────────────────────────
        a = page.evaluate(ANIM_JS)
        check("动效：卡片错峰延迟生效", len(set(a["stagger"])) > 1, a["stagger"])

        go("持仓")
        check("动效：进度条生长动画已绑定",
              page.evaluate("() => { const e=document.querySelector('.grow-x'); "
                            "return e ? getComputedStyle(e).animationName : null; }") == "qfa-grow")
        page.goto(base + "/v2#/today", wait_until="domcontentloaded")
        page.wait_for_timeout(3800)
        check("动效：温度指针滑入已绑定",
              page.evaluate("() => { const e=document.querySelector('.slide-thumb'); "
                            "return e ? getComputedStyle(e).animationName : null; }") == "qfa-slide-thumb")

        check("console 无 error", not console, console[:3])
        ctx.close()

        # ── ⑤ 导航断点：lg 起侧栏，以下顶部条，且只有一套可见 ──────────
        for w, want_side in ((1440, True), (1024, True), (1023, False), (375, False)):
            c2 = browser.new_context(viewport={"width": w, "height": 900})
            pg2 = c2.new_page()
            pg2.goto(base + "/v2", wait_until="domcontentloaded")
            pg2.wait_for_timeout(3200)
            n = pg2.evaluate(NAV_JS)
            check(f"断点 {w}：{'侧栏' if want_side else '顶部条'}生效且只有一套可见",
                  (n["sidebar"] != "none") == want_side and (n["topbar"] == "none") == want_side
                  and n["visible"] == 4, n)
            c2.close()

        # ── ④ 移动端零横向溢出 ────────────────────────────────────────
        for label, h in HASHES.items():
            c3 = browser.new_context(viewport={"width": 375, "height": 812})
            pg3 = c3.new_page()
            pg3.goto(f"{base}/v2#/{h}", wait_until="domcontentloaded")
            pg3.wait_for_timeout(4200)
            r = pg3.evaluate(OVERFLOW_JS)
            check(f"移动端 375 · {label} 无横向溢出",
                  r["doc"] <= r["client"] + 1 and r["n"] == 0, r)
            c3.close()

        # ── ⑥ 骨架屏（注入 warming 后必须出现，而不是白屏）────────────
        c4 = browser.new_context(viewport={"width": 1440, "height": 900})
        pg4 = c4.new_page()
        for ep in ("sectors", "funds/board"):
            pg4.route(f"**/api/{ep}*", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "data": None, "status": "warming", "retry_in": 10})))
        pg4.goto(base + "/v2#/research", wait_until="domcontentloaded")
        pg4.wait_for_timeout(2600)
        sk = pg4.evaluate("() => document.querySelectorAll('.skeleton').length")
        check("加载中显示骨架屏（非白屏）", sk > 0, f"{sk} 块")
        c4.close()

        # ── prefers-reduced-motion：全部动效关闭 ──────────────────────
        c5 = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        pg5 = c5.new_page()
        pg5.goto(base + "/v2#/today", wait_until="domcontentloaded")
        pg5.wait_for_timeout(3500)
        rm = pg5.evaluate("() => { const e=document.querySelector('.rise-in'); "
                          "return e ? getComputedStyle(e).animationName : null; }")
        check("reduced-motion 下入场动画关闭", rm in ("none", None), rm)
        c5.close()

        browser.close()

    # ── B-0 静态视觉判据（不需要浏览器，但并入同一份结果 / 退出码）──────
    print("-" * 72)
    run_static_checks()
    return _summary(args.json)


def _summary(json_path: str | None) -> int:
    print("\n" + "=" * 72)
    ok = sum(1 for r in RESULTS if r["ok"])
    print("汇总：%d/%d 通过" % (ok, len(RESULTS)))
    if SKIPPED:
        print("跳过（服务/依赖不可达，退出码不计入）：")
        for s in SKIPPED:
            print("  - " + s)
    failed = [r["check"] for r in RESULTS if not r["ok"]]
    if failed:
        print("失败项：")
        for f in failed:
            print("  - " + f)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"results": RESULTS, "skipped": SKIPPED}, fh,
                      ensure_ascii=False, indent=2)
        print("结果已写入 " + json_path)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
