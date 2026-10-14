# STATUS.md — 当前状态（单一事实来源）

> **只装「现在」**；历史一律进 `docs/_archive/`。**预算 ≤160 行**，由 `tests/test_boot_budget.py` 钉住。

---

**最后更新：2026-10-14（S0 + S1 + S1'-a/b/c1）｜更新人：DSH**

## 现在是什么项目
**《基于多源金融数据的个人投资者交易行为分析与建模》**（原「量化基金系统」）。
定位：**多源金融数据平台（采集→治理→分层→PIT 特征）+ 投资者行为审计与建模**。
换锚理由与证据：`docs/05-重构/00_重构蓝图.md`。

## 本轮已完成
**S0（治理骨架）**：清理 33 个整库快照（`data/` **30.8→7.3 GB**）；落地 `AGENTS.md` + `docs/agents/` +
`docs/00-规范/` + `docs/05-重构/` + `docs/_archive/` + `tests/test_boot_budget.py`；安全网 `git tag refactor-baseline-20261014`。

**S1 / S1'（归档冻结，`git mv` → `legacy/`，共 **11** 个模块，不是删除）**
- S1：`vol_model_comparison` `garch_baseline` `factor_test` `return_cluster` `temperature_history` + 脚本 2 + 测试 3
- S1'-a：`sector_analyzer` `sentiment_monitor` + 测试 1（摘 CLI `sector`/`sentiment`）
- **S1'-b（口径拆分）**：新建**保留**模块 `src/analysis/fund_classify.py`（`type_bucket` / `format_fee` /
  申购状态标记），`fund_scorer` 改 re-export；`reporter` / `web` / `cli` 改指向新模块。
- S1'-c1：`historical_recommender` `oos_validate` + 测试 2（摘 CLI `recommend`/`oos`）

**web 侧策略**：被冻结模块的 import 一律改**占位降级**（路由保留、返回空/错误），不破坏 app 启动。

## 门禁（实测）
- `pytest -q` → **772 passed**（835→812→804→772，均因模块随归档出树）。
- `pytest tests/test_boot_budget.py -q` → **3 passed**。
- `scripts/check_ledger_invariants.py` → **24 项 0 失败**。
- CLI 命令表已无 `sector`/`sentiment`/`recommend`/`oos`（实测）。
- `data/` 占用 **7.3 GB**。

## 未收口
- **S1'-c2**：冻 `thermometer` `fund_scorer` `rebalance_advisor` `strategy_engine` `backtest`
  —— ⚠️ 各自连带改 `reporter.py`、`src/web/app.py`（`/api/overview` `/api/explain` `/api/rebalance`
  `/api/funds` `/api/all` `/api/quant_models`）、`cli/analysis_cmds.py`/`output_cmds.py`/`backtest_cmds.py`，
  以及 `test_explain_api` `test_temp_*` `test_thermometer` `test_rebalance_advisor` `test_strategy_engine`
  `test_backtest` `test_equity_ssot` `test_api_degraded` `test_constraint_api` `test_e2_materialized`
  `test_fee_ter` `test_purchase_status` `test_batch_a_thresholds` `test_web_api`（**约 14 个**）。
- **S1''**：`src/` 目录分层 `ingest/governance/warehouse/features/ledger/audit/ml/deliver`。
- **S2** ML 目标重定向；**S3** 一页行为审计交付物 + 论文口径。

## 已知坑（不许再踩）
- ⚠️ **冻结前先按「谁 import 了它」分类**（已三次踩）；有引用方必须连带改。
- ⚠️ **web 侧 frozen 模块用「占位降级」**，别直接删 import（会让 app 启动失败）。
- ⚠️ **口径先拆再冻**：load-bearing 口径（`type_bucket` 等）必须剥到保留模块，否则冻不动。
- ⚠️ 冻结后 `test_doc_refs` 必报断链 → 历史文档登记 `EXCEPTIONS`（现 20 条），当前文档改路径。
- ⚠️ **数据新鲜度守卫是假绿**：用**全局 `max(nav_date)`**（被 7 只持仓顶到 `2026-09-24`），
  而 **22,738 只基金真实停在 `2026-09-18`**。应改**覆盖率判据**。
- ⚠️ `trade_calendar` 只到 `2026-09-09`（**13 条持仓批次**未覆盖）。
- ⚠️ `transactions` 卖出 **34** vs `holdings` 已卖 **30**（3 笔部分卖出未回写）；已卖批次 `shares` 全空。
- ⚠️ `market_temperature.pe_score=50.0` 有 **274/5210 天**是占位值。
- ⚠️ **`test_doc_refs` 的归档跳过有 bug**（`os.sep` 匹配不上 git 的 `/`）。
- ⚠️ `pytest` 须串行、每次换全新 `--basetemp`；沙箱内需 `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`。
- ⚠️ **未 commit**：所有改动在工作区，需用户确认后再 commit。
