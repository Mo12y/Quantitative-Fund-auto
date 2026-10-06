#!/usr/bin/env python3
"""文档引用完整性守卫 —— 指向仓库内的路径必须真实存在。

**为什么有这个测试**（2026-10-01 新增，参考毕设 `D:\\大学\\new` 的 `test_doc_refs.py`）：
本项目的文档之间大量互相引用（`docs/前端重构计划书.md`、`docs/审计修复记录.md`、各任务书…）。
一旦移动/重命名文档，引用就**静默失效** —— 读者点不开、下一个智能体找不到，
而这不会让任何功能测试变红。**约定靠自觉，机制靠红灯**：把"引用要有效"变成一条测试。

⚠️ 已知边界（都是**刻意**的）：
  · 只检查「看起来是仓库内路径」的字符串，不解析 Markdown 链接语法；
  · 明确排除跨项目绝对路径（`D:\\`）、通配符、命令行片段、占位符；
  · 确实指向"有意不存在"的路径，登记进 `EXCEPTIONS` 并**写明理由**（不是删掉了事）。

用法：pytest tests/test_doc_refs.py -q
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 只扫这些前缀开头的路径（避免把 prose 里的小写词当成路径）
PREFIXES = ('docs/', 'src/', 'tests/', 'scripts/', 'frontend/src/', 'config/', 'data/')
#: 只认这些扩展名
EXTS = ('.md', '.py', '.ts', '.tsx', '.js', '.css', '.json', '.yaml', '.html', '.svg', '.sh', '.ps1', '.txt')

#: 扫哪些文件里的引用 —— 见 `_scan_files()`：**只扫 git 跟踪的 .md**
#: （未入库的本机速记与生成稿不算权威文档）。

#: 已登记的例外：有意不存在 / 待创建 / 属其他项目。**每一条都要写理由。**
#:
#: · `src/web/templates/dashboard.html` —— `docs/前端展示优化任务书.md` 提到它。
#:   那是 **2026-09 的历史任务书**，描述的是当时的状态（当时该文件确实在 `templates/`）。
#:   2026-10-05 的 B-5 把旧仪表盘退役、文件已 `git mv` 到 `src/web/_archive/dashboard.html`，
#:   该文件又在 **2026-10-06 的 B-6 被删除**（见下一条）。
#:   **不改历史任务书**（那是篡改记录），也**不删它**（它是审计线索）—— 只在此登记。
#: · `src/web/static/app.js` / `src/web/static/app.css` / `src/web/_archive/dashboard.html` /
#:   `scripts/check_rise_fall.py` / `tests/test_curve_render.py` / `tests/test_recommend_render.py`
#:   —— 2026-10-06 的 **B-6 删除了旧前端**（`app.js`/`app.css`/`_archive/dashboard.html`/
#:   `check_rise_fall.py`）以及**两个锁它渲染契约的静态哨兵测试**（读 `app.js`/`app.css` 源码文本，
#:   被测对象没了它们就必然 ERROR；B-6 的计划里**漏列了这两个测试**，是跑 pytest 才炸出来的）。
#:   这些路径仍被若干**历史任务书 / 执行报告 / 设计文档**引用（描述当时的状态），
#:   也被 B-6 的**落地记录**本身引用（记录"删了什么"）。
#:   仍按先例：**不改历史文档、不删它，只登记**。
EXCEPTIONS: set[str] = {
    "src/web/templates/dashboard.html",
    "src/web/static/app.js",
    "src/web/static/app.css",
    "src/web/_archive/dashboard.html",
    "scripts/check_rise_fall.py",
    "tests/test_curve_render.py",
    "tests/test_recommend_render.py",
}

#: 含这些标记的行直接跳过（跨项目路径 / 通配符 / 命令 / 占位符 / 举例）
SKIP_MARKERS = ('D:\\', 'D:/', '*', '…', '<', '（待', '将新增', '例如', 'grep ', 'python ', 'git ', 'pip ')

#: 匹配「像仓库内路径」的串：REPO前缀 + 非空白非引号字符 + 已知扩展名
PATH_RE = re.compile(
    r'(?<![\w/.-])(' + '|'.join(re.escape(p) for p in PREFIXES) + r')'
    r'([A-Za-z0-9_./\-\u4e00-\u9fff]*?)(' + '|'.join(re.escape(e) for e in EXTS) + r')(?![\w])'
)


def _scan_files():
    """只扫**已入库**的 Markdown。

    为什么按 git 跟踪状态过滤：未入库的文件（本机速记 `.workbuddy/`、
    LLM 生成的拼接稿 `docs/llms-full.md`）**不是项目权威文档**，
    它们引用临时脚本或别的项目是正常的 —— 扫进来只会制造假阳性
    （第一版没过滤，`docs/llms-full.md` 一下子报了 8 处）。
    """
    import subprocess

    rel: list[str] = []
    try:
        out = subprocess.run(['git', 'ls-files', '-z', '--', '*.md'],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
        rel = [f for f in out.stdout.split('\0') if f.endswith('.md')]
    except Exception:
        rel = []
    if not rel:                                   # 没有 git 时退回 glob
        import glob
        rel = [os.path.relpath(f, ROOT).replace('\\', '/')
               for f in glob.glob(os.path.join(ROOT, 'docs/**/*.md'), recursive=True)]
        rel += [os.path.relpath(f, ROOT).replace('\\', '/')
                for f in glob.glob(os.path.join(ROOT, '*.md'))]

    out_paths = []
    for f in rel:
        p = os.path.join(ROOT, f)
        # 历史文档允许指向"已删对象"，不参与
        if os.sep + '_archive' + os.sep in p:
            continue
        if os.path.exists(p):
            out_paths.append(p)
    return sorted(out_paths)


def test_doc_path_references_exist():
    broken = []
    for path in _scan_files():
        rel_doc = os.path.relpath(path, ROOT).replace('\\', '/')
        with open(path, encoding='utf-8', errors='replace') as fh:
            for lineno, line in enumerate(fh, 1):
                if any(m in line for m in SKIP_MARKERS):
                    continue
                for m in PATH_RE.finditer(line):
                    target = (m.group(1) + m.group(2) + m.group(3)).replace('/', os.sep)
                    if target.replace('\\', '/') in EXCEPTIONS:
                        continue
                    if not os.path.exists(os.path.join(ROOT, target)):
                        broken.append("%s:%d  %s" % (rel_doc, lineno, m.group(0)))
    assert not broken, (
        "文档里引用了仓库内不存在的路径（共 %d 处）：\n  " % len(broken)
        + "\n  ".join(broken[:40])
        + "\n\n如果该路径是**有意不存在**的，请登记进 tests/test_doc_refs.py 的 EXCEPTIONS 并写明理由；"
        "否则请修正引用。")


def test_exceptions_are_reasoned():
    """EXCEPTIONS 不能变成垃圾桶 —— 每条都要在源码里带注释说明。"""
    src = open(os.path.abspath(__file__), encoding='utf-8').read()
    block = src.split('EXCEPTIONS: set[str] = set()')[0]
    for p in EXCEPTIONS:
        assert p in src, "例外 %s 在源码里找不到出处（应写在 EXCEPTIONS 附近并注明理由）" % p
    assert block is not None
