# STATUS.md — 当前状态（单一事实来源）

> **只装「现在」**；历史一律进 `docs/_archive/`。**预算 ≤160 行**，由 `tests/test_boot_budget.py` 钉住。

---

**最后更新：2026-10-16（**T0.1/T0.2 数据刷新完成** · T0.3 ~ T5.2 全部完成 · 全链路收口）｜更新人：DSH**

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
顶层计划：`docs/05-重构/01_决策支持系统_执行计划书.md`（5 阶段；**T0.1/T0.2 数据刷新已完成** 2026-10-16）。
- **T0.1 / T0.2 已完成**（2026-10-16 数据刷新）：快照 → `snapshot`/`nav`/`calendar`/`index` 全量采集
  （snapshot +44,963 行、nav 591 只 +948,633 行、日历至 **2026-12-31**）→ 守卫复跑 **24 项 0 失败**。
  期间修出**两个真 bug**：① `collector._parse_size` 不做万→亿换算（**257 行**被写成万元数值，
  守卫第 7 条首跑报红 224 条 → 全部 ÷10000 回写，快照逐行比对确认）；
  ② `parse_daily_snapshot` 把 acc 越界/缺失写成 `0.0`（**21 行**，实测 NULL 计数 14083→14104）→ 改 **None**。
  新增 `TestParseSize` **6 例** + 改写 1 例（zeroed→nulled）；**刷新后全量数字复核并回写权威口径 §一/二/四/六/七**。
- **T0.3 已完成**：修"新鲜度守卫假绿" —— 第 19 条由"物化表 `asof` 追平最新净值日"（全局 MAX，
  被 7 只持仓顶绿）改为**净值新鲜度覆盖率 ≥ 90%**（窗口 = `index_daily` 最近 5 个交易日），
  "看不见数据一律判红"；负对照固化于 `tests/test_data_contract.py`（15 passed）。
- **T1.1 已完成**：题目与入口对齐（README 全文重写 +「系统 vs 实例」诚实声明；DESIGN / AGENTS §一 / docs/README）。
- **T1.2 已完成**：权威口径重写（行为镜实测 + 反事实占位 + 体积三档；引用点同步，含 AGENTS 基线 538→541）。
- **T2.0 已完成**：行为口径对照表（8 口径全判【项目自定】）+ spot-check 9 项（2 项差异已记录）。
- **T2.1 已完成**：决策台 7 个 API（behavior / counterfactual / breakeven / risk / peer / exclusion /
  research〔`/api/quant_models` 改名〕）；参照系缓存补 **ter 网格**并重建（旧 5 指标零漂移）；
  新增 `tests/test_web_behavior_api.py` 8 例（真实数据锚点 + 与 CLI 同源一致）。
- **T2.2 已完成**：删壳 —— 6 个占位类 + 6 个死路由（funds / rebalance / sectors / sentiment /
  recommend / funds·board）+ 全部连带辅助/常量/后台预热；`precompute` 只剩 overview（**0.2s**）；
  删 `test_api_degraded.py` 等 **15** 个死路由测试（EXCEPTIONS 登记）；死缓存快照已清。
- **T2.3 已完成**：`main.py` 无参数改为打印帮助（退出码 0，原调已删的 `report` 会崩）；
  docstring 对齐真实 25 命令。**阶段 2 收口**。
- **T3.1 / T3.2 已完成**：前端重做为三页 —— `Decision`（决策台，7 个新 API）/ `Ledger`（账本）/
  `Data`（数据状态）+ 三入口路由；删旧四页与 5 个死组件；`verify_frontend.py` 改造
  （**61 项全绿** = 三入口 + 新判据 + **密度基线按新页重设**）；品牌改「基金决策台」。**阶段 3 收口**。
- **T4.2 已完成**（阶段 4 的可选任务）：`Database` **相对路径按项目根解析**（不再随 cwd）——
  项目根外构造不再在 cwd 找/建空库；新增 `tests/test_database_path.py` 4 例。
- **T5.1 已完成**：反事实正式整理并**回填**权威口径 §七（**T0.1 刷新后该表全量重算**：实际
  **−55.26%** vs 完全不动 **−61.27%** = **+601 bp/年**，同期基准 −14.84%）；卖出规则/同标的表
  一并整理；T2.0 的两项差异（94 vs 93 笔、单只 vs 汇总）**闭环**。
- **T5.2 已完成**：论文叙事与结论分级（`docs/05-重构/02_论文叙事与结论分级.md`）—— 正结果主体
  （行为度量 / 风险刻画 / 排雷）+ **负结果边界**（选基）+ 分级总表 + 答辩要点 + 禁用表述清单；
  每结论标【实测】/【文献】。**全链路收口 ✅**（计划书 §8 七项验收全绿）。
- **下一步**：维护期 —— 论文按导师意见迭代（先读 `02_论文叙事` §五 禁用表述）。
  ⚠️ **论文初稿不在本工作区做**（用户另开新工作区同步本项目后制作）。

## 门禁（实测）
- `pytest -q` → **544 passed**（演化：S1' 后 538；T0.3 +3、T2.1 +8、T2.2 −15、T4.2 +4〔净 +3〕；
  **T0.1 +6**〔`_parse_size`/acc 过滤等新测试〕）。
- `python src/main.py precompute` → 只剩 overview，**0.2s**（原含筛选池 ~110s）。
- `python scripts/verify_frontend.py` → **61 项全绿**（T3：三入口 + 密度基线按新页重设）。
- `npm run build`（frontend/）→ 成功（`tsc -b` 0 错 + vite ~7s）。
- `python src/main.py`（无参数）→ 打印帮助，**退出码 0**；输出含 `behavior`/`counterfactual`、不含 `score`/`temp`（T2.3）。
- `pytest tests/test_web_behavior_api.py -q` → **8 passed**（决策台 7 路由：真实锚点 + 同源一致）。
- `pytest tests/test_data_contract.py -q` → **15 passed**（T0.3 负对照含"红→恢复→绿"完整演绎）。
- `pytest tests/test_boot_budget.py -q` → **3 passed**。
- `import src.web.app` → **OK**；CLI 命令表 **25 个**（实测）。
- `scripts/check_ledger_invariants.py` → **24 项 0 失败**（第 19 条 = 净值新鲜度覆盖率，实测 **94.6%**；
  第 20/21 条已按"温度冻结 / 日历含未来排期"修订）。
- **计划书 §8 验收总览七项全绿**（2026-10-16 收口终验；T0.1 刷新后复跑契约全绿）：pytest **544** /
  契约 24 / doc_refs / boot_budget / 前端 61 / 死路由 0 命中 / precompute 不崩。
- `data/` 顶层 4 db **9.7 GB**（= 主库 2.44 + 快照 3×2.43；全目录 **12.9 GB**，另含 `backups/` ≈3.2 GB）。
  ⚠️ 快照 3 个（≈7.3 GB）**待用户确认后清理**。

## 未收口
- **决策支持系统重构（2026-10-15 起）**：T0.1 ~ T5.2 **全部完成**（含 2026-10-16 数据刷新），
  §8 验收全绿。**唯一未做**：论文初稿 —— 用户决定在**新工作区**（同步本项目后）制作。
  ⚠️ 下列旧未收口项与新计划书的关系**待拍板**：S1''（目录分层）/ S2（ML 重定向）计划书未列。
- **S1''**：`src/` 目录分层 `ingest/governance/warehouse/features/ledger/audit/ml/deliver`
  —— ⚠️ 会**大面积改 import**（kept 模块 + cli + web），最后做。
- **S2**：ML 目标重定向（标签 →「该笔是否亏损/是否触发 7 天惩罚费」）。
- （S3「一页行为审计 + 论文口径」已由 T5.2 论文叙事文档 + 决策台三页吸收；前端「随 S3 重构」
  亦已完成 —— 2026-10-16 复核：`frontend/` 只引用存活路由，无被冻路由引用。）

## 已知坑（不许再踩）
- ⚠️ **冻结前先按「谁 import 了它」分类**（已四次踩）；有引用方必须连带改。
- ⚠️ **口径先拆再冻**（S1'-b 已验证）：load-bearing 口径（`type_bucket` 等）必须剥到保留模块。
- ⚠️ **web 侧「占位降级」模式已废止**（T2.2 完成删壳）：冻结模块的残留应**整链删除**
  （占位类 + 死路由 + 辅助 + 测试 + CLI job + 缓存键），而不是降级保留 —— 降级只是 S1' 的过渡态。
- ⚠️ 冻结/删除后 `test_doc_refs` 必报断链 → 历史文档与计划书（描述性引用）登记
  `EXCEPTIONS`（现 **46** 条 —— 计数以 `len(EXCEPTIONS)` 实测为准，别手工维护），当前文档改路径。
- ⚠️ 有的**测试直接读源文件**（如 `test_fee_semantics.py` 读 `fund_scorer.py`）→ 归档模块时必连带归档。
- 数据新鲜度守卫（第 19 条）**已修（T0.3）**：原「全局 `max(nav_date)` 顶绿」已改**覆盖率判据**
  —— 见上「决策支持系统重构」节。
- ⚠️ **守卫第 13 条对空 `index_daily` 会崩**（`MIN(close)` 为 NULL → `%.2f` TypeError）——
  T0.3 测试构造时发现、属既有缺陷**未修**；影响：空/新库跑守卫会中断；修法 = 重排第 13 条
  detail 构造（可随后续任一捎带任务修复）。
- ✅ `trade_calendar` 已补至 **2026-12-31**（8,798 天，T0.1；未覆盖持仓批次 **0**）。
- ⚠️ 守卫第 20/21 条已修订（T0.1）：原"温度与指数必须同期"在温度模块归档后**永远判红**；
  原式 `0 <= lag <= 60` 把"日历覆盖到未来"（lag=−77）判红 —— 现分别改为
  「温度不得超前指数（派生方向）/ 日历不落后净值日 >60 天」。
- ⚠️ `collector._parse_size` 的单位口径是**亿元**（含"万"时 ÷10000）——守卫第 7 条
  「规模 ≤500 亿」依赖它；改动必须同步 `test_snapshot_parse.py`。
- ⚠️ `fund_nav.acc_nav` 越界/缺失存 **NULL**（不是 `0.0`）——写 `0.0` 会触发第 4/14 条值域判据
  （21 条货币基金曾踩，T0.1 修复）。
- ℹ️ 数据刷新后**真实数据锚点测试**须同步更新（先例：`test_web_behavior_api.py` 的
  chase 2.51→2.58、市值 854.52→780.18；这是真实库测试的固有代价，接受）。
- ⚠️ `transactions` 卖出 **34** vs `holdings` 已卖 **30**；已卖批次 `shares` 全空。
- ⚠️ `market_temperature.pe_score=50.0` 有 **274/5210 天**是占位值。
- ✅ `test_doc_refs` 的归档跳过 bug **已修**（2026-10-16 T3：`os.sep` 混合斜杠导致
  `_archive/` 从未被跳过；T3 删旧页面时它曾当场暴出 6 处历史断链）。
- ⚠️ 写 `Database` 行为测试时注意：`tests/conftest.py` 全局设 `QFA_DB_ALLOW_CREATE=1` ——
  验证"默认拒绝"语义需先 `monkeypatch.delenv(...)`（先例：`test_database_path.py` / `test_ledger_write.py`）。
- ⚠️ `pytest` 须串行、每次换全新 `--basetemp`；沙箱内需 `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`。
