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
重构 **S0 + S1 + S1'-a/b/c1** 已完成。`legacy/` 现有 **11 个模块**：
S1 五个（`vol_model_comparison` `garch_baseline` `factor_test` `return_cluster` `temperature_history`）
+ S1'-a（`sector_analyzer` `sentiment_monitor`）+ S1'-c1（`historical_recommender` `oos_validate`）
+ 脚本 2 + 测试 6。
**S1'-b 口径拆分**：新建**保留**模块 `src/analysis/fund_classify.py`（`type_bucket`/`format_fee`/申购标记），
`fund_scorer` re-export，`reporter`/`web`/`cli` 已改指向它。
CLI 已摘 `sector`/`sentiment`/`recommend`/`oos`；web 侧 3 处 import 改**占位降级**。
**门禁**：`pytest -q` **772 passed**、boot_budget 3 passed、数据契约 24 项 0 失败。

**下一步（S1'-c2，按顺序，每步跑 pytest）：**
冻 `thermometer` `fund_scorer` `rebalance_advisor` `strategy_engine` `backtest`。各**连带**改：
- `src/output/reporter.py`（`thermometer` + `FundScreener`）、`src/web/app.py`
  （`/api/overview` `/api/explain` `/api/rebalance` `/api/funds` `/api/all` `/api/quant_models` 及 `_all_*` 助手）、
  `src/cli/analysis_cmds.py`（`cmd_score`/`cmd_temp`/`cmd_rebalance`）、`output_cmds.py`（`cmd_report`）、
  `backtest_cmds.py`（整组 `backtest*`/`strategy`）；摘 CLI 注册 `score`/`temp`/`rebalance`/`strategy`/`backtest*`；
- 测试（约 14 个）：`test_explain_api` `test_temp_insufficient` `test_temp_scope` `test_thermometer`
  `test_rebalance_advisor` `test_strategy_engine` `test_backtest` `test_equity_ssot` `test_api_degraded`
  `test_constraint_api` `test_e2_materialized` `test_fee_ter` `test_purchase_status` `test_batch_a_thresholds` `test_web_api`
  —— 逐个判断「测的是命题层 → 一并归档」还是「测的是保留口径 → 改指向」。
- 之后 **S1''**（`src/` 目录分层）、**S2**（ML 重定向）、**S3**（一页审计）。

**注意事项（坑）：**
- ⚠️ **口径先拆再冻**（S1'-b 已验证）：`type_bucket` 这类 load-bearing 口径不剥出来，就冻不动 `fund_scorer`。
- ⚠️ **web 侧用「占位降级」**，别删 import；路由保留但返回空。
- ⚠️ 冻结后 `test_doc_refs` 必报断链 → 历史文档登记 `EXCEPTIONS`（现 20 条），当前文档改路径。
- ⚠️ **未 commit**：本轮改动全在工作区；回滚点 `refactor-baseline-20261014`。

**关键文件：** `docs/05-重构/00_重构蓝图.md`、`docs/agents/STATUS.md`（顶部=现在）、
`legacy/`（归档区，11 模块）、`src/analysis/fund_classify.py`（新保留口径）、`tests/test_doc_refs.py`（EXCEPTIONS 20 条）。
