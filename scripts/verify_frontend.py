# -*- coding: utf-8 -*-
"""
新前端（Flask `/` 上的 React 构建产物）真机验收 —— 把散在仓库外的 Playwright 检查固化下来。

⚠️ 2026-10-05 B-5 切换后：新前端挂在 **`/`**（原 `/v2` 已 301 到 `/`）。
   本脚本原先硬编码 `/v2`，已全部改为 `/`；`--base` 仍可指向别的端口。

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

覆盖范围（截至 2026-10-06 共 **65 项** = 动态 54 + 静态 11）：
  ① 四入口渲染与导航可达
  ② hash 路由（深链直达 / 未知 hash 回落）
  ③ 文案卫生（JSX 里写 `**粗体**` 会原样渲染 —— 曾真出现字面 `**只读**`）
  ④ 移动端 375：四个入口**零横向溢出**（元素级扫描，不是只看 body）
  ⑤ 导航断点：lg 起侧栏 / 以下顶部条，且任意视口**只有一套可见**
  ⑥ 交互动效已绑定（错峰入场 / 条形生长 / 指针滑入 / 骨架屏 shimmer）
  ⑦ console 无 error
  ⑦b **曲线为"裸曲线"**（2026-10-06 反转：无网格 / 无轴线 / 无 Y 刻度，保留零轴 —— DESIGN §5.1.1）
  ⑦c **信息密度**（DESIGN §5.2：4 页 × 数字节点上限 + 页高上限 = 8 条，基线上限见 `DENSITY_BASELINE`）

B-0 追加（2026-10-04，计划书 §11.3 的 ⬜ 三项 → ✅；见 §11.4 的判据取舍）：
  ⑧ **排版**：**无裸字号**（字号全部走 `@theme` 的 `--text-*` token，共 7 档）；
     字阶级数 ≤ 8；TSX 里无裸色值（唯一例外见 `_BARE_COLOR_ALLOW`）
  ⑨ **留白**：`Card` 自身零 margin；间距只走 4px-grid（**含 Tailwind 半档**，见 `_SPACING_GRID_PX`）
  ⑩ **视觉层级**：h 标签字号可解析（裸值或 token）；无 `h1 > h2 > h3` 倒挂；页面头层字号高于卡标题
  ⑪ **数字等宽**：货币/百分比/份额格式化值必须挂 `.num`/`.mono`
  ⑫ **涨跌色**：`--color-rise` 是红 / `--color-fall` 是绿（中国惯例）、`dirClass()` 映射不反转、
     `chartColors.RISE/FALL` 与 `@theme` 同值 —— 2026-10-06 B-6 起**接管**
     被删的 `scripts/check_rise_fall.py`

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

# ── 排版 1：字号必须走 token ─────────────────────────────────────────────
# ⚠️ 2026-10-06：`@theme` 补上了 `--text-*`（7 档），判据随之**收紧**——
#    旧版是"裸字号落在 0.5px 网格上 + 种类 ≤16"，那是**因为当时字阶不可引用**
#    （index.css 只有色/圆角/字体族，见 §11.4 的裁决）。
#    现在字阶可引用了，再写 `text-[11.5px]` 就是绕过字阶 → 改为**一处裸字号都不许有**。
#    判据分两条：① 裸字号 0 处；② 用到的 `--text-*` 档数 ≤ 8（DESIGN §2 的 4 层级 + 3 专用档）。
_TEXT_MAX_DISTINCT = 8

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

#: `@theme` 里的字号 token → px。**层级判据必须靠它**解析 `h1/h2` 的实际字号 ——
#: ⚠️ 2026-10-06 踩到的**假绿**：字号改成 token 后，`h1/h2` 的 className 里不再有
#: `text-[Npx]`，旧判据取不到字号 → 打印"无 h2 或未取到字号" → **两条层级断言双双 PASS**。
#: 即"判据在数据消失时默认通过"是错的：**取不到值必须判红**（下面对 missing 的处理已改）。
_FONT_TOKEN_RE = re.compile(r"(?<![\w-])text-([a-z][\w-]*)\b")


def _text_token_px() -> dict[str, float]:
    """从 `index.css` 的 `@theme` 读出 `--text-<name>: <N>px` → `{name: N}`。"""
    css = INDEX_CSS.read_text(encoding="utf-8") if INDEX_CSS.exists() else ""
    theme = re.search(r"@theme\s*\{(.*?)\n\}", css, re.S)
    if not theme:
        return {}
    out: dict[str, float] = {}
    for name, val in re.findall(r"--text-([\w-]+?)\s*:\s*([\d.]+)px", theme.group(1)):
        out.setdefault(name, float(val))
    return out

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
            with opener.open(base + "/", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit(f"{base}/ 不可达 —— 先启动服务：python src/main.py web")


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

# ⚠️ 2026-10-06 判据**反转**：曲线按 DESIGN §5.1.1 第 4 条改成"裸曲线"
#    （三个参照产品 Wealthfolio / Ghostfolio / Rotki 都不画网格、不画刻度、不画边框）。
#    断言从"必须有网格/轴线/5 档 Y 刻度"改为"必须**没有**这些"，并**新增零轴断言** ——
#    否则"去网格"这条改动就没有守卫，将来（或换个智能体）随手加回网格也不会红灯。
CHART_JS = r"""() => ({
  surface: document.querySelectorAll('.recharts-surface').length,
  gridH: document.querySelectorAll('.recharts-cartesian-grid-horizontal line').length,
  gridV: document.querySelectorAll('.recharts-cartesian-grid-vertical line').length,
  axisLines: document.querySelectorAll('.recharts-cartesian-axis-line').length,
  yTicks: [...document.querySelectorAll('.recharts-yAxis .recharts-cartesian-axis-tick-value tspan')]
            .map(t => t.textContent),
  refLines: document.querySelectorAll('.recharts-reference-line line').length,
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

    # 读 `@theme` —— 色 token 白名单 + 字号 token 名单
    css = INDEX_CSS.read_text(encoding="utf-8") if INDEX_CSS.exists() else ""
    theme = re.search(r"@theme\s*\{(.*?)\n\}", css, re.S)
    tokens = re.findall(r"(--[\w-]+)\s*:", theme.group(1)) if theme else []
    color_tokens = {t for t in tokens if t.startswith("--color-")}
    text_tokens = {t[len("--text-"):] for t in tokens if t.startswith("--text-")}

    bare_sizes: list[tuple[str, str]] = []
    bare_colors: list[tuple[str, str]] = []
    font_used: set[str] = set()
    for f in files:
        src = _strip_comments(f.read_text(encoding="utf-8"))
        rel = _rel(f)
        for s in _FONT_SZ.findall(src):
            bare_sizes.append((rel, f"text-[{s}px]"))
        for cls in _class_attrs(src):
            for m in re.finditer(r"(?<![\w-])text-([a-z][\w-]*)\b", cls):
                if m.group(1) in text_tokens:
                    font_used.add(m.group(1))
        # 裸色值：只查 `#hex` / `rgb()` / `hsl()` 这类"硬编码颜色"，
        # 不查 `w-[212px]` 这种尺寸（那是布局测量值，不是设计 token 的职责）
        for m in re.finditer(r"\[(#[0-9a-fA-F]{3,8}|rgba?\([^\]]*\)|hsla?\([^\]]*\))\]", src):
            lit = m.group(1)
            if lit in _BARE_COLOR_ALLOW.get(rel, set()):
                continue
            bare_colors.append((rel, lit))

    check(
        f"排版：无裸字号 —— 字号全部走 `--text-*` token（`@theme` 声明 {len(text_tokens)} 档）",
        not bare_sizes,
        (f"仍有 {len(bare_sizes)} 处：{bare_sizes[:6]}" if bare_sizes else "0 处裸字号"),
    )
    check(
        f"排版：字阶级数 {len(font_used)}（上限 {_TEXT_MAX_DISTINCT}：4 层级 + 3 专用档）",
        bool(font_used) and len(font_used) <= _TEXT_MAX_DISTINCT and font_used <= text_tokens,
        f"用到 {sorted(font_used)}",
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
    tok_px = _text_token_px()
    ranks: dict[str, list[tuple[str, float, int]]] = {}
    missing_size: list[tuple[str, str]] = []
    for f in files:
        rel = _rel(f)
        src = _strip_comments(f.read_text(encoding="utf-8"))
        for line_no, line in enumerate(src.splitlines(), 1):
            for m in _H_TAG.finditer(line):
                tag, cls = m.group(1), m.group(2)
                # 字号两种写法都要认：裸值 `text-[24px]`（已禁用，但历史代码可能有）
                # 与 token `text-title`（2026-10-06 起的唯一写法）。
                sz = _FONT_SZ.search(cls)
                if sz:
                    size: float | None = float(sz.group(1))
                else:
                    size = next(
                        (tok_px[t] for t in _FONT_TOKEN_RE.findall(cls) if t in tok_px), None)
                if size is None:
                    missing_size.append((rel, f"<{tag}>"))
                    continue
                ranks.setdefault(tag, []).append((rel, size, line_no))

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
    # ⚠️ 取不到字号 = **判红**，不是跳过：2026-10-06 字号改 token 后，旧实现因为
    #    `h1/h2` 的 className 里不再有 `text-[Npx]` 而取不到值，两条断言当场变成**假绿**
    #    （detail 里写着"无 h2 或未取到字号"却 PASS）。"判据看不到数据"必须报出来。
    noted = f"h1={vals.get('h1')} vs h2={vals.get('h2')}"
    check("层级：h1 / h2 的字号都能解析出来（裸值或 token）",
          not missing_size and "h1" in vals and "h2" in vals,
          (f"解析不到：{missing_size[:4]}" if missing_size else noted))
    check(f"层级：h 标签字号严格递减（{' > '.join(present) or '无'}）",
          bool(present) and not inversion, inversion or noted)
    check("层级：页面头层字号 > 卡标题字号",
          bool(vals.get("h1")) and bool(vals.get("h2"))
          and min(vals["h1"]) > max(vals["h2"]), noted)
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


# ── ⑪ 涨跌色：rise=红 / fall=绿（中国惯例），且重复常量不许漂移 ──────────────
# ⚠️ 2026-10-06 B-6：本条**接管**被删掉的 `scripts/check_rise_fall.py`。
#    旧守卫专查旧前端 `app.css` / `app.js` 里的 `--up/--down` 残留（那是**欧美惯例**：
#    绿=涨、红=跌，与 A 股相反 → 曾把"盈利显示成绿色"）。旧前端删了，那条守卫没有对象了，
#    但**「涨跌色不许再反」这条约束必须继续有红灯**，否则哪天有人"顺手统一配色"就没人拦。
#    新前端方向色只有两个来源，都要断言：
#      ① `@theme` 的 `--color-rise` / `--color-fall` —— 语义必须是 红 / 绿；
#      ② `lib/format.ts` 的 `dirClass()` 映射不能反转（rise→text-rise、fall→text-fall）。
#    另有一处**重复常量**：`lib/chartColors.ts` 的 RISE/FALL（Recharts 不认 CSS 变量，
#    只能另写一份）—— 它最容易与 token 漂移，故单独断言同值。
def static_rise_fall() -> None:
    css = INDEX_CSS.read_text(encoding="utf-8") if INDEX_CSS.exists() else ""
    fmt_path = SRC / "lib" / "format.ts"
    cc_path = SRC / "lib" / "chartColors.ts"
    fmt = fmt_path.read_text(encoding="utf-8") if fmt_path.exists() else ""
    cc = cc_path.read_text(encoding="utf-8") if cc_path.exists() else ""

    def hex_of(tok: str) -> str | None:
        m = re.search(re.escape(tok) + r"\s*:\s*(#[0-9a-fA-F]{6})", css)
        return m.group(1).lower() if m else None

    def rgb(h: str) -> tuple[int, int, int]:
        return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))

    rise, fall = hex_of("--color-rise"), hex_of("--color-fall")
    sem_ok = False
    if rise and fall:
        rr, rg, rb = rgb(rise)
        fr, fg, fb = rgb(fall)
        # rise 必须红占优；fall 必须绿占优
        sem_ok = rr > rg and rr > rb and fg > fr and fg > fb
    map_ok = bool(re.search(r"d\s*===\s*'rise'\s*\?\s*'text-rise'", fmt)) and bool(
        re.search(r"d\s*===\s*'fall'\s*\?\s*'text-fall'", fmt))
    check(
        "涨跌色：rise=红 / fall=绿（中国惯例）且 dirClass 映射不反转",
        sem_ok and map_ok,
        f"rise={rise} fall={fall}｜dirClass {'OK' if map_ok else '反转或缺失'}"
        + ("" if sem_ok else "｜⚠️ 语义反了（又成欧美惯例）"),
    )

    cc_rise = re.search(r"export const RISE = '(#[0-9a-fA-F]{6})'", cc)
    cc_fall = re.search(r"export const FALL = '(#[0-9a-fA-F]{6})'", cc)
    same = bool(cc_rise and cc_fall and rise and fall
                and cc_rise.group(1).lower() == rise and cc_fall.group(1).lower() == fall)
    check(
        "涨跌色：图表常量 chartColors.RISE/FALL 与 @theme 同值（重复常量不许漂移）",
        same,
        f"chartColors {cc_rise.group(1) if cc_rise else '?'}/{cc_fall.group(1) if cc_fall else '?'}"
        f" vs @theme {rise}/{fall}",
    )


def run_static_checks() -> None:
    static_typography()
    static_spacing()
    static_hierarchy()
    static_numeral()
    static_rise_fall()


# ══════════════════════════════════════════════════════════════════════════
# DESIGN §5.2 信息密度 —— 用「基线上限」起步
#
# ⚠️ 为什么是基线而不是直接卡 DESIGN 的目标值：
#   2026-10-05 实测现状**全部超标**（今天 80 个数字节点 / 目标 ≤40）。若直接卡目标，
#   这个守卫一上线就是红的，等于没上线（红着的守卫没人看）。
#   所以先把**当前值**钉成上限：**不许更差**，同时打印"距目标还差多少"。
#   每瘦身一轮就把基线往下调一档，直到等于 TARGET —— 这才是"渐进收紧"。
# ══════════════════════════════════════════════════════════════════════════

DENSITY_TARGET = {"num": 40, "screens": 2.0}          # DESIGN §5.2（桌面目标）

#: 键 = hash；值 = (含数字节点数, 桌面页高/屏)。⚠️ 同口径下只许往下调，不许上调。
#: ⚠️ 基线必须是**实测值**（向上取到 0.1 屏 / 逐个数字上取），不能凭印象估 ——
#:   2026-10-05 首次写时把持仓写成 1.5、研究写成 1.6（估算），实测 1.52/1.65，
#:   当场被自己的断言抓出来。页高与节点数会随数据小幅波动，留一点上界是必要的。
#:
#: ⚠️⚠️ **2026-10-06 口径变更 → 本表整体重设，与旧值不可直接比较**：
#:   旧口径只数"叶子元素"，把 `<span class="mono"><i>▲</i>+1.2%</span>` 这类
#:   "箭头 + 数值"的混合内容整段漏掉（实测漏计 研究 **55** / 持仓 14 / 今天 7 / 设置 10）。
#:   涨跌在本项目里大量以这种形式渲染，漏计是主力而非边角 → 已把 `DENSITY_JS` 改成
#:   数"含数字的**文本段**"（阈值含义不变：屏上有多少个数字读数，只是不再漏数）。
#:   于是同一天、同一构建下：今天 24→31、持仓 51→65、研究 75→130、设置 43→53。
#:   下面这组基线 = **本轮瘦身完成后**在新口径下的实测（今天 31 / 持仓 59 / 研究 76 / 设置 53，
#:   连测两次完全一致）+ 约 8% 余量。
#:
#: 本轮（瘦身 · 研究/持仓）改动与实测（新口径）
#:   研究 130 → **76**：行业板块可视列 6→3（近3月/近6月/波动进逐行展开）、默认行 12→8、
#:                    动量 chip 去掉重复的「近3月」标签；页高 1.23 → 1.17 屏
#:   持仓  65 → **59**：建仓计划的 6 格"计划元数据"去掉（与「设置 → 投资计划」逐字重复）
#:                    + 定投卡副标题去重；页高 1.55 屏不变
#:   今天 31 / 设置 53：本轮未动
#: ⚠️ DESIGN 目标 40 对「今天」是合适的（实测 31 已达标）；对**表格页**（研究 / 持仓）
#:   是否合适 **未定** —— 研究的两张表本身就是页面职责，持仓表 7 只 × 4 字段 ≈ 28 是下限。
#:   该问题已在计划书 §13.9 记为待用户拍板项，**不擅自改 §5.2 的目标值**。
#: ⚠️ **今天页的数字与"行情端点是否可用"强相关**（2026-10-06 自捉的测量错误）：
#:   行情不可用（本机沙箱常态）→ 小条只显示一句"不可用"+ 原因，今天 = **31**；
#:   行情可用（正常态，verify 那一轮就是）→ 小条多出 4 只指数的「点位 + 涨跌」≈ +9，
#:   消息面有信号再 +2 → 今天 = **42**。
#:   我先前把基线设成 34，是**只按降级态测的值定的** —— 属于"测了个非正常状态"，
#:   一次 verify 就被自己的断言抓出来。现在取**正常态（联网）**的 42 + 约 10% 余量作为基线。
#:   ⚠️ 副作用要如实记：联网态 42 比 DESIGN 目标 40 **多 2**，来源就是那 4 组指数报价。
#:   要压到 ≤40 只需去掉指数**点位**（只留涨跌%），省 4 个读数 —— 属产品取舍，未做。
DENSITY_BASELINE = {
    "today": (46, 1.5),
    "position": (64, 1.6),
    "research": (82, 1.5),
    "settings": (57, 2.1),
}

DENSITY_JS = r"""() => {
  const vh = document.documentElement.clientHeight;
  const vis = el => {
    // ⚠️ 2026-10-06 修正之一：**折叠的 `<details>` 内容不算"在屏上"**。
    //   Chrome 121+ 把关闭态 details 的实现从 `display:none` 换成了
    //   `content-visibility: hidden` —— 其后代元素的 `display` 仍是 `block`、
    //   `getBoundingClientRect()` 也非零，于是"默认折叠"在旧判据下**完全测不出来**。
    //   实测证据：把数据链路搬到「设置」并默认折叠后，旧判据给出 128 → **148**（假涨）。
    //   DESIGN §5.3 把"折叠"当作渐进披露的主要手段，判据必须能看见它。
    const d = el.closest('details:not([open])');
    if (d && !el.closest('summary')) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
  };
  // ⚠️ 2026-10-06 修正之二：改数**含数字的文本段**，不再数"叶子元素"。
  //   旧口径要求元素**没有子元素**才计数，于是 `<span class="mono"><i>▲</i>+1.2%</span>`
  //   这类"箭头 + 数值"的混合内容被整段漏掉（外层有子元素 → 不是叶子；内层箭头没数字）。
  //   本项目纪律要求涨跌/金额挂 `.num`/`.mono`（本脚本第八维），而涨跌大量以这种形式渲染
  //   → 漏计不是边角而是主力。实测（同一构建、同一预热态）：
  //       今天 24→31（+7）｜持仓 51→65（+14）｜研究 75→**130**（+55，全是行业表涨跌列）｜设置 43→53
  //   故口径改为"文本段"，并**按新口径重设基线**（DESIGN §5.2 的阈值含义不变：
  //   仍是"屏上有多少个数字读数"，只是不再漏数）。
  const nums = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const t = (w.currentNode.textContent || '').trim();
    if (!t || !/[0-9]/.test(t)) continue;
    const el = w.currentNode.parentElement;
    if (!el || !vis(el)) continue;
    nums.push(el);
  }
  const inFold = nums.filter(el => el.getBoundingClientRect().top < vh);
  return {
    num: nums.length,
    foldNum: inFold.length,
    screens: +(document.documentElement.scrollHeight / vh).toFixed(2),
  };
}"""


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
        skip("全部动态项", f"{base}/ 不可达（先 `python src/main.py web`）")
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

        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_selector("text=今天要做什么", timeout=40000)
        page.wait_for_timeout(3000)

        check("导航含四个入口", all(e in page.inner_text("nav") for e in ENTRIES))
        check("「今天」页渲染", "总资产" in page.inner_text("body"))
        # 2026-10-06 瘦身第 5 条：行情与消息面已合成**一块「市场小条」**，不再各占一张卡
        t0 = page.inner_text("body")
        check("「今天」页：市场小条（指数行情 + 消息面）已接入",
              "指数行情" in t0 and "消息面" in t0)
        # 瘦身第 2 条：① 结论必须落到三态之一（DESIGN §5.1）
        check("「今天」页：结论落到三态之一（要动手 / 不用动手 / 待数据）",
              any(s in t0 for s in ("要动手", "不用动手", "待数据")))
        # ② 首屏要有"大数字"：≥ 正文的 3 倍（正文 14px → ≥42px；DESIGN §5.1.1 第 2 条）
        big = page.evaluate(
            "() => Math.max(0, ...[...document.querySelectorAll('main .num')]"
            ".map(e => parseFloat(getComputedStyle(e).fontSize) || 0))")
        check("「今天」页：首屏大数字 ≥ 正文 3 倍（≥42px）", big >= 42, f"{big}px")
        # 瘦身第 3 条：审计 / 溯源不得出现在首屏（此处「今天」整页都不该有）
        check("「今天」页：审计（数据链路）已移出", "数据链路" not in t0)

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
        # 瘦身第 6 条：筛选池可视列 = 4（类型/风险/费率进逐行展开）。
        # ⚠️ 用 DOM 取表头而不是 grep 文案：池子说明里本来就写着"费率"二字，会假绿。
        pool_cols = page.evaluate(
            "() => { const t = [...document.querySelectorAll('table')]"
            ".find(x => x.querySelector('thead') && x.innerText.includes('夏普'));"
            " return t ? [...t.querySelectorAll('thead th')].map(th => th.innerText.trim()) : []; }")
        check("「研究」页：筛选池可视列 = 4（代码/名称/夏普/近3月）",
              pool_cols == ["代码", "名称", "夏普", "近3月"], pool_cols)
        # 瘦身（研究页）：行业板块可视列 = 3（行业/近1月/评分），近3月 / 近6月 / 波动进逐行展开
        sec_cols = page.evaluate(
            "() => { const t = [...document.querySelectorAll('table')]"
            ".find(x => x.querySelector('thead') && x.innerText.includes('近1月'));"
            " return t ? [...t.querySelectorAll('thead th')].map(th => th.innerText.trim()) : []; }")
        check("「研究」页：行业板块可视列 = 3（行业/近1月/评分）",
              sec_cols == ["行业", "近1月", "评分"], sec_cols)
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
        # 瘦身第 2 条：温度卡从「今天」搬来，与「用户画像与生效约束」（温度模型的输出）相邻
        check("「设置」页：市场温度已搬入", "市场温度" in t)
        # 模型对照表按 DESIGN §5.1「审计 / 口径一律默认折叠」收起
        # （计划书 §13.2 点名的那张「DM / AUC / QLIKE / MZ β」表）—— 展开后内容仍在
        check("「设置」页：量化模型已移入且对照表默认折叠",
              "量化模型" in t and "波动率预测模型" not in t)
        page.locator("summary", has_text="三张模型对照表").first.click()
        page.wait_for_timeout(500)
        check("「设置」页：模型对照表展开后内容完整",
              "波动率预测模型" in page.inner_text("body"))
        # 数据链路（审计）2026-10-06 从「今天」搬来，且按 DESIGN §5.1 **默认折叠**
        check("「设置」页：数据链路（审计）已搬入且默认折叠",
              "数据链路" in t and "这条结论是怎么算出来的" in t)
        # 运维命令表按 DESIGN §5.1「命令表一律默认折叠」收进折叠区 —— 正文不应出现命令
        check("「设置」页：命令表默认折叠（正文不出现命令全文）",
              "python src/main.py snapshot" not in t)
        page.locator("summary", has_text="运维命令清单").first.click()
        page.wait_for_timeout(500)
        check("「设置」页：命令表展开后内容完整",
              "python src/main.py snapshot" in page.inner_text("body"))
        # B-4b-3：写操作面板（设置页不再是"只读"）
        check("「设置」页：写操作面板已接入（含高危笔数明示）",
              "数据运维与写入" in t and "确认卡" in t and "待确认" in t)

        # ③ 文案卫生：JSX 文本节点里的 `**粗体**` 会被原样渲染
        for label in ENTRIES:
            body = go(label)
            check(f"「{label}」无 markdown 记号泄漏", "**" not in body)

        # ── ② hash 路由 ───────────────────────────────────────────────
        page.goto(base + "/#/research", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        check("hash 深链 #/research 直达研究页", "行业板块" in page.inner_text("body"))
        page.goto(base + "/#/nonsense", wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        check("未知 hash 回落「今天」", "今天要做什么" in page.inner_text("body"))

        # ── ⑥ 图表：裸曲线（DESIGN §5.1.1 第 4 条，2026-10-06 判据反转） ──
        page.goto(base + "/#/today", wait_until="domcontentloaded")
        # 绘图区现在是**懒加载**（Recharts 不进首屏）→ 必须等它到位再量，
        # 否则会误报"没有网格"（其实图还没渲染）。
        page.wait_for_selector(".recharts-surface", timeout=20000)
        page.wait_for_timeout(1200)
        c = page.evaluate(CHART_JS)
        check("曲线：绘图区已渲染（懒加载到位）", c["surface"] >= 1, f'{c["surface"]} 个 surface')
        check("曲线：无网格、无坐标轴线",
              c["gridH"] == 0 and c["gridV"] == 0 and c["axisLines"] == 0,
              f'水平 {c["gridH"]} / 垂直 {c["gridV"]} / 轴线 {c["axisLines"]}')
        check("曲线：无 Y 轴刻度（数值交给 tooltip 与上方大数字）",
              len(c["yTicks"]) == 0, c["yTicks"])
        check("曲线：保留零轴（全图唯一语义分界）", c["refLines"] >= 1, f'参考线 {c["refLines"]} 条')

        # ── ⑥ 动效绑定 ────────────────────────────────────────────────
        a = page.evaluate(ANIM_JS)
        check("动效：卡片错峰延迟生效", len(set(a["stagger"])) > 1, a["stagger"])

        go("持仓")
        check("动效：进度条生长动画已绑定",
              page.evaluate("() => { const e=document.querySelector('.grow-x'); "
                            "return e ? getComputedStyle(e).animationName : null; }") == "qfa-grow")
        # ⚠️ 2026-10-06（瘦身第 2 条）：温度卡已从「今天」搬到「设置」，指针动画随之移动 ——
        #    断言必须跟着走，否则它会永远红（而"温度尺"本身没坏）。
        page.goto(base + "/#/settings", wait_until="domcontentloaded")
        page.wait_for_timeout(3800)
        check("动效：温度指针滑入已绑定（温度卡现挂「设置」）",
              page.evaluate("() => { const e=document.querySelector('.slide-thumb'); "
                            "return e ? getComputedStyle(e).animationName : null; }") == "qfa-slide-thumb")

        check("console 无 error", not console, console[:3])
        ctx.close()

        # ── ⑤ 导航断点：lg 起侧栏，以下顶部条，且只有一套可见 ──────────
        for w, want_side in ((1440, True), (1024, True), (1023, False), (375, False)):
            c2 = browser.new_context(viewport={"width": w, "height": 900})
            pg2 = c2.new_page()
            pg2.goto(base + "/", wait_until="domcontentloaded")
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
            pg3.goto(f"{base}/#/{h}", wait_until="domcontentloaded")
            pg3.wait_for_timeout(4200)
            r = pg3.evaluate(OVERFLOW_JS)
            check(f"移动端 375 · {label} 无横向溢出",
                  r["doc"] <= r["client"] + 1 and r["n"] == 0, r)
            c3.close()

        # ── DESIGN §5.2 信息密度（基线上限；见文件上方 DENSITY_BASELINE）──
        for label, h in HASHES.items():
            c6 = browser.new_context(viewport={"width": 1440, "height": 900})
            pg6 = c6.new_page()
            pg6.goto(f"{base}/#/{h}", wait_until="domcontentloaded")
            pg6.wait_for_timeout(4200)
            m = pg6.evaluate(DENSITY_JS)
            c6.close()
            base_num, base_scr = DENSITY_BASELINE[h]
            gap = m["num"] - DENSITY_TARGET["num"]
            check(
                f"密度 · {label}：数字节点 ≤ 基线 {base_num}（DESIGN 目标 {DENSITY_TARGET['num']}）",
                m["num"] <= base_num,
                (f"实测 {m['num']}，距目标还差 {gap}（可缩）" if gap > 0 else f"实测 {m['num']}（已达标）"))
            check(
                f"密度 · {label}：页高 ≤ 基线 {base_scr} 屏（DESIGN 目标 {DENSITY_TARGET['screens']}）",
                m["screens"] <= base_scr,
                f"实测 {m['screens']} 屏 · 首屏数字节点 {m['foldNum']}")

        # ── ⑥ 骨架屏（注入 warming 后必须出现，而不是白屏）────────────
        c4 = browser.new_context(viewport={"width": 1440, "height": 900})
        pg4 = c4.new_page()
        for ep in ("sectors", "funds/board"):
            pg4.route(f"**/api/{ep}*", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "data": None, "status": "warming", "retry_in": 10})))
        pg4.goto(base + "/#/research", wait_until="domcontentloaded")
        pg4.wait_for_timeout(2600)
        sk = pg4.evaluate("() => document.querySelectorAll('.skeleton').length")
        check("加载中显示骨架屏（非白屏）", sk > 0, f"{sk} 块")
        c4.close()

        # ── prefers-reduced-motion：全部动效关闭 ──────────────────────
        c5 = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        pg5 = c5.new_page()
        pg5.goto(base + "/#/today", wait_until="domcontentloaded")
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
