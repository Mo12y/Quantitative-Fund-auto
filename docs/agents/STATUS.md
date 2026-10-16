# STATUS.md — 当前状态（单一事实来源）

> **只装「现在」**；历史一律进 `docs/_archive/`。**预算 ≤160 行**，由 `tests/test_boot_budget.py` 钉住。

---

**最后更新：2026-10-16（T0.3 / T1.1 / T1.2 / T2.0 / T2.1 完成）｜更新人：DSH**

## 现在是什么项目
**《基于多源金融数据的个人投资者交易行为分析与建模》**（原「量化基金系统」）。
定位：**多源金融数据平台（采集→治理→分层→PIT 特征）+ 投资者行为审计与建模**。
换锚理由与证据：`docs/05-重构/00_重构蓝图.md`。

## 本轮已完成
**S0（治理骨架）**：清理 33 个整库快照（`data/` **30.8→7.3 GB**）；落地 `AGENTS.md` + `docs/agents/` +
`docs/00-规范/` + `docs/05-重构/` + `docs/_archive/` + `tests/test_boot_budget.py`；安全网 tag `refactor-baseline-20261014`。

**S1 / S1'（归档冻结命题层 → `legacy/`，共 **14** 个模块 + **21** 个测试，`git mv` 非删除）**
- S1：`vol_model_comparison` `garch_baseline` `factor_test` `return_cluster` `temperature_history`
- S1'-a：`sector_analyzer` `sentiment_monitor`
- S1'-b：**口径拆分**——新建**保留**模块 `src/analysis/fund_classify.py`（`type_bucket`/`format_fee`/申购标记）
- S1'-c1：`historical_recommender` `oos_validate`
- **S1'-c2**：`thermometer` `fund_scorer` `rebalance_advisor` `strategy_engine` `backtest`
  + `src/output/reporter.py` + `src/cli/backtest_cmds.py`

**CLI 精简**：命令表从 38 → **25**，已摘 `score`/`temp`/`rebalance`/`sentiment`/`sector`/`recommend`/
`oos`/`strategy`/`backtest`/`backtest2`/`backtest3`/`backtest4`/`report`。
**web 侧**：所有被冻模块的 import 改**占位降级**（路由保留、返回空），`src/web/app.py` 可正常启动（实测）。

## 决策支持系统重构（2026-10-15 起）
顶层计划：`docs/05-重构/01_决策支持系统_执行计划书.md`（5 阶段；数据刷新 T0.1/T0.2 已决策**延期**）。
- **T0.3 已完成**：修"新鲜度守卫假绿" —— 第 19 条由"物化表 `asof` 追平最新净值日"（全局 MAX，
  被 7 只持仓顶绿）改为**净值新鲜度覆盖率 ≥ 90%**（窗口 = `index_daily` 最近 5 个交易日），
  "看不见数据一律判红"；负对照固化于 `tests/test_data_contract.py`（15 passed）。
- **T1.1 已完成**：题目与入口对齐（README 全文重写 +「系统 vs 实例」诚实声明；DESIGN / AGENTS §一 / docs/README）。
- **T1.2 已完成**：权威口径重写（行为镜实测 + 反事实占位 + 体积三档；引用点同步，含 AGENTS 基线 538→541）。
- **T2.0 已完成**：行为口径对照表（8 口径全判【项目自定】）+ spot-check 9 项（2 项差异已记录）。
- **T2.1 已完成**：决策台 7 个 API（behavior / counterfactual / breakeven / risk / peer / exclusion /
  research〔`/api/quant_models` 改名〕）；参照系缓存补 **ter 网格**并重建（旧 5 指标零漂移）；
  新增 `tests/test_web_behavior_api.py` 8 例（真实数据锚点 + 与 CLI 同源一致）。
- **下一步**：T2.2（删壳 + `precompute` 同步）→ T2.3 → 阶段 3~5（见计划书 §6/§7）。

## 门禁（实测）
- `pytest -q` → **549 passed**（S1' 后 538；T0.3 净 +3、T2.1 净 +8）。
- `pytest tests/test_web_behavior_api.py -q` → **8 passed**（决策台 7 路由：真实锚点 + 同源一致）。
- `pytest tests/test_data_contract.py -q` → **15 passed**（T0.3 负对照含"红→恢复→绿"完整演绎）。
- `pytest tests/test_boot_budget.py -q` → **3 passed**。
- `import src.web.app` → **OK**；CLI 命令表 **25 个**（实测）。
- `scripts/check_ledger_invariants.py` → **24 项 0 失败**（第 19 条 = 净值新鲜度覆盖率，实测 94.7%）。
- `data/` 顶层 3 db **7.3 GB**（全目录 **10.5 GB**，另含 `backups/` ≈3.2 GB）。

## 未收口
- **决策支持系统重构（2026-10-15 起）**：T1.1~T5.2 未做 → 按计划书推进。
  ⚠️ 下列旧未收口项与新计划书的关系**待拍板**：S1''（目录分层）/ S2（ML 重定向）计划书未列；
  S3 / 前端已被计划书**阶段 3**（决策台三页）吸收。
- **S1''**：`src/` 目录分层 `ingest/governance/warehouse/features/ledger/audit/ml/deliver`
  —— ⚠️ 会**大面积改 import**（kept 模块 + cli + web），最后做。
- **S2**：ML 目标重定向（标签 →「该笔是否亏损/是否触发 7 天惩罚费」）。
- **S3**：一页行为审计交付物 + 论文口径。
- **前端**：`frontend/` 仍引用被冻路由（`/api/sectors` `/api/sentiment` `/api/funds` `/api/rebalance`
  `/api/overview` `/api/explain` `/api/recommend` `/api/quant_models`），现返回空 → **需随 S3 一并重构**。

## 已知坑（不许再踩）
- ⚠️ **冻结前先按「谁 import 了它」分类**（已四次踩）；有引用方必须连带改。
- ⚠️ **口径先拆再冻**（S1'-b 已验证）：load-bearing 口径（`type_bucket` 等）必须剥到保留模块。
- ⚠️ **web 侧用「占位降级」**，别删 import；路由保留但返回空。
- ⚠️ 冻结后 `test_doc_refs` 必报断链 → 历史文档登记 `EXCEPTIONS`（现 **42** 条），当前文档改路径。
- ⚠️ 有的**测试直接读源文件**（如 `test_fee_semantics.py` 读 `fund_scorer.py`）→ 归档模块时必连带归档。
- 数据新鲜度守卫（第 19 条）**已修（T0.3）**：原「全局 `max(nav_date)` 顶绿」已改**覆盖率判据**
  —— 见上「决策支持系统重构」节。
- ⚠️ **守卫第 13 条对空 `index_daily` 会崩**（`MIN(close)` 为 NULL → `%.2f` TypeError）——
  T0.3 测试构造时发现、属既有缺陷**未修**；影响：空/新库跑守卫会中断；修法 = 重排第 13 条
  detail 构造（可随 T2.x 捎带）。
- ⚠️ `trade_calendar` 只到 `2026-09-09`（**13 条持仓批次**未覆盖）。
- ⚠️ `transactions` 卖出 **34** vs `holdings` 已卖 **30**；已卖批次 `shares` 全空。
- ⚠️ `market_temperature.pe_score=50.0` 有 **274/5210 天**是占位值。
- ⚠️ **`test_doc_refs` 的归档跳过有 bug**（`os.sep` 匹配不上 git 的 `/`）。
- ⚠️ `pytest` 须串行、每次换全新 `--basetemp`；沙箱内需 `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`。
