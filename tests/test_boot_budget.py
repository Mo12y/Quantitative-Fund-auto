#!/usr/bin/env python3
"""test_boot_budget.py — 「开工必读链」体积守卫（2026-10-14 新增）

## 为什么要有这条守卫（移植自参照项目 `D:\大学\new` 的同名守卫）

本项目"文档太多、新智能体接手要读半天"是**反复犯的毛病**，不是一次性疏漏：
治理骨架刚立，若不钉住预算，`STATUS.md` 会像参照项目那样重新长到 885 行 / 64 KB（≈32k tokens），
而 `AGENTS.md §五-2` 明明写着「> 30KB 就该滚动归档」——**越限 2.1 倍，规则等于没有**。
⇒ 结论：**没有守卫的预算等于没有预算**，所以立此条把它钉住。

## 依据（一手，与参照项目一致）

1. Anthropic《How Claude remembers your project》：`target under 200 lines per CLAUDE.md file.
   Longer files consume more context and reduce adherence.` ⇒ 「单个指令文件目标 < 200 行」。
2. llms.txt v2：`Agents are best served by concise, expert-level information gathered in a single,
   accessible location.` ⇒ 「精炼 + 单一可及位置」。
3. Chroma《Context Rot》(2025)：`models do not use their context uniformly; their performance grows
   increasingly unreliable as input length grows` ⇒ 「长 ≠ 只是慢，是更不可靠」。

## 本守卫管什么（三条）
1. 四个必读/高频文件的**行数**不得超过预算；
2. 「开工必读链」的**字符总量**不得超过总预算（防靠"把行写更长"绕过第 1 条）；
3. 必读链文件**必须存在**（防有人删了文件让守卫空转）。

⚠️ 调大预算需要理由，且**要同步 `AGENTS.md §六`**——否则文档与守卫两套说法。
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 行数预算（依据 Anthropic 的「< 200 行」，结合本仓实际）
LINE_BUDGET = {
    'AGENTS.md': 200,                 # L0：自动加载，最贵
    'docs/agents/STATUS.md': 160,     # L1：全文读，只装"现在"
    'docs/agents/HANDOFF.md': 160,    # L1：只读最上面一个交接块
    'docs/agents/TASKS.md': 200,      # L2：只跳读
}
# 「开工必读链」字符总预算（L0 + L1 + L2 四个文件合计）
CHAIN_CHAR_BUDGET = 24000


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


def test_boot_files_exist():
    missing = [rel for rel in LINE_BUDGET if not os.path.exists(os.path.join(ROOT, rel))]
    assert not missing, "开工必读链文件缺失（守卫会空转）：%s" % missing


def test_boot_files_line_budget():
    over = []
    for rel, budget in LINE_BUDGET.items():
        n = len(_read(rel).splitlines())
        if n > budget:
            over.append("%s: %d > %d" % (rel, n, budget))
    assert not over, "以下必读文件超行数预算（请滚动归档到 docs/_archive/）：\n  " + "\n  ".join(over)


def test_boot_chain_char_budget():
    total = sum(len(_read(rel)) for rel in LINE_BUDGET)
    assert total <= CHAIN_CHAR_BUDGET, (
        "开工必读链字符总量 %d > 预算 %d（靠把行写更长绕不过行数守卫）"
        % (total, CHAIN_CHAR_BUDGET)
    )
