# HANDOFF.md — 智能体交接记录

> **用途：切换智能体时，交接方写「我做到哪了」，接手方读完就知道从哪继续。**
> **只有最上面一个交接块是「当前」**；历史块进 `docs/_archive/`。**预算 ≤160 行**，由 `tests/test_boot_budget.py` 钉住。

---

## 交接块模板

```markdown
### 交接：YYYY-MM-DD（A → B）

**当前进度：** 一句话说明做到哪了

**下一步：** 接手方要做什么（具体到可执行）

**注意事项：** 有什么坑、什么文件别动、什么还没对齐

**关键文件：** 涉及哪些文件的最新状态
```

---

## 交接记录

### 交接：2026-10-14（DSH → 下一位）

**当前进度：**
重构 **S0 + S1 + S1' 全部**已完成。`legacy/` 现有 **14 个命题层模块 + 21 个测试**：
S1 五个 + S1'-a 两个 + S1'-c1 两个 + **S1'-c2 五个**（`thermometer` `fund_scorer` `rebalance_advisor`
`strategy_engine` `backtest`）+ `src/output/reporter.py` + `src/cli/backtest_cmds.py`。
**S1'-b 口径拆分**：保留模块 `src/analysis/fund_classify.py`（`type_bucket`/`format_fee`）。
**CLI 命令表 38 → 25**；web 侧全部被冻模块改**占位降级**，`src/web/app.py` 可启动（实测）。
**门禁**：`pytest -q` **538 passed**、boot_budget 3 passed、数据契约 24 项 0 失败。

**下一步（按顺序，每步跑 pytest）：**
1. **S1''**：`src/` 目录分层 `ingest/governance/warehouse/features/ledger/audit/ml/deliver`
   —— ⚠️ 大面积改 import（kept 模块 + `cli` + `web`）。建议用 `git mv` 分批 + 逐批跑 pytest。
2. **S3（前端 + 交付物）**：`frontend/` 仍引用被冻路由（`/api/sectors` `/api/sentiment` `/api/funds`
   `/api/rebalance` `/api/overview` `/api/explain` `/api/recommend` `/api/quant_models`，现返回空）
   —— 改为**行为审计一页**（`behavior` + `counterfactual` + `portfolio` → 一页漏损报告）。
3. **S2**：ML 目标重定向（PIT 面板复用，标签 →「该笔是否亏损/是否触发 7 天惩罚费」）。

**注意事项（坑）：**
- ⚠️ **冻结前先按「谁 import 了它」分类**；有引用方必须连带改（含**直接读源文件的测试**）。
- ⚠️ **口径先拆再冻**；**web 用占位降级**别删 import。
- ⚠️ 冻结后 `test_doc_refs` 必报断链 → 历史文档登记 `EXCEPTIONS`（现 **41** 条）。
- ⚠️ 本轮**已 commit** `ab22557`（S0+S1）；S1'-a/b/c1/c2 的改动**尚未 commit**。

**关键文件：** `docs/05-重构/00_重构蓝图.md`、`docs/agents/STATUS.md`（顶部=现在）、
`legacy/`（归档区，14 模块）、`src/analysis/fund_classify.py`（保留口径）、`tests/test_doc_refs.py`（EXCEPTIONS 41 条）。
