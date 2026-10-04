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
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("缺少 playwright。请用装了它的 Python 运行：\n"
             "  pip install playwright && python -m playwright install chrome")

ENTRIES = ["今天", "持仓", "研究", "设置"]
HASHES = {"今天": "today", "持仓": "position", "研究": "research", "设置": "settings"}

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  | " + str(detail)[:160]) if detail else ""))


def probe_server(base: str, tries: int = 40) -> None:
    """等服务就绪；顺带给出"服务没起"的明确指引，而不是让 Playwright 报超时。"""
    for _ in range(tries):
        try:
            with urllib.request.urlopen(base + "/v2", timeout=5) as r:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5020")
    ap.add_argument("--json", default=None, help="把结果另存为 JSON")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    probe_server(base)
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

        def go(label: str) -> str:
            page.get_by_role("button", name=label, exact=True).click()
            page.wait_for_timeout(3500)
            return page.inner_text("body")

        t = go("持仓")
        check("「持仓」页：调仓 + 建仓计划 + 定投", "调仓" in t and "建仓计划" in t and "定投计划" in t)

        t = go("研究")
        check("「研究」页：行业板块 + 筛选池", "行业板块" in t and "筛选池" in t)
        check("「研究」页：量化模型已移出", "量化模型" not in t)
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

    ok = sum(1 for r in RESULTS if r["ok"])
    print("\n" + "=" * 72)
    print("汇总：%d/%d 通过" % (ok, len(RESULTS)))
    failed = [r["check"] for r in RESULTS if not r["ok"]]
    if failed:
        print("失败项：")
        for f in failed:
            print("  - " + f)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(RESULTS, fh, ensure_ascii=False, indent=2)
        print("结果已写入 " + args.json)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
